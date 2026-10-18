"""Diagnóstico inicial del atleta: nivel, sesiones por semana, carga actual y necesidad de test."""

from __future__ import annotations

from datetime import date, timedelta

from coach.analytics import history, load
from coach.methodology import rules

ZONES_MAX_AGE_DAYS = 56  # SUPUESTO: repetir el test de umbral cada ~8 semanas


def best_times(profile: dict, records: dict | None = None) -> tuple[dict[str, int], dict[str, str]]:
    """Marcas del perfil y, para las distancias que falten, las de Strava. Devuelve (marcas, origen)."""
    best = {k: int(v) for k, v in (profile.get("best_times") or {}).items() if v}
    source = {k: "marca real" for k in best}
    for key, r in history.best_times_from_records(records).items():
        if key not in best:
            best[key] = int(r["time_s"])
            source[key] = f"marca Strava ({r['date']})"
    return best, source


def reference_time(profile: dict, distance: str, records: dict | None = None) -> tuple[int | None, str]:
    """Mejor marca para la distancia objetivo, o su equivalente desde otra distancia."""
    best, source = best_times(profile, records)
    if best.get(distance):
        return best[distance], source[distance]
    for other, t in best.items():
        if t and other in rules.DISTANCE_KM:
            eq = rules.riegel(t, rules.DISTANCE_KM[other], rules.DISTANCE_KM[distance])
            return int(eq), f"equivalente desde {other} (Riegel, {source[other]})"
    return None, "sin marcas"


def assess(profile: dict, activities: list[dict], zones: dict | None, today: date,
           records: dict | None = None) -> dict:
    distance = profile.get("goal_distance", "half")
    ref_time, ref_source = reference_time(profile, distance, records)
    days_available = profile.get("days_available") or []

    if ref_time:
        rec, cap = rules.sessions_per_week(distance, ref_time)
    else:
        rec, cap = 3, 3  # sin marcas: empezar conservador como el método sugiere para principiantes
    if days_available:
        cap = min(cap, len(days_available))
        rec = min(rec, cap)

    level = None
    if distance == "half":
        best, _ = best_times(profile, records)
        lv = rules.half_level(best.get("10k"), best.get("half"))
        if lv:
            level = {"name": lv.name, "category": lv.category, "sessions": lv.sessions,
                     "weekly_hours": lv.weekly_hours, "longest_session_min": lv.longest_session_min}

    series = load.fitness_series(activities, today - timedelta(days=90), today)
    ctl = series[-1]["ctl"] if series else 0.0
    recent_weeks = [load.weekly_summary(activities, today - timedelta(days=today.weekday() + 7 * i))
                    for i in range(1, 5)]
    avg_weekly_tss = sum(w["tss"] for w in recent_weeks) / 4

    zones_age = None
    if zones and zones.get("updated"):
        zones_age = (today - date.fromisoformat(zones["updated"])).days
    needs_test = zones is None or zones_age is None or zones_age > ZONES_MAX_AGE_DAYS

    return {
        "distance": distance,
        "reference_time_s": ref_time,
        "reference_source": ref_source,
        "sessions_recommended": rec,
        "sessions_max": cap,
        "half_level": level,
        "ctl": ctl,
        "avg_weekly_tss_4w": round(avg_weekly_tss, 1),
        "start_weekly_tss": round(max(avg_weekly_tss, ctl * 7, 120.0), 1),
        "needs_threshold_test": needs_test,
        "zones_age_days": zones_age,
        "days_available": days_available,
    }
