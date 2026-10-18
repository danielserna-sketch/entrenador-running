"""Lambda programada (EventBridge Scheduler, cada 30 min): sincroniza Garmin y dispara el análisis
de cada actividad de carrera nueva."""

from __future__ import annotations

import logging

from coach.garmin.provider import GarminAuthError
from coach.garmin.sync import sync
from coach.handlers import proactive
from coach.handlers.common import garmin_factory, make_coach

log = logging.getLogger()

AUTH_ALERT = ("⚠️ Garmin: hay que renovar la sesión. Corre `uv run python scripts/garmin_login.py` "
              "en tu computador. Hasta entonces no puedo leer tus entrenamientos nuevos.")


def handler(event, context):
    coach = make_coach("proactive")
    try:
        garmin = garmin_factory()
    except GarminAuthError:
        log.error("GARMIN_AUTH_FAILED")  # métrica/alarma en CloudWatch
        if not coach.repo.alert_sent_recently("garmin_auth", coach.today):
            coach.repo.mark_alert("garmin_auth", coach.today, {})
            proactive.deliver(coach, AUTH_ALERT)
        return {"ok": False, "reason": "garmin_auth"}

    try:
        result = sync(garmin, coach.repo, coach.today, days_back=int(event.get("days_back", 2)))
    finally:
        garmin.close()

    analyzed = []
    for activity in result["new_activities"]:
        if activity.get("sport") != "running" or event.get("skip_analysis"):
            continue
        text = proactive.post_activity(coach, activity)
        proactive.deliver(coach, text)
        analyzed.append(activity["activity_id"])

    if analyzed:
        alert_text = proactive.alerts_check(coach)
        if alert_text:
            proactive.deliver(coach, alert_text)
    return {"ok": True, "new": len(result["new_activities"]), "analyzed": analyzed}
