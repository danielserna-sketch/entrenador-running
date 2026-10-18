"""Normalización de las respuestas JSON de Garmin Connect a dicts simples y estables."""

from __future__ import annotations

from datetime import date

RUNNING_TYPES = {"running", "treadmill_running", "trail_running", "track_running", "indoor_running",
                 "street_running", "virtual_run", "ultra_run"}


def _speed_to_pace(speed_mps: float | None) -> float | None:
    if not speed_mps or speed_mps <= 0:
        return None
    return round(1000 / speed_mps, 1)


def map_activity(raw: dict) -> dict:
    type_key = (raw.get("activityType") or {}).get("typeKey", "other")
    start_local = raw.get("startTimeLocal") or ""
    stride_cm = raw.get("avgStrideLength")
    return {
        "activity_id": str(raw.get("activityId")),
        "name": raw.get("activityName"),
        "date": start_local[:10],
        "start_local": start_local,
        "type": type_key,
        "sport": "running" if type_key in RUNNING_TYPES else type_key,
        "distance_km": round((raw.get("distance") or 0) / 1000, 2),
        "duration_s": raw.get("movingDuration") or raw.get("duration"),
        "elapsed_s": raw.get("elapsedDuration") or raw.get("duration"),
        "avg_pace_s_per_km": _speed_to_pace(raw.get("averageSpeed")),
        # Pace ajustado por pendiente de Garmin: aproximación al NGP de TrainingPeaks.
        "ngp_s_per_km": _speed_to_pace(raw.get("avgGradeAdjustedSpeed")),
        "avg_hr": raw.get("averageHR"),
        "max_hr": raw.get("maxHR"),
        "avg_cadence_spm": raw.get("averageRunningCadenceInStepsPerMinute"),
        "avg_stride_m": round(stride_cm / 100, 2) if stride_cm else None,
        "elevation_gain_m": raw.get("elevationGain"),
        "avg_power_w": raw.get("avgPower"),
        "norm_power_w": raw.get("normPower"),
        "aerobic_te": raw.get("aerobicTrainingEffect"),
        "anaerobic_te": raw.get("anaerobicTrainingEffect"),
        "garmin_training_load": raw.get("activityTrainingLoad"),
        "vo2max": raw.get("vO2MaxValue"),
        "calories": raw.get("calories"),
    }


def map_scheduled(raw: dict) -> dict:
    """Entrenamiento agendado (workoutScheduleByPeriod). Forma confirmada con una cuenta real."""
    workout = raw.get("workout") or {}
    return {
        "schedule_id": str(raw.get("workoutScheduleId") or ""),
        "workout_id": str(workout.get("workoutId") or ""),
        "date": (raw.get("calendarDate") or "")[:10],
        "title": workout.get("workoutName"),
    }


def _first(value: list | dict | None) -> dict | None:
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else None
    return value if isinstance(value, dict) else None


def map_performance(max_metrics: list | dict | None, race: dict | None, status: dict | None,
                    lactate: dict | None) -> dict:
    """Métricas de rendimiento de Garmin ya resumidas. Cada sección es None si no hay dato."""
    generic = (_first(max_metrics) or {}).get("generic") or {}
    out = {}
    if generic.get("vo2MaxValue"):
        out["vo2max"] = {"fecha": generic.get("calendarDate"), "vo2max": generic["vo2MaxValue"]}
    if race:
        out["prediccion_carrera_s"] = {"5k": race.get("time5K"), "10k": race.get("time10K"),
                                       "media": race.get("timeHalfMarathon"), "maraton": race.get("timeMarathon")}
    device = next(iter((status or {}).get("mostRecentTrainingStatus", {})
                       .get("latestTrainingStatusData", {}).values()), None)
    if device:
        out["estado_entrenamiento"] = {k: v for k, v in device.items()
                                       if isinstance(v, (int, float, str)) and v is not None}
    if lactate:
        speed = (lactate.get("speed_and_heart_rate") or {}).get("speed")
        power = lactate.get("power") or {}
        out["umbral_lactato"] = {
            "fc": (lactate.get("speed_and_heart_rate") or {}).get("heartRate"),
            "ritmo_s_per_km": round(1000 / speed, 1) if speed else None,
            "ftp_w": power.get("functionalThresholdPower"),
        }
    return out


def map_splits(raw: dict | list | None) -> list[dict]:
    laps = (raw or {}).get("lapDTOs", []) if isinstance(raw, dict) else (raw or [])
    out = []
    for i, lap in enumerate(laps, start=1):
        out.append({
            "lap": i,
            "distance_km": round((lap.get("distance") or 0) / 1000, 2),
            "duration_s": lap.get("duration"),
            "pace_s_per_km": _speed_to_pace(lap.get("averageSpeed")),
            "avg_hr": lap.get("averageHR"),
            "cadence_spm": lap.get("averageRunCadence"),
            "elevation_gain_m": lap.get("elevationGain"),
        })
    return out


def map_hr_zones(raw: list | None) -> list[dict]:
    return [
        {"zone": z.get("zoneNumber"), "seconds": z.get("secsInZone"), "low_bpm": z.get("zoneLowBoundary")}
        for z in (raw or [])
    ]


def map_daily(day: date, sleep: dict | None, hrv: dict | None, stats: dict | None,
              readiness: list | dict | None) -> dict:
    sleep_dto = (sleep or {}).get("dailySleepDTO") or {}
    scores = sleep_dto.get("sleepScores") or {}
    hrv_summary = (hrv or {}).get("hrvSummary") or {}
    stats = stats or {}
    if isinstance(readiness, list):
        readiness = readiness[0] if readiness else {}
    sleep_s = sleep_dto.get("sleepTimeSeconds")
    return {
        "date": day.isoformat(),
        "sleep_hours": round(sleep_s / 3600, 2) if sleep_s else None,
        "sleep_score": (scores.get("overall") or {}).get("value"),
        "hrv_last_night": hrv_summary.get("lastNightAvg"),
        "hrv_weekly_avg": hrv_summary.get("weeklyAvg"),
        "hrv_status": hrv_summary.get("status"),
        "resting_hr": stats.get("restingHeartRate"),
        "body_battery_max": stats.get("bodyBatteryHighestValue"),
        "body_battery_min": stats.get("bodyBatteryLowestValue"),
        "stress_avg": stats.get("averageStressLevel"),
        "steps": stats.get("totalSteps"),
        "training_readiness": (readiness or {}).get("score"),
    }
