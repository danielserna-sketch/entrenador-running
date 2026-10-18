"""Lo que el agente sabe del atleta: ficha determinista, resumen semanal y resumen de la conversación.

La ficha se arma en cada turno solo desde el repositorio (no llama al LLM ni a Garmin/Strava) y tiene
un presupuesto fijo de caracteres para controlar el costo. Va en la parte dinámica del system, que
solo cambia cuando cambian los datos, así que la caché del proveedor la reutiliza entre turnos.

Los resúmenes sí usan el modelo rápido, pero pocas veces: el del atleta una vez por semana y el de la
conversación cuando el historial crece (ver `Coach._history`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from coach.analytics import history
from coach.analytics import zones as zones_mod
from coach.llm.base import LLMClient, SystemPrompt
from coach.planning import periodization
from coach.storage.repo import Repo

# Lo mínimo para crear el plan (lo exige PlanService.create_plan).
REQUIRED_PROFILE = ["goal_distance", "race_date", "days_available"]
# Lo que el plan usa si existe; también se pregunta, pero no bloquea.
EXTRA_PROFILE = ["max_hours_week", "longest_session_min"]
FIELD_LABELS = {
    "goal_distance": "distancia objetivo", "race_date": "fecha de la carrera",
    "days_available": "días disponibles para entrenar", "max_hours_week": "horas máximas por semana",
    "longest_session_min": "duración máxima de una sesión (min)",
}
DISTANCE_LABELS = {"5k": "5K", "10k": "10K", "half": "media maratón", "marathon": "maratón"}

FICHA_MAX_CHARS = 2000
NOTES_DETAIL_DAYS = 14
NOTES_SUMMARY_DAYS = 60
NOTES_DETAIL_MAX = 8
MEMORY_MAX_FACTS = 200
FACT_MAX_CHARS = 200
ENTRIES_MAX = 20
CONFLICT_TOLERANCE = 0.05  # 5 % de diferencia entre la marca del perfil y la de Strava
ATHLETE_SUMMARY_MAX_CHARS = 800
CONVERSATION_SUMMARY_MAX_CHARS = 1500


# ---------------------------------------------------------------------------- normalización
def normalize_best_times(raw: dict | None) -> dict[str, dict]:
    """Marcas como {tiempo_s, fecha, fuente}. Antes se guardaban como segundos sueltos."""
    out = {}
    for key, value in (raw or {}).items():
        if isinstance(value, dict):
            if value.get("tiempo_s"):
                out[key] = value
        elif value:
            out[key] = {"tiempo_s": int(value), "fecha": None, "fuente": "atleta"}
    return out


def normalize_entries(raw: str | list | None) -> list[dict]:
    """Lesiones y notas del perfil como lista con fecha. Antes eran un texto que se sobrescribía."""
    if not raw:
        return []
    if isinstance(raw, str):
        return [{"fecha": None, "texto": raw}]
    return [e if isinstance(e, dict) else {"fecha": None, "texto": str(e)} for e in raw]


def normalize_memory(memory: dict | None) -> dict:
    """Cada hecho con id y fuente. Los hechos antiguos reciben un id la primera vez que se leen."""
    memory = dict(memory or {})
    facts = [dict(f) for f in memory.get("facts", [])]
    used = {str(f["id"]) for f in facts if f.get("id")}
    n = 1
    for f in facts:
        if not f.get("id"):
            while str(n) in used:
                n += 1
            f["id"] = str(n)
            used.add(f["id"])
        f["id"] = str(f["id"])
        f.setdefault("fuente", "chat")
    memory["facts"] = facts
    memory.setdefault("summary", "")
    return memory


def active_facts(memory: dict) -> list[dict]:
    return [f for f in memory["facts"] if not f.get("olvidado")]


def next_fact_id(facts: list[dict]) -> str:
    return str(max((int(f["id"]) for f in facts if str(f.get("id", "")).isdigit()), default=0) + 1)


def cap_facts(facts: list[dict]) -> list[dict]:
    """Respeta el límite descartando primero los olvidados y después los más antiguos."""
    extra = len(facts) - MEMORY_MAX_FACTS
    if extra <= 0:
        return facts
    forgotten = [f for f in facts if f.get("olvidado")][:extra]
    facts = [f for f in facts if f not in forgotten]
    return facts[max(len(facts) - MEMORY_MAX_FACTS, 0):]


# ---------------------------------------------------------------------------- ficha
def _hms(seconds: float | None) -> str:
    if not seconds:
        return "?"
    seconds = round(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def missing_fields(profile: dict, records: dict | None) -> list[str]:
    out = [FIELD_LABELS[f] for f in REQUIRED_PROFILE + EXTRA_PROFILE if not profile.get(f)]
    if "injuries" not in profile:
        out.append("lesiones actuales o pasadas (si no tiene, guardar 'ninguna')")
    if not normalize_best_times(profile.get("best_times")) and not history.best_times_from_records(records):
        out.append("mejores marcas recientes")
    return out


def conflicts(profile: dict, records: dict | None) -> list[str]:
    """Marca del perfil distinta a una de Strava más reciente. Al reconfirmarla se guarda con fecha de
    hoy y deja de aparecer."""
    out = []
    strava = history.best_times_from_records(records)
    for key, mark in normalize_best_times(profile.get("best_times")).items():
        s = strava.get(key)
        if not s or not s["recent"] or (mark.get("fecha") and mark["fecha"] >= s["date"]):
            continue
        if abs(mark["tiempo_s"] - s["time_s"]) / s["time_s"] > CONFLICT_TOLERANCE:
            out.append(f"Marca {DISTANCE_LABELS.get(key, key)}: perfil {_hms(mark['tiempo_s'])}, "
                       f"Strava {_hms(s['time_s'])} ({s['date']})")
    return out


@dataclass
class Ficha:
    faltantes: list[str]
    conflictos: list[str]
    secciones: list[tuple[str, list[str]]] = field(default_factory=list)

    def to_prompt(self) -> str:
        parts = ["FICHA DEL ATLETA (datos guardados; revísala antes de preguntar)"]
        parts.append("Falta saber: " + ("; ".join(self.faltantes) if self.faltantes else "nada"))
        if self.conflictos:
            parts.append("Por confirmar una vez: " + "; ".join(self.conflictos))
        for title, lines in self.secciones:
            if lines:
                parts.append(f"{title}:\n" + "\n".join(f"- {line}" for line in lines))
        return "\n".join(parts)


def _profile_lines(profile: dict) -> list[str]:
    lines = []
    if profile.get("name"):
        lines.append(f"Nombre: {profile['name']}")
    goal = [DISTANCE_LABELS.get(profile.get("goal_distance"), profile.get("goal_distance"))]
    if profile.get("race_name"):
        goal.append(profile["race_name"])
    if profile.get("race_date"):
        goal.append(f"el {profile['race_date']}")
    if profile.get("goal_time_s"):
        goal.append(f"objetivo {_hms(profile['goal_time_s'])}")
    if any(goal):
        lines.append("Carrera: " + " ".join(str(g) for g in goal if g))
    if profile.get("days_available"):
        lines.append("Días: " + ", ".join(profile["days_available"]))
    if profile.get("max_hours_week"):
        lines.append(f"Máximo {profile['max_hours_week']} h/semana")
    if profile.get("longest_session_min"):
        lines.append(f"Sesión más larga posible: {profile['longest_session_min']} min")
    if "has_power_meter" in profile:
        lines.append("Potenciómetro: " + ("sí" if profile["has_power_meter"] else "no"))
    for key, label in (("injuries", "Lesiones"), ("notes", "Notas")):
        entries = normalize_entries(profile.get(key))[-3:]
        if entries:
            lines.append(f"{label}: " + " | ".join(
                f"{e['fecha'] + ': ' if e.get('fecha') else ''}{e['texto']}" for e in entries))
    return lines


def _marks_lines(profile: dict, records: dict | None) -> list[str]:
    lines = []
    strava = history.best_times_from_records(records)
    for key, mark in normalize_best_times(profile.get("best_times")).items():
        when = f", {mark['fecha']}" if mark.get("fecha") else ""
        lines.append(f"{DISTANCE_LABELS.get(key, key)}: {_hms(mark['tiempo_s'])} (dicha por el atleta{when})")
    for key, s in strava.items():
        period = "últimos 12 meses" if s["recent"] else "de siempre"
        lines.append(f"{DISTANCE_LABELS.get(key, key)}: {_hms(s['time_s'])} (Strava, {period}, {s['date']})")
    return lines


def _plan_lines(repo: Repo, today: date) -> list[str]:
    lines = []
    macro = repo.get_macro()
    if macro:
        lines.append(f"Plan desde {macro['start']} hasta la carrera {macro['race_date']} "
                     f"({macro['total_weeks']} semanas)")
    week = repo.get_week(periodization.monday_of(today))
    if week:
        lines.append(f"Semana {week.get('index')} · {week.get('phase_label')} · {week.get('kind')}")
        lines += [f"{s['date']}: {s['title']}" for s in week.get("sessions", [])]
    return lines


def _zones_lines(zones: dict | None, profile: dict, records: dict | None) -> list[str]:
    if not zones:
        return ["Sin zonas: falta el test de 20 minutos"]
    lines = [(f"Umbral: ritmo {zones.get('threshold_pace')} min/km, FC {zones.get('threshold_hr')} "
              f"(actualizadas {zones.get('updated')})")]
    ten_k = (normalize_best_times(profile.get("best_times")).get("10k") or {}).get("tiempo_s") or \
        (history.best_times_from_records(records).get("10k") or {}).get("time_s")
    ceiling = zones_mod.easy_pace_ceiling(zones, ten_k)
    if ceiling:
        lines.append(f"Rodaje fácil: no más rápido que {_hms(ceiling)}/km")
    return lines


def _note_line(n: dict) -> str:
    parts = []
    if n.get("rpe") is not None:
        parts.append(f"RPE {n['rpe']}")
    if n.get("pain"):
        parts.append(f"dolor {n['pain']}/10" + (f" {n['pain_location']}" if n.get("pain_location") else ""))
    if n.get("text"):
        parts.append(str(n["text"])[:100])
    return f"{n.get('date')}: " + ", ".join(parts)


def _older_notes_line(notes: list[dict]) -> str | None:
    if not notes:
        return None
    rpes = [n["rpe"] for n in notes if n.get("rpe") is not None]
    pains = [n for n in notes if n.get("pain")]
    line = f"{len(notes)} notas anteriores"
    if rpes:
        line += f", RPE medio {sum(rpes) / len(rpes):.1f}"
    if pains:
        places = sorted({n.get("pain_location") for n in pains if n.get("pain_location")})
        line += f", dolor reportado {len(pains)} veces" + (f" ({', '.join(places)})" if places else "")
    return line


def _fit(lines: list[str], budget: int, more_hint: str) -> list[str]:
    """Toma líneas en orden hasta agotar el presupuesto y avisa cuántas quedaron fuera."""
    out, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 3 > budget:
            out.append(f"(+{len(lines) - i} más; {more_hint})")
            break
        out.append(line)
        used += len(line) + 3
    return out


def build_ficha(repo: Repo, today: date) -> Ficha:
    profile = repo.get_profile()
    records = repo.get_records()
    ficha = Ficha(faltantes=missing_fields(profile, records), conflictos=conflicts(profile, records))
    fixed = [("Perfil", _profile_lines(profile)), ("Marcas", _marks_lines(profile, records)),
             ("Zonas", _zones_lines(repo.get_zones(), profile, records)), ("Plan", _plan_lines(repo, today))]
    ficha.secciones = list(fixed)
    budget = FICHA_MAX_CHARS - len(ficha.to_prompt())

    notes = repo.notes(today - timedelta(days=NOTES_SUMMARY_DAYS))
    cutoff = (today - timedelta(days=NOTES_DETAIL_DAYS)).isoformat()
    recent = sorted((n for n in notes if (n.get("date") or "") >= cutoff), key=lambda n: n.get("date") or "",
                    reverse=True)
    older = [n for n in notes if (n.get("date") or "") < cutoff]
    note_lines = _fit([_note_line(n) for n in recent[:NOTES_DETAIL_MAX]], budget // 3, "ver notas con el atleta")
    older_line = _older_notes_line(older)
    if older_line:
        note_lines.append(older_line)
    budget -= sum(len(line) + 3 for line in note_lines) + 30

    facts = sorted(active_facts(normalize_memory(repo.get_memory())),
                   key=lambda f: (f.get("date") or "", int(f["id"]) if f["id"].isdigit() else 0), reverse=True)
    fact_lines = _fit([f"[{f['id']}] {f['fact'][:FACT_MAX_CHARS]}" for f in facts], budget,
                      "consulta get_profile antes de preguntar")
    ficha.secciones += [(f"Notas (detalle de {NOTES_DETAIL_DAYS} días)", note_lines),
                        ("Hechos guardados (id entre corchetes)", fact_lines)]
    return ficha


# ---------------------------------------------------------------------------- resúmenes con LLM
SUMMARY_SYSTEM = SystemPrompt(static=(
    "Eres el asistente de memoria de un entrenador de running. Resumes en español, con frases cortas y "
    "solo con datos presentes en la información que recibes. No inventes ni supongas nada."
))


def summarize_conversation(llm: LLMClient, previous: str, messages: list[dict]) -> str:
    """Integra mensajes viejos al resumen de la conversación (para no perderlos al compactar)."""
    chat = "\n".join(f"{'Atleta' if m['role'] == 'user' else 'Entrenador'}: {m['text']}" for m in messages)
    prompt = (
        "Actualiza el resumen de la conversación entre el entrenador y el atleta. Conserva lo que sirva "
        "para no volver a preguntar: datos que dio el atleta, decisiones, acuerdos, preferencias, fechas y "
        f"preguntas pendientes. Máximo {CONVERSATION_SUMMARY_MAX_CHARS} caracteres.\n\n"
        f"Resumen anterior:\n{previous or '(vacío)'}\n\nMensajes nuevos a integrar:\n{chat}"
    )
    result = llm.complete_json(SUMMARY_SYSTEM, prompt, {
        "type": "object", "properties": {"resumen": {"type": "string"}}, "required": ["resumen"],
        "additionalProperties": False}, tier="fast")
    return result["resumen"].strip()[:CONVERSATION_SUMMARY_MAX_CHARS]


def refresh_athlete_summary(repo: Repo, llm: LLMClient, today: date) -> dict:
    """Resumen semanal del atleta y hechos nuevos que el agente no guardó durante la semana."""
    week_start = (today - timedelta(days=7)).isoformat()
    chat = "\n".join(f"{'Atleta' if m['role'] == 'user' else 'Entrenador'}: {m['text'][:500]}"
                     for m in repo.messages_since(week_start))
    notes = "\n".join(_note_line(n) for n in repo.notes(today - timedelta(days=7)))
    memory = normalize_memory(repo.get_memory())
    prompt = (
        f"Escribe el resumen del atleta (máximo {ATHLETE_SUMMARY_MAX_CHARS} caracteres): quién es como "
        "corredor, objetivo, contexto de vida que afecta el entrenamiento, cómo viene respondiendo y qué "
        "conviene recordar. Además lista en hechos_nuevos (máximo 5) datos duraderos de los mensajes de "
        "esta semana que NO estén ya en la ficha.\n\n"
        f"{build_ficha(repo, today).to_prompt()}\n\nResumen anterior:\n{memory['summary'] or '(vacío)'}\n\n"
        f"Notas de la semana:\n{notes or '(ninguna)'}\n\nMensajes de la semana:\n{chat or '(ninguno)'}"
    )
    result = llm.complete_json(SUMMARY_SYSTEM, prompt, {
        "type": "object", "properties": {"resumen": {"type": "string"},
                                         "hechos_nuevos": {"type": "array", "items": {"type": "string"}}},
        "required": ["resumen", "hechos_nuevos"], "additionalProperties": False}, tier="fast")

    known = {f["fact"].strip().lower() for f in active_facts(memory)}
    added = []
    for fact in result.get("hechos_nuevos", [])[:5]:
        fact = fact.strip()
        if fact and fact.lower() not in known:
            memory["facts"].append({"id": next_fact_id(memory["facts"]), "date": today.isoformat(),
                                    "fact": fact, "fuente": "resumen_semanal"})
            known.add(fact.lower())
            added.append(fact)
    memory["facts"] = cap_facts(memory["facts"])
    memory["summary"] = result["resumen"].strip()[:ATHLETE_SUMMARY_MAX_CHARS]
    memory["summary_updated"] = today.isoformat()
    repo.save_memory(memory)
    return {"resumen": memory["summary"], "hechos_nuevos": added}
