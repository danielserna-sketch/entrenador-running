"""Generación de la semana (microciclo) con el LLM, dentro de las restricciones del método.

El LLM propone sesiones en formato neutral; el validador revisa las reglas y, si hay errores,
se le devuelven para que corrija (máximo MAX_ATTEMPTS intentos).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from coach.analytics import cadence
from coach.llm.base import LLMClient, SystemPrompt
from coach.methodology import rules
from coach.planning import validator

MAX_ATTEMPTS = 3
ZONE_ENUM = rules.ZONE_ORDER

_SIMPLE_STEP = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["warmup", "interval", "recovery", "rest", "cooldown", "main"]},
        "duration_min": {"type": "number"},
        "distance_km": {"type": "number"},
        "zone": {"type": "string", "enum": ZONE_ENUM},
        "note": {"type": "string"},
    },
    "required": ["kind", "zone"],
    "additionalProperties": False,
}
_STEP = {
    "type": "object",
    "properties": {
        "kind": {"type": "string",
                 "enum": ["warmup", "interval", "recovery", "rest", "cooldown", "main", "repeat"]},
        "duration_min": {"type": "number"},
        "distance_km": {"type": "number"},
        "zone": {"type": "string", "enum": ZONE_ENUM},
        "note": {"type": "string"},
        "times": {"type": "integer"},
        "steps": {"type": "array", "items": _SIMPLE_STEP},
    },
    "required": ["kind"],
    "additionalProperties": False,
}
WEEK_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Objetivo de la semana en 1-3 frases"},
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "title": {"type": "string"},
                    "type": {"type": "string",
                             "enum": ["easy", "long", "intervals", "tempo", "test", "recovery", "technique"]},
                    "purpose": {"type": "string", "description": "Para qué sirve la sesión, 1-2 frases simples"},
                    "description": {"type": "string",
                                    "description": "Cómo hacerla: sensaciones, respiración, RPE. Sin rangos de FC "
                                                   "ni ritmo (el sistema los agrega desde las zonas)."},
                    "cadence_focus": {"type": "boolean"},
                    "steps": {"type": "array", "items": _STEP},
                },
                "required": ["date", "title", "type", "purpose", "description", "cadence_focus", "steps"],
                "additionalProperties": False,
            },
        },
        "strength_notes": {"type": "string", "description": "Fuerza/movilidad sugerida (si aplica)"},
    },
    "required": ["summary", "sessions", "strength_notes"],
    "additionalProperties": False,
}


def _main_zone(session: dict) -> str | None:
    best, best_min = None, -1.0
    for step in validator._iter_steps(session["steps"]):
        minutes = step.get("duration_min") or (step.get("distance_km") or 0) * 6
        if step.get("kind") not in ("warmup", "cooldown") and minutes > best_min:
            best, best_min = step.get("zone"), minutes
    return best


def _pace(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_prompt(ctx: dict) -> str:
    w = ctx["week"]
    lines = [
        f"Genera el microciclo de la semana {w['index']} del plan (lunes {w['week_start']}).",
        f"Fase: {w['phase_label']} ({w['phase']}) — {rules.PHASE_PURPOSE[w['phase']]}",
        f"Tipo de semana: {w['kind']}. TSS objetivo: {w['target_tss']} (±15%), ~{w['target_hours']} h.",
        f"Zonas permitidas en esta fase: {sorted(rules.PHASE_ALLOWED_ZONES[w['phase']])}.",
        f"Sesiones: recomendado {ctx['sessions_recommended']}, máximo {ctx['sessions_max']}.",
        f"Días disponibles: {', '.join(ctx['days_available']) or 'cualquiera'}.",
        f"Sesión más larga permitida: {ctx.get('longest_session_min') or 'sin límite definido'} min.",
        (f"PRIMERO LENTO: al menos {rules.EASY_SHARE_MIN_BY_PHASE[w['phase']]:.0%} del tiempo en Z1-Z2 "
         f"(sin contar test ni carrera). Máximo {ctx.get('max_quality', 2)} sesiones de calidad (Z3 o más), "
         "nunca en días consecutivos; el resto son rodajes suaves. El fondo va en Z1-Z2."),
        (f"Series y tempo: empieza con {rules.QUALITY_WARMUP_MIN}-25 min de calentamiento muy suave y termina con "
         "5-10 min de enfriamiento; ritmos medidos para terminar entero, no al máximo."
         + (f" Máximo {rules.MAX_INTERVAL_KM[ctx['distance']]} km de series por sesión."
            if ctx.get("distance") in rules.MAX_INTERVAL_KM else "")),
        "Usa cadence_focus=true en 1-2 sesiones para trabajar cadencia con metrónomo.",
        "Cada paso con duración (duration_min) o distancia (distance_km) y una zona Friel.",
        ("Cada sesión lleva 'purpose' (para qué sirve, en palabras simples: p. ej. 'Construir base aeróbica sin "
         "acumular fatiga') y 'description' (cómo hacerla: sensaciones, respiración, RPE). No escribas rangos de "
         "FC ni de ritmo: el sistema los pone en las notas y en el reloj a partir de las zonas."),
    ]
    if ctx.get("easy_ceiling_s"):
        lines.append(f"Ritmo de rodaje fácil: nunca más rápido que {_pace(ctx['easy_ceiling_s'])}/km.")
    if (ctx.get("readiness") or {}).get("reason"):
        lines.append(f"Ajuste por bienestar: {ctx['readiness']['reason']}. La carga ya se bajó; prioriza rodajes "
                     "suaves y cortos.")
    if ctx.get("needs_test"):
        lines.append("OBLIGATORIO: incluye una sesión type='test': calentamiento 15 min Z1-Z2, "
                     "20 min al máximo esfuerzo sostenible (zone Z4, note 'Test 20 min: esfuerzo máximo "
                     "sostenido'), enfriamiento 10 min Z1. Ponlo al inicio de la semana tras un día suave.")
    if ctx.get("race"):
        lines.append(f"La carrera es el {ctx['race']['date']} ({ctx['race']['distance']}). "
                     "Si cae en esta semana, inclúyela como sesión type='tempo' titulada 'Carrera'.")
    lines += [
        "",
        "Zonas actuales del atleta:",
        json.dumps(ctx.get("zones_table") or "sin zonas (usa FC/RPE descriptivo)", ensure_ascii=False),
        "Resumen de la semana anterior (cumplimiento por colores y carga):",
        json.dumps(ctx.get("previous_week") or {}, ensure_ascii=False, default=str),
        "Bienestar reciente y notas del atleta:",
        json.dumps(ctx.get("wellness") or {}, ensure_ascii=False, default=str),
        json.dumps(ctx.get("notes") or [], ensure_ascii=False, default=str),
    ]
    if ctx.get("adjustments"):
        lines.append(f"Ajustes pedidos para esta semana: {ctx['adjustments']}")
    return "\n".join(lines)


def fallback_week(ctx: dict, zones: dict | None) -> dict:
    """Semana segura: rodajes en Z2 en los días disponibles y un fondo en Z2, escalados al TSS objetivo.
    Incluye el test si hace falta y la carrera si cae en la semana."""
    w = ctx["week"]
    monday = date.fromisoformat(w["week_start"])
    available = [d.lower() for d in ctx.get("days_available") or []] or list(validator.WEEKDAYS)
    days = [i for i, name in enumerate(validator.WEEKDAYS) if name in available]
    n = max(1, min(ctx.get("sessions_recommended") or len(days), len(days)))
    picked = [days[round(i * (len(days) - 1) / (n - 1))] for i in range(n)] if n > 1 else [days[-1]]
    picked = sorted(set(picked))
    race_day = None
    if ctx.get("race"):
        race = date.fromisoformat(ctx["race"]["date"])
        if monday <= race <= monday + timedelta(days=6):
            race_day = race.weekday()
            picked = [d for d in picked if d != race_day]

    def day(i: int) -> str:
        return (monday + timedelta(days=i)).isoformat()

    sessions: list[dict] = []
    if ctx.get("needs_test") and picked:
        sessions.append({"date": day(picked.pop(0)), "title": "Test 20 min", "type": "test", "cadence_focus": False,
                         "purpose": "Medir tu umbral para ajustar las zonas de ritmo del plan.",
                         "description": "Calentamiento suave, 20 min al máximo esfuerzo sostenible y enfriamiento.",
                         "steps": [{"kind": "warmup", "duration_min": 15, "zone": "Z1"},
                                   {"kind": "interval", "duration_min": 20, "zone": "Z4",
                                    "note": "Test 20 min: esfuerzo máximo sostenido"},
                                   {"kind": "cooldown", "duration_min": 10, "zone": "Z1"}]})
    if race_day is not None:
        km = rules.DISTANCE_KM.get(ctx["race"]["distance"], 10.0)
        sessions.append({"date": day(race_day), "title": "Carrera", "type": "tempo", "cadence_focus": False,
                         "purpose": "La carrera objetivo: correr a lo que preparaste.",
                         "description": "Día de carrera.", "steps": [{"kind": "main", "distance_km": km, "zone": "Z4"}]})
    if picked:
        done = sum(validator.session_tss(s, zones) for s in sessions)
        z2_tss_per_min = rules.ZONE_IF["Z2"] ** 2 * 100 / 60
        minutes = max(w["target_tss"] - done, 0) / z2_tss_per_min
        cap = ctx.get("longest_session_min") or 150
        long_min = min(round(minutes * 0.4), cap) if len(picked) > 1 else min(round(minutes), cap)
        rest = picked[:-1]
        each = min(round((minutes - long_min) / len(rest)), cap) if rest else 0
        for d in rest:
            sessions.append({"date": day(d), "title": "Rodaje suave", "type": "easy", "cadence_focus": False,
                             "purpose": "Sumar base aeróbica sin acumular fatiga.",
                             "description": "Rodaje en Z2: conversando, sin apurarte.",
                             "steps": [{"kind": "main", "duration_min": max(each, 20), "zone": "Z2"}]})
        sessions.append({"date": day(picked[-1]), "title": "Fondo suave", "type": "long", "cadence_focus": False,
                         "purpose": "Resistencia: acostumbrar el cuerpo a correr más tiempo a ritmo cómodo.",
                         "description": "Fondo en Z2, ritmo cómodo de principio a fin.",
                         "steps": [{"kind": "main", "duration_min": max(long_min, 30), "zone": "Z2"}]})
    return {"summary": "Semana segura de rodajes suaves en Z2 (la propuesta automática no cumplía las reglas).",
            "sessions": sessions, "strength_notes": "Fuerza 1-2 veces por semana, 20-30 min, en días suaves."}


def generate_week(llm: LLMClient, system: SystemPrompt, ctx: dict, zones: dict | None,
                  activities: list[dict]) -> dict:
    week_start = date.fromisoformat(ctx["week"]["week_start"])
    prompt = build_prompt(ctx)
    errors: list[str] = []
    week: dict = {}
    for attempt in range(MAX_ATTEMPTS):
        full_prompt = prompt if not errors else (
            prompt + "\n\nTu propuesta anterior incumple estas reglas, corrígela:\n- " + "\n- ".join(errors)
            + "\n\nPropuesta anterior:\n" + json.dumps(week, ensure_ascii=False))
        week = llm.complete_json(system, full_prompt, WEEK_SCHEMA, tier="main")
        errors = validator.validate_week(
            week, week_start=week_start, phase=ctx["week"]["phase"], target_tss=ctx["week"]["target_tss"],
            sessions_max=ctx["sessions_max"], days_available=ctx["days_available"],
            longest_session_min=ctx.get("longest_session_min"), needs_test=ctx.get("needs_test", False),
            zones=zones, distance=ctx.get("distance"), max_quality=ctx.get("max_quality"),
        )
        if not errors:
            break

    llm_errors, fallback = errors, False
    if errors:
        # El LLM no logró cumplir las reglas: semana segura de rodajes suaves, si esa sí las cumple.
        candidate = fallback_week(ctx, zones)
        candidate_errors = validator.validate_week(
            candidate, week_start=week_start, phase=ctx["week"]["phase"], target_tss=ctx["week"]["target_tss"],
            sessions_max=ctx["sessions_max"], days_available=ctx["days_available"],
            longest_session_min=ctx.get("longest_session_min"), needs_test=ctx.get("needs_test", False),
            zones=zones, distance=ctx.get("distance"), max_quality=ctx.get("max_quality"),
        )
        if not candidate_errors:
            week, errors, fallback = candidate, [], True

    for s in week["sessions"]:
        s["tss"] = validator.session_tss(s, zones)
        s["duration_min"] = round(validator.estimated_duration_s(s["steps"], zones) / 60)
        s["main_zone"] = _main_zone(s)
        s["cadence"] = None
        if s.get("cadence_focus") and s["main_zone"]:
            prev = cadence.previous_equivalent_cadence(activities, s["main_zone"], date.fromisoformat(s["date"]))
            if prev:
                s["cadence"] = cadence.metronome_target(prev)
    week["sessions"].sort(key=lambda s: s["date"])
    return {
        **ctx["week"],
        "summary": week["summary"],
        "strength_notes": week.get("strength_notes", ""),
        "sessions": week["sessions"],
        "planned_tss": round(sum(s["tss"] for s in week["sessions"]), 1),
        "validation_errors": errors,
        "attempts": attempt + 1,
        "fallback": fallback,
        "llm_errors": llm_errors if fallback else [],
    }
