"""Ajuste determinista de la semana según el bienestar: si el cuerpo no está recuperado, menos carga y
menos calidad. Las reglas son SUPUESTO (ver rules.py) y no dependen del LLM."""

from __future__ import annotations

from datetime import date, timedelta

from coach.analytics import wellness
from coach.analytics.alerts import PAIN_THRESHOLD
from coach.methodology import rules

NO_ADJUSTMENT = {"tss_factor": 1.0, "quality_delta": 0, "reason": None}


def assess(dailies: list[dict], notes: list[dict], today: date) -> dict:
    reasons = []
    base = wellness.baseline(dailies, "hrv_last_night", today)
    if base and base["sd"]:
        recent = wellness.latest(dailies, "hrv_last_night", rules.HRV_LOW_DAYS, today)
        limit = base["mean"] - rules.HRV_LOW_SD * base["sd"]
        if len(recent) >= rules.HRV_LOW_DAYS and all(v < limit for v in recent):
            reasons.append(f"HRV baja {rules.HRV_LOW_DAYS} días seguidos ({recent[-1]:.0f} vs base "
                           f"{base['mean']:.0f})")
    sleep = wellness.latest(dailies, "sleep_hours", rules.SLEEP_LOW_DAYS, today)
    if len(sleep) >= rules.SLEEP_LOW_DAYS and all(v < rules.SLEEP_LOW_HOURS for v in sleep):
        reasons.append(f"menos de {rules.SLEEP_LOW_HOURS:.0f} h de sueño {rules.SLEEP_LOW_DAYS} noches seguidas")
    week_ago = (today - timedelta(days=7)).isoformat()
    pain = [n for n in notes if (n.get("pain") or 0) >= PAIN_THRESHOLD and (n.get("date") or "") >= week_ago]
    if pain:
        places = sorted({n.get("pain_location") for n in pain if n.get("pain_location")})
        reasons.append("dolor reportado esta semana" + (f" ({', '.join(places)})" if places else ""))
    if not reasons:
        return dict(NO_ADJUSTMENT)
    return {"tss_factor": rules.READINESS_TSS_FACTOR, "quality_delta": -1, "reason": "; ".join(reasons)}
