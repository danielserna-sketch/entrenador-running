"""Sincroniza Garmin → DynamoDB y calcula carga (TSS/IF) por actividad."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from coach.analytics import load
from coach.analytics import zones as zones_mod
from coach.garmin.provider import GarminProvider
from coach.storage.repo import Repo

log = logging.getLogger(__name__)


def enrich_activity(activity: dict, zones: dict | None) -> dict:
    activity.update(load.activity_load(activity, zones))
    if zones and activity.get("avg_hr"):
        activity["main_zone"] = zones_mod.zone_for_hr(activity["avg_hr"], zones)
    elif zones and activity.get("avg_pace_s_per_km"):
        activity["main_zone"] = zones_mod.zone_for_pace(activity["avg_pace_s_per_km"], zones)
    if activity.get("duration_s"):
        activity["duration_min"] = round(activity["duration_s"] / 60, 1)
    return activity


def sync(garmin: GarminProvider, repo: Repo, today: date, days_back: int = 3) -> dict:
    """Trae actividades y bienestar de los últimos días. Devuelve las actividades nuevas."""
    from coach.planning.assessment import ensure_zones

    zones = ensure_zones(repo, garmin, today)
    start = today - timedelta(days=days_back)
    new_activities = []
    for activity in garmin.activities(start, today):
        if activity["sport"] == "running":
            try:
                activity["detail"] = garmin.activity_detail(activity["activity_id"])
            except Exception as e:  # noqa: BLE001
                log.info("Sin detalle para %s: %s", activity["activity_id"], e)
        enrich_activity(activity, zones)
        if repo.save_activity(activity):
            new_activities.append(activity)
            repo.put_object(f"raw/activities/{activity['date']}/{activity['activity_id']}.json",
                            json.dumps(activity, default=str))

    for i in range(days_back + 1):
        day = today - timedelta(days=i)
        try:
            repo.save_daily(garmin.daily_wellness(day))
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo leer bienestar de %s: %s", day, e)
    return {"new_activities": new_activities}
