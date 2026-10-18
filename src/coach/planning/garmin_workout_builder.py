"""Convierte una sesión planeada (formato neutral) en un workout estructurado de Garmin.

Formato neutral de sesión (lo produce el microciclo):
{
  "date": "2026-10-20", "title": "Rodaje Z2 con cadencia", "type": "easy|long|intervals|tempo|test|recovery",
  "description": "...", "cadence": {"target_rpm": 82, "metronome_bpm": 164} | null,
  "steps": [
    {"kind": "warmup", "duration_min": 10, "zone": "Z1"},
    {"kind": "repeat", "times": 5, "steps": [
        {"kind": "interval", "duration_min": 3, "zone": "Z4"},
        {"kind": "recovery", "duration_min": 2, "zone": "Z1"}]},
    {"kind": "interval", "distance_km": 5, "zone": "Z3"},
    {"kind": "cooldown", "duration_min": 10, "zone": "Z1"}
  ]
}
Los objetivos se envían como rango de pace (m/s) si hay zonas de pace, si no como rango de FC.
"""

from __future__ import annotations

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


def _target(zone: str | None, zones: dict, prefer: str = "pace") -> dict:
    no_target = {"targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}}
    if not zone or not zones:
        return no_target
    pace = next((z for z in zones.get("pace", []) if z["zone"] == zone), None)
    hr = next((z for z in zones.get("hr", []) if z["zone"] == zone), None)
    if pace and (prefer == "pace" or not hr):
        # Garmin: límite inferior = pace más lento (menos m/s).
        slow = min(pace["slow_s"], 600)  # evita rangos absurdos en Z1
        fast = pace["fast_s"]
        # Primero lento: en zona fácil el reloj no deja ir más rápido que el ritmo máximo de rodaje.
        if zone in rules.EASY_ZONES and zones.get("easy_ceiling_s"):
            fast = max(fast, zones["easy_ceiling_s"])
            slow = max(slow, fast + 20)
        return {
            "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
            "targetValueOne": _pace_s_to_mps(slow),
            "targetValueTwo": _pace_s_to_mps(fast),
        }
    if hr:
        return {
            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            "targetValueOne": max(hr["min_bpm"], 60),
            "targetValueTwo": hr["max_bpm"],
        }
    return no_target


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
            "description": step.get("note"),
        })
    return out


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


def build_workout(session: dict, zones: dict | None, prefer: str = "pace") -> dict:
    zones = zones or {}
    description = session.get("description") or ""
    cadence = session.get("cadence")
    if cadence:
        description += (f"\nCadencia objetivo: {cadence['target_rpm']} rpm. Metrónomo a "
                        f"{cadence['metronome_bpm']} bpm, alerta cada 2 ciclos, solo vibración.")
    return {
        "workoutName": f"{WORKOUT_PREFIX}{session['title']}"[:80],
        "description": description.strip()[:1000],
        "sportType": RUNNING,
        "estimatedDurationInSecs": estimated_duration_s(session["steps"], zones),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": RUNNING,
            "workoutSteps": _build_steps(session["steps"], zones, prefer, [0]),
        }],
    }
