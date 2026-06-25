"""
Ana v3 — Secretária Virtual de Anestesiologia
PostgreSQL + SQLite, email, Google Calendar, relatórios
"""

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any, List
import os, logging, hashlib, secrets, json, base64, io
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

DATABASE_URL   = os.environ.get("DATABASE_URL", "")
SECRET         = os.environ.get("SECRET_KEY", "ana-secretaria-default-secret-change-me")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")
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

USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

def hash_pin(pin): return hashlib.sha256(f"{pin}{SECRET}".encode()).hexdigest()

import sqlite3
if USE_POSTGRES:
    try:
        import psycopg2, psycopg2.extras
        log.info("Usando PostgreSQL")
    except ImportError:
        log.warning("psycopg2 nao disponivel — usando SQLite como fallback")
        USE_POSTGRES = False
else:
    log.info("Usando SQLite")

# ── DB CONNECTION ──────────────────────────────────────────
def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    conn = sqlite3.connect(os.environ.get("DB_PATH", "ana.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def fetchall(cursor):
    if USE_POSTGRES:
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    return [dict(r) for r in cursor.fetchall()]

def fetchone(cursor):
    if USE_POSTGRES:
        row = cursor.fetchone()
        if not row: return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    row = cursor.fetchone()
    return dict(row) if row else None

def P():
    return "%s" if USE_POSTGRES else "?"

def Ps(n):
    return ",".join([P()] * n)

# ── INIT DB ────────────────────────────────────────────────
def init_db():
    conn = get_db()
    c = conn.cursor()

    if USE_POSTGRES:
        stmts = [
            """CREATE TABLE IF NOT EXISTS orgs (
                id TEXT PRIMARY KEY, nome TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS usuarios (
                id TEXT PRIMARY KEY, nome TEXT NOT NULL, pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'medico', email TEXT DEFAULT '', medico_id TEXT DEFAULT '',
                org_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS sessoes (
                token TEXT PRIMARY KEY, usuario_id TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS eventos (
                id SERIAL PRIMARY KEY, doc TEXT NOT NULL, setor TEXT NOT NULL,
                proc TEXT NOT NULL, paciente TEXT DEFAULT '', date TEXT NOT NULL,
                time TEXT NOT NULL, obs TEXT DEFAULT '', ai INTEGER DEFAULT 0,
                criado_por TEXT DEFAULT '', gcal_event_id TEXT DEFAULT '',
                pdf_filename TEXT DEFAULT '', pdf_data TEXT DEFAULT '',
                duracao_min INTEGER DEFAULT 60, org_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS medicos (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                spec TEXT DEFAULT '', email TEXT DEFAULT '', org_id TEXT DEFAULT 'default')""",
            """CREATE TABLE IF NOT EXISTS setores (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                color TEXT DEFAULT '#CECBF6', text_color TEXT DEFAULT '#3C3489',
                org_id TEXT DEFAULT 'default')""",
            """CREATE TABLE IF NOT EXISTS memorias (
                id TEXT PRIMARY KEY, texto TEXT NOT NULL,
                icone TEXT DEFAULT 'ti-brain', tipo TEXT DEFAULT 'aprendido',
                uso INTEGER DEFAULT 0, org_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY, doc TEXT, setor TEXT, proc TEXT,
                paciente TEXT, date TEXT, time TEXT, obs TEXT,
                criado_por TEXT DEFAULT '', org_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY, nivel TEXT, mensagem TEXT,
                usuario TEXT DEFAULT '', ip TEXT DEFAULT '', org_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS correcoes (
                id SERIAL PRIMARY KEY, contexto TEXT, campo TEXT,
                valor_errado TEXT, valor_certo TEXT,
                usuario TEXT DEFAULT '', org_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS config (
                chave TEXT NOT NULL, valor TEXT DEFAULT '', org_id TEXT DEFAULT 'default',
                PRIMARY KEY (chave, org_id))""",
        ]
        for s in stmts:
            c.execute(s)
    else:
        c.executescript("""
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY, nome TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY, nome TEXT NOT NULL, pin_hash TEXT NOT NULL,
    role TEXT DEFAULT 'medico', email TEXT DEFAULT '', medico_id TEXT DEFAULT '',
    org_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessoes (
    token TEXT PRIMARY KEY, usuario_id TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL, setor TEXT NOT NULL,
    proc TEXT NOT NULL, paciente TEXT DEFAULT '', date TEXT NOT NULL,
    time TEXT NOT NULL, obs TEXT DEFAULT '', ai INTEGER DEFAULT 0,
    criado_por TEXT DEFAULT '', gcal_event_id TEXT DEFAULT '',
    pdf_filename TEXT DEFAULT '', pdf_data TEXT DEFAULT '',
    duracao_min INTEGER DEFAULT 60, org_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS medicos (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT DEFAULT '', email TEXT DEFAULT '',
    org_id TEXT DEFAULT 'default');
CREATE TABLE IF NOT EXISTS setores (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    color TEXT DEFAULT '#CECBF6', text_color TEXT DEFAULT '#3C3489',
    org_id TEXT DEFAULT 'default',
    endereco TEXT DEFAULT '',
    tempo_manual INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS deslocamentos (
    id TEXT PRIMARY KEY,
    setor_origem TEXT NOT NULL, setor_destino TEXT NOT NULL,
    org_id TEXT DEFAULT 'default',
    minutos INTEGER NOT NULL,
    fonte TEXT DEFAULT 'manual',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS memorias (
    id TEXT PRIMARY KEY, texto TEXT NOT NULL,
    icone TEXT DEFAULT 'ti-brain', tipo TEXT DEFAULT 'aprendido',
    uso INTEGER DEFAULT 0, org_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT, setor TEXT, proc TEXT,
    paciente TEXT, date TEXT, time TEXT, obs TEXT, criado_por TEXT DEFAULT '',
    org_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nivel TEXT, mensagem TEXT,
    usuario TEXT DEFAULT '', ip TEXT DEFAULT '', org_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS correcoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, contexto TEXT, campo TEXT,
    valor_errado TEXT, valor_certo TEXT, usuario TEXT DEFAULT '',
    org_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS config (
    chave TEXT NOT NULL, valor TEXT DEFAULT '', org_id TEXT DEFAULT 'default',
    PRIMARY KEY (chave, org_id));
""")

    # Índices
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_ev_date ON eventos(date)",
        "CREATE INDEX IF NOT EXISTS idx_hist ON historico(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_ev_org ON eventos(org_id, date)",
        "CREATE INDEX IF NOT EXISTS idx_ev_org_doc ON eventos(org_id, doc)",
        "CREATE INDEX IF NOT EXISTS idx_medicos_org ON medicos(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_setores_org ON setores(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_memorias_org ON memorias(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_usuarios_org ON usuarios(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_logs_org ON logs(org_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_correcoes_org ON correcoes(org_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sessoes_token ON sessoes(token)",
    ]:
        try: c.execute(idx)
        except: pass

    # Migrações — adiciona colunas novas em bancos já existentes (ignora erro se já existir)
    for alter in [
        "ALTER TABLE eventos ADD COLUMN pdf_filename TEXT DEFAULT ''",
        "ALTER TABLE eventos ADD COLUMN pdf_data TEXT DEFAULT ''",
        "ALTER TABLE eventos ADD COLUMN duracao_min INTEGER DEFAULT 60",
        "ALTER TABLE usuarios ADD COLUMN medico_id TEXT DEFAULT ''",
        "ALTER TABLE usuarios ADD COLUMN gcal_access_token TEXT DEFAULT ''",
        "ALTER TABLE usuarios ADD COLUMN gcal_refresh_token TEXT DEFAULT ''",
        "ALTER TABLE usuarios ADD COLUMN gcal_token_expiry TEXT DEFAULT ''",
        "ALTER TABLE usuarios ADD COLUMN gcal_email TEXT DEFAULT ''",
        "ALTER TABLE usuarios ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE eventos ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE medicos ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE setores ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE setores ADD COLUMN endereco TEXT DEFAULT ''",
        "ALTER TABLE setores ADD COLUMN tempo_manual INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS deslocamentos (
            id TEXT PRIMARY KEY, setor_origem TEXT NOT NULL, setor_destino TEXT NOT NULL,
            org_id TEXT DEFAULT 'default', minutos INTEGER NOT NULL,
            fonte TEXT DEFAULT 'manual', updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        "ALTER TABLE memorias ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE historico ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE logs ADD COLUMN org_id TEXT DEFAULT 'default'",
        "ALTER TABLE correcoes ADD COLUMN org_id TEXT DEFAULT 'default'",
    ]:
        try:
            c.execute(alter); conn.commit()
        except Exception:
            pass

    # Garante que existe a organização "default" (para onde vai todo dado pré-existente)
    c.execute(f"SELECT id FROM orgs WHERE id={P()}", ("default",))
    if not fetchone(c):
        c.execute(f"INSERT INTO orgs (id,nome) VALUES ({Ps(2)})", ("default", "Grupo Principal"))
        conn.commit()

    p = P()
    # Nenhum admin é criado automaticamente — o primeiro acesso é feito
    # pela tela de "setup" (ver /api/setup-needed e /api/setup)
    # Médicos, setores e memórias NÃO são mais pré-populados automaticamente:
    # cada nova organização começa vazia e o admin cadastra o que for relevante para o grupo dela.

    conn.commit(); conn.close()
    log.info("Banco inicializado com sucesso")

init_db()

# ── AUTH ───────────────────────────────────────────────────
def create_token(uid):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=12)).isoformat()
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM sessoes WHERE expires_at < {P()}", (datetime.now().isoformat(),))
    c.execute(f"INSERT INTO sessoes VALUES ({Ps(3)})", (token, uid, expires))
    conn.commit(); conn.close(); return token

def get_user(token):
    if not token: return None
    conn = get_db(); c = conn.cursor()
    c.execute(f"""SELECT u.* FROM sessoes s JOIN usuarios u ON s.usuario_id=u.id
                  WHERE s.token={P()} AND s.expires_at > {P()}""",
              (token, datetime.now().isoformat()))
    row = fetchone(c); conn.close(); return row

def auth(request: Request):
    token = request.headers.get("X-Token","") or request.cookies.get("ana_token","")
    user = get_user(token)
    if not user: raise HTTPException(401, "Não autorizado.")
    return user

def db_log(nivel, msg, usuario="", ip="", org_id="default"):
    try:
        conn = get_db(); c = conn.cursor()
        c.execute(f"INSERT INTO logs (nivel,mensagem,usuario,ip,org_id) VALUES ({Ps(5)})",
                  (nivel, msg, usuario, ip, org_id))
        conn.commit(); conn.close()
    except: pass

# ── CONFIG PERSISTENTE (chave/valor no banco, por organização) ──
def get_config(chave: str, default: str = "", org_id: str = "default") -> str:
    try:
        conn = get_db(); c = conn.cursor()
        c.execute(f"SELECT valor FROM config WHERE chave={P()} AND org_id={P()}", (chave, org_id))
        row = fetchone(c); conn.close()
        return row["valor"] if row and row.get("valor") else default
    except Exception:
        return default

def set_config(chave: str, valor: str, org_id: str = "default"):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT chave FROM config WHERE chave={P()} AND org_id={P()}", (chave, org_id))
    if fetchone(c):
        c.execute(f"UPDATE config SET valor={P()} WHERE chave={P()} AND org_id={P()}", (valor, chave, org_id))
    else:
        c.execute(f"INSERT INTO config (chave,valor,org_id) VALUES ({Ps(3)})", (chave, valor, org_id))
    conn.commit(); conn.close()

def get_gcal_id(org_id: str = "default") -> str:
    """Prioriza o valor configurado pelo usuário no app; cai para a env var como fallback."""
    return get_config("gcal_calendar_id", GCAL_ID, org_id=org_id)

# ── EMAIL ──────────────────────────────────────────────────
async def send_email(to, subject, body):
    if not SMTP_HOST or not to: return
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
        log.info(f"Email → {to}")
    except Exception as e: log.error(f"Email erro: {e}")

def email_html(ev, setor_name):
    return f"""<div style="font-family:Arial;max-width:480px;margin:0 auto;padding:20px">
      <div style="background:#6C63D4;color:#fff;border-radius:10px 10px 0 0;padding:14px 18px">
        <b>Ana · Novo agendamento</b></div>
      <div style="background:#f9f9ff;border:1px solid #E4E4EF;border-radius:0 0 10px 10px;padding:18px">
        <table style="width:100%;font-size:13px">
          <tr><td style="color:#888;padding:4px 0;width:110px">Setor</td><td><b>{setor_name}</b></td></tr>
          <tr><td style="color:#888;padding:4px 0">Procedimento</td><td>{ev.get('proc','—')}</td></tr>
          <tr><td style="color:#888;padding:4px 0">Paciente</td><td>{ev.get('paciente','—')}</td></tr>
          <tr><td style="color:#888;padding:4px 0">Data</td><td>{ev.get('date','—')}</td></tr>
          <tr><td style="color:#888;padding:4px 0">Horário</td><td>{ev.get('time','—')}</td></tr>
          {f"<tr><td style='color:#888;padding:4px 0'>Obs</td><td>{ev.get('obs')}</td></tr>" if ev.get('obs') else ''}
        </table>
        <p style="font-size:10px;color:#aaa;margin-top:14px">Ana · Secretária Virtual</p>
      </div></div>"""

# ── HELPERS DE HORÁRIO ──────────────────────────────────────
def _time_to_min(t: str) -> int:
    """Converte 'HH:MM' em minutos desde meia-noite."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0

# ── DESLOCAMENTO ENTRE SETORES (HERE API + cache) ──────────
def _cache_key(setor_a: str, setor_b: str, org_id: str) -> str:
    return f"{org_id}:{setor_a}:{setor_b}"

def get_deslocamento(setor_a: str, setor_b: str, org_id: str) -> Optional[int]:
    """Retorna minutos de deslocamento entre dois setores (cache → HERE API → tempo_manual → None).
    Retorna 0 se forem o mesmo setor."""
    if setor_a == setor_b:
        return 0
    conn = get_db(); c = conn.cursor()
    # 1. Tenta cache bilateral (a→b ou b→a, pois o tempo de deslocamento é simétrico)
    cid1 = _cache_key(setor_a, setor_b, org_id)
    cid2 = _cache_key(setor_b, setor_a, org_id)
    c.execute(f"SELECT minutos FROM deslocamentos WHERE id IN ({P()},{P()})", (cid1, cid2))
    cached = c.fetchone()
    if cached:
        conn.close()
        return cached[0]
    # 2. Busca endereços e tempo manual dos setores
    c.execute(f"SELECT id,endereco,tempo_manual FROM setores WHERE id IN ({P()},{P()}) AND org_id={P()}",
              (setor_a, setor_b, org_id))
    rows = {r[0]: {"endereco": r[1], "tempo_manual": r[2]} for r in c.fetchall()}
    conn.close()
    sa = rows.get(setor_a, {})
    sb = rows.get(setor_b, {})
    end_a = (sa.get("endereco") or "").strip()
    end_b = (sb.get("endereco") or "").strip()
    # 3. Se ambos têm endereço, tenta Google Routes API
    if end_a and end_b and GOOGLE_ROUTES_API_KEY:
        minutos = _google_routes_duration(end_a, end_b)
        if minutos is not None:
            _save_deslocamento(setor_a, setor_b, org_id, minutos, "google_routes")
            return minutos
    # 4. Fallback: tempo manual (máximo dos dois setores envolvidos)
    tm_a = sa.get("tempo_manual") or 0
    tm_b = sb.get("tempo_manual") or 0
    manual = max(tm_a, tm_b)
    if manual > 0:
        _save_deslocamento(setor_a, setor_b, org_id, manual, "manual")
        return manual
    return None

def _google_routes_duration(origem: str, destino: str) -> Optional[int]:
    """Chama a Google Routes API v2 e retorna tempo em minutos com trânsito histórico.
    Usa TRAFFIC_AWARE_OPTIMAL para considerar padrões históricos de tráfego."""
    try:
        # Google Routes API v2 — endpoint único, sem necessidade de geocodificação prévia
        # aceita endereços diretamente no campo address
        payload = json.dumps({
            "origin": {"address": origem},
            "destination": {"address": destino},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "departureTime": datetime.now().strftime("%Y-%m-%dT08:00:00Z"),
            "computeAlternativeRoutes": False,
            "routeModifiers": {"avoidTolls": False, "avoidHighways": False},
            "languageCode": "pt-BR",
            "units": "METRIC"
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": GOOGLE_ROUTES_API_KEY,
                "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.staticDuration",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())

        routes = data.get("routes", [])
        if not routes:
            log.warning(f"Google Routes: nenhuma rota encontrada de '{origem}' para '{destino}'")
            return None

        # duration considera trânsito; staticDuration ignora — usamos duration
        duration_str = routes[0].get("duration", "")
        if not duration_str:
            return None
        # formato "NNNs" (segundos) ex: "1234s"
        segundos = int(duration_str.rstrip("s"))
        minutos = max(1, round(segundos / 60))
        dist_km = routes[0].get("distanceMeters", 0) / 1000
        log.info(f"Google Routes: {origem} → {destino} = {minutos} min / {dist_km:.1f}km (com trânsito)")
        return minutos
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        log.error(f"Google Routes API erro {e.code}: {body[:300]}")
        return None
    except Exception as e:
        log.error(f"Google Routes API erro: {e}")
        return None

def _save_deslocamento(setor_a: str, setor_b: str, org_id: str, minutos: int, fonte: str):
    try:
        conn = get_db(); c = conn.cursor()
        cid = _cache_key(setor_a, setor_b, org_id)
        now = datetime.now().isoformat()
        c.execute(f"SELECT id FROM deslocamentos WHERE id={P()}", (cid,))
        if c.fetchone():
            c.execute(f"UPDATE deslocamentos SET minutos={P()},fonte={P()},updated_at={P()} WHERE id={P()}",
                      (minutos, fonte, now, cid))
        else:
            c.execute(f"INSERT INTO deslocamentos (id,setor_origem,setor_destino,org_id,minutos,fonte,updated_at) VALUES ({Ps(7)})",
                      (cid, setor_a, setor_b, org_id, minutos, fonte, now))
        conn.commit(); conn.close()
    except Exception as e:
        log.error(f"Erro ao salvar cache de deslocamento: {e}")

def _build_matrix_deslocamento(setores_ids: list, org_id: str) -> dict:
    """Constrói a matrix completa de deslocamentos entre todos os pares de setores.
    Retorna dict {(a,b): minutos} para todos os pares com deslocamento conhecido."""
    matrix = {}
    for i, a in enumerate(setores_ids):
        for b in setores_ids[i+1:]:
            minutos = get_deslocamento(a, b, org_id)
            if minutos is not None:
                matrix[(a, b)] = minutos
                matrix[(b, a)] = minutos
    return matrix

# ── ROTAS DE DESLOCAMENTO ───────────────────────────────────
@app.get("/api/deslocamentos")
def list_deslocamentos(user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM deslocamentos WHERE org_id={P()} ORDER BY setor_origem,setor_destino", (org_id,))
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/deslocamentos/recalcular")
def recalcular_deslocamentos(user=Depends(auth)):
    """Limpa o cache e recalcula todos os pares de setores (admin apenas)."""
    if user["role"] != "admin":
        raise HTTPException(403, "Acesso negado.")
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM deslocamentos WHERE org_id={P()}", (org_id,))
    conn.commit()
    c.execute(f"SELECT id FROM setores WHERE org_id={P()}", (org_id,))
    ids = [r[0] for r in c.fetchall()]; conn.close()
    matrix = _build_matrix_deslocamento(ids, org_id)
    return {"ok": True, "pares_calculados": len(matrix) // 2}

# ── GOOGLE CALENDAR ────────────────────────────────────────
GOOGLE_OAUTH_SCOPES = "https://www.googleapis.com/auth/calendar email"

def _oauth_redirect_uri() -> str:
    base = APP_BASE_URL.rstrip("/") if APP_BASE_URL else ""
    return f"{base}/api/oauth/google/callback"

def build_oauth_url(state: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "response_type": "code",
        "scope": GOOGLE_OAUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"

def exchange_oauth_code(code: str) -> dict:
    """Troca o código de autorização por access_token + refresh_token."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": _oauth_redirect_uri(),
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def refresh_oauth_token(refresh_token: str) -> dict:
    data = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def get_user_google_token(usuario_id: str) -> Optional[str]:
    """Retorna um access_token válido para o usuário, renovando se necessário. None se não conectado."""
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT gcal_access_token,gcal_refresh_token,gcal_token_expiry FROM usuarios WHERE id={P()}", (usuario_id,))
    row = fetchone(c); conn.close()
    if not row or not row.get("gcal_refresh_token"):
        log.info(f"GCal token: usuário {usuario_id} sem refresh_token salvo.")
        return None
    expiry = row.get("gcal_token_expiry") or ""
    try:
        expiry_dt = datetime.fromisoformat(expiry) if expiry else datetime.min
    except Exception:
        expiry_dt = datetime.min
    if row.get("gcal_access_token") and datetime.now() < expiry_dt:
        log.info(f"GCal token: usando access_token em cache para {usuario_id} (expira {expiry}).")
        return row["gcal_access_token"]
    # token expirado — renova
    log.info(f"GCal token: access_token expirado/ausente para {usuario_id}, renovando via refresh_token...")
    try:
        tok = refresh_oauth_token(row["gcal_refresh_token"])
        new_access = tok.get("access_token", "")
        if not new_access:
            log.error(f"GCal token: refresh não retornou access_token. Resposta: {tok}")
            return None
        expires_in = tok.get("expires_in", 3600)
        new_expiry = (datetime.now() + timedelta(seconds=expires_in - 60)).isoformat()
        conn2 = get_db(); c2 = conn2.cursor()
        c2.execute(f"UPDATE usuarios SET gcal_access_token={P()}, gcal_token_expiry={P()} WHERE id={P()}",
                   (new_access, new_expiry, usuario_id))
        conn2.commit(); conn2.close()
        log.info(f"GCal token: renovado com sucesso para {usuario_id}.")
        return new_access
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        log.error(f"GCal token: erro HTTP ao renovar token de {usuario_id}: {e.code} {body[:300]}")
        return None
    except Exception as e:
        log.error(f"Erro ao renovar token Google de {usuario_id}: {e}")
        return None

def _build_gcal_service(user_access_token: Optional[str] = None):
    """Retorna um serviço do Calendar API, usando OAuth do usuário se disponível, senão a conta de serviço."""
    from googleapiclient.discovery import build
    if user_access_token:
        import google.oauth2.credentials
        creds = google.oauth2.credentials.Credentials(token=user_access_token)
        return build("calendar", "v3", credentials=creds)
    if GCAL_CREDS:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(GCAL_CREDS), scopes=["https://www.googleapis.com/auth/calendar"])
        return build("calendar", "v3", credentials=creds)
    return None

async def gcal_create(ev, setor_name, criado_por_id: Optional[str] = None, org_id: str = "default"):
    # Prioriza o calendário pessoal do médico que criou o evento (OAuth), senão usa a conta de serviço/calendário do grupo
    user_token = get_user_google_token(criado_por_id) if criado_por_id else None
    calendar_id = "primary" if user_token else get_gcal_id(org_id)
    log.info(f"GCal criar: criado_por_id={criado_por_id}, org_id={org_id}, tem_token_pessoal={bool(user_token)}, calendar_id={calendar_id}")
    try:
        svc = _build_gcal_service(user_token)
        if not svc:
            log.error("GCal criar: serviço não pôde ser construído (sem token e sem GCAL_CREDS).")
            return ""
        ini_min = _time_to_min(ev["time"])
        fim_min = ini_min + max(ev.get("duracao_min") or 60, 1)
        fim_min = min(fim_min, 23*60+59)  # não passa da meia-noite
        end_h, end_m = fim_min // 60, fim_min % 60
        body = {
            "summary": f"{ev['proc']} — {ev.get('paciente','—')}",
            "description": f"Médico: {ev['doc']}\nSetor: {setor_name}\nObs: {ev.get('obs','')}",
            "start": {"dateTime": f"{ev['date']}T{ev['time']}:00", "timeZone": "America/Sao_Paulo"},
            "end": {"dateTime": f"{ev['date']}T{end_h:02d}:{end_m:02d}:00", "timeZone": "America/Sao_Paulo"},
        }
        r = svc.events().insert(calendarId=calendar_id, body=body).execute()
        log.info(f"GCal evento criado com sucesso: {r.get('id')} (calendário: {'pessoal' if user_token else 'grupo'}, id={calendar_id})")
        return r.get("id","")
    except Exception as e:
        log.error(f"GCal criar — FALHA: {type(e).__name__}: {e}")
        return ""

async def gcal_delete(gcal_id, criado_por_id: Optional[str] = None, org_id: str = "default"):
    if not gcal_id: return
    user_token = get_user_google_token(criado_por_id) if criado_por_id else None
    calendar_id = "primary" if user_token else get_gcal_id(org_id)
    try:
        svc = _build_gcal_service(user_token)
        if not svc: return
        svc.events().delete(calendarId=calendar_id, eventId=gcal_id).execute()
    except Exception as e: log.error(f"GCal delete: {e}")

# ── MODELOS ────────────────────────────────────────────────
class LoginData(BaseModel):
    usuario_id: str; pin: str

class Evento(BaseModel):
    doc: str; setor: str; proc: str
    paciente: Optional[str]=""; date: str; time: str
    obs: Optional[str]=""; ai: Optional[bool]=True
    pdf_filename: Optional[str]=""; pdf_data: Optional[str]=""
    duracao_min: Optional[int]=60

class EventoUpdate(BaseModel):
    doc: Optional[str]=None; setor: Optional[str]=None; proc: Optional[str]=None
    paciente: Optional[str]=None; date: Optional[str]=None; time: Optional[str]=None
    obs: Optional[str]=None; duracao_min: Optional[int]=None

class Medico(BaseModel):
    id: str; name: str; spec: Optional[str]=""; email: Optional[str]=""

class Setor(BaseModel):
    id: str; name: str
    color: Optional[str]="#CECBF6"; text_color: Optional[str]="#3C3489"
    endereco: Optional[str]=""
    tempo_manual: Optional[int]=0

class Memoria(BaseModel):
    id: str; texto: str
    icone: Optional[str]="ti-brain"; tipo: Optional[str]="aprendido"

class Usuario(BaseModel):
    id: str; nome: str; pin: str
    role: Optional[str]="medico"; email: Optional[str]=""
    medico_id: Optional[str]=""

class ChangePin(BaseModel):
    pin_atual: str; pin_novo: str

class Correcao(BaseModel):
    contexto: str  # texto original que o usuário digitou
    campo: str     # qual campo estava errado (ex: "setor", "horario", "medico")
    valor_errado: Optional[str] = ""
    valor_certo: str

# ── AUTH ROUTES ────────────────────────────────────────────
class SetupData(BaseModel):
    usuario_id: str; nome: str; pin: str
    gcal_calendar_id: Optional[str] = ""

@app.get("/api/setup-needed")
def setup_needed():
    """Indica se ainda não há nenhum usuário cadastrado (primeiro acesso)."""
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM usuarios")
    count = c.fetchone()[0]
    conn.close()
    return {"needed": count == 0}

@app.post("/api/setup")
def setup(data: SetupData, request: Request):
    """Cria o primeiro usuário admin. Se já houver usuários, requer X-Emergency-Key para limpar e recriar."""
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM usuarios")
    count = c.fetchone()[0]
    if count > 0:
        key = request.headers.get("X-Emergency-Key", "")
        if key != "reset-ana-2026":
            conn.close()
            raise HTTPException(400, "Já existe pelo menos um usuário. Use a tela de login normal.")
        # limpa usuários e sessões existentes
        c.execute("DELETE FROM sessoes")
        c.execute("DELETE FROM usuarios")
    uid = data.usuario_id.lower().strip()
    if not uid or not data.pin or len(data.pin) < 4:
        conn.close()
        raise HTTPException(400, "ID e PIN (mínimo 4 dígitos) são obrigatórios.")
    c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email) VALUES ({Ps(5)})",
              (uid, data.nome or uid, hash_pin(data.pin), "admin", ""))
    conn.commit(); conn.close()
    if data.gcal_calendar_id and data.gcal_calendar_id.strip():
        set_config("gcal_calendar_id", data.gcal_calendar_id.strip())
        log.info(f"Setup inicial: Google Calendar definido como {data.gcal_calendar_id.strip()}")
    db_log("INFO", f"Primeiro usuário criado via setup: {uid}")
    log.info(f"Setup inicial: usuário {uid} criado como admin")
    return {"ok": True}

class SignupData(BaseModel):
    usuario_id: str; nome: str; pin: str
    org_nome: Optional[str] = ""

@app.post("/api/signup")
def signup(data: SignupData, request: Request):
    """Cadastro público — cria um NOVO grupo (organização) isolado, e o usuário vira admin desse grupo."""
    uid = data.usuario_id.lower().strip().replace(" ", "")
    nome = data.nome.strip()
    if not uid or not nome or not data.pin or len(data.pin) < 4:
        raise HTTPException(400, "Nome, ID e PIN (mínimo 4 dígitos) são obrigatórios.")
    if not uid.replace("_","").replace("-","").isalnum():
        raise HTTPException(400, "ID de acesso deve conter apenas letras, números, - ou _.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id FROM usuarios WHERE id={P()}", (uid,))
    if fetchone(c):
        conn.close()
        raise HTTPException(400, "Esse ID de acesso já está em uso. Escolha outro.")

    org_id = f"org_{secrets.token_hex(6)}"
    org_nome = data.org_nome.strip() if data.org_nome else f"Grupo de {nome}"
    c.execute(f"INSERT INTO orgs (id,nome) VALUES ({Ps(2)})", (org_id, org_nome))
    # Cada nova conta criada pelo cadastro público vira ADMIN do seu próprio grupo,
    # já que ela está fundando uma organização nova e isolada.
    c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email,org_id) VALUES ({Ps(6)})",
              (uid, nome, hash_pin(data.pin), "admin", "", org_id))
    conn.commit(); conn.close()
    db_log("INFO", f"Novo grupo criado via cadastro público: {org_nome} ({org_id})", usuario=uid, org_id=org_id)
    log.info(f"Cadastro público: usuário {uid} ({nome}) criou o grupo {org_id}")
    return {"ok": True}

@app.post("/api/login")
def login(data: LoginData, request: Request):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM usuarios WHERE id={P()} AND pin_hash={P()}",
              (data.usuario_id.lower(), hash_pin(data.pin)))
    user = fetchone(c); conn.close()
    if not user:
        db_log("WARN", f"Login inválido: {data.usuario_id}",
               ip=request.client.host if request.client else "")
        raise HTTPException(401, "Usuário ou PIN incorretos.")
    token = create_token(user["id"])
    db_log("INFO", f"Login: {user['nome']}", usuario=user["id"],
           ip=request.client.host if request.client else "")
    return {"token": token, "usuario": {k:v for k,v in user.items() if k!="pin_hash"}}

@app.post("/api/logout")
def logout(request: Request):
    token = request.headers.get("X-Token","")
    if token:
        conn = get_db(); c = conn.cursor()
        c.execute(f"DELETE FROM sessoes WHERE token={P()}", (token,))
        conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/me")
def me(user=Depends(auth)):
    return {k:v for k,v in user.items() if k!="pin_hash"}

@app.post("/api/change-pin")
def change_pin(data: ChangePin, user=Depends(auth)):
    if hash_pin(data.pin_atual) != user["pin_hash"]:
        raise HTTPException(400, "PIN atual incorreto.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE usuarios SET pin_hash={P()} WHERE id={P()}",
              (hash_pin(data.pin_novo), user["id"]))
    conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/usuarios")
def list_usuarios(user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id,nome,role,email,medico_id,created_at FROM usuarios WHERE org_id={P()} ORDER BY nome", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/usuarios")
def create_usuario(u: Usuario, user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email,medico_id,org_id) VALUES ({Ps(7)})",
                  (u.id.lower(), u.nome, hash_pin(u.pin), u.role or "medico", u.email or "", u.medico_id or "", user.get("org_id","default")))
        conn.commit()
    except Exception: raise HTTPException(400,"ID já existe.")
    conn.close(); return {"ok": True}

@app.delete("/api/usuarios/{uid}")
def delete_usuario(uid: str, user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    if uid==user["id"]: raise HTTPException(400,"Não pode remover a si mesmo.")
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id FROM usuarios WHERE id={P()} AND org_id={P()}", (uid, org_id))
    if not fetchone(c):
        conn.close()
        raise HTTPException(404, "Usuário não encontrado neste grupo.")
    c.execute(f"DELETE FROM usuarios WHERE id={P()} AND org_id={P()}", (uid, org_id))
    conn.commit(); conn.close(); return {"ok": True}

# ── EVENTOS ────────────────────────────────────────────────
def find_overlap(c, doc: str, date: str, time: str, duracao_min: int, org_id: str, exclude_id: Optional[int] = None):
    """Retorna o evento que sobrepõe o intervalo [time, time+duracao_min) do mesmo médico no mesmo dia, ou None."""
    c.execute(f"SELECT id,proc,time,duracao_min,paciente FROM eventos WHERE doc={P()} AND date={P()} AND org_id={P()}", (doc, date, org_id))
    existentes = fetchall(c)
    novo_ini = _time_to_min(time)
    novo_fim = novo_ini + max(duracao_min or 60, 1)
    for e in existentes:
        if exclude_id is not None and e["id"] == exclude_id:
            continue
        e_ini = _time_to_min(e["time"])
        e_fim = e_ini + max(e.get("duracao_min") or 60, 1)
        # Sobreposição se os intervalos se cruzam
        if novo_ini < e_fim and e_ini < novo_fim:
            return e
    return None

@app.get("/api/eventos")
def list_eventos(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE org_id={P()} ORDER BY date, time", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/eventos")
async def create_evento(ev: Evento, bg: BackgroundTasks, request: Request, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    cf = find_overlap(c, ev.doc, ev.date, ev.time, ev.duracao_min or 60, org_id)
    if cf:
        conn.close()
        cf_ini = cf["time"]
        cf_dur = cf.get("duracao_min") or 60
        cf_fim_min = _time_to_min(cf_ini) + cf_dur
        cf_fim = f"{cf_fim_min//60:02d}:{cf_fim_min%60:02d}"
        raise HTTPException(400, f"Conflito: {ev.doc} já tem '{cf['proc']}' das {cf_ini} às {cf_fim} (paciente {cf.get('paciente') or '—'}).")

    # Busca setor e email do médico (dentro do mesmo grupo)
    c.execute(f"SELECT name FROM setores WHERE id={P()} AND org_id={P()}", (ev.setor, org_id))
    sr = fetchone(c); sname = sr["name"] if sr else ev.setor
    c.execute(f"SELECT email FROM medicos WHERE name={P()} AND org_id={P()}", (ev.doc, org_id))
    mr = fetchone(c); med_email = mr["email"] if mr else ""

    gcal_id = ""
    # Tenta usar o calendário pessoal do usuário (OAuth) ou cai para a conta de serviço/calendário do grupo
    if get_user_google_token(user["id"]) or GCAL_CREDS:
        gcal_id = await gcal_create(ev.dict(), sname, criado_por_id=user["id"], org_id=org_id)

    c.execute(f"""INSERT INTO eventos (doc,setor,proc,paciente,date,time,obs,ai,criado_por,gcal_event_id,pdf_filename,pdf_data,duracao_min,org_id)
                  VALUES ({Ps(14)})""",
              (ev.doc, ev.setor, ev.proc, ev.paciente or "", ev.date, ev.time,
               ev.obs or "", int(ev.ai or 1), user["id"], gcal_id,
               (ev.pdf_filename or "")[:200], (ev.pdf_data or "")[:3000000],
               ev.duracao_min or 60, org_id))

    if USE_POSTGRES:
        c.execute("SELECT lastval()"); new_id = c.fetchone()[0]
    else:
        new_id = c.lastrowid

    c.execute(f"""INSERT INTO historico (doc,setor,proc,paciente,date,time,obs,criado_por,org_id)
                  VALUES ({Ps(9)})""",
              (ev.doc, ev.setor, ev.proc, ev.paciente or "",
               ev.date, ev.time, ev.obs or "", user["id"], org_id))
    conn.commit(); conn.close()

    db_log("INFO", f"Agendado: {ev.proc} | {ev.paciente} | {ev.date} {ev.time} | {ev.doc}",
           usuario=user["id"], org_id=org_id)

    if SMTP_HOST and med_email:
        bg.add_task(send_email, med_email,
                    f"Ana · {ev.proc} — {ev.date} {ev.time}",
                    email_html(ev.dict(), sname))

    return {"id": new_id, **ev.dict()}

@app.delete("/api/eventos/{ev_id}")
async def delete_evento(ev_id: int, bg: BackgroundTasks, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE id={P()} AND org_id={P()}", (ev_id, org_id))
    ev = fetchone(c)
    if not ev: conn.close(); raise HTTPException(404,"Não encontrado.")
    gcal_id = ev.get("gcal_event_id","")
    criado_por = ev.get("criado_por","")
    c.execute(f"DELETE FROM eventos WHERE id={P()} AND org_id={P()}", (ev_id, org_id))
    conn.commit(); conn.close()
    db_log("INFO", f"Cancelado: {ev['proc']} | {ev['date']}", usuario=user["id"], org_id=org_id)
    if gcal_id: bg.add_task(gcal_delete, gcal_id, criado_por, org_id)
    return {"ok": True}

@app.get("/api/eventos/{ev_id}")
def get_evento(ev_id: int, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE id={P()} AND org_id={P()}", (ev_id, user.get("org_id","default")))
    ev = fetchone(c); conn.close()
    if not ev: raise HTTPException(404,"Não encontrado.")
    return ev

@app.put("/api/eventos/{ev_id}")
async def update_evento(ev_id: int, ev: EventoUpdate, bg: BackgroundTasks, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE id={P()} AND org_id={P()}", (ev_id, org_id))
    current = fetchone(c)
    if not current: conn.close(); raise HTTPException(404,"Não encontrado.")

    merged = {
        "doc": ev.doc if ev.doc is not None else current["doc"],
        "setor": ev.setor if ev.setor is not None else current["setor"],
        "proc": ev.proc if ev.proc is not None else current["proc"],
        "paciente": ev.paciente if ev.paciente is not None else current["paciente"],
        "date": ev.date if ev.date is not None else current["date"],
        "time": ev.time if ev.time is not None else current["time"],
        "obs": ev.obs if ev.obs is not None else current["obs"],
        "duracao_min": ev.duracao_min if ev.duracao_min is not None else (current.get("duracao_min") or 60),
    }

    # Verifica sobreposição (ignorando o próprio evento)
    cf = find_overlap(c, merged["doc"], merged["date"], merged["time"], merged["duracao_min"], org_id, exclude_id=ev_id)
    if cf:
        conn.close()
        cf_ini = cf["time"]
        cf_fim_min = _time_to_min(cf_ini) + (cf.get("duracao_min") or 60)
        cf_fim = f"{cf_fim_min//60:02d}:{cf_fim_min%60:02d}"
        raise HTTPException(400, f"Conflito: {merged['doc']} já tem '{cf['proc']}' das {cf_ini} às {cf_fim}.")

    c.execute(f"""UPDATE eventos SET doc={P()},setor={P()},proc={P()},paciente={P()},
                  date={P()},time={P()},obs={P()},duracao_min={P()} WHERE id={P()} AND org_id={P()}""",
              (merged["doc"], merged["setor"], merged["proc"], merged["paciente"] or "",
               merged["date"], merged["time"], merged["obs"] or "", merged["duracao_min"], ev_id, org_id))
    conn.commit(); conn.close()
    db_log("INFO", f"Editado: evento #{ev_id} → {merged['proc']} | {merged['date']} {merged['time']}",
           usuario=user["id"], org_id=org_id)

    # Atualiza Google Calendar: remove o antigo e cria novo (mais simples e confiável)
    old_gcal = current.get("gcal_event_id","")
    criado_por = current.get("criado_por","")
    usa_gcal = bool(get_user_google_token(criado_por) or GCAL_CREDS)
    if usa_gcal and old_gcal:
        bg.add_task(gcal_delete, old_gcal, criado_por, org_id)
    if usa_gcal:
        conn2 = get_db(); c2 = conn2.cursor()
        c2.execute(f"SELECT name FROM setores WHERE id={P()} AND org_id={P()}", (merged["setor"], org_id))
        sr = fetchone(c2); conn2.close()
        sname = sr["name"] if sr else merged["setor"]
        new_gcal = await gcal_create(merged, sname, criado_por_id=criado_por, org_id=org_id)
        conn3 = get_db(); c3 = conn3.cursor()
        c3.execute(f"UPDATE eventos SET gcal_event_id={P()} WHERE id={P()} AND org_id={P()}", (new_gcal, ev_id, org_id))
        conn3.commit(); conn3.close()

    return {"id": ev_id, **merged}

# ── MÉDICOS ────────────────────────────────────────────────
@app.get("/api/medicos")
def list_medicos(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM medicos WHERE org_id={P()} ORDER BY name", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/medicos")
def create_medico(m: Medico, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO medicos (id,name,spec,email,org_id) VALUES ({Ps(5)})", (m.id, m.name, m.spec or "", m.email or "", org_id))
        conn.commit()
    except: raise HTTPException(400,"ID já existe.")
    conn.close(); return m

@app.put("/api/medicos/{mid}")
def update_medico(mid: str, m: Medico, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE medicos SET name={P()},spec={P()},email={P()} WHERE id={P()} AND org_id={P()}",
              (m.name, m.spec or "", m.email or "", mid, user.get("org_id","default")))
    conn.commit(); conn.close(); return m

@app.delete("/api/medicos/{mid}")
def delete_medico(mid: str, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM medicos WHERE id={P()} AND org_id={P()}", (mid, user.get("org_id","default")))
    conn.commit(); conn.close(); return {"ok": True}

# ── SETORES ────────────────────────────────────────────────
@app.get("/api/setores")
def list_setores(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM setores WHERE org_id={P()} ORDER BY name", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/setores")
def create_setor(s: Setor, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO setores (id,name,color,text_color,org_id,endereco,tempo_manual) VALUES ({Ps(7)})",
                  (s.id, s.name, s.color, s.text_color, org_id, s.endereco or "", s.tempo_manual or 0))
        conn.commit()
    except: raise HTTPException(400,"Código já existe.")
    conn.close(); return s

@app.put("/api/setores/{sid}")
def update_setor(sid: str, s: Setor, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE setores SET name={P()},color={P()},text_color={P()},endereco={P()},tempo_manual={P()} WHERE id={P()} AND org_id={P()}",
              (s.name, s.color, s.text_color, s.endereco or "", s.tempo_manual or 0, sid, org_id))
    # Invalida cache de deslocamentos do setor alterado
    c.execute(f"DELETE FROM deslocamentos WHERE (setor_origem={P()} OR setor_destino={P()}) AND org_id={P()}",
              (sid, sid, org_id))
    conn.commit(); conn.close(); return s

@app.delete("/api/setores/{sid}")
def delete_setor(sid: str, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM setores WHERE id={P()} AND org_id={P()}", (sid, org_id))
    c.execute(f"DELETE FROM deslocamentos WHERE (setor_origem={P()} OR setor_destino={P()}) AND org_id={P()}",
              (sid, sid, org_id))
    conn.commit(); conn.close(); return {"ok": True}

# ── MEMÓRIAS ───────────────────────────────────────────────
@app.get("/api/memorias")
def list_memorias(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM memorias WHERE org_id={P()} ORDER BY uso DESC, created_at DESC LIMIT 40", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/memorias")
def create_memoria(m: Memoria, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id FROM memorias WHERE texto={P()} AND org_id={P()}", (m.texto, org_id))
    existing = fetchone(c)
    if existing:
        c.execute(f"UPDATE memorias SET uso=uso+1 WHERE id={P()}", (existing["id"],))
    else:
        c.execute(f"INSERT INTO memorias (id,texto,icone,tipo,org_id) VALUES ({Ps(5)})",
                  (m.id, m.texto, m.icone or "ti-brain", m.tipo or "aprendido", org_id))
    conn.commit()
    conn.close(); return m

@app.delete("/api/memorias/{mid}")
def delete_memoria(mid: str, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM memorias WHERE id={P()} AND org_id={P()}", (mid, user.get("org_id","default")))
    conn.commit(); conn.close(); return {"ok": True}

@app.delete("/api/memorias")
def clear_memorias(user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM memorias WHERE tipo != {P()} AND org_id={P()}", ("padrao", user.get("org_id","default")))
    conn.commit(); conn.close(); return {"ok": True}

# ── CORREÇÕES (aprendizado a partir de erros) ───────────────
@app.post("/api/correcoes")
def create_correcao(c_in: Correcao, user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"""INSERT INTO correcoes (contexto,campo,valor_errado,valor_certo,usuario,org_id)
                  VALUES ({Ps(6)})""",
              (c_in.contexto[:300], c_in.campo[:50], (c_in.valor_errado or "")[:200],
               c_in.valor_certo[:200], user["id"], org_id))
    conn.commit(); conn.close()
    db_log("INFO", f"Correção registrada: {c_in.campo} → {c_in.valor_certo}", usuario=user["id"], org_id=org_id)
    return {"ok": True}

@app.get("/api/correcoes")
def list_correcoes(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM correcoes WHERE org_id={P()} ORDER BY created_at DESC LIMIT 30", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

# ── CONTEXTO IA ────────────────────────────────────────────
def _calc_preferencias_medicos(c, org_id: str) -> dict:
    """Analisa o histórico e extrai padrões por médico: setor mais comum,
    horário mais comum, duração média — para a IA usar como sugestão default."""
    c.execute(f"""SELECT doc, setor, time, duracao_min FROM eventos
                 WHERE org_id={P()} ORDER BY created_at DESC LIMIT 300""", (org_id,))
    rows = fetchall(c)
    por_medico = {}
    for r in rows:
        doc = r.get("doc")
        if not doc:
            continue
        por_medico.setdefault(doc, {"setores": {}, "horarios": {}, "duracoes": []})
        s = r.get("setor") or ""
        if s:
            por_medico[doc]["setores"][s] = por_medico[doc]["setores"].get(s, 0) + 1
        t = r.get("time") or ""
        if t:
            por_medico[doc]["horarios"][t] = por_medico[doc]["horarios"].get(t, 0) + 1
        d = r.get("duracao_min")
        if d:
            por_medico[doc]["duracoes"].append(d)

    resultado = {}
    for doc, dados in por_medico.items():
        setor_top = max(dados["setores"], key=dados["setores"].get) if dados["setores"] else None
        horario_top = max(dados["horarios"], key=dados["horarios"].get) if dados["horarios"] else None
        dur_media = round(sum(dados["duracoes"]) / len(dados["duracoes"])) if dados["duracoes"] else None
        resultado[doc] = {
            "setor_frequente": setor_top,
            "horario_frequente": horario_top,
            "duracao_media": dur_media,
        }
    return resultado

@app.get("/api/contexto-ia")
def get_contexto(user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    desde = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    c.execute(f"""SELECT id,doc,setor,proc,paciente,date,time,obs,duracao_min FROM eventos
                  WHERE date >= {P()} AND org_id={P()} ORDER BY date,time LIMIT 80""", (desde, org_id))
    eventos = fetchall(c)
    c.execute(f"SELECT texto FROM memorias WHERE org_id={P()} ORDER BY uso DESC LIMIT 25", (org_id,))
    memorias = [r["texto"] for r in fetchall(c)]
    c.execute(f"SELECT doc,setor,proc,paciente,date,time FROM historico WHERE org_id={P()} ORDER BY created_at DESC LIMIT 15", (org_id,))
    historico = fetchall(c)
    c.execute(f"SELECT name FROM medicos WHERE org_id={P()} ORDER BY name", (org_id,))
    medicos = [r["name"] for r in fetchall(c)]
    c.execute(f"SELECT id,name FROM setores WHERE org_id={P()}", (org_id,))
    setores = {r["id"]:r["name"] for r in fetchall(c)}
    preferencias_medicos = _calc_preferencias_medicos(c, org_id)
    c.execute(f"SELECT campo,valor_errado,valor_certo FROM correcoes WHERE org_id={P()} ORDER BY created_at DESC LIMIT 15", (org_id,))
    correcoes = fetchall(c)
    # Matrix de deslocamentos entre setores
    setores_ids = list(setores.keys())
    conn.close()
    matrix_raw = _build_matrix_deslocamento(setores_ids, org_id)
    # Converte chaves tuple para string serializable e adiciona nomes legíveis
    matrix = {}
    for (a, b), mins in matrix_raw.items():
        nome_a = setores.get(a, a)
        nome_b = setores.get(b, b)
        matrix[f"{nome_a} → {nome_b}"] = f"{mins} min"
    return {"eventos":eventos,"memorias":memorias,"historico":historico,
            "medicos":medicos,"setores":setores,
            "preferencias_medicos":preferencias_medicos,
            "correcoes":correcoes,
            "deslocamentos":matrix}

# ── RELATÓRIOS ─────────────────────────────────────────────
@app.get("/api/relatorios/resumo")
def relatorio_resumo(user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    hoje = datetime.now().strftime("%Y-%m-%d")
    mes_ini = datetime.now().strftime("%Y-%m-01")

    c.execute(f"SELECT COUNT(*) FROM eventos WHERE org_id={P()}", (org_id,)); total = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM eventos WHERE date={P()} AND org_id={P()}", (hoje, org_id)); hoje_n = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM eventos WHERE date>={P()} AND org_id={P()}", (hoje, org_id)); futuros = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM eventos WHERE date>={P()} AND org_id={P()}", (mes_ini, org_id)); mes_n = c.fetchone()[0]

    c.execute(f"SELECT doc, COUNT(*) as total FROM eventos WHERE org_id={P()} GROUP BY doc ORDER BY total DESC", (org_id,))
    por_medico = fetchall(c)

    c.execute(f"SELECT setor, COUNT(*) as total FROM eventos WHERE org_id={P()} GROUP BY setor ORDER BY total DESC", (org_id,))
    por_setor = fetchall(c)

    if USE_POSTGRES:
        c.execute(f"""SELECT EXTRACT(DOW FROM date::date)::int as dow, COUNT(*) as total
                     FROM eventos WHERE date >= NOW()::date - 90 AND org_id={P()}
                     GROUP BY dow ORDER BY dow""", (org_id,))
    else:
        c.execute(f"""SELECT CAST(strftime('%w', date) AS INTEGER) as dow, COUNT(*) as total
                     FROM eventos WHERE date >= date('now','-90 days') AND org_id={P()}
                     GROUP BY dow ORDER BY dow""", (org_id,))
    por_dia = fetchall(c)

    if USE_POSTGRES:
        c.execute(f"""SELECT TO_CHAR(date::date,'YYYY-MM') as mes, COUNT(*) as total
                     FROM eventos WHERE date >= NOW()::date - 365 AND org_id={P()}
                     GROUP BY mes ORDER BY mes""", (org_id,))
    else:
        c.execute(f"""SELECT strftime('%Y-%m', date) as mes, COUNT(*) as total
                     FROM eventos WHERE date >= date('now','-365 days') AND org_id={P()}
                     GROUP BY mes ORDER BY mes""", (org_id,))
    por_mes = fetchall(c)
    conn.close()

    return {"total":total,"hoje":hoje_n,"futuros":futuros,"mes":mes_n,
            "por_medico":por_medico,"por_setor":por_setor,
            "por_dia":por_dia,"por_mes":por_mes}

# ── MAPA CIRÚRGICO (organizado por sala/setor) ──────────────
@app.get("/api/mapa-cirurgico")
def mapa_cirurgico(data: str, user=Depends(auth)):
    """Retorna os procedimentos de uma data, agrupados por setor e ordenados por horário —
    formato de mapa cirúrgico clássico, uma seção por sala."""
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()

    c.execute(f"SELECT id,nome FROM orgs WHERE id={P()}", (org_id,))
    org_row = fetchone(c)
    nome_grupo = org_row["nome"] if org_row else "Grupo de Anestesia"

    c.execute(f"SELECT id,name,color,text_color FROM setores WHERE org_id={P()} ORDER BY name", (org_id,))
    setores = fetchall(c)

    c.execute(f"""SELECT doc,setor,proc,paciente,time,obs,duracao_min FROM eventos
                  WHERE date={P()} AND org_id={P()} ORDER BY setor, time""", (data, org_id))
    eventos = fetchall(c)
    conn.close()

    mapa = []
    for s in setores:
        evs_setor = [e for e in eventos if e["setor"] == s["id"]]
        if not evs_setor:
            continue
        mapa.append({
            "setor_id": s["id"], "setor_nome": s["name"],
            "color": s.get("color") or "#CECBF6", "text_color": s.get("text_color") or "#3C3489",
            "procedimentos": evs_setor,
        })

    # Eventos cujo setor não bate com nenhum setor cadastrado (setor excluído depois, por exemplo)
    setores_ids = {s["id"] for s in setores}
    orfaos = [e for e in eventos if e["setor"] not in setores_ids]
    if orfaos:
        mapa.append({"setor_id": "", "setor_nome": "Outros", "color": "#E5E5E5", "text_color": "#444", "procedimentos": orfaos})

    return {"data": data, "nome_grupo": nome_grupo, "total": len(eventos), "salas": mapa}

# ── HISTÓRICO E LOGS ───────────────────────────────────────
@app.get("/api/historico")
def list_historico(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM historico WHERE org_id={P()} ORDER BY created_at DESC LIMIT 100", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

@app.get("/api/logs")
def list_logs(user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM logs WHERE org_id={P()} ORDER BY created_at DESC LIMIT 300", (user.get("org_id","default"),))
    rows = fetchall(c); conn.close(); return rows

# ── LEMBRETE DIÁRIO ──────────────────────────────────────────
def reminder_email_html(eventos_dia, data_str):
    rows = ""
    for e in eventos_dia:
        s = e.get("setor","")
        rows += f"""<tr>
          <td style="padding:5px 8px;border-bottom:0.5px solid #eee">{e['time']}</td>
          <td style="padding:5px 8px;border-bottom:0.5px solid #eee">{s}</td>
          <td style="padding:5px 8px;border-bottom:0.5px solid #eee">{e['proc']}</td>
          <td style="padding:5px 8px;border-bottom:0.5px solid #eee">{e.get('paciente','—')}</td>
          <td style="padding:5px 8px;border-bottom:0.5px solid #eee;font-size:11px;color:#888">{e['doc']}</td>
        </tr>"""
    return f"""<div style="font-family:Arial;max-width:560px;margin:0 auto;padding:20px">
      <div style="background:#6C63D4;color:#fff;border-radius:10px 10px 0 0;padding:14px 18px">
        <b>Ana · Resumo de {data_str}</b></div>
      <div style="background:#f9f9ff;border:1px solid #E4E4EF;border-radius:0 0 10px 10px;padding:18px">
        <p style="font-size:13px;color:#555;margin-bottom:10px">{len(eventos_dia)} procedimento(s) agendado(s) para o dia.</p>
        <table style="width:100%;font-size:12px;border-collapse:collapse">
          <tr><th style="text-align:left;padding:5px 8px;background:#EEEDFE;color:#3C3489">Hora</th>
              <th style="text-align:left;padding:5px 8px;background:#EEEDFE;color:#3C3489">Setor</th>
              <th style="text-align:left;padding:5px 8px;background:#EEEDFE;color:#3C3489">Procedimento</th>
              <th style="text-align:left;padding:5px 8px;background:#EEEDFE;color:#3C3489">Paciente</th>
              <th style="text-align:left;padding:5px 8px;background:#EEEDFE;color:#3C3489">Médico</th></tr>
          {rows}
        </table>
        <p style="font-size:10px;color:#aaa;margin-top:14px">Ana · Secretária Virtual — lembrete automático</p>
      </div></div>"""

async def run_daily_reminder(org_id: Optional[str] = None):
    """Envia email de resumo do dia seguinte para cada médico com email cadastrado.
    Se org_id for None, roda para TODAS as organizações (usado pelo scheduler automático)."""
    if not SMTP_HOST:
        return {"sent": 0, "reason": "SMTP não configurado"}

    conn = get_db(); c = conn.cursor()
    if org_id:
        orgs_alvo = [org_id]
    else:
        c.execute("SELECT id FROM orgs")
        orgs_alvo = [r["id"] for r in fetchall(c)]
    conn.close()

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    amanha_str = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    total_sent = 0

    for oid in orgs_alvo:
        conn = get_db(); c = conn.cursor()
        c.execute(f"SELECT e.*, s.name as setor_nome FROM eventos e LEFT JOIN setores s ON e.setor=s.id AND s.org_id={P()} WHERE e.date={P()} AND e.org_id={P()} ORDER BY e.time", (oid, amanha, oid))
        eventos_amanha = fetchall(c)
        for ev in eventos_amanha:
            ev["setor"] = ev.get("setor_nome") or ev.get("setor")
        c.execute(f"SELECT name, email FROM medicos WHERE email != '' AND org_id={P()}", (oid,))
        medicos_com_email = fetchall(c)
        conn.close()

        sent = 0
        for m in medicos_com_email:
            evs_medico = [e for e in eventos_amanha if e["doc"] == m["name"]]
            if not evs_medico:
                continue
            await send_email(m["email"], f"Ana · Sua agenda de {amanha_str}",
                             reminder_email_html(evs_medico, amanha_str))
            sent += 1
        if sent:
            db_log("INFO", f"Lembrete diário enviado para {sent} médico(s) — {amanha_str}", org_id=oid)
        total_sent += sent

    return {"sent": total_sent, "date": amanha}

@app.post("/api/lembrete-diario")
async def trigger_daily_reminder(user=Depends(auth)):
    """Dispara manualmente o envio do lembrete do dia seguinte (admin) — só para o grupo do usuário."""
    if user["role"] != "admin":
        raise HTTPException(403, "Acesso negado.")
    result = await run_daily_reminder(org_id=user.get("org_id","default"))
    return result

@app.on_event("startup")
async def start_scheduler():
    import asyncio
    async def scheduler_loop():
        last_run_date = None
        while True:
            now = datetime.now()
            # Dispara uma vez por dia, próximo das 18h
            if now.hour == 18 and last_run_date != now.date():
                try:
                    await run_daily_reminder()
                except Exception as e:
                    log.error(f"Erro no lembrete diário: {e}")
                last_run_date = now.date()
            await asyncio.sleep(60 * 10)  # checa a cada 10 minutos
    asyncio.create_task(scheduler_loop())

# ── CHAT PROXY (Groq — gratuito, evita CORS) ──────────────
def _extract_pdf_text(pdf_b64: str, max_chars: int = 4000) -> str:
    """Extrai texto de um PDF em base64. Retorna string vazia se falhar."""
    if not PDF_SUPPORT or not pdf_b64:
        return ""
    try:
        pdf_bytes = base64.b64decode(pdf_b64)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
            if len(text) >= max_chars:
                break
        result = text[:max_chars].strip()
        log.info(f"PDF extraído: {len(result)} chars de {len(reader.pages)} página(s)")
        return result
    except Exception as e:
        log.error(f"Erro ao extrair texto do PDF: {e}")
        return ""

class ChatRequest(BaseModel):
    system: str
    messages: List[Any]
    max_tokens: Optional[int] = 1500

@app.post("/api/chat")
def chat_proxy(req: ChatRequest, user=Depends(auth)):
    if not GROQ_KEY:
        raise HTTPException(500, "GROQ_API_KEY não configurada no servidor.")
    log.info(f"Chat proxy Groq: system={len(req.system)} chars, msgs={len(req.messages)}")

    # Converte formato Anthropic (content pode ser string ou lista de partes) → OpenAI/Groq
    openai_messages = [{"role": "system", "content": req.system}]
    for msg in req.messages:
        content = msg.get("content", "")
        role = msg.get("role", "user")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_parts = []
            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "document":
                    src = part.get("source", {})
                    pdf_b64 = src.get("data", "")
                    extracted = _extract_pdf_text(pdf_b64)
                    if extracted:
                        text_parts.append(f"[Conteúdo extraído do PDF anexado]\n{extracted}")
                    else:
                        text_parts.append("[Aviso: não foi possível ler o conteúdo do PDF anexado. "
                                          "Peça ao usuário para descrever o pedido médico em texto.]")
            text = "\n".join(text_parts)
        else:
            text = str(content)
        openai_messages.append({"role": role, "content": text})

    payload = json.dumps({
        "model": "llama-3.3-70b-versatile",
        "max_tokens": req.max_tokens or 1500,
        "messages": openai_messages,
        "temperature": 0.3,
    }).encode("utf-8")

    total_chars = sum(len(m.get("content","")) for m in openai_messages)
    log.info(f"Chat proxy: {len(openai_messages)} msgs, ~{total_chars} chars totais no contexto, payload={len(payload)} bytes")

    try:
        http_req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_KEY}",
                "User-Agent": "Mozilla/5.0 (compatible; AnaSecretaria/3.0; +https://railway.app)",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(http_req, timeout=60) as resp:
            groq_resp = json.loads(resp.read())
        text = groq_resp["choices"][0]["message"]["content"]
        log.info("Chat proxy: resposta OK (Groq)")
        # Retorna no mesmo formato que o frontend espera (estilo Anthropic)
        return {"content": [{"type": "text", "text": text}]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        log.error(f"Groq API erro {e.code}: {body[:500]}")
        raise HTTPException(502, f"Erro da API Groq ({e.code}): {body[:300]}")
    except urllib.error.URLError as e:
        log.error(f"Chat proxy URLError: {e}")
        raise HTTPException(502, f"Erro de conexão com Groq: {e.reason}")
    except (KeyError, IndexError) as e:
        log.error(f"Resposta inesperada da Groq: {e}")
        raise HTTPException(502, "Resposta inesperada da API Groq.")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Chat proxy erro: {e}")
        raise HTTPException(500, str(e))

# ── CONFIGURAÇÃO DO GOOGLE CALENDAR (via app) ───────────────
class GCalConfig(BaseModel):
    calendar_id: str

@app.get("/api/config/gcal")
def get_gcal_config(user=Depends(auth)):
    org_id = user.get("org_id","default")
    return {"calendar_id": get_gcal_id(org_id), "configurado_via": "app" if get_config("gcal_calendar_id", org_id=org_id) else "env_var_ou_padrao"}

@app.post("/api/config/gcal")
def set_gcal_config(cfg: GCalConfig, user=Depends(auth)):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores podem alterar essa configuração.")
    org_id = user.get("org_id","default")
    set_config("gcal_calendar_id", cfg.calendar_id.strip(), org_id=org_id)
    db_log("INFO", f"Google Calendar ID alterado para: {cfg.calendar_id}", usuario=user["id"], org_id=org_id)
    return {"ok": True, "calendar_id": cfg.calendar_id.strip()}

# ── DADOS DO GRUPO (nome institucional para cabeçalhos de relatórios) ──
class OrgInfo(BaseModel):
    nome: str

@app.get("/api/org/info")
def get_org_info(user=Depends(auth)):
    org_id = user.get("org_id","default")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id,nome FROM orgs WHERE id={P()}", (org_id,))
    row = fetchone(c); conn.close()
    return {"id": org_id, "nome": row["nome"] if row else "Grupo de Anestesia"}

@app.post("/api/org/info")
def set_org_info(info: OrgInfo, user=Depends(auth)):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores podem alterar essa configuração.")
    org_id = user.get("org_id","default")
    nome = info.nome.strip()[:120] or "Grupo de Anestesia"
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE orgs SET nome={P()} WHERE id={P()}", (nome, org_id))
    conn.commit(); conn.close()
    db_log("INFO", f"Nome do grupo alterado para: {nome}", usuario=user["id"], org_id=org_id)
    return {"ok": True, "nome": nome}

@app.get("/api/config/gcal/list")
def list_gcal_calendars(user=Depends(auth)):
    """Lista os calendários acessíveis pela conta de serviço configurada — ajuda a escolher o ID certo."""
    if not GCAL_CREDS:
        raise HTTPException(400, "Google Calendar não está configurado (GCAL_CREDENTIALS ausente).")
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_info(
            json.loads(GCAL_CREDS),
            scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc = build("calendar", "v3", credentials=creds)
        result = svc.calendarList().list().execute()
        cals = [{"id": c.get("id"), "summary": c.get("summary"), "primary": c.get("primary", False)}
                for c in result.get("items", [])]
        return {"calendars": cals}
    except Exception as e:
        log.error(f"Erro ao listar calendários: {e}")
        raise HTTPException(502, f"Não foi possível listar calendários: {str(e)[:200]}")

# ── OAUTH GOOGLE (calendário pessoal por usuário) ───────────
@app.get("/api/oauth/google/status")
def oauth_google_status(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT gcal_refresh_token,gcal_email FROM usuarios WHERE id={P()}", (user["id"],))
    row = fetchone(c); conn.close()
    connected = bool(row and row.get("gcal_refresh_token"))
    return {"connected": connected, "email": row.get("gcal_email","") if row else "",
            "oauth_disponivel": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and APP_BASE_URL)}

@app.get("/api/oauth/google/start")
def oauth_google_start(request: Request, user=Depends(auth)):
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and APP_BASE_URL):
        raise HTTPException(400, "OAuth do Google não está configurado no servidor. Configure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET e APP_BASE_URL no Railway.")
    # state = token de sessão atual, para sabermos a qual usuário vincular no callback
    token = request.headers.get("X-Token","") or request.query_params.get("token","")
    if not token:
        raise HTTPException(401, "Token ausente.")
    url = build_oauth_url(state=token)
    return {"auth_url": url}

@app.get("/api/oauth/google/callback")
def oauth_google_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<html><body style='font-family:sans-serif;text-align:center;padding:40px'><h3>Autorização cancelada</h3><p>{error}</p><script>setTimeout(()=>window.close(),2000)</script></body></html>")
    user = get_user(state)
    if not user:
        return HTMLResponse("<html><body style='font-family:sans-serif;text-align:center;padding:40px'><h3>Sessão inválida ou expirada</h3><p>Volte ao app e tente novamente.</p></body></html>")
    try:
        tok = exchange_oauth_code(code)
        access_token = tok.get("access_token","")
        refresh_token = tok.get("refresh_token","")
        expires_in = tok.get("expires_in", 3600)
        expiry = (datetime.now() + timedelta(seconds=expires_in - 60)).isoformat()

        # Busca o email da conta Google conectada
        email = ""
        try:
            req = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo",
                                          headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                info = json.loads(resp.read())
                email = info.get("email","")
        except Exception:
            pass

        conn = get_db(); c = conn.cursor()
        if refresh_token:
            c.execute(f"""UPDATE usuarios SET gcal_access_token={P()}, gcal_refresh_token={P()},
                          gcal_token_expiry={P()}, gcal_email={P()} WHERE id={P()}""",
                      (access_token, refresh_token, expiry, email, user["id"]))
        else:
            # Google só manda refresh_token na primeira autorização; se já existir, mantém o antigo
            c.execute(f"UPDATE usuarios SET gcal_access_token={P()}, gcal_token_expiry={P()}, gcal_email={P()} WHERE id={P()}",
                      (access_token, expiry, email, user["id"]))
        conn.commit(); conn.close()
        db_log("INFO", f"Google Calendar pessoal conectado: {email}", usuario=user["id"])
        return HTMLResponse(f"""<html><body style='font-family:sans-serif;text-align:center;padding:40px'>
            <h3>✅ Calendário conectado!</h3><p>{email}</p>
            <p style='color:#888;font-size:13px'>Pode fechar esta janela.</p>
            <script>setTimeout(()=>{{window.close();if(window.opener)window.opener.location.reload();}},1500)</script>
            </body></html>""")
    except Exception as e:
        log.error(f"Erro no callback OAuth: {e}")
        return HTMLResponse(f"<html><body style='font-family:sans-serif;text-align:center;padding:40px'><h3>Erro ao conectar</h3><p>{str(e)[:200]}</p></body></html>")

@app.post("/api/oauth/google/disconnect")
def oauth_google_disconnect(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"""UPDATE usuarios SET gcal_access_token='', gcal_refresh_token='',
                  gcal_token_expiry='', gcal_email='' WHERE id={P()}""", (user["id"],))
    conn.commit(); conn.close()
    db_log("INFO", "Google Calendar pessoal desconectado", usuario=user["id"])
    return {"ok": True}

# ── HEALTH ─────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status":"ok","version":"3.0.0",
            "db":"postgres" if USE_POSTGRES else "sqlite",
            "email":bool(SMTP_HOST),"gcal":bool(GCAL_CREDS),
            "ai":bool(GROQ_KEY),
            "oauth_google":bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and APP_BASE_URL),
            "google_routes":bool(GOOGLE_ROUTES_API_KEY),
            "timestamp":datetime.now().isoformat()}

# ── PÁGINAS PÚBLICAS (política de privacidade / termos) ────
PRIVACY_HTML = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Política de Privacidade — Ana Secretária Virtual</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:700px;margin:0 auto;padding:32px 20px;color:#1a1a2e;line-height:1.6}
h1{font-size:22px;color:#6C63D4;margin-bottom:4px}
h2{font-size:16px;color:#3C3489;margin-top:28px}
p,li{font-size:14px;color:#333}
.upd{font-size:12px;color:#888;margin-bottom:24px}
a{color:#6C63D4}
ul{padding-left:20px}
</style></head>
<body>
<h1>Política de Privacidade</h1>
<div class="upd">Ana — Secretária Virtual de Anestesiologia · Última atualização: junho de 2026</div>

<p>Esta política descreve como a Ana, sistema de agendamento para grupos de anestesiologia, coleta, usa e protege as informações dos usuários.</p>

<h2>Quem somos</h2>
<p>A Ana é uma aplicação de uso interno desenvolvida para auxiliar grupos de profissionais de anestesiologia a organizar agendamentos de procedimentos médicos. Contato: <a href="mailto:rodrigomorlin@gmail.com">rodrigomorlin@gmail.com</a>.</p>

<h2>Quais dados coletamos</h2>
<ul>
<li><b>Dados de cadastro:</b> nome, identificador de usuário e PIN de acesso (armazenado de forma criptografada).</li>
<li><b>Dados de agendamento:</b> data, horário, setor, procedimento, médico responsável e, quando informado, nome do paciente.</li>
<li><b>Documentos anexados:</b> PDFs de pedidos médicos enviados pelo usuário para agendamento automático.</li>
<li><b>Dados do Google Calendar:</b> quando o usuário conecta sua conta Google, criamos e gerenciamos eventos no calendário pessoal dele, exclusivamente para refletir os agendamentos feitos dentro da Ana.</li>
</ul>

<h2>Como usamos os dados</h2>
<p>Os dados são usados exclusivamente para: organizar a agenda de procedimentos do grupo, sincronizar agendamentos com o Google Calendar do usuário (quando autorizado), enviar notificações por email sobre agendamentos, e gerar relatórios internos de uso do grupo.</p>

<h2>Acesso ao Google Calendar</h2>
<p>Ao conectar sua conta Google, a Ana solicita permissão para criar, editar e excluir eventos no seu calendário. Essa permissão é usada apenas para refletir os agendamentos feitos através do sistema. A Ana não lê, acessa ou compartilha outros eventos já existentes no seu calendário além dos que ela mesma cria.</p>

<h2>Compartilhamento de dados</h2>
<p>Os dados não são vendidos, alugados ou compartilhados com terceiros para fins de marketing. Dados podem ser processados por provedores de infraestrutura (hospedagem em nuvem) e de inteligência artificial (para interpretação de linguagem natural dos pedidos de agendamento), estritamente para o funcionamento do serviço.</p>

<h2>Retenção e exclusão de dados</h2>
<p>Os dados são mantidos enquanto a conta do usuário estiver ativa. O usuário pode solicitar a exclusão de seus dados e a desconexão do Google Calendar a qualquer momento, através do administrador do sistema ou diretamente na tela de configurações (opção "Desconectar").</p>

<h2>Segurança</h2>
<p>PINs de acesso são armazenados com hash criptográfico. Tokens de acesso ao Google são armazenados de forma segura e usados apenas para as operações descritas nesta política.</p>

<h2>Alterações nesta política</h2>
<p>Esta política pode ser atualizada periodicamente. A data da última atualização está sempre indicada no topo desta página.</p>

<h2>Contato</h2>
<p>Para dúvidas sobre esta política ou para solicitar a exclusão de dados, entre em contato: <a href="mailto:rodrigomorlin@gmail.com">rodrigomorlin@gmail.com</a>.</p>

</body></html>"""

TERMS_HTML = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Termos de Serviço — Ana Secretária Virtual</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:700px;margin:0 auto;padding:32px 20px;color:#1a1a2e;line-height:1.6}
h1{font-size:22px;color:#6C63D4;margin-bottom:4px}
h2{font-size:16px;color:#3C3489;margin-top:28px}
p,li{font-size:14px;color:#333}
.upd{font-size:12px;color:#888;margin-bottom:24px}
a{color:#6C63D4}
</style></head>
<body>
<h1>Termos de Serviço</h1>
<div class="upd">Ana — Secretária Virtual de Anestesiologia · Última atualização: junho de 2026</div>

<p>Ao utilizar a Ana, você concorda com os termos abaixo.</p>

<h2>Uso do serviço</h2>
<p>A Ana é destinada ao uso interno de grupos de profissionais de anestesiologia para fins de organização de agendamentos. O acesso é restrito a usuários autorizados pelo administrador do grupo.</p>

<h2>Responsabilidade pelos dados inseridos</h2>
<p>O usuário é responsável pela exatidão das informações inseridas no sistema, incluindo dados de agendamento e documentos anexados. A Ana é uma ferramenta de apoio organizacional e não substitui o julgamento clínico profissional.</p>

<h2>Integração com Google Calendar</h2>
<p>A conexão com o Google Calendar é opcional e pode ser desfeita pelo usuário a qualquer momento na tela de configurações. Ao conectar, o usuário autoriza a Ana a criar, editar e remover eventos correspondentes aos agendamentos feitos no sistema.</p>

<h2>Disponibilidade</h2>
<p>O serviço é fornecido "como está", sem garantias de disponibilidade contínua. Esforços razoáveis são feitos para manter o sistema funcionando, mas interrupções podem ocorrer.</p>

<h2>Alterações</h2>
<p>Estes termos podem ser atualizados periodicamente, com a data de revisão indicada no topo desta página.</p>

<h2>Contato</h2>
<p><a href="mailto:rodrigomorlin@gmail.com">rodrigomorlin@gmail.com</a></p>

</body></html>"""

@app.get("/privacidade", response_class=HTMLResponse)
def privacy_policy():
    return HTMLResponse(PRIVACY_HTML)

@app.get("/termos", response_class=HTMLResponse)
def terms_of_service():
    return HTMLResponse(TERMS_HTML)

# ── STATIC FILES ───────────────────────────────────────────
@app.get("/sw.js")
def sw(): return HTMLResponse(open("sw.js").read(), media_type="application/javascript")

@app.get("/manifest.json")
def manifest(): return JSONResponse(json.load(open("manifest.json")))

@app.get("/", response_class=HTMLResponse)
def index(): return HTMLResponse(open("index.html", encoding="utf-8").read())
