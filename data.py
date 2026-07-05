# ============================================================
# data.py — Camada de dados Supabase da Ana
# Traduz o schema do Supabase (compartilhado com o MedSS) para
# os formatos JSON que o frontend legado da Ana já entende.
# Ativada por ANA_DATA_BACKEND=supabase no main.py (feature flag).
# ============================================================
import json
import urllib.parse
from datetime import datetime, timedelta
from fastapi import HTTPException

DEPS = {}  # preenchido por main.init → sb_rest, log, routes_duration, default_group

# Status: Supabase (EN) ⇄ frontend legado (PT)
ST_EN = {"aguardando": "scheduled", "confirmado": "confirmed",
         "realizado": "done", "cancelado": "cancelled"}
ST_PT = {v: k for k, v in ST_EN.items()}


def init(**deps):
    DEPS.update(deps)


def _sb(method, path, body=None):
    return DEPS["sb_rest"](method, path, body)


def _log(user, action, payload=None):
    try:
        gid = _gid(user)
        _sb("POST", "/ana_logs", {
            "group_id": gid,
            "user_id": user["id"] if user.get("auth_source") == "supabase" else None,
            "action": action,
            "payload": payload or {"usuario": user.get("nome", "")},
        })
    except Exception:
        pass


def _gid(user) -> str:
    """Resolve o group_id do Supabase para o usuário atual."""
    if user.get("auth_source") == "supabase":
        return user["org_id"]
    gid = DEPS.get("default_group", "")
    if not gid:
        raise HTTPException(500, "ANA_DEFAULT_GROUP_ID não configurado no Railway — "
                                 "necessário para usar o backend Supabase com o login antigo.")
    return gid


def _q(v: str) -> str:
    return urllib.parse.quote(str(v), safe="")


def _hhmm(t) -> str:
    if not t:
        return ""
    return str(t)[:5]


# ── caches simples por request-burst (nome de médico/setor) ──
def _doctors_map(gid):
    rows = _sb("GET", f"/doctors?group_id=eq.{_q(gid)}&select=id,name,specialty,phone")
    return {r["id"]: r for r in rows}


def _sectors_map(gid):
    rows = _sb("GET", f"/sectors?group_id=eq.{_q(gid)}&active=eq.true&select=id,name,color,address")
    return {r["id"]: r for r in rows}


def _appt_to_evento(a, docs, secs):
    doc = docs.get(a.get("doctor_id") or "", {})
    return {
        "id": a["id"],
        "doc": doc.get("name", "") or (a.get("surgeon") or ""),
        "setor": a.get("sector_id") or "",
        "proc": a.get("procedure") or "",
        "paciente": a.get("patient_name") or "",
        "date": a.get("appointment_date") or "",
        "time": _hhmm(a.get("appointment_time")),
        "obs": a.get("notes") or "",
        "status": ST_PT.get(a.get("status", "scheduled"), "aguardando"),
        "ai": 1 if a.get("source") in ("chat", "pdf", "image") else 0,
        "pdf_filename": "", "gcal_event_id": "", "duracao_min": 0,
        "criado_por": a.get("created_by") or "",
    }


def _find_doctor_id(gid, name):
    if not name:
        return None
    rows = _sb("GET", f"/doctors?group_id=eq.{_q(gid)}&name=eq.{_q(name)}&select=id")
    if rows:
        return rows[0]["id"]
    rows = _sb("GET", f"/doctors?group_id=eq.{_q(gid)}&name=ilike.{_q('*' + name + '*')}&select=id&limit=1")
    return rows[0]["id"] if rows else None


# ── SETORES ─────────────────────────────────────────────────
def sb_list_setores(user):
    gid = _gid(user)
    rows = _sb("GET", f"/sectors?group_id=eq.{_q(gid)}&active=eq.true&select=*&order=name")
    return [{"id": r["id"], "name": r["name"], "color": r.get("color") or "#6366f1",
             "text_color": "#1A1A2E", "endereco": r.get("address") or "",
             "tempo_manual": 0, "org_id": gid} for r in rows]


def sb_create_setor(user, s):
    gid = _gid(user)
    body = {"group_id": gid, "name": s.name, "color": s.color or "#6366f1",
            "address": (s.endereco or "").strip() or None}
    try:
        rows = _sb("POST", "/sectors", body)
    except HTTPException:
        raise HTTPException(400, "Já existe um setor com esse nome no grupo.")
    r = rows[0]
    # tempo_manual vira deslocamento manual padrão? Sem par definido, ignorado.
    _log(user, f"Setor criado: {s.name}")
    return {"id": r["id"], "name": r["name"], "color": r.get("color"),
            "text_color": "#1A1A2E", "endereco": r.get("address") or "", "tempo_manual": 0}


def sb_update_setor(user, sid, s):
    gid = _gid(user)
    body = {"name": s.name, "color": s.color or "#6366f1",
            "address": (s.endereco or "").strip() or None, "updated_at": "now()"}
    _sb("PATCH", f"/sectors?id=eq.{_q(sid)}&group_id=eq.{_q(gid)}", body)
    # invalida cache de deslocamentos do setor alterado
    _sb("DELETE", f"/sector_displacements?group_id=eq.{_q(gid)}&or=(from_sector_id.eq.{_q(sid)},to_sector_id.eq.{_q(sid)})")
    _log(user, f"Setor atualizado: {s.name}")
    return {"id": sid, "name": s.name, "color": s.color, "text_color": "#1A1A2E",
            "endereco": s.endereco or "", "tempo_manual": 0}


def sb_delete_setor(user, sid):
    gid = _gid(user)
    # soft-delete (appointments referenciam o setor)
    _sb("PATCH", f"/sectors?id=eq.{_q(sid)}&group_id=eq.{_q(gid)}", {"active": False})
    _sb("DELETE", f"/sector_displacements?group_id=eq.{_q(gid)}&or=(from_sector_id.eq.{_q(sid)},to_sector_id.eq.{_q(sid)})")
    _log(user, f"Setor desativado: {sid}")
    return {"ok": True}


# ── MÉDICOS ─────────────────────────────────────────────────
def sb_list_medicos(user):
    gid = _gid(user)
    rows = _sb("GET", f"/doctors?group_id=eq.{_q(gid)}&select=id,name,specialty,phone&order=name")
    return [{"id": r["id"], "name": r["name"], "spec": r.get("specialty") or "",
             "email": "", "phone": r.get("phone") or ""} for r in rows]


def sb_create_medico(user, m):
    gid = _gid(user)
    body = {"group_id": gid, "name": m.name, "specialty": m.spec or None}
    rows = _sb("POST", "/doctors", body)
    r = rows[0]
    _log(user, f"Médico criado: {m.name}")
    return {"id": r["id"], "name": r["name"], "spec": r.get("specialty") or "", "email": ""}


def sb_delete_medico(user, mid):
    gid = _gid(user)
    _sb("DELETE", f"/doctors?id=eq.{_q(mid)}&group_id=eq.{_q(gid)}")
    _log(user, f"Médico removido: {mid}")
    return {"ok": True}


# ── DESLOCAMENTOS ───────────────────────────────────────────
def sb_get_deslocamento(gid, sa, sb_id):
    if not sa or not sb_id or sa == sb_id:
        return 0 if sa == sb_id and sa else None
    rows = _sb("GET", f"/sector_displacements?group_id=eq.{_q(gid)}"
                      f"&or=(and(from_sector_id.eq.{_q(sa)},to_sector_id.eq.{_q(sb_id)}),"
                      f"and(from_sector_id.eq.{_q(sb_id)},to_sector_id.eq.{_q(sa)}))&select=minutes&limit=1")
    if rows:
        return rows[0]["minutes"]
    # tenta Routes API se ambos têm endereço
    secs = _sectors_map(gid)
    end_a = (secs.get(sa, {}).get("address") or "").strip()
    end_b = (secs.get(sb_id, {}).get("address") or "").strip()
    if end_a and end_b and DEPS.get("routes_duration"):
        mins = DEPS["routes_duration"](end_a, end_b)
        if mins:
            try:
                _sb("POST", "/sector_displacements",
                    {"group_id": gid, "from_sector_id": sa, "to_sector_id": sb_id,
                     "minutes": mins, "source": "routes_api"})
            except Exception:
                pass
            return mins
    return None


def sb_list_deslocamentos(user):
    gid = _gid(user)
    rows = _sb("GET", f"/sector_displacements?group_id=eq.{_q(gid)}&select=*&order=updated_at.desc")
    return [{"id": r["id"], "setor_origem": r["from_sector_id"], "setor_destino": r["to_sector_id"],
             "minutos": r["minutes"], "fonte": r["source"], "updated_at": r.get("updated_at")} for r in rows]


def sb_recalcular_deslocamentos(user):
    if user["role"] != "admin":
        raise HTTPException(403, "Acesso negado.")
    gid = _gid(user)
    _sb("DELETE", f"/sector_displacements?group_id=eq.{_q(gid)}&source=eq.routes_api")
    secs = list(_sectors_map(gid).items())
    pares = 0
    for i, (ida, a) in enumerate(secs):
        for idb, b in secs[i + 1:]:
            end_a = (a.get("address") or "").strip()
            end_b = (b.get("address") or "").strip()
            if end_a and end_b and DEPS.get("routes_duration"):
                mins = DEPS["routes_duration"](end_a, end_b)
                if mins:
                    try:
                        _sb("POST", "/sector_displacements",
                            {"group_id": gid, "from_sector_id": ida, "to_sector_id": idb,
                             "minutes": mins, "source": "routes_api"})
                        pares += 1
                    except Exception:
                        pass
    _log(user, f"Deslocamentos recalculados: {pares} pares")
    return {"ok": True, "pares_calculados": pares}


# ── EVENTOS (appointments) ──────────────────────────────────
def sb_list_eventos(user):
    gid = _gid(user)
    docs = _doctors_map(gid)
    secs = _sectors_map(gid)
    rows = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}&select=*"
                      f"&order=appointment_date.desc,appointment_time.desc&limit=500")
    evs = [_appt_to_evento(a, docs, secs) for a in rows]
    evs.sort(key=lambda e: (e["date"], e["time"]))
    return evs


def sb_create_evento(user, ev):
    gid = _gid(user)
    doctor_id = _find_doctor_id(gid, ev.doc)
    body = {
        "group_id": gid,
        "doctor_id": doctor_id,
        "sector_id": ev.setor or None,
        "appointment_date": ev.date,
        "appointment_time": ev.time or None,
        "patient_name": ev.paciente or "—",
        "procedure": ev.proc,
        "notes": ev.obs or None,
        "status": "scheduled",
        "source": "pdf" if getattr(ev, "pdf_filename", "") else ("chat" if getattr(ev, "ai", 1) else "manual"),
        "created_by": user["id"] if user.get("auth_source") == "supabase" else None,
    }
    rows = _sb("POST", "/appointments", body)
    saved = rows[0]

    avisos = []
    # conflito exato: mesmo médico, mesma data/hora
    if doctor_id and ev.time:
        same = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}&doctor_id=eq.{_q(doctor_id)}"
                          f"&appointment_date=eq.{_q(ev.date)}&appointment_time=eq.{_q(ev.time)}"
                          f"&id=neq.{_q(saved['id'])}&status=neq.cancelled&select=procedure,patient_name")
        if same:
            c = same[0]
            avisos.append(f"⚠️ {ev.doc} já tem '{c['procedure']}' às {ev.time} "
                          f"(paciente: {c.get('patient_name') or '—'}). Agendado mesmo assim.")

    # deslocamento entre setores no mesmo dia
    if doctor_id and ev.setor and ev.time:
        outros = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}&doctor_id=eq.{_q(doctor_id)}"
                            f"&appointment_date=eq.{_q(ev.date)}&id=neq.{_q(saved['id'])}"
                            f"&status=neq.cancelled&select=sector_id,appointment_time,procedure")
        def tomin(t):
            try:
                h, m = _hhmm(t).split(":"); return int(h) * 60 + int(m)
            except Exception:
                return None
        novo = tomin(ev.time)
        for o in outros:
            if not o.get("sector_id") or o["sector_id"] == ev.setor:
                continue
            om = tomin(o.get("appointment_time"))
            if novo is None or om is None:
                continue
            intervalo = abs(novo - om)
            mins = sb_get_deslocamento(gid, ev.setor, o["sector_id"])
            if mins and intervalo < mins:
                avisos.append(f"🚗 Deslocamento apertado: apenas {intervalo}min de intervalo, "
                              f"deslocamento estimado {mins}min.")
                break
            elif mins and intervalo < mins + 10:
                avisos.append(f"⏱️ Deslocamento justo: {intervalo}min de intervalo, "
                              f"deslocamento estimado {mins}min.")
                break

    # superpoder: médico de plantão no mesmo dia (shifts do MedSS)
    if doctor_id:
        try:
            shifts = _sb("GET", f"/shifts?doctor_id=eq.{_q(doctor_id)}"
                                f"&shift_date=eq.{_q(ev.date)}&select=shift_type,is_hnt_ambulatory")
            if shifts:
                tipos = {"morning": "manhã", "afternoon": "tarde", "night": "noite"}
                lst = ", ".join(tipos.get(s["shift_type"], s["shift_type"]) for s in shifts)
                avisos.append(f"📋 Atenção: {ev.doc} está de plantão ({lst}) neste dia segundo a escala do MedSS.")
        except Exception:
            pass

    _log(user, f"Agendado: {ev.proc} | {ev.paciente} | {ev.date} {ev.time} | {ev.doc}")
    docs = _doctors_map(gid); secs = _sectors_map(gid)
    out = _appt_to_evento(saved, docs, secs)
    out["aviso"] = " | ".join(avisos) if avisos else None
    return out


def sb_update_evento(user, ev_id, ev):
    gid = _gid(user)
    cur = _sb("GET", f"/appointments?id=eq.{_q(ev_id)}&group_id=eq.{_q(gid)}&select=*")
    if not cur:
        raise HTTPException(404, "Não encontrado.")
    body = {"updated_at": "now()"}
    if ev.doc is not None:
        body["doctor_id"] = _find_doctor_id(gid, ev.doc)
        body["surgeon"] = None
    if ev.setor is not None: body["sector_id"] = ev.setor or None
    if ev.proc is not None: body["procedure"] = ev.proc
    if ev.paciente is not None: body["patient_name"] = ev.paciente or "—"
    if ev.date is not None: body["appointment_date"] = ev.date
    if ev.time is not None: body["appointment_time"] = ev.time or None
    if ev.obs is not None: body["notes"] = ev.obs or None
    rows = _sb("PATCH", f"/appointments?id=eq.{_q(ev_id)}&group_id=eq.{_q(gid)}", body)
    _log(user, f"Editado: agendamento {ev_id}")
    docs = _doctors_map(gid); secs = _sectors_map(gid)
    out = _appt_to_evento(rows[0], docs, secs)
    out["aviso"] = None
    return out


def sb_delete_evento(user, ev_id):
    gid = _gid(user)
    _sb("DELETE", f"/appointments?id=eq.{_q(ev_id)}&group_id=eq.{_q(gid)}")
    _log(user, f"Excluído: agendamento {ev_id}")
    return {"ok": True}


def sb_update_status(user, ev_id, status):
    gid = _gid(user)
    st_en = ST_EN.get(status)
    if not st_en:
        raise HTTPException(400, f"Status inválido. Use: {', '.join(ST_EN)}")
    rows = _sb("PATCH", f"/appointments?id=eq.{_q(ev_id)}&group_id=eq.{_q(gid)}",
               {"status": st_en, "updated_at": "now()"})
    if not rows:
        raise HTTPException(404, "Não encontrado.")
    _log(user, f"Status: agendamento {ev_id} → {status}")
    return {"ok": True, "status": status}


def sb_list_pacientes(user, q=""):
    gid = _gid(user)
    docs = _doctors_map(gid)
    path = (f"/appointments?group_id=eq.{_q(gid)}&patient_name=neq.—"
            f"&select=patient_name,doctor_id,procedure,appointment_date,appointment_time,sector_id"
            f"&order=appointment_date.desc,appointment_time.desc&limit=400")
    if q:
        path += f"&patient_name=ilike.{_q('*' + q + '*')}"
    rows = _sb("GET", path)
    seen = {}
    for r in rows:
        n = r["patient_name"]
        if n and n not in seen:
            seen[n] = {"paciente": n, "doc": docs.get(r.get("doctor_id") or "", {}).get("name", ""),
                       "proc": r.get("procedure") or "", "date": r.get("appointment_date") or "",
                       "time": _hhmm(r.get("appointment_time")), "setor": r.get("sector_id") or ""}
    return sorted(seen.values(), key=lambda x: x["paciente"].lower())[:50]


# ── MEMÓRIAS / CORREÇÕES ────────────────────────────────────
def sb_list_memorias(user):
    gid = _gid(user)
    rows = _sb("GET", f"/ana_memories?group_id=eq.{_q(gid)}&select=*&order=created_at.desc&limit=60")
    return [{"id": r["id"], "texto": r["content"], "icone": "ti-brain", "tipo": "aprendido"} for r in rows]


def sb_create_memoria(user, m):
    gid = _gid(user)
    rows = _sb("POST", "/ana_memories",
               {"group_id": gid, "content": m.texto,
                "created_by": user["id"] if user.get("auth_source") == "supabase" else None})
    r = rows[0]
    return {"id": r["id"], "texto": r["content"], "icone": m.icone or "ti-brain", "tipo": m.tipo or "aprendido"}


def sb_delete_memoria(user, mid):
    gid = _gid(user)
    if mid == "all":
        _sb("DELETE", f"/ana_memories?group_id=eq.{_q(gid)}")
    else:
        _sb("DELETE", f"/ana_memories?id=eq.{_q(mid)}&group_id=eq.{_q(gid)}")
    return {"ok": True}


def sb_list_correcoes(user):
    gid = _gid(user)
    rows = _sb("GET", f"/ana_corrections?group_id=eq.{_q(gid)}&select=*&order=created_at.desc&limit=30")
    out = []
    for r in rows:
        try:
            c = json.loads(r["content"])
            c["id"] = r["id"]
            out.append(c)
        except Exception:
            out.append({"id": r["id"], "campo": "geral", "valor_errado": "", "valor_certo": r["content"], "contexto": ""})
    return out


def sb_create_correcao(user, c):
    gid = _gid(user)
    content = json.dumps({"campo": c.campo, "valor_errado": c.valor_errado,
                          "valor_certo": c.valor_certo, "contexto": c.contexto or ""}, ensure_ascii=False)
    _sb("POST", "/ana_corrections",
        {"group_id": gid, "content": content,
         "created_by": user["id"] if user.get("auth_source") == "supabase" else None})
    return {"ok": True}


# ── LOGS / HISTÓRICO ────────────────────────────────────────
def sb_list_logs(user):
    gid = _gid(user)
    rows = _sb("GET", f"/ana_logs?group_id=eq.{_q(gid)}&select=*&order=created_at.desc&limit=200")
    out = []
    for r in rows:
        p = r.get("payload") or {}
        out.append({"id": r["id"], "nivel": p.get("nivel", "INFO"), "mensagem": r["action"],
                    "usuario": p.get("usuario", ""), "ts": r.get("created_at", "")})
    return out


def sb_list_historico(user):
    gid = _gid(user)
    docs = _doctors_map(gid); secs = _sectors_map(gid)
    rows = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}&select=*&order=created_at.desc&limit=100")
    return [_appt_to_evento(a, docs, secs) for a in rows]


# ── ORG INFO (groups) ───────────────────────────────────────
def sb_get_org_info(user):
    gid = _gid(user)
    rows = _sb("GET", f"/groups?id=eq.{_q(gid)}&select=id,name")
    nome = rows[0]["name"] if rows else "Grupo de Anestesia"
    return {"id": gid, "nome": nome}


def sb_set_org_info(user, info):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores podem alterar essa configuração.")
    gid = _gid(user)
    nome = info.nome.strip()[:120] or "Grupo de Anestesia"
    _sb("PATCH", f"/groups?id=eq.{_q(gid)}", {"name": nome})
    _log(user, f"Nome do grupo alterado para: {nome}")
    return {"ok": True, "nome": nome}


# ── CONTEXTO IA / RELATÓRIOS / MAPA ─────────────────────────
def sb_contexto_ia(user):
    gid = _gid(user)
    docs = _doctors_map(gid)
    secs = _sectors_map(gid)
    appts = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}&select=*"
                       f"&order=appointment_date.desc,appointment_time.desc&limit=300")
    evs = [_appt_to_evento(a, docs, secs) for a in appts]
    eventos = sorted(evs, key=lambda e: (e["date"], e["time"]))[-80:]

    mems = _sb("GET", f"/ana_memories?group_id=eq.{_q(gid)}&select=content&order=created_at.desc&limit=25")
    memorias = [m["content"] for m in mems]

    historico = evs[:15]
    medicos = [d["name"] for d in docs.values()]
    setores = {sid: s["name"] for sid, s in secs.items()}

    # padrões por médico
    prefs = {}
    for e in evs:
        if not e["doc"]:
            continue
        p = prefs.setdefault(e["doc"], {"setores": {}, "horas": {}})
        if e["setor"]:
            p["setores"][e["setor"]] = p["setores"].get(e["setor"], 0) + 1
        if e["time"]:
            p["horas"][e["time"]] = p["horas"].get(e["time"], 0) + 1
    preferencias = {}
    for doc, p in prefs.items():
        pref = {}
        if p["setores"]:
            top = max(p["setores"], key=p["setores"].get)
            if p["setores"][top] >= 3:
                pref["setor_frequente"] = setores.get(top, top)
        if p["horas"]:
            top = max(p["horas"], key=p["horas"].get)
            if p["horas"][top] >= 3:
                pref["horario_frequente"] = top
        if pref:
            preferencias[doc] = pref

    correcoes = sb_list_correcoes(user)

    # matrix de deslocamentos
    despl = _sb("GET", f"/sector_displacements?group_id=eq.{_q(gid)}&select=from_sector_id,to_sector_id,minutes")
    matrix = {}
    for d in despl:
        na = setores.get(d["from_sector_id"], "?")
        nb = setores.get(d["to_sector_id"], "?")
        matrix[f"{na} → {nb}"] = f"{d['minutes']} min"
        matrix[f"{nb} → {na}"] = f"{d['minutes']} min"

    # pacientes conhecidos
    seen = {}
    for e in evs:
        if e["paciente"] and e["paciente"] != "—" and e["paciente"] not in seen:
            seen[e["paciente"]] = {"nome": e["paciente"], "doc": e["doc"], "proc": e["proc"], "date": e["date"]}
    pacientes = list(seen.values())[:30]

    return {"eventos": eventos, "memorias": memorias, "historico": historico,
            "medicos": medicos, "setores": setores,
            "preferencias_medicos": preferencias, "correcoes": correcoes,
            "deslocamentos": matrix, "pacientes": pacientes}


def sb_relatorio_resumo(user):
    gid = _gid(user)
    hoje = datetime.now().strftime("%Y-%m-%d")
    ano_atras = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    docs = _doctors_map(gid)
    secs = _sectors_map(gid)
    appts = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}"
                       f"&appointment_date=gte.{ano_atras}&select=*&limit=3000")
    total = len(appts)
    hoje_n = sum(1 for a in appts if a["appointment_date"] == hoje)
    futuros = sum(1 for a in appts if a["appointment_date"] >= hoje)
    mes = hoje[:7]
    mes_n = sum(1 for a in appts if (a["appointment_date"] or "").startswith(mes))

    por_medico, por_setor, por_dia, por_mes = {}, {}, {}, {}
    d90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    for a in appts:
        d = a["appointment_date"] or ""
        nome = docs.get(a.get("doctor_id") or "", {}).get("name", "—")
        por_medico[nome] = por_medico.get(nome, 0) + 1
        sn = secs.get(a.get("sector_id") or "", {}).get("name", "—")
        por_setor[sn] = por_setor.get(sn, 0) + 1
        por_mes[d[:7]] = por_mes.get(d[:7], 0) + 1
        if d >= d90:
            try:
                dow = datetime.strptime(d, "%Y-%m-%d").weekday()  # 0=seg
                dow = (dow + 1) % 7  # converte para 0=domingo (compat SQLite strftime %w)
                por_dia[dow] = por_dia.get(dow, 0) + 1
            except Exception:
                pass

    return {"total": total, "hoje": hoje_n, "futuros": futuros, "mes": mes_n,
            "por_medico": [{"doc": k, "total": v} for k, v in sorted(por_medico.items(), key=lambda x: -x[1])],
            "por_setor": [{"setor": k, "total": v} for k, v in sorted(por_setor.items(), key=lambda x: -x[1])],
            "por_dia": [{"dow": k, "total": v} for k, v in sorted(por_dia.items())],
            "por_mes": [{"mes": k, "total": v} for k, v in sorted(por_mes.items())]}


def sb_mapa_cirurgico(user, data):
    gid = _gid(user)
    docs = _doctors_map(gid)
    secs = _sectors_map(gid)
    org = sb_get_org_info(user)
    appts = _sb("GET", f"/appointments?group_id=eq.{_q(gid)}&appointment_date=eq.{_q(data)}"
                       f"&status=neq.cancelled&select=*&order=appointment_time")
    evs = [_appt_to_evento(a, docs, secs) for a in appts]
    salas = []
    for sid, s in sorted(secs.items(), key=lambda x: x[1]["name"]):
        do_setor = [e for e in evs if e["setor"] == sid]
        if do_setor:
            salas.append({"setor_id": sid, "setor_nome": s["name"],
                          "color": s.get("color") or "#CECBF6", "text_color": "#1A1A2E",
                          "procedimentos": do_setor})
    orfaos = [e for e in evs if e["setor"] not in secs]
    if orfaos:
        salas.append({"setor_id": "", "setor_nome": "Outros", "color": "#E5E5E5",
                      "text_color": "#444", "procedimentos": orfaos})
    return {"data": data, "nome_grupo": org["nome"], "total": len(evs), "salas": salas}
