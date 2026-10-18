"""Líneas base y tendencias de bienestar: HRV, FC en reposo, sueño, Body Battery, readiness."""

from __future__ import annotations

import statistics
from datetime import date, timedelta


def _values(dailies: list[dict], key: str, start: date, end: date) -> list[float]:
    return [
        float(d[key]) for d in dailies
        if d.get(key) is not None and start <= date.fromisoformat(d["date"]) <= end
    ]


def baseline(dailies: list[dict], key: str, today: date, days: int = 28) -> dict | None:
    """Media y desviación de los `days` días anteriores a hoy (sin incluir hoy)."""
    vals = _values(dailies, key, today - timedelta(days=days), today - timedelta(days=1))
    if len(vals) < 5:
        return None
    return {"mean": round(statistics.mean(vals), 1), "sd": round(statistics.pstdev(vals), 1), "n": len(vals)}


def latest(dailies: list[dict], key: str, days: int = 1, today: date | None = None) -> list[float]:
    today = today or date.today()
    return _values(dailies, key, today - timedelta(days=days - 1), today)


def wellness_snapshot(dailies: list[dict], today: date) -> dict:
    keys = ["hrv_last_night", "resting_hr", "sleep_score", "sleep_hours", "body_battery_max", "stress_avg"]
    # Training readiness solo si el reloj lo envía alguna vez (Garmin puede no darlo).
    if any(d.get("training_readiness") is not None for d in dailies):
        keys.append("training_readiness")
    snap: dict = {"date": today.isoformat()}
    for key in keys:
        vals = latest(dailies, key, 1, today)
        snap[key] = vals[-1] if vals else None
        snap[f"{key}_baseline"] = baseline(dailies, key, today)
    today_rows = [d for d in dailies if d["date"] == today.isoformat()]
    if today_rows:
        snap["hrv_status"] = today_rows[0].get("hrv_status")
    return snap
