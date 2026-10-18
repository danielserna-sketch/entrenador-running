"""Generación de la semana (microciclo) con el LLM, dentro de las restricciones del método.

El LLM propone sesiones en formato neutral; el validador revisa las reglas y, si hay errores,
se le devuelven para que corrija (máximo MAX_ATTEMPTS intentos).
"""

from __future__ import annotations

import json
from datetime import date

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
                    "description": {"type": "string"},
                    "cadence_focus": {"type": "boolean"},
                    "steps": {"type": "array", "items": _STEP},
                },
                "required": ["date", "title", "type", "description", "cadence_focus", "steps"],
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
        ("Distribuye la carga sin dos sesiones intensas en días consecutivos. Incluye una sesión larga "
        "en zona baja. Usa cadence_focus=true en 1-2 sesiones para trabajar cadencia con metrónomo."),
        "Cada paso con duración (duration_min) o distancia (distance_km) y una zona Friel.",
    ]
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
            zones=zones,
        )
        if not errors:
            break

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
    }
