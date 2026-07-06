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
    rows = _sb("GET", f"/doctors?group_id=eq.{_q(gid)}&select=id,name,specialty,phone,user_id")
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


def _is_uuid(v: str) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", str(v or "")))


def _find_sector_id(gid, valor):
    """Resolve um setor a partir de UUID ou nome (a IA às vezes manda o nome)."""
    if not valor:
        return None
    if _is_uuid(valor):
        rows = _sb("GET", f"/sectors?id=eq.{_q(valor)}&group_id=eq.{_q(gid)}&select=id")
        if rows:
            return valor
    rows = _sb("GET", f"/sectors?group_id=eq.{_q(gid)}&name=ilike.{_q(valor)}&select=id&limit=1")
    if rows:
        return rows[0]["id"]
    rows = _sb("GET", f"/sectors?group_id=eq.{_q(gid)}&name=ilike.{_q('*' + str(valor) + '*')}&select=id&limit=1")
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
    sector_id = _find_sector_id(gid, ev.setor)
    body = {
        "group_id": gid,
        "doctor_id": doctor_id,
        "sector_id": sector_id,
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
    if doctor_id and sector_id and ev.time:
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
            if not o.get("sector_id") or o["sector_id"] == sector_id:
                continue
            om = tomin(o.get("appointment_time"))
            if novo is None or om is None:
                continue
            intervalo = abs(novo - om)
            mins = sb_get_deslocamento(gid, sector_id, o["sector_id"])
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
    out["setor_nome"] = secs.get(saved.get("sector_id") or "", {}).get("name", "")
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
    if ev.setor is not None: body["sector_id"] = _find_sector_id(gid, ev.setor)
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


def sb_escala(user, mes):
    """Escala do mês (tabela shifts do MedSS) para os médicos do grupo.
    mes: 'YYYY-MM'."""
    gid = _gid(user)
    docs = _doctors_map(gid)
    if not docs:
        return {"mes": mes, "medicos": [], "plantoes": {}}
    ids = ",".join(f'"{i}"' for i in docs)
    ini = f"{mes}-01"
    ano, m = int(mes[:4]), int(mes[5:7])
    prox = f"{ano + 1}-01-01" if m == 12 else f"{ano}-{m + 1:02d}-01"
    rows = _sb("GET", f"/shifts?doctor_id=in.({ids})&shift_date=gte.{ini}&shift_date=lt.{prox}"
                      f"&select=id,doctor_id,shift_date,shift_type,is_hnt_ambulatory,is_half_shift,sector_id"
                      f"&order=shift_date")
    secs = _sectors_map(gid)
    # especiais vinculados aos plantões do mês
    especiais_por_shift = {}
    if rows:
        sids = ",".join(f'"{r["id"]}"' for r in rows)
        try:
            scc = _sb("GET", f"/shift_custom_credits?shift_id=in.({sids})&select=shift_id,custom_credit_type_id")
            if scc:
                types = {t["id"]: t["name"] for t in _sb("GET",
                         f"/custom_credit_types?group_id=eq.{_q(gid)}&select=id,name")}
                for x in scc:
                    n = types.get(x["custom_credit_type_id"])
                    if n:
                        especiais_por_shift.setdefault(x["shift_id"], []).append(n)
        except Exception:
            pass
    plantoes = {}
    for r in rows:
        d = docs.get(r["doctor_id"], {}).get("name", "?")
        dia = r["shift_date"]
        plantoes.setdefault(d, {}).setdefault(dia, []).append({
            "id": r["id"],
            "turno": r["shift_type"],
            "meio": bool(r.get("is_half_shift")),
            "hnt": bool(r.get("is_hnt_ambulatory")),
            "setor": secs.get(r.get("sector_id") or "", {}).get("name", ""),
            "especiais": especiais_por_shift.get(r["id"], []),
        })
    medicos = sorted(docs.values(), key=lambda x: x["name"])
    meu = next((d["name"] for d in docs.values()
                if d.get("user_id") and str(d["user_id"]) == str(user.get("id"))), None)
    return {"mes": mes,
            "medicos": [m["name"] for m in medicos],
            "meu_medico": meu,
            "plantoes": plantoes}


def sb_create_plantao(user, p):
    """Cria um plantão na tabela shifts do MedSS."""
    gid = _gid(user)
    doctor_id = _find_doctor_id(gid, p.medico)
    if not doctor_id:
        raise HTTPException(400, f"Médico '{p.medico}' não encontrado no grupo.")
    if p.turno not in ("morning", "afternoon", "night"):
        raise HTTPException(400, "Turno inválido.")
    body = {"group_id": gid, "doctor_id": doctor_id,
            "shift_date": p.data, "shift_type": p.turno,
            "is_half_shift": bool(p.meio), "is_hnt_ambulatory": bool(p.hnt),
            "sector_id": _find_sector_id(gid, p.setor) if p.setor else None}
    rows = _sb("POST", "/shifts", body)
    shift_id = rows[0]["id"]
    # vincula créditos especiais marcados
    for tid in (getattr(p, "creditos_especiais", None) or []):
        try:
            _sb("POST", "/shift_custom_credits", {"shift_id": shift_id, "custom_credit_type_id": tid})
        except Exception as e:
            log_ = DEPS.get("log")
            if log_: log_.warning(f"vínculo crédito especial falhou: {e}")
    _log(user, f"Plantão criado: {p.medico} {p.data} {p.turno}")
    return {"ok": True, "id": shift_id}


def sb_delete_plantao(user, shift_id):
    gid = _gid(user)
    try:
        _sb("DELETE", f"/shift_custom_credits?shift_id=eq.{_q(shift_id)}")
    except Exception:
        pass
    _sb("DELETE", f"/shifts?id=eq.{_q(shift_id)}&group_id=eq.{_q(gid)}")
    _log(user, f"Plantão removido: {shift_id}")
    return {"ok": True}


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


# ── CRÉDITOS (credit_settings + custom_credit_types do MedSS) ──
CS_DEFAULTS = {"morning_credit": 2.5, "afternoon_credit": 2.5, "night_credit": 3.0,
               "saturday_credit": 6.0, "sunday_credit": 6.0, "hnt_ambulatory_credit": 3.5,
               "morning_label": "Manhã", "afternoon_label": "Tarde", "night_label": "Noite",
               "saturday_label": "Sábado", "sunday_label": "Domingo",
               "hnt_ambulatory_label": "Ambulatório HNT"}

def sb_credit_settings(user):
    gid = _gid(user)
    rows = _sb("GET", f"/credit_settings?group_id=eq.{_q(gid)}&select=*&limit=1")
    if rows:
        out = {k: rows[0].get(k, v) for k, v in CS_DEFAULTS.items()}
        out["exists"] = True
        return out
    return {**CS_DEFAULTS, "exists": False}

def sb_credit_settings_save(user, body: dict):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores podem alterar os valores de crédito.")
    gid = _gid(user)
    patch = {k: body[k] for k in CS_DEFAULTS if k in body and body[k] is not None}
    rows = _sb("GET", f"/credit_settings?group_id=eq.{_q(gid)}&select=id&limit=1")
    if rows:
        _sb("PATCH", f"/credit_settings?group_id=eq.{_q(gid)}", {**patch, "updated_at": "now()"})
    else:
        _sb("POST", "/credit_settings", {"group_id": gid, **patch})
    _log(user, "Valores de crédito atualizados")
    return {"ok": True}

def _shift_base_credit(s, cs) -> float:
    """Regra de crédito de um plantão (mesma derivação do MedSS):
    HNT ambulatorial > fim de semana > valor do turno; meio turno = metade."""
    from datetime import datetime as _dt
    if s.get("is_hnt_ambulatory"):
        base = float(cs["hnt_ambulatory_credit"])
    else:
        dow = _dt.strptime(s["shift_date"], "%Y-%m-%d").weekday()  # 5=sáb 6=dom
        if dow == 5:
            base = float(cs["saturday_credit"])
        elif dow == 6:
            base = float(cs["sunday_credit"])
        else:
            base = float(cs[{"morning": "morning_credit", "afternoon": "afternoon_credit",
                             "night": "night_credit"}.get(s["shift_type"], "morning_credit")])
    if s.get("is_half_shift"):
        base = base / 2
    return base

def sb_creditos(user, mes):
    """Créditos do mês por médico — derivados dos plantões, como no MedSS."""
    gid = _gid(user)
    cs = sb_credit_settings(user)
    docs = _doctors_map(gid)
    if not docs:
        return {"mes": mes, "medicos": [], "labels": cs}
    ids = ",".join(f'"{i}"' for i in docs)
    ini = f"{mes}-01"
    ano, m = int(mes[:4]), int(mes[5:7])
    prox = f"{ano + 1}-01-01" if m == 12 else f"{ano}-{m + 1:02d}-01"
    shifts = _sb("GET", f"/shifts?doctor_id=in.({ids})&shift_date=gte.{ini}&shift_date=lt.{prox}"
                        f"&select=id,doctor_id,shift_date,shift_type,is_hnt_ambulatory,is_half_shift")
    # créditos extras aplicados a plantões do mês
    extras_by_shift = {}
    if shifts:
        sids = ",".join(f'"{s["id"]}"' for s in shifts)
        scc = _sb("GET", f"/shift_custom_credits?shift_id=in.({sids})&select=shift_id,custom_credit_type_id")
        if scc:
            types = {t["id"]: t for t in _sb("GET",
                     f"/custom_credit_types?group_id=eq.{_q(gid)}&active=eq.true&select=id,name,credit_value,is_additional")}
            for x in scc:
                t = types.get(x["custom_credit_type_id"])
                if t:
                    extras_by_shift.setdefault(x["shift_id"], []).append(t)
    from datetime import datetime as _dt
    por_medico = {}
    meu = next((d["name"] for d in docs.values()
                if d.get("user_id") and str(d["user_id"]) == str(user.get("id"))), None)
    for s in shifts:
        nome = docs.get(s["doctor_id"], {}).get("name", "?")
        r = por_medico.setdefault(nome, {"total": 0.0, "plantoes": 0, "detalhe": {}})
        base = _shift_base_credit(s, cs)
        extra = 0.0
        for t in extras_by_shift.get(s["id"], []):
            if t.get("is_additional"):
                extra += float(t["credit_value"])
            else:
                base = float(t["credit_value"])
        r["total"] += base + extra
        r["plantoes"] += 1
        # rótulo do detalhe
        if s.get("is_hnt_ambulatory"):
            key = cs["hnt_ambulatory_label"]
        else:
            dow = _dt.strptime(s["shift_date"], "%Y-%m-%d").weekday()
            key = cs["saturday_label"] if dow == 5 else cs["sunday_label"] if dow == 6 else \
                  cs[{"morning": "morning_label", "afternoon": "afternoon_label",
                      "night": "night_label"}.get(s["shift_type"], "morning_label")]
        r["detalhe"][key] = r["detalhe"].get(key, 0) + 1
    lista = [{"medico": k, **v, "total": round(v["total"], 2)} for k, v in por_medico.items()]
    lista.sort(key=lambda x: -x["total"])
    return {"mes": mes, "medicos": lista, "meu_medico": meu, "labels": cs}

# ── TROCA DE PLANTÕES (shift_swap_requests) ──────────────────
def _meu_doctor_id(user, gid):
    rows = _sb("GET", f"/doctors?group_id=eq.{_q(gid)}&user_id=eq.{_q(user['id'])}&select=id,name&limit=1")
    return (rows[0]["id"], rows[0]["name"]) if rows else (None, None)

def _shift_info(gid, shift_id):
    rows = _sb("GET", f"/shifts?id=eq.{_q(shift_id)}&group_id=eq.{_q(gid)}"
                      f"&select=id,doctor_id,shift_date,shift_type")
    return rows[0] if rows else None

def sb_swap_create(user, body):
    gid = _gid(user)
    meu_id, meu_nome = _meu_doctor_id(user, gid)
    if not meu_id and user["role"] != "admin":
        raise HTTPException(400, "Sua conta não está vinculada a um médico do grupo.")
    meu_shift = _shift_info(gid, body.meu_shift_id)
    alvo_shift = _shift_info(gid, body.alvo_shift_id)
    if not meu_shift or not alvo_shift:
        raise HTTPException(404, "Plantão não encontrado.")
    if user["role"] != "admin" and meu_shift["doctor_id"] != meu_id:
        raise HTTPException(403, "Você só pode oferecer os seus próprios plantões.")
    if meu_shift["doctor_id"] == alvo_shift["doctor_id"]:
        raise HTTPException(400, "Os dois plantões são do mesmo médico.")
    rows = _sb("POST", "/shift_swap_requests", {
        "group_id": gid,
        "requester_shift_id": meu_shift["id"], "target_shift_id": alvo_shift["id"],
        "requester_doctor_id": meu_shift["doctor_id"], "target_doctor_id": alvo_shift["doctor_id"],
        "message": (body.mensagem or "")[:300] or None, "status": "pending"})
    _log(user, f"Troca proposta: {meu_shift['shift_date']} ⇄ {alvo_shift['shift_date']}")
    return {"ok": True, "id": rows[0]["id"]}

def sb_swap_list(user):
    gid = _gid(user)
    docs = _doctors_map(gid)
    meu_id, _ = _meu_doctor_id(user, gid)
    rows = _sb("GET", f"/shift_swap_requests?group_id=eq.{_q(gid)}&status=eq.pending"
                      f"&select=*&order=created_at.desc&limit=50")
    out = []
    for r in rows:
        rs = _shift_info(gid, r["requester_shift_id"]) or {}
        ts = _shift_info(gid, r["target_shift_id"]) or {}
        TUR = {"morning": "Manhã", "afternoon": "Tarde", "night": "Noite"}
        out.append({
            "id": r["id"], "mensagem": r.get("message") or "",
            "de": docs.get(r["requester_doctor_id"], {}).get("name", "?"),
            "para": docs.get(r["target_doctor_id"], {}).get("name", "?"),
            "oferece": {"data": rs.get("shift_date", ""), "turno": TUR.get(rs.get("shift_type"), "")},
            "pede": {"data": ts.get("shift_date", ""), "turno": TUR.get(ts.get("shift_type"), "")},
            "sou_alvo": bool(meu_id and r["target_doctor_id"] == meu_id),
            "sou_autor": bool(meu_id and r["requester_doctor_id"] == meu_id),
        })
    return out

def sb_swap_respond(user, swap_id, aceitar: bool):
    gid = _gid(user)
    rows = _sb("GET", f"/shift_swap_requests?id=eq.{_q(swap_id)}&group_id=eq.{_q(gid)}&select=*")
    if not rows:
        raise HTTPException(404, "Solicitação não encontrada.")
    sw = rows[0]
    if sw["status"] != "pending":
        raise HTTPException(400, "Esta solicitação já foi respondida.")
    meu_id, _ = _meu_doctor_id(user, gid)
    if user["role"] != "admin" and sw["target_doctor_id"] != meu_id:
        raise HTTPException(403, "Apenas o médico alvo (ou admin) pode responder.")
    if aceitar:
        # troca efetiva: os dois plantões trocam de médico
        _sb("PATCH", f"/shifts?id=eq.{_q(sw['requester_shift_id'])}",
            {"doctor_id": sw["target_doctor_id"], "updated_at": "now()"})
        _sb("PATCH", f"/shifts?id=eq.{_q(sw['target_shift_id'])}",
            {"doctor_id": sw["requester_doctor_id"], "updated_at": "now()"})
    _sb("PATCH", f"/shift_swap_requests?id=eq.{_q(swap_id)}",
        {"status": "accepted" if aceitar else "rejected", "updated_at": "now()"})
    _log(user, f"Troca {'aceita' if aceitar else 'recusada'}: {swap_id}")
    return {"ok": True, "status": "accepted" if aceitar else "rejected"}

def sb_swap_cancel(user, swap_id):
    gid = _gid(user)
    rows = _sb("GET", f"/shift_swap_requests?id=eq.{_q(swap_id)}&group_id=eq.{_q(gid)}&select=*")
    if not rows:
        raise HTTPException(404, "Não encontrada.")
    meu_id, _ = _meu_doctor_id(user, gid)
    if user["role"] != "admin" and rows[0]["requester_doctor_id"] != meu_id:
        raise HTTPException(403, "Apenas quem propôs pode cancelar.")
    _sb("PATCH", f"/shift_swap_requests?id=eq.{_q(swap_id)}",
        {"status": "cancelled", "updated_at": "now()"})
    return {"ok": True}


# ── TIPOS DE CRÉDITO PERSONALIZADOS (custom_credit_types) ────
def sb_custom_types_list(user):
    gid = _gid(user)
    rows = _sb("GET", f"/custom_credit_types?group_id=eq.{_q(gid)}&active=eq.true&select=*&order=created_at")
    return [{"id": r["id"], "name": r["name"], "credit_value": float(r["credit_value"]),
             "applies_to_days": r.get("applies_to_days") or ["all"],
             "applies_to_shifts": r.get("applies_to_shifts") or ["all"],
             "is_additional": bool(r.get("is_additional", True))} for r in rows]

def sb_custom_types_create(user, body: dict):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores.")
    gid = _gid(user)
    name = (body.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "Informe o nome do tipo de crédito.")
    row = {
        "group_id": gid, "name": name,
        "credit_value": float(body.get("credit_value") or 0),
        "applies_to_days": body.get("applies_to_days") or ["all"],
        "applies_to_shifts": body.get("applies_to_shifts") or ["all"],
        "is_additional": bool(body.get("is_additional", True)),
        "active": True,
    }
    rows = _sb("POST", "/custom_credit_types", row)
    _log(user, f"Tipo de crédito criado: {name}")
    return {"ok": True, "id": rows[0]["id"]}

def sb_custom_types_delete(user, type_id):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores.")
    gid = _gid(user)
    _sb("PATCH", f"/custom_credit_types?id=eq.{_q(type_id)}&group_id=eq.{_q(gid)}", {"active": False})
    _log(user, f"Tipo de crédito desativado: {type_id}")
    return {"ok": True}
