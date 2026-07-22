"""
Ana v3 — Secretária Virtual de Anestesiologia
PostgreSQL + SQLite, email, Google Calendar, relatórios
"""

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any, List
import os, logging, hashlib, secrets, json, base64, io
import re, time
from datetime import datetime, timedelta
import urllib.request, urllib.error, urllib.parse

try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger("ana")

app = FastAPI(title="Ana v3", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SECRET         = os.environ.get("SECRET_KEY", "ana-secretaria-default-secret-change-me")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL   = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "A.N.A <onboarding@resend.dev>")
SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASS      = os.environ.get("SMTP_PASS", "")
SMTP_FROM      = os.environ.get("SMTP_FROM", "ana@grupo-anestesia.com")
GCAL_CREDS     = os.environ.get("GCAL_CREDENTIALS", "")
GCAL_ID        = os.environ.get("GCAL_CALENDAR_ID", "primary")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL         = os.environ.get("APP_BASE_URL", "")
GOOGLE_ROUTES_API_KEY = os.environ.get("GOOGLE_ROUTES_API_KEY", "")
GEMINI_API_KEY       = os.environ.get("GEMINI_API_KEY", "")
OCR_SPACE_API_KEY    = os.environ.get("OCR_SPACE_API_KEY", "")
VAPID_PUBLIC_KEY     = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY    = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS_EMAIL   = os.environ.get("VAPID_CLAIMS_EMAIL", "rodrigomorlin@gmail.com")

# ── SUPABASE (migração em andamento — coexiste com auth antigo) ──
SUPABASE_URL              = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY         = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_ANON_KEY):
    raise RuntimeError("Configure SUPABASE_URL, SUPABASE_ANON_KEY e SUPABASE_SERVICE_ROLE_KEY no Railway.")
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data as ana_data

# ── AUTH ───────────────────────────────────────────────────
def auth(request: Request):
    """Autenticação: JWT do Supabase (Authorization: Bearer <token>)."""
    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer ") and SUPABASE_URL:
        user = _auth_supabase(request, authz[7:])
        if user:
            return user
    raise HTTPException(401, "Não autorizado.")

def db_log(nivel, msg, usuario="", ip="", org_id=""):
    """Log de aplicação (o log persistente por grupo é o ana_logs, via data.py)."""
    log.info(f"[{nivel}] {msg}" + (f" · user={usuario}" if usuario else ""))

# ── SUPABASE AUTH (JWT via JWKS) ─────────────────────────────
_jwks_client = None
_membership_cache: dict = {}  # user_id → (timestamp, [memberships])

def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        import jwt as pyjwt
        _jwks_client = pyjwt.PyJWKClient(
            f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
            cache_keys=True, lifespan=3600)
    return _jwks_client

def validate_supabase_jwt(token: str) -> Optional[dict]:
    """Valida um JWT do Supabase. Retorna claims ou None."""
    try:
        import jwt as pyjwt
        header = pyjwt.get_unverified_header(token)
        alg = header.get("alg", "")
        if alg in ("RS256", "ES256"):
            key = _get_jwks_client().get_signing_key_from_jwt(token).key
            claims = pyjwt.decode(token, key, algorithms=[alg], audience="authenticated")
        elif alg == "HS256":
            # Projetos antigos do Supabase assinam com o JWT secret (não exposto);
            # sem o secret não dá para validar HS256 — rejeita com log claro.
            log.warning("JWT Supabase HS256 recebido — configure JWT assimétrico no projeto ou forneça o secret.")
            return None
        else:
            return None
        return claims
    except Exception as e:
        log.warning(f"JWT Supabase inválido: {type(e).__name__}: {e}")
        return None

def sb_rest(method: str, path: str, body=None, use_service_role=True, user_jwt: str = ""):
    """Chamada ao PostgREST do Supabase. path ex: '/group_members?user_id=eq.xxx&select=group_id,role'"""
    key = SUPABASE_SERVICE_ROLE_KEY if use_service_role else SUPABASE_ANON_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {user_jwt or key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="ignore")[:300]
        log.error(f"Supabase REST {method} {path} → {e.code}: {body_err}")
        raise HTTPException(502, f"Erro Supabase ({e.code})")

def get_memberships(user_id: str) -> list:
    """Retorna [{group_id, role}] do usuário, com cache de 60s."""
    import time as _t
    cached = _membership_cache.get(user_id)
    if cached and _t.time() - cached[0] < 60:
        return cached[1]
    rows = sb_rest("GET", f"/group_members?user_id=eq.{user_id}&select=group_id,role")
    _membership_cache[user_id] = (_t.time(), rows)
    return rows

def _auth_supabase(request: Request, token: str) -> Optional[dict]:
    """Autentica via JWT Supabase. Retorna user dict compatível com o resto do código."""
    claims = validate_supabase_jwt(token)
    if not claims:
        return None
    user_id = claims.get("sub", "")
    email = claims.get("email", "")
    if not user_id:
        return None
    memberships = get_memberships(user_id)
    if not memberships:
        raise HTTPException(403, "Usuário não pertence a nenhum grupo. Peça convite ao administrador.")
    # Multi-grupo: header X-Group-Id seleciona; valida pertencimento
    wanted = request.headers.get("X-Group-Id", "")
    if wanted:
        m = next((m for m in memberships if m["group_id"] == wanted), None)
        if not m:
            raise HTTPException(403, "Você não pertence a esse grupo.")
    else:
        m = memberships[0]
    # Nome: metadados do JWT ou email
    meta = claims.get("user_metadata") or {}
    nome = meta.get("full_name") or meta.get("name") or (email.split("@")[0] if email else user_id[:8])
    role = "admin" if m.get("role") == "admin" else "medico"
    return {"id": user_id, "nome": nome, "role": role,
            "org_id": m["group_id"], "email": email,
            "auth_source": "supabase", "jwt": token,
            "memberships": memberships}

# ── CONFIG PERSISTENTE (chave/valor no banco, por organização) ──
def get_config(chave: str, default: str = "", org_id: str = "") -> str:
    """Config por grupo — ana_gcal_config no Supabase (calendar_id)."""
    if chave != "gcal_calendar_id" or not org_id:
        return default
    try:
        rows = sb_rest("GET", f"/ana_gcal_config?group_id=eq.{org_id}&select=calendar_id")
        return (rows[0].get("calendar_id") or default) if rows else default
    except Exception:
        return default

def set_config(chave: str, valor: str, org_id: str = ""):
    if chave != "gcal_calendar_id" or not org_id:
        return
    rows = sb_rest("GET", f"/ana_gcal_config?group_id=eq.{org_id}&select=group_id")
    if rows:
        sb_rest("PATCH", f"/ana_gcal_config?group_id=eq.{org_id}", {"calendar_id": valor, "updated_at": "now()"})
    else:
        sb_rest("POST", "/ana_gcal_config", {"group_id": org_id, "calendar_id": valor})

def get_gcal_id(org_id: str = "default") -> str:
    """Prioriza o valor configurado pelo usuário no app; cai para a env var como fallback."""
    return get_config("gcal_calendar_id", GCAL_ID, org_id=org_id)

# ── EMAIL ──────────────────────────────────────────────────
async def send_email(to, subject, body):
    """Envio de email: Resend (RESEND_API_KEY) com fallback SMTP."""
    if not to: return
    if RESEND_API_KEY:
        try:
            payload = json.dumps({"from": EMAIL_FROM, "to": [to],
                                  "subject": subject, "html": body}).encode("utf-8")
            http_req = urllib.request.Request(
                "https://api.resend.com/emails", data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {RESEND_API_KEY}"})
            with urllib.request.urlopen(http_req, timeout=20) as resp:
                r = json.loads(resp.read())
            log.info(f"Email (Resend) → {to} · id={r.get('id','?')}")
            return
        except urllib.error.HTTPError as e:
            log.error(f"Resend erro {e.code}: {e.read().decode('utf-8','ignore')[:300]}")
            return
        except Exception as e:
            log.error(f"Resend erro: {e}")
            return
    if not SMTP_HOST: return
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = SMTP_FROM; msg["To"] = to
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_FROM, to, msg.as_string())
        log.info(f"Email (SMTP) → {to}")
    except Exception as e: log.error(f"Email erro: {e}")

def email_html(ev, setor_name):
    return f"""<div style="font-family:Arial;max-width:480px;margin:0 auto;padding:20px">
      <div style="background:#6C63D4;color:#fff;border-radius:10px 10px 0 0;padding:14px 18px">
        <b>A.N.A · Novo agendamento</b></div>
      <div style="background:#f9f9ff;border:1px solid #E4E4EF;border-radius:0 0 10px 10px;padding:18px">
        <table style="width:100%;font-size:13px">
          <tr><td style="color:#888;padding:4px 0;width:110px">Setor</td><td><b>{setor_name}</b></td></tr>
          <tr><td style="color:#888;padding:4px 0">Procedimento</td><td>{ev.get('proc','—')}</td></tr>
          <tr><td style="color:#888;padding:4px 0">Paciente</td><td>{ev.get('paciente','—')}</td></tr>
          <tr><td style="color:#888;padding:4px 0">Data</td><td>{ev.get('date','—')}</td></tr>
          <tr><td style="color:#888;padding:4px 0">Horário</td><td>{ev.get('time','—')}</td></tr>
          {f"<tr><td style='color:#888;padding:4px 0'>Obs</td><td>{ev.get('obs')}</td></tr>" if ev.get('obs') else ''}
        </table>
        <p style="font-size:10px;color:#aaa;margin-top:14px">A.N.A · Secretária Virtual</p>
      </div></div>"""

# ── HELPERS DE HORÁRIO ──────────────────────────────────────
def _time_to_min(t: str) -> int:
    """Converte 'HH:MM' em minutos desde meia-noite."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0