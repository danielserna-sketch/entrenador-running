"""Lambda programada (EventBridge Scheduler, cada hora): avanza el backfill del historial de Strava
y, cuando termina, trae las actividades nuevas. Recalcula los récords en cada corrida con cambios."""

from __future__ import annotations

import logging

from coach.handlers import proactive
from coach.handlers.common import make_coach
from coach.strava.client import StravaAuthError, StravaClient, StravaNotConfigured
from coach.strava.sync import sync

log = logging.getLogger()

AUTH_ALERT = ("⚠️ Strava: perdí el acceso a tu cuenta. Corre `uv run python scripts/strava_login.py` "
              "en tu computador para volver a conectarla. Mientras tanto no actualizo tu historial ni tus récords.")


def handler(event, context):
    coach = make_coach("proactive")
    try:
        client = StravaClient()
    except StravaNotConfigured:
        log.info("Strava aún no está conectado; nada que hacer.")
        return {"ok": False, "reason": "strava_not_configured"}

    try:
        result = sync(client, coach.repo, coach.today, max_seconds=float(event.get("max_seconds", 480)))
    except StravaAuthError:
        log.error("STRAVA_AUTH_FAILED")
        if not coach.repo.alert_sent_recently("strava_auth", coach.today):
            coach.repo.mark_alert("strava_auth", coach.today, {})
            proactive.deliver(coach, AUTH_ALERT)
        return {"ok": False, "reason": "strava_auth"}
    finally:
        client.close()

    log.info("Strava sync: %s", result)
    return {"ok": True, **result}
