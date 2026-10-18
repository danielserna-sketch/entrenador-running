"""Normalización de las respuestas de Strava al mismo esquema que `garmin.mappers.map_activity`."""

from __future__ import annotations

from coach.garmin.mappers import _speed_to_pace

RUNNING_TYPES = {"Run", "TrailRun", "VirtualRun"}
WORKOUT_TYPES = {0: "normal", 1: "carrera", 2: "fondo", 3: "entrenamiento"}

# Nombres de best_efforts de Strava → claves internas (las de profile.best_times donde aplica)
EFFORT_KEYS = {
    "400m": "400m", "1/2 mile": "half_mile", "1k": "1k", "1 mile": "mile", "2 mile": "2mile",
    "5k": "5k", "10k": "10k", "15k": "15k", "10 mile": "10mile", "20k": "20k",
    "half-marathon": "half", "30k": "30k", "marathon": "marathon", "50k": "50k",
}
EFFORT_KM = {
    "400m": 0.4, "half_mile": 0.8047, "1k": 1.0, "mile": 1.6093, "2mile": 3.2187, "5k": 5.0,
    "10k": 10.0, "15k": 15.0, "10mile": 16.0934, "20k": 20.0, "half": 21.0975, "30k": 30.0,
    "marathon": 42.195, "50k": 50.0,
}


def map_activity(raw: dict) -> dict:
    sport_type = raw.get("sport_type") or raw.get("type") or "other"
    start_local = (raw.get("start_date_local") or "").rstrip("Z")
    cadence = raw.get("average_cadence")
    is_run = sport_type in RUNNING_TYPES
    workout_type = raw.get("workout_type")
    external_id = raw.get("external_id") or ""
    return {
        "activity_id": str(raw.get("id")),
        "source": "strava",
        "name": raw.get("name"),
        "date": start_local[:10],
        "start_local": start_local,
        "start_utc": raw.get("start_date"),
        "type": sport_type,
        "sport": "running" if is_run else sport_type.lower(),
        "distance_km": round((raw.get("distance") or 0) / 1000, 2),
        "duration_s": raw.get("moving_time"),
        "elapsed_s": raw.get("elapsed_time"),
        "avg_pace_s_per_km": _speed_to_pace(raw.get("average_speed")),
        "avg_hr": raw.get("average_heartrate"),
        "max_hr": raw.get("max_heartrate"),
        # Strava reporta zancadas por minuto de una pierna en carrera: ×2 para pasos por minuto.
        "avg_cadence_spm": round(cadence * 2, 1) if cadence and is_run else cadence,
        "elevation_gain_m": raw.get("total_elevation_gain"),
        "avg_power_w": raw.get("average_watts"),
        "norm_power_w": raw.get("weighted_average_watts"),
        "calories": raw.get("calories"),
        "suffer_score": raw.get("suffer_score"),
        "workout_type": WORKOUT_TYPES.get(workout_type, workout_type) if is_run else None,
        "gear_id": raw.get("gear_id"),
        "device": raw.get("device_name"),
        "external_id": external_id or None,
        "from_garmin": external_id.startswith("garmin"),
        "trainer": raw.get("trainer"),
        "manual": raw.get("manual"),
        "pr_count": raw.get("pr_count"),
    }


def effort_key(name: str | None) -> str | None:
    return EFFORT_KEYS.get((name or "").strip().lower())


def map_best_efforts(raw: dict) -> list[dict]:
    activity_id = str(raw.get("id"))
    out = []
    for e in raw.get("best_efforts") or []:
        key = effort_key(e.get("name"))
        elapsed = e.get("elapsed_time")
        if not key or not elapsed:
            continue
        out.append({
            "distance": key,
            "elapsed_s": elapsed,
            "pace_s_per_km": round(elapsed / EFFORT_KM[key], 1),
            "date": (e.get("start_date_local") or raw.get("start_date_local") or "")[:10],
            "activity_id": activity_id,
            "pr_rank": e.get("pr_rank"),
        })
    return out


def map_splits(raw: dict) -> list[dict]:
    out = []
    for s in raw.get("splits_metric") or []:
        out.append({
            "km": s.get("split"),
            "distance_km": round((s.get("distance") or 0) / 1000, 2),
            "duration_s": s.get("moving_time"),
            "pace_s_per_km": _speed_to_pace(s.get("average_speed")),
            "gap_s_per_km": _speed_to_pace(s.get("average_grade_adjusted_speed")),
            "avg_hr": s.get("average_heartrate"),
            "elevation_diff_m": s.get("elevation_difference"),
        })
    return out


def map_detail(raw: dict) -> dict:
    """Actividad completa: resumen + best_efforts + splits por km."""
    activity = map_activity(raw)
    activity["best_efforts"] = map_best_efforts(raw)
    activity["splits"] = map_splits(raw)
    activity["detailed"] = True
    return activity
