"""Reglas deterministas de alertas. El LLM solo redacta el mensaje a partir de estas señales."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from coach.analytics import load, wellness


@dataclass
class Alert:
    kind: str
    severity: str  # info | warning | critical
    title: str
    data: dict = field(default_factory=dict)
    cooldown_days: int = 3


ACWR_HIGH = 1.5
ACWR_LOW = 0.8
RHR_DELTA_BPM = 5
SLEEP_SCORE_LOW = 60
PAIN_THRESHOLD = 4  # escala 0-10


def evaluate(activities: list[dict], dailies: list[dict], notes: list[dict], week_compliance: list[dict],
             today: date) -> list[Alert]:
    alerts: list[Alert] = []

    ratio = load.acwr(activities, today)
    if ratio is not None and ratio > ACWR_HIGH:
        alerts.append(Alert("acwr_high", "warning", "Carga aguda muy por encima de la crónica",
                            {"acwr": ratio, "limit": ACWR_HIGH}))
    elif ratio is not None and ratio < ACWR_LOW and _had_plan(week_compliance):
        alerts.append(Alert("acwr_low", "info", "Carga reciente baja frente a tu base", {"acwr": ratio},
                            cooldown_days=7))

    # HRV: dos noches seguidas por debajo de media - 1 SD, o estado de Garmin LOW/UNBALANCED.
    hrv_base = wellness.baseline(dailies, "hrv_last_night", today)
    last_hrv = wellness.latest(dailies, "hrv_last_night", 2, today)
    if hrv_base and len(last_hrv) == 2 and all(v < hrv_base["mean"] - hrv_base["sd"] for v in last_hrv):
        alerts.append(Alert("hrv_low", "warning", "HRV por debajo de tu línea base dos noches seguidas",
                            {"last": last_hrv, "baseline": hrv_base}))
    else:
        status = next((d.get("hrv_status") for d in dailies if d["date"] == today.isoformat()), None)
        if status and str(status).upper() in ("LOW", "POOR"):
            alerts.append(Alert("hrv_low", "warning", f"Garmin reporta HRV {status}", {"status": status}))

    rhr_base = wellness.baseline(dailies, "resting_hr", today)
    last_rhr = wellness.latest(dailies, "resting_hr", 2, today)
    if rhr_base and len(last_rhr) == 2 and all(v >= rhr_base["mean"] + RHR_DELTA_BPM for v in last_rhr):
        alerts.append(Alert("rhr_high", "warning", "FC en reposo elevada dos días seguidos",
                            {"last": last_rhr, "baseline": rhr_base}))

    last_sleep = wellness.latest(dailies, "sleep_score", 3, today)
    if len(last_sleep) == 3 and all(v < SLEEP_SCORE_LOW for v in last_sleep):
        alerts.append(Alert("sleep_poor", "warning", "Tres noches seguidas de sueño deficiente",
                            {"scores": last_sleep}))

    reds = [r for r in week_compliance if r.get("color") == "rojo"]
    if len(reds) >= 2:
        alerts.append(Alert("missed_sessions", "info", f"{len(reds)} sesiones no realizadas esta semana",
                            {"dates": [r["session"]["date"] for r in reds]}, cooldown_days=5))

    recent_pain = [n for n in notes if (n.get("pain") or 0) >= PAIN_THRESHOLD
                   and date.fromisoformat(n["date"]) >= today - timedelta(days=3)]
    if recent_pain:
        alerts.append(Alert("pain", "critical", "Reportaste dolor: hay que ajustar la carga",
                            {"notes": recent_pain}, cooldown_days=2))
    return alerts


def _had_plan(week_compliance: list[dict]) -> bool:
    return any(r.get("session") for r in week_compliance)
