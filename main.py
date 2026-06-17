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
import urllib.request, urllib.error

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
            """CREATE TABLE IF NOT EXISTS usuarios (
                id TEXT PRIMARY KEY, nome TEXT NOT NULL, pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'medico', email TEXT DEFAULT '', medico_id TEXT DEFAULT '',
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
                duracao_min INTEGER DEFAULT 60,
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS medicos (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                spec TEXT DEFAULT '', email TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS setores (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                color TEXT DEFAULT '#CECBF6', text_color TEXT DEFAULT '#3C3489')""",
            """CREATE TABLE IF NOT EXISTS memorias (
                id TEXT PRIMARY KEY, texto TEXT NOT NULL UNIQUE,
                icone TEXT DEFAULT 'ti-brain', tipo TEXT DEFAULT 'aprendido',
                uso INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY, doc TEXT, setor TEXT, proc TEXT,
                paciente TEXT, date TEXT, time TEXT, obs TEXT,
                criado_por TEXT DEFAULT '', created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY, nivel TEXT, mensagem TEXT,
                usuario TEXT DEFAULT '', ip TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS correcoes (
                id SERIAL PRIMARY KEY, contexto TEXT, campo TEXT,
                valor_errado TEXT, valor_certo TEXT,
                usuario TEXT DEFAULT '', created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS config (
                chave TEXT PRIMARY KEY, valor TEXT DEFAULT '')""",
        ]
        for s in stmts:
            c.execute(s)
    else:
        c.executescript("""
CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY, nome TEXT NOT NULL, pin_hash TEXT NOT NULL,
    role TEXT DEFAULT 'medico', email TEXT DEFAULT '', medico_id TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessoes (
    token TEXT PRIMARY KEY, usuario_id TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL, setor TEXT NOT NULL,
    proc TEXT NOT NULL, paciente TEXT DEFAULT '', date TEXT NOT NULL,
    time TEXT NOT NULL, obs TEXT DEFAULT '', ai INTEGER DEFAULT 0,
    criado_por TEXT DEFAULT '', gcal_event_id TEXT DEFAULT '',
    pdf_filename TEXT DEFAULT '', pdf_data TEXT DEFAULT '',
    duracao_min INTEGER DEFAULT 60,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS medicos (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT DEFAULT '', email TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS setores (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    color TEXT DEFAULT '#CECBF6', text_color TEXT DEFAULT '#3C3489');
CREATE TABLE IF NOT EXISTS memorias (
    id TEXT PRIMARY KEY, texto TEXT NOT NULL UNIQUE,
    icone TEXT DEFAULT 'ti-brain', tipo TEXT DEFAULT 'aprendido',
    uso INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT, setor TEXT, proc TEXT,
    paciente TEXT, date TEXT, time TEXT, obs TEXT, criado_por TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nivel TEXT, mensagem TEXT,
    usuario TEXT DEFAULT '', ip TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS correcoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, contexto TEXT, campo TEXT,
    valor_errado TEXT, valor_certo TEXT, usuario TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY, valor TEXT DEFAULT '');
""")

    # Índices
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_ev_date ON eventos(date)",
        "CREATE INDEX IF NOT EXISTS idx_hist ON historico(created_at)",
    ]:
        try: c.execute(idx)
        except: pass

    # Migrações — adiciona colunas novas em bancos já existentes (ignora erro se já existir)
    for alter in [
        "ALTER TABLE eventos ADD COLUMN pdf_filename TEXT DEFAULT ''",
        "ALTER TABLE eventos ADD COLUMN pdf_data TEXT DEFAULT ''",
        "ALTER TABLE eventos ADD COLUMN duracao_min INTEGER DEFAULT 60",
        "ALTER TABLE usuarios ADD COLUMN medico_id TEXT DEFAULT ''",
    ]:
        try:
            c.execute(alter); conn.commit()
        except Exception:
            pass

    p = P()
    # Nenhum admin é criado automaticamente — o primeiro acesso é feito
    # pela tela de "setup" (ver /api/setup-needed e /api/setup)

    c.execute("SELECT COUNT(*) FROM medicos")
    if c.fetchone()[0] == 0:
        for r in [("d1","Dr. Carlos Mendes","Anestesia Geral",""),
                  ("d2","Dra. Ana Souza","Obstetrícia",""),
                  ("d3","Dr. Rafael Lima","Bloqueios Regionais",""),
                  ("d4","Dra. Patrícia Neves","UTI / Urgência","")]:
            c.execute(f"INSERT INTO medicos VALUES ({Ps(4)})", r)

    c.execute("SELECT COUNT(*) FROM setores")
    if c.fetchone()[0] == 0:
        for r in [("c","Centro Cirúrgico","#CECBF6","#3C3489"),
                  ("t","UTI","#9FE1CB","#085041"),
                  ("a","Ambulatório","#FAC775","#633806"),
                  ("p","Pronto-socorro","#F4C0D1","#72243E"),
                  ("b","Endoscopia","#B5D4F4","#0C447C")]:
            c.execute(f"INSERT INTO setores VALUES ({Ps(4)})", r)

    c.execute("SELECT COUNT(*) FROM memorias")
    if c.fetchone()[0] == 0:
        for r in [("m1","CC = Centro Cirúrgico","ti-map-pin","padrao"),
                  ("m2","Raqui = Raquianestesia","ti-brain","padrao"),
                  ("m3","AG = Anestesia Geral","ti-brain","padrao"),
                  ("m4","Cesáreas costumam ser às 07h","ti-clock","padrao")]:
            c.execute(f"INSERT INTO memorias (id,texto,icone,tipo) VALUES ({Ps(4)})", r)

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

def db_log(nivel, msg, usuario="", ip=""):
    try:
        conn = get_db(); c = conn.cursor()
        c.execute(f"INSERT INTO logs (nivel,mensagem,usuario,ip) VALUES ({Ps(4)})",
                  (nivel, msg, usuario, ip))
        conn.commit(); conn.close()
    except: pass

# ── CONFIG PERSISTENTE (chave/valor no banco) ───────────────
def get_config(chave: str, default: str = "") -> str:
    try:
        conn = get_db(); c = conn.cursor()
        c.execute(f"SELECT valor FROM config WHERE chave={P()}", (chave,))
        row = fetchone(c); conn.close()
        return row["valor"] if row and row.get("valor") else default
    except Exception:
        return default

def set_config(chave: str, valor: str):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT chave FROM config WHERE chave={P()}", (chave,))
    if fetchone(c):
        c.execute(f"UPDATE config SET valor={P()} WHERE chave={P()}", (valor, chave))
    else:
        c.execute(f"INSERT INTO config (chave,valor) VALUES ({Ps(2)})", (chave, valor))
    conn.commit(); conn.close()

def get_gcal_id() -> str:
    """Prioriza o valor configurado pelo usuário no app; cai para a env var como fallback."""
    return get_config("gcal_calendar_id", GCAL_ID)

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

# ── GOOGLE CALENDAR ────────────────────────────────────────
async def gcal_create(ev, setor_name):
    if not GCAL_CREDS: return ""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_info(
            json.loads(GCAL_CREDS),
            scopes=["https://www.googleapis.com/auth/calendar"])
        svc = build("calendar", "v3", credentials=creds)
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
        r = svc.events().insert(calendarId=get_gcal_id(), body=body).execute()
        log.info(f"GCal evento criado: {r.get('id')}")
        return r.get("id","")
    except Exception as e: log.error(f"GCal criar: {e}"); return ""

async def gcal_delete(gcal_id):
    if not GCAL_CREDS or not gcal_id: return
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_info(
            json.loads(GCAL_CREDS),
            scopes=["https://www.googleapis.com/auth/calendar"])
        svc = build("calendar", "v3", credentials=creds)
        svc.events().delete(calendarId=get_gcal_id(), eventId=gcal_id).execute()
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
    db_log("INFO", f"Primeiro usuário criado via setup: {uid}")
    log.info(f"Setup inicial: usuário {uid} criado como admin")
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
    c.execute("SELECT id,nome,role,email,medico_id,created_at FROM usuarios ORDER BY nome")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/usuarios")
def create_usuario(u: Usuario, user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email,medico_id) VALUES ({Ps(6)})",
                  (u.id.lower(), u.nome, hash_pin(u.pin), u.role or "medico", u.email or "", u.medico_id or ""))
        conn.commit()
    except Exception: raise HTTPException(400,"ID já existe.")
    conn.close(); return {"ok": True}

@app.delete("/api/usuarios/{uid}")
def delete_usuario(uid: str, user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    if uid==user["id"]: raise HTTPException(400,"Não pode remover a si mesmo.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM usuarios WHERE id={P()}", (uid,))
    conn.commit(); conn.close(); return {"ok": True}

# ── EVENTOS ────────────────────────────────────────────────
def find_overlap(c, doc: str, date: str, time: str, duracao_min: int, exclude_id: Optional[int] = None):
    """Retorna o evento que sobrepõe o intervalo [time, time+duracao_min) do mesmo médico no mesmo dia, ou None."""
    c.execute(f"SELECT id,proc,time,duracao_min,paciente FROM eventos WHERE doc={P()} AND date={P()}", (doc, date))
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
    c.execute("SELECT * FROM eventos ORDER BY date, time")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/eventos")
async def create_evento(ev: Evento, bg: BackgroundTasks, request: Request, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    cf = find_overlap(c, ev.doc, ev.date, ev.time, ev.duracao_min or 60)
    if cf:
        conn.close()
        cf_ini = cf["time"]
        cf_dur = cf.get("duracao_min") or 60
        cf_fim_min = _time_to_min(cf_ini) + cf_dur
        cf_fim = f"{cf_fim_min//60:02d}:{cf_fim_min%60:02d}"
        raise HTTPException(400, f"Conflito: {ev.doc} já tem '{cf['proc']}' das {cf_ini} às {cf_fim} (paciente {cf.get('paciente') or '—'}).")

    # Busca setor e email do médico
    c.execute(f"SELECT name FROM setores WHERE id={P()}", (ev.setor,))
    sr = fetchone(c); sname = sr["name"] if sr else ev.setor
    c.execute(f"SELECT email FROM medicos WHERE name={P()}", (ev.doc,))
    mr = fetchone(c); med_email = mr["email"] if mr else ""

    gcal_id = ""
    if GCAL_CREDS:
        gcal_id = await gcal_create(ev.dict(), sname)

    c.execute(f"""INSERT INTO eventos (doc,setor,proc,paciente,date,time,obs,ai,criado_por,gcal_event_id,pdf_filename,pdf_data,duracao_min)
                  VALUES ({Ps(13)})""",
              (ev.doc, ev.setor, ev.proc, ev.paciente or "", ev.date, ev.time,
               ev.obs or "", int(ev.ai or 1), user["id"], gcal_id,
               (ev.pdf_filename or "")[:200], (ev.pdf_data or "")[:3000000],
               ev.duracao_min or 60))

    if USE_POSTGRES:
        c.execute("SELECT lastval()"); new_id = c.fetchone()[0]
    else:
        new_id = c.lastrowid

    c.execute(f"""INSERT INTO historico (doc,setor,proc,paciente,date,time,obs,criado_por)
                  VALUES ({Ps(8)})""",
              (ev.doc, ev.setor, ev.proc, ev.paciente or "",
               ev.date, ev.time, ev.obs or "", user["id"]))
    conn.commit(); conn.close()

    db_log("INFO", f"Agendado: {ev.proc} | {ev.paciente} | {ev.date} {ev.time} | {ev.doc}",
           usuario=user["id"])

    if SMTP_HOST and med_email:
        bg.add_task(send_email, med_email,
                    f"Ana · {ev.proc} — {ev.date} {ev.time}",
                    email_html(ev.dict(), sname))

    return {"id": new_id, **ev.dict()}

@app.delete("/api/eventos/{ev_id}")
async def delete_evento(ev_id: int, bg: BackgroundTasks, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE id={P()}", (ev_id,))
    ev = fetchone(c)
    if not ev: conn.close(); raise HTTPException(404,"Não encontrado.")
    gcal_id = ev.get("gcal_event_id","")
    c.execute(f"DELETE FROM eventos WHERE id={P()}", (ev_id,))
    conn.commit(); conn.close()
    db_log("INFO", f"Cancelado: {ev['proc']} | {ev['date']}", usuario=user["id"])
    if gcal_id: bg.add_task(gcal_delete, gcal_id)
    return {"ok": True}

@app.get("/api/eventos/{ev_id}")
def get_evento(ev_id: int, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE id={P()}", (ev_id,))
    ev = fetchone(c); conn.close()
    if not ev: raise HTTPException(404,"Não encontrado.")
    return ev

@app.put("/api/eventos/{ev_id}")
async def update_evento(ev_id: int, ev: EventoUpdate, bg: BackgroundTasks, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM eventos WHERE id={P()}", (ev_id,))
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
    cf = find_overlap(c, merged["doc"], merged["date"], merged["time"], merged["duracao_min"], exclude_id=ev_id)
    if cf:
        conn.close()
        cf_ini = cf["time"]
        cf_fim_min = _time_to_min(cf_ini) + (cf.get("duracao_min") or 60)
        cf_fim = f"{cf_fim_min//60:02d}:{cf_fim_min%60:02d}"
        raise HTTPException(400, f"Conflito: {merged['doc']} já tem '{cf['proc']}' das {cf_ini} às {cf_fim}.")

    c.execute(f"""UPDATE eventos SET doc={P()},setor={P()},proc={P()},paciente={P()},
                  date={P()},time={P()},obs={P()},duracao_min={P()} WHERE id={P()}""",
              (merged["doc"], merged["setor"], merged["proc"], merged["paciente"] or "",
               merged["date"], merged["time"], merged["obs"] or "", merged["duracao_min"], ev_id))
    conn.commit(); conn.close()
    db_log("INFO", f"Editado: evento #{ev_id} → {merged['proc']} | {merged['date']} {merged['time']}",
           usuario=user["id"])

    # Atualiza Google Calendar: remove o antigo e cria novo (mais simples e confiável)
    old_gcal = current.get("gcal_event_id","")
    if GCAL_CREDS and old_gcal:
        bg.add_task(gcal_delete, old_gcal)
    if GCAL_CREDS:
        conn2 = get_db(); c2 = conn2.cursor()
        c2.execute(f"SELECT name FROM setores WHERE id={P()}", (merged["setor"],))
        sr = fetchone(c2); conn2.close()
        sname = sr["name"] if sr else merged["setor"]
        new_gcal = await gcal_create(merged, sname)
        conn3 = get_db(); c3 = conn3.cursor()
        c3.execute(f"UPDATE eventos SET gcal_event_id={P()} WHERE id={P()}", (new_gcal, ev_id))
        conn3.commit(); conn3.close()

    return {"id": ev_id, **merged}

# ── MÉDICOS ────────────────────────────────────────────────
@app.get("/api/medicos")
def list_medicos(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM medicos ORDER BY name")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/medicos")
def create_medico(m: Medico, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO medicos VALUES ({Ps(4)})", (m.id, m.name, m.spec or "", m.email or ""))
        conn.commit()
    except: raise HTTPException(400,"ID já existe.")
    conn.close(); return m

@app.put("/api/medicos/{mid}")
def update_medico(mid: str, m: Medico, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE medicos SET name={P()},spec={P()},email={P()} WHERE id={P()}",
              (m.name, m.spec or "", m.email or "", mid))
    conn.commit(); conn.close(); return m

@app.delete("/api/medicos/{mid}")
def delete_medico(mid: str, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM medicos WHERE id={P()}", (mid,))
    conn.commit(); conn.close(); return {"ok": True}

# ── SETORES ────────────────────────────────────────────────
@app.get("/api/setores")
def list_setores(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM setores ORDER BY name")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/setores")
def create_setor(s: Setor, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO setores VALUES ({Ps(4)})", (s.id, s.name, s.color, s.text_color))
        conn.commit()
    except: raise HTTPException(400,"Código já existe.")
    conn.close(); return s

@app.delete("/api/setores/{sid}")
def delete_setor(sid: str, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM setores WHERE id={P()}", (sid,))
    conn.commit(); conn.close(); return {"ok": True}

# ── MEMÓRIAS ───────────────────────────────────────────────
@app.get("/api/memorias")
def list_memorias(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM memorias ORDER BY uso DESC, created_at DESC LIMIT 40")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/memorias")
def create_memoria(m: Memoria, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO memorias (id,texto,icone,tipo) VALUES ({Ps(4)})",
                  (m.id, m.texto, m.icone or "ti-brain", m.tipo or "aprendido"))
        conn.commit()
    except:
        c.execute(f"UPDATE memorias SET uso=uso+1 WHERE texto={P()}", (m.texto,))
        conn.commit()
    conn.close(); return m

@app.delete("/api/memorias/{mid}")
def delete_memoria(mid: str, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM memorias WHERE id={P()}", (mid,))
    conn.commit(); conn.close(); return {"ok": True}

@app.delete("/api/memorias")
def clear_memorias(user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"DELETE FROM memorias WHERE tipo != {P()}", ("padrao",))
    conn.commit(); conn.close(); return {"ok": True}

# ── CORREÇÕES (aprendizado a partir de erros) ───────────────
@app.post("/api/correcoes")
def create_correcao(c_in: Correcao, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"""INSERT INTO correcoes (contexto,campo,valor_errado,valor_certo,usuario)
                  VALUES ({Ps(5)})""",
              (c_in.contexto[:300], c_in.campo[:50], (c_in.valor_errado or "")[:200],
               c_in.valor_certo[:200], user["id"]))
    conn.commit(); conn.close()
    db_log("INFO", f"Correção registrada: {c_in.campo} → {c_in.valor_certo}", usuario=user["id"])
    return {"ok": True}

@app.get("/api/correcoes")
def list_correcoes(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM correcoes ORDER BY created_at DESC LIMIT 30")
    rows = fetchall(c); conn.close(); return rows

# ── CONTEXTO IA ────────────────────────────────────────────
def _calc_preferencias_medicos(c) -> dict:
    """Analisa o histórico e extrai padrões por médico: setor mais comum,
    horário mais comum, duração média — para a IA usar como sugestão default."""
    c.execute("""SELECT doc, setor, time, duracao_min FROM eventos
                 ORDER BY created_at DESC LIMIT 300""")
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
    conn = get_db(); c = conn.cursor()
    desde = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    c.execute(f"""SELECT id,doc,setor,proc,paciente,date,time,obs,duracao_min FROM eventos
                  WHERE date >= {P()} ORDER BY date,time LIMIT 80""", (desde,))
    eventos = fetchall(c)
    c.execute("SELECT texto FROM memorias ORDER BY uso DESC LIMIT 25")
    memorias = [r["texto"] for r in fetchall(c)]
    c.execute("SELECT doc,setor,proc,paciente,date,time FROM historico ORDER BY created_at DESC LIMIT 15")
    historico = fetchall(c)
    c.execute("SELECT name FROM medicos ORDER BY name")
    medicos = [r["name"] for r in fetchall(c)]
    c.execute("SELECT id,name FROM setores")
    setores = {r["id"]:r["name"] for r in fetchall(c)}
    preferencias_medicos = _calc_preferencias_medicos(c)
    c.execute("SELECT campo,valor_errado,valor_certo FROM correcoes ORDER BY created_at DESC LIMIT 15")
    correcoes = fetchall(c)
    conn.close()
    return {"eventos":eventos,"memorias":memorias,"historico":historico,
            "medicos":medicos,"setores":setores,
            "preferencias_medicos":preferencias_medicos,
            "correcoes":correcoes}

# ── RELATÓRIOS ─────────────────────────────────────────────
@app.get("/api/relatorios/resumo")
def relatorio_resumo(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    hoje = datetime.now().strftime("%Y-%m-%d")
    mes_ini = datetime.now().strftime("%Y-%m-01")

    c.execute("SELECT COUNT(*) FROM eventos"); total = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM eventos WHERE date={P()}", (hoje,)); hoje_n = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM eventos WHERE date>={P()}", (hoje,)); futuros = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM eventos WHERE date>={P()}", (mes_ini,)); mes_n = c.fetchone()[0]

    c.execute("SELECT doc, COUNT(*) as total FROM eventos GROUP BY doc ORDER BY total DESC")
    por_medico = fetchall(c)

    c.execute("SELECT setor, COUNT(*) as total FROM eventos GROUP BY setor ORDER BY total DESC")
    por_setor = fetchall(c)

    if USE_POSTGRES:
        c.execute("""SELECT EXTRACT(DOW FROM date::date)::int as dow, COUNT(*) as total
                     FROM eventos WHERE date >= NOW()::date - 90
                     GROUP BY dow ORDER BY dow""")
    else:
        c.execute("""SELECT CAST(strftime('%w', date) AS INTEGER) as dow, COUNT(*) as total
                     FROM eventos WHERE date >= date('now','-90 days')
                     GROUP BY dow ORDER BY dow""")
    por_dia = fetchall(c)

    if USE_POSTGRES:
        c.execute("""SELECT TO_CHAR(date::date,'YYYY-MM') as mes, COUNT(*) as total
                     FROM eventos WHERE date >= NOW()::date - 365
                     GROUP BY mes ORDER BY mes""")
    else:
        c.execute("""SELECT strftime('%Y-%m', date) as mes, COUNT(*) as total
                     FROM eventos WHERE date >= date('now','-365 days')
                     GROUP BY mes ORDER BY mes""")
    por_mes = fetchall(c)
    conn.close()

    return {"total":total,"hoje":hoje_n,"futuros":futuros,"mes":mes_n,
            "por_medico":por_medico,"por_setor":por_setor,
            "por_dia":por_dia,"por_mes":por_mes}

# ── HISTÓRICO E LOGS ───────────────────────────────────────
@app.get("/api/historico")
def list_historico(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM historico ORDER BY created_at DESC LIMIT 100")
    rows = fetchall(c); conn.close(); return rows

@app.get("/api/logs")
def list_logs(user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 300")
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

async def run_daily_reminder():
    """Envia email de resumo do dia seguinte para cada médico com email cadastrado."""
    if not SMTP_HOST:
        return {"sent": 0, "reason": "SMTP não configurado"}
    conn = get_db(); c = conn.cursor()
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    amanha_str = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    c.execute(f"SELECT e.*, s.name as setor_nome FROM eventos e LEFT JOIN setores s ON e.setor=s.id WHERE e.date={P()} ORDER BY e.time", (amanha,))
    eventos_amanha = fetchall(c)
    for ev in eventos_amanha:
        ev["setor"] = ev.get("setor_nome") or ev.get("setor")
    c.execute("SELECT name, email FROM medicos WHERE email != ''")
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
    db_log("INFO", f"Lembrete diário enviado para {sent} médico(s) — {amanha_str}")
    return {"sent": sent, "date": amanha}

@app.post("/api/lembrete-diario")
async def trigger_daily_reminder(user=Depends(auth)):
    """Dispara manualmente o envio do lembrete do dia seguinte (admin)."""
    if user["role"] != "admin":
        raise HTTPException(403, "Acesso negado.")
    result = await run_daily_reminder()
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
def _extract_pdf_text(pdf_b64: str, max_chars: int = 6000) -> str:
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
        return text[:max_chars].strip()
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
    return {"calendar_id": get_gcal_id(), "configurado_via": "app" if get_config("gcal_calendar_id") else "env_var_ou_padrao"}

@app.post("/api/config/gcal")
def set_gcal_config(cfg: GCalConfig, user=Depends(auth)):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores podem alterar essa configuração.")
    set_config("gcal_calendar_id", cfg.calendar_id.strip())
    db_log("INFO", f"Google Calendar ID alterado para: {cfg.calendar_id}", usuario=user["id"])
    return {"ok": True, "calendar_id": cfg.calendar_id.strip()}

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

# ── HEALTH ─────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status":"ok","version":"3.0.0",
            "db":"postgres" if USE_POSTGRES else "sqlite",
            "email":bool(SMTP_HOST),"gcal":bool(GCAL_CREDS),
            "ai":bool(GROQ_KEY),
            "timestamp":datetime.now().isoformat()}

# ── STATIC FILES ───────────────────────────────────────────
@app.get("/sw.js")
def sw(): return HTMLResponse(open("sw.js").read(), media_type="application/javascript")

@app.get("/manifest.json")
def manifest(): return JSONResponse(json.load(open("manifest.json")))

@app.get("/", response_class=HTMLResponse)
def index(): return HTMLResponse(open("index.html", encoding="utf-8").read())
