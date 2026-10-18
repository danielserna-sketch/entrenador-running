"""Lambda programada para los trabajos proactivos.

Evento: {"job": "morning_brief" | "weekly_review" | "alerts"}
- morning_brief: todos los días 6:00 (hora local).
- weekly_review: domingos 18:00 → genera y publica la próxima semana y envía el reporte.
- alerts: todos los días 20:00 (además del chequeo tras cada actividad).
"""

from __future__ import annotations

import logging

from coach.garmin.provider import GarminAuthError
from coach.handlers import proactive
from coach.handlers.common import make_coach

log = logging.getLogger()


def handler(event, context):
    job = event.get("job")
    coach = make_coach("proactive")
    if not coach.repo.get_profile() and job != "alerts":
        return {"ok": True, "skipped": "sin perfil todavía"}
    try:
        if job == "morning_brief":
            text = proactive.morning_brief(coach)
        elif job == "weekly_review":
            if not coach.repo.get_macro():
                return {"ok": True, "skipped": "sin plan todavía"}
            text = proactive.weekly_review(coach)
        elif job == "alerts":
            text = proactive.alerts_check(coach)
        else:
            raise ValueError(f"Job desconocido: {job}")
    except GarminAuthError:
        log.error("GARMIN_AUTH_FAILED")
        return {"ok": False, "reason": "garmin_auth"}
    if text:
        mode = proactive.deliver(coach, text)
        return {"ok": True, "job": job, "delivery": mode}
    return {"ok": True, "job": job, "delivery": None}
