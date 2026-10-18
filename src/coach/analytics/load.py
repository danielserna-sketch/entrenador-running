"""Carga de entrenamiento: rTSS, hrTSS, IF, CTL/ATL/TSB, ACWR y monotonía.

Garmin no entrega TSS para carrera, así que se calcula aquí al estilo TrainingPeaks:
- rTSS = horas × IF² × 100, con IF = pace umbral / pace normalizado (NGP; si no hay NGP, pace promedio).
- hrTSS (aproximación) = horas × (FC promedio / FC umbral)² × 100.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

CTL_DAYS = 42
ATL_DAYS = 7


def intensity_factor(pace_s: float | None, threshold_pace_s: float | None) -> float | None:
    if not pace_s or not threshold_pace_s:
        return None
    return threshold_pace_s / pace_s


def rtss(duration_s: float, pace_s: float | None, threshold_pace_s: float | None) -> float | None:
    if_ = intensity_factor(pace_s, threshold_pace_s)
    if if_ is None:
        return None
    return duration_s / 3600 * if_**2 * 100


def hrtss(duration_s: float, avg_hr: float | None, threshold_hr: float | None) -> float | None:
    if not avg_hr or not threshold_hr:
        return None
    return duration_s / 3600 * (avg_hr / threshold_hr) ** 2 * 100


def activity_load(activity: dict, zones: dict | None) -> dict:
    """Calcula TSS e IF de una actividad normalizada. Prefiere rTSS sobre hrTSS."""
    zones = zones or {}
    duration = activity.get("duration_s") or 0
    pace = activity.get("ngp_s_per_km") or activity.get("avg_pace_s_per_km")
    tss = rtss(duration, pace, zones.get("threshold_pace_s"))
    method = "rTSS"
    if tss is None:
        tss = hrtss(duration, activity.get("avg_hr"), zones.get("threshold_hr"))
        method = "hrTSS"
    if tss is None:
        return {"tss": None, "if": None, "tss_method": None}
    if_ = intensity_factor(pace, zones.get("threshold_pace_s"))
    if if_ is None and activity.get("avg_hr") and zones.get("threshold_hr"):
        if_ = activity["avg_hr"] / zones["threshold_hr"]
    return {"tss": round(tss, 1), "if": round(if_, 3) if if_ else None, "tss_method": method}


def daily_tss(activities: list[dict]) -> dict[date, float]:
    totals: dict[date, float] = {}
    for a in activities:
        d = date.fromisoformat(a["date"])
        totals[d] = totals.get(d, 0.0) + float(a.get("tss") or 0)
    return totals


def fitness_series(activities: list[dict], start: date, end: date) -> list[dict]:
    """CTL (forma física), ATL (fatiga) y TSB (frescura) diarios por media exponencial."""
    per_day = daily_tss(activities)
    ctl = atl = 0.0
    out = []
    d = start
    while d <= end:
        tss = per_day.get(d, 0.0)
        ctl += (tss - ctl) / CTL_DAYS
        atl += (tss - atl) / ATL_DAYS
        out.append({"date": d.isoformat(), "tss": round(tss, 1), "ctl": round(ctl, 1),
                    "atl": round(atl, 1), "tsb": round(ctl - atl, 1)})
        d += timedelta(days=1)
    return out


def acwr(activities: list[dict], today: date) -> float | None:
    """Carga aguda (7 días) / crónica (promedio semanal de 28 días)."""
    per_day = daily_tss(activities)
    acute = sum(per_day.get(today - timedelta(days=i), 0) for i in range(7))
    chronic = sum(per_day.get(today - timedelta(days=i), 0) for i in range(28)) / 4
    if chronic <= 0:
        return None
    return round(acute / chronic, 2)


def monotony(activities: list[dict], today: date) -> float | None:
    """Monotonía de Foster: media / desviación del TSS diario de los últimos 7 días."""
    per_day = daily_tss(activities)
    loads = [per_day.get(today - timedelta(days=i), 0.0) for i in range(7)]
    sd = statistics.pstdev(loads)
    if sd == 0:
        return None
    return round(statistics.mean(loads) / sd, 2)


def weekly_summary(activities: list[dict], monday: date) -> dict:
    week = [a for a in activities if monday <= date.fromisoformat(a["date"]) < monday + timedelta(days=7)]
    return {
        "week_start": monday.isoformat(),
        "sessions": len(week),
        "tss": round(sum(float(a.get("tss") or 0) for a in week), 1),
        "duration_min": round(sum(float(a.get("duration_s") or 0) for a in week) / 60),
        "distance_km": round(sum(float(a.get("distance_km") or 0) for a in week), 1),
    }
