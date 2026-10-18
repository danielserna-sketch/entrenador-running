"""Convierte una sesión planeada (formato neutral) en un workout estructurado de Garmin.

Formato neutral de sesión (lo produce el microciclo):
{
  "date": "2026-10-20", "title": "Rodaje Z2 con cadencia", "type": "easy|long|intervals|tempo|test|recovery",
  "purpose": "para qué sirve la sesión", "description": "cómo hacerla", "cadence": {"target_rpm": 82, "metronome_bpm": 164} | null,
  "steps": [
    {"kind": "warmup", "duration_min": 10, "zone": "Z1"},
    {"kind": "repeat", "times": 5, "steps": [
        {"kind": "interval", "duration_min": 3, "zone": "Z4"},
        {"kind": "recovery", "duration_min": 2, "zone": "Z1"}]},
    {"kind": "interval", "distance_km": 5, "zone": "Z3"},
    {"kind": "cooldown", "duration_min": 10, "zone": "Z1"}
  ]
}
Objetivo en el reloj: las zonas suaves (Z1-Z2) como rango de FC; las de calidad como rango de pace (m/s)
si hay zonas de pace, si no como FC. Las notas explican la sesión y dan la FC y el ritmo de cada bloque.
"""

from __future__ import annotations

from coach.analytics.zones import pace_to_str
from coach.methodology import rules

STEP_TYPES = {
    "warmup": (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "recovery": (4, "recovery"),
    "rest": (5, "rest"),
    "repeat": (6, "repeat"),
    "main": (8, "main"),
}
# Todo entrenamiento que el agente crea en Garmin lleva este prefijo, para distinguirlo en el reloj.
WORKOUT_PREFIX = "Agents - "
RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}


def _pace_s_to_mps(pace_s: float) -> float:
    return round(1000 / pace_s, 3)


def _zone(zones: dict, kind: str, zone: str | None) -> dict | None:
    return next((z for z in zones.get(kind, []) if z["zone"] == zone), None)


def _pace_range(zone: str, zones: dict) -> tuple[float, float] | None:
    """(rápido, lento) en s/km. En zona fácil el rápido es el ritmo máximo de rodaje (primero lento)."""
    pace = _zone(zones, "pace", zone)
    if not pace:
        return None
    slow = min(pace["slow_s"], 600)  # evita rangos absurdos en Z1
    fast = pace["fast_s"]
    if zone in rules.EASY_ZONES and zones.get("easy_ceiling_s"):
        fast = max(fast, zones["easy_ceiling_s"])
        slow = max(slow, fast + 20)
    return fast, slow


def _uses_hr(zone: str, zones: dict, prefer: str) -> bool:
    """Suaves por FC (lo que manda en un rodaje es el pulso); calidad por ritmo si hay zonas de ritmo."""
    if not _zone(zones, "hr", zone):
        return False
    if prefer == "hr" or not _zone(zones, "pace", zone):
        return True
    return prefer == "auto" and zone in rules.EASY_ZONES


def _target(zone: str | None, zones: dict, prefer: str = "auto") -> dict:
    no_target = {"targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}}
    if not zone or not zones:
        return no_target
    if _uses_hr(zone, zones, prefer):
        hr = _zone(zones, "hr", zone)
        return {
            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            "targetValueOne": max(hr["min_bpm"], 60),
            "targetValueTwo": hr["max_bpm"],
        }
    pace = _pace_range(zone, zones)
    if pace:
        fast, slow = pace
        # Garmin: límite inferior = pace más lento (menos m/s).
        return {
            "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
            "targetValueOne": _pace_s_to_mps(slow),
            "targetValueTwo": _pace_s_to_mps(fast),
        }
    return no_target


def zone_text(zone: str | None, zones: dict) -> str:
    """'Z3 · 151-161 ppm · 5:40-6:05/km': la FC y el ritmo de la zona, los que existan."""
    if not zone:
        return ""
    parts = [zone]
    hr = _zone(zones, "hr", zone)
    if hr and zone.startswith("Z5") and zones.get("hr_source") == "garmin":
        parts.append(f"más de {hr['min_bpm'] - 1} ppm")  # la Z5 de Garmin no tiene techo
    elif hr:
        parts.append(f"{max(hr['min_bpm'], 60)}-{hr['max_bpm']} ppm")
    pace = _pace_range(zone, zones)
    if pace:
        fast, slow = pace
        if zone in rules.EASY_ZONES and zones.get("easy_ceiling_s"):
            parts.append(f"no más rápido que {pace_to_str(fast)}/km")
        else:
            parts.append(f"{pace_to_str(fast)}-{pace_to_str(slow)}/km")
    return " · ".join(parts)


def _end_condition(step: dict) -> dict:
    if step.get("distance_km"):
        return {"endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
                "endConditionValue": round(step["distance_km"] * 1000)}
    if step.get("duration_min"):
        return {"endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": round(step["duration_min"] * 60)}
    return {"endCondition": {"conditionTypeId": 1, "conditionTypeKey": "lap.button"}}


def _build_steps(steps: list[dict], zones: dict, prefer: str, counter: list[int]) -> list[dict]:
    out = []
    for step in steps:
        counter[0] += 1
        order = counter[0]
        if step["kind"] == "repeat":
            type_id, type_key = STEP_TYPES["repeat"]
            out.append({
                "type": "RepeatGroupDTO",
                "stepOrder": order,
                "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key},
                "numberOfIterations": int(step["times"]),
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
                "endConditionValue": int(step["times"]),
                "smartRepeat": False,
                "workoutSteps": _build_steps(step["steps"], zones, prefer, counter),
            })
            continue
        type_id, type_key = STEP_TYPES.get(step["kind"], STEP_TYPES["interval"])
        out.append({
            "type": "ExecutableStepDTO",
            "stepOrder": order,
            "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key},
            **_end_condition(step),
            **_target(step.get("zone"), zones, prefer),
            "description": " — ".join(t for t in (zone_text(step.get("zone"), zones), step.get("note")) if t) or None,
        })
    return out


STEP_LABELS = {"warmup": "Calentamiento", "cooldown": "Enfriamiento", "interval": "Intervalo",
               "recovery": "Recuperación", "rest": "Descanso", "main": "Principal"}


def _amount(step: dict) -> str:
    if step.get("distance_km"):
        return f"{step['distance_km']:g} km"
    if step.get("duration_min"):
        return f"{step['duration_min']:g}′"
    return "hasta botón de vuelta"


def _block_line(step: dict, zones: dict) -> str:
    text = zone_text(step.get("zone"), zones)
    return f"{STEP_LABELS.get(step['kind'], 'Intervalo')} {_amount(step)}" + (f" · {text}" if text else "")


def _block_lines(steps: list[dict], zones: dict) -> list[str]:
    """Una línea por bloque con su FC y ritmo, calculados de las zonas (no los escribe el LLM)."""
    lines = []
    for step in steps:
        if step["kind"] == "repeat":
            lines.append(f"• {step['times']} veces:")
            lines += [f"   – {_block_line(s, zones)}" for s in step["steps"]]
        else:
            lines.append(f"• {_block_line(step, zones)}")
    return lines


def workout_notes(session: dict, zones: dict) -> str:
    """Notas del entrenamiento en Garmin: para qué sirve, cómo hacerlo y a qué FC/ritmo cada bloque."""
    parts = []
    if session.get("purpose"):
        parts.append(f"Objetivo: {session['purpose'].strip()}")
    if session.get("description"):
        parts.append(f"Cómo hacerla: {session['description'].strip()}")
    parts.append("Bloques:\n" + "\n".join(_block_lines(session["steps"], zones)))
    if not (zones.get("hr") or zones.get("pace")):
        parts.append("Aún no hay zonas: guíate por la respiración y el esfuerzo indicado.")
    cadence = session.get("cadence")
    if cadence:
        parts.append(f"Cadencia objetivo: {cadence['target_rpm']} rpm. Metrónomo a {cadence['metronome_bpm']} bpm, "
                     "alerta cada 2 ciclos, solo vibración.")
    return "\n\n".join(parts)


def estimated_duration_s(steps: list[dict], zones: dict | None = None) -> int:
    total = 0.0
    for step in steps:
        if step["kind"] == "repeat":
            total += step["times"] * estimated_duration_s(step["steps"], zones)
        elif step.get("duration_min"):
            total += step["duration_min"] * 60
        elif step.get("distance_km"):
            pace = next((z for z in (zones or {}).get("pace", []) if z["zone"] == step.get("zone")), None)
            pace_s = ((pace["fast_s"] + min(pace["slow_s"], 600)) / 2) if pace else 360
            total += step["distance_km"] * pace_s
    return int(total)


def build_workout(session: dict, zones: dict | None, prefer: str = "auto") -> dict:
    zones = zones or {}
    return {
        "workoutName": f"{WORKOUT_PREFIX}{session['title']}"[:80],
        "description": workout_notes(session, zones)[:1000],
        "sportType": RUNNING,
        "estimatedDurationInSecs": estimated_duration_s(session["steps"], zones),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": RUNNING,
            "workoutSteps": _build_steps(session["steps"], zones, prefer, [0]),
        }],
    }
