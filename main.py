"""
Ana v3 — Secretária Virtual de Anestesiologia
PostgreSQL + SQLite, email, Google Calendar, relatórios
"""

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, logging, hashlib, secrets, json
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger("ana")

app = FastAPI(title="Ana v3", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SECRET       = os.environ.get("SECRET_KEY", "ana-secretaria-default-secret-change-me")
SMTP_HOST    = os.environ.get("SMTP_HOST", "")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
SMTP_FROM    = os.environ.get("SMTP_FROM", "ana@grupo-anestesia.com")
GCAL_CREDS   = os.environ.get("GCAL_CREDENTIALS", "")
GCAL_ID      = os.environ.get("GCAL_CALENDAR_ID", "primary")

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
                role TEXT DEFAULT 'medico', email TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS sessoes (
                token TEXT PRIMARY KEY, usuario_id TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS eventos (
                id SERIAL PRIMARY KEY, doc TEXT NOT NULL, setor TEXT NOT NULL,
                proc TEXT NOT NULL, paciente TEXT DEFAULT '', date TEXT NOT NULL,
                time TEXT NOT NULL, obs TEXT DEFAULT '', ai INTEGER DEFAULT 0,
                criado_por TEXT DEFAULT '', gcal_event_id TEXT DEFAULT '',
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
        ]
        for s in stmts:
            c.execute(s)
    else:
        c.executescript("""
CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY, nome TEXT NOT NULL, pin_hash TEXT NOT NULL,
    role TEXT DEFAULT 'medico', email TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessoes (
    token TEXT PRIMARY KEY, usuario_id TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL, setor TEXT NOT NULL,
    proc TEXT NOT NULL, paciente TEXT DEFAULT '', date TEXT NOT NULL,
    time TEXT NOT NULL, obs TEXT DEFAULT '', ai INTEGER DEFAULT 0,
    criado_por TEXT DEFAULT '', gcal_event_id TEXT DEFAULT '',
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
""")

    # Índices
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_ev_date ON eventos(date)",
        "CREATE INDEX IF NOT EXISTS idx_hist ON historico(created_at)",
    ]:
        try: c.execute(idx)
        except: pass

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
        h, m = ev["time"].split(":")
        end_h = str(int(h)+2).zfill(2)
        body = {
            "summary": f"{ev['proc']} — {ev.get('paciente','—')}",
            "description": f"Médico: {ev['doc']}\nSetor: {setor_name}\nObs: {ev.get('obs','')}",
            "start": {"dateTime": f"{ev['date']}T{ev['time']}:00", "timeZone": "America/Sao_Paulo"},
            "end": {"dateTime": f"{ev['date']}T{end_h}:{m}:00", "timeZone": "America/Sao_Paulo"},
        }
        r = svc.events().insert(calendarId=GCAL_ID, body=body).execute()
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
        svc.events().delete(calendarId=GCAL_ID, eventId=gcal_id).execute()
    except Exception as e: log.error(f"GCal delete: {e}")

# ── MODELOS ────────────────────────────────────────────────
class LoginData(BaseModel):
    usuario_id: str; pin: str

class Evento(BaseModel):
    doc: str; setor: str; proc: str
    paciente: Optional[str]=""; date: str; time: str
    obs: Optional[str]=""; ai: Optional[bool]=True

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

class ChangePin(BaseModel):
    pin_atual: str; pin_novo: str

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
    c.execute("SELECT id,nome,role,email,created_at FROM usuarios ORDER BY nome")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/usuarios")
def create_usuario(u: Usuario, user=Depends(auth)):
    if user["role"]!="admin": raise HTTPException(403,"Acesso negado.")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email) VALUES ({Ps(5)})",
                  (u.id.lower(), u.nome, hash_pin(u.pin), u.role or "medico", u.email or ""))
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
@app.get("/api/eventos")
def list_eventos(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM eventos ORDER BY date, time")
    rows = fetchall(c); conn.close(); return rows

@app.post("/api/eventos")
async def create_evento(ev: Evento, bg: BackgroundTasks, request: Request, user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id,proc FROM eventos WHERE doc={P()} AND date={P()} AND time={P()}",
              (ev.doc, ev.date, ev.time))
    cf = fetchone(c)
    if cf:
        conn.close()
        raise HTTPException(400, f"Conflito: {ev.doc} já tem '{cf['proc']}' às {ev.time}.")

    # Busca setor e email do médico
    c.execute(f"SELECT name FROM setores WHERE id={P()}", (ev.setor,))
    sr = fetchone(c); sname = sr["name"] if sr else ev.setor
    c.execute(f"SELECT email FROM medicos WHERE name={P()}", (ev.doc,))
    mr = fetchone(c); med_email = mr["email"] if mr else ""

    gcal_id = ""
    if GCAL_CREDS:
        gcal_id = await gcal_create(ev.dict(), sname)

    c.execute(f"""INSERT INTO eventos (doc,setor,proc,paciente,date,time,obs,ai,criado_por,gcal_event_id)
                  VALUES ({Ps(10)})""",
              (ev.doc, ev.setor, ev.proc, ev.paciente or "", ev.date, ev.time,
               ev.obs or "", int(ev.ai or 1), user["id"], gcal_id))

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

# ── CONTEXTO IA ────────────────────────────────────────────
@app.get("/api/contexto-ia")
def get_contexto(user=Depends(auth)):
    conn = get_db(); c = conn.cursor()
    desde = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    c.execute(f"""SELECT id,doc,setor,proc,paciente,date,time,obs FROM eventos
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
    conn.close()
    return {"eventos":eventos,"memorias":memorias,"historico":historico,
            "medicos":medicos,"setores":setores}

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

# ── HEALTH ─────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status":"ok","version":"3.0.0",
            "db":"postgres" if USE_POSTGRES else "sqlite",
            "email":bool(SMTP_HOST),"gcal":bool(GCAL_CREDS),
            "timestamp":datetime.now().isoformat()}

# ── RESET DE EMERGÊNCIA (remover após uso) ──────────────────
@app.get("/api/debug-info")
def debug_info(request: Request):
    key = request.headers.get("X-Emergency-Key", "")
    if key != "reset-ana-2026":
        raise HTTPException(403, "Chave incorreta.")
    db_path = os.environ.get("DB_PATH", "ana.db")
    info = {
        "db_path_env": db_path,
        "db_path_exists": os.path.exists(db_path),
        "db_path_abs": os.path.abspath(db_path),
        "use_postgres": USE_POSTGRES,
        "secret_is_default": SECRET == "ana-secretaria-default-secret-change-me",
        "cwd": os.getcwd(),
    }
    try:
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT id, nome, role FROM usuarios")
        info["usuarios"] = fetchall(c)
        conn.close()
    except Exception as e:
        info["db_error"] = str(e)
    return info

@app.post("/api/emergency-reset-admin")
def emergency_reset_admin(request: Request):
    """Reseta o usuário admin para PIN 1234. Requer header X-Emergency-Key."""
    key = request.headers.get("X-Emergency-Key", "")
    if key != "reset-ana-2026":
        raise HTTPException(403, "Chave incorreta.")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT id FROM usuarios WHERE id={P()}", ("admin",))
    exists = fetchone(c)
    if exists:
        c.execute(f"UPDATE usuarios SET pin_hash={P()}, role={P()} WHERE id={P()}",
                  (hash_pin("1234"), "admin", "admin"))
    else:
        c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email) VALUES ({Ps(5)})",
                  ("admin","Administrador",hash_pin("1234"),"admin",""))
    c.execute(f"DELETE FROM sessoes")
    conn.commit(); conn.close()
    log.info("Admin resetado via rota de emergência")
    return {"ok": True, "message": "Admin resetado. Use admin / 1234"}

@app.post("/api/emergency-create-user")
def emergency_create_user(request: Request):
    """Cria/atualiza um usuário admin específico. Requer header X-Emergency-Key."""
    key = request.headers.get("X-Emergency-Key", "")
    if key != "reset-ana-2026":
        raise HTTPException(403, "Chave incorreta.")
    conn = get_db(); c = conn.cursor()
    uid = "rodrigomorlin"
    nome = "Rodrigo Morlin"
    pin = "1710"
    c.execute(f"SELECT id FROM usuarios WHERE id={P()}", (uid,))
    exists = fetchone(c)
    if exists:
        c.execute(f"UPDATE usuarios SET nome={P()}, pin_hash={P()}, role={P()} WHERE id={P()}",
                  (nome, hash_pin(pin), "admin", uid))
    else:
        c.execute(f"INSERT INTO usuarios (id,nome,pin_hash,role,email) VALUES ({Ps(5)})",
                  (uid, nome, hash_pin(pin), "admin", ""))
    conn.commit(); conn.close()
    log.info(f"Usuário {uid} criado/atualizado via rota de emergência")
    return {"ok": True, "message": f"Usuário '{uid}' criado. Use {uid} / {pin}"}

# ── STATIC FILES ───────────────────────────────────────────
@app.get("/sw.js")
def sw(): return HTMLResponse(open("sw.js").read(), media_type="application/javascript")

@app.get("/manifest.json")
def manifest(): return JSONResponse(json.load(open("manifest.json")))

@app.get("/", response_class=HTMLResponse)
def index(): return HTMLResponse(open("index.html", encoding="utf-8").read())
