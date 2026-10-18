"""Historial de carrera (Strava): récords por distancia, evolución de ritmos y resumen de volumen."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise

from coach.strava.mappers import EFFORT_KM

EASY_ZONES = {"Z1", "Z2"}
GAP_DAYS = 21  # pausas más largas que esto se reportan (lesión, viaje, descanso)


def _runs(activities: list[dict]) -> list[dict]:
    return [a for a in activities if a.get("sport") == "running" and (a.get("distance_km") or 0) > 0]


def _effort(e: dict) -> dict:
    return {"time_s": e["elapsed_s"], "pace_s_per_km": e["pace_s_per_km"], "date": e["date"],
            "activity_id": e["activity_id"]}


def personal_records(activities: list[dict], today: date) -> dict:
    """Mejor tiempo por distancia: de siempre, últimos 12 meses y año actual, top 3 y mejor por año."""
    efforts: dict[str, list[dict]] = defaultdict(list)
    for a in _runs(activities):
        for e in a.get("best_efforts") or []:
            efforts[e["distance"]].append(e)

    year_ago = (today - timedelta(days=365)).isoformat()
    this_year = str(today.year)
    out: dict[str, dict] = {}
    for key in sorted(efforts, key=lambda k: EFFORT_KM.get(k, 0)):
        items = sorted(efforts[key], key=lambda e: e["elapsed_s"])
        recent = [e for e in items if e["date"] >= year_ago]
        current = [e for e in items if e["date"].startswith(this_year)]
        by_year: dict[str, dict] = {}
        for e in items:
            by_year.setdefault(e["date"][:4], _effort(e))
        out[key] = {
            "all_time": _effort(items[0]),
            "last_12_months": _effort(recent[0]) if recent else None,
            "this_year": _effort(current[0]) if current else None,
            "top3": [_effort(e) for e in items[:3]],
            "best_by_year": dict(sorted(by_year.items())),
        }
    return {"updated": today.isoformat(), "distances": out}


def best_times_from_records(records: dict | None) -> dict[str, dict]:
    """Marcas de referencia para el plan: la de los últimos 12 meses si existe, si no la de siempre."""
    out = {}
    for key in ("5k", "10k", "half", "marathon"):
        r = ((records or {}).get("distances") or {}).get(key)
        if not r:
            continue
        best = r.get("last_12_months") or r["all_time"]
        out[key] = {"time_s": best["time_s"], "date": best["date"],
                    "recent": bool(r.get("last_12_months"))}
    return out


def _period(day: str, group: str) -> str:
    return day[:4] if group == "year" else day[:7]


def pace_trends(activities: list[dict], group: str = "month") -> list[dict]:
    """Por mes o año: volumen, ritmo medio, ritmo de rodaje suave y eficiencia aeróbica."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for a in _runs(activities):
        buckets[_period(a["date"], group)].append(a)

    out = []
    for period in sorted(buckets):
        runs = buckets[period]
        km = sum(a["distance_km"] for a in runs)
        secs = sum(a.get("duration_s") or 0 for a in runs)
        easy = [a for a in runs if _is_easy(a)]
        easy_km = sum(a["distance_km"] for a in easy)
        easy_secs = sum(a.get("duration_s") or 0 for a in easy)
        with_hr = [a for a in easy if a.get("avg_hr") and a.get("duration_s")]
        efficiency = None
        if with_hr:
            # metros por latido en rodajes suaves: si sube, corres más rápido con el mismo esfuerzo
            efficiency = round(sum(a["distance_km"] * 1000 / (a["avg_hr"] * a["duration_s"] / 60)
                                   for a in with_hr) / len(with_hr), 3)
        hrs = [a["avg_hr"] for a in runs if a.get("avg_hr")]
        out.append({
            "period": period,
            "runs": len(runs),
            "km": round(km, 1),
            "hours": round(secs / 3600, 1),
            "avg_pace_s_per_km": round(secs / km, 1) if km and secs else None,
            "easy_pace_s_per_km": round(easy_secs / easy_km, 1) if easy_km and easy_secs else None,
            "longest_run_km": max(a["distance_km"] for a in runs),
            "avg_hr": round(sum(hrs) / len(hrs)) if hrs else None,
            "meters_per_beat_easy": efficiency,
            "races": [{"date": a["date"], "name": a.get("name"), "km": a["distance_km"],
                       "pace_s_per_km": a.get("avg_pace_s_per_km")}
                      for a in runs if a.get("workout_type") == "carrera"],
        })
    return out


def _is_easy(a: dict) -> bool:
    if a.get("workout_type") in ("carrera", "entrenamiento"):
        return False
    if a.get("main_zone"):
        return a["main_zone"] in EASY_ZONES
    return a.get("workout_type") in ("normal", None)


def history_summary(activities: list[dict]) -> dict:
    """Totales del historial, km por año, semana con más km, fondo más largo y pausas largas."""
    runs = sorted(_runs(activities), key=lambda a: a["date"])
    if not runs:
        return {"runs": 0}
    weeks: dict[date, float] = defaultdict(float)
    by_year: dict[str, float] = defaultdict(float)
    for a in runs:
        d = date.fromisoformat(a["date"])
        weeks[d - timedelta(days=d.weekday())] += a["distance_km"]
        by_year[a["date"][:4]] += a["distance_km"]
    top_week = max(weeks.items(), key=lambda kv: kv[1])
    longest = max(runs, key=lambda a: a["distance_km"])
    gaps = []
    for prev, nxt in pairwise(runs):
        days = (date.fromisoformat(nxt["date"]) - date.fromisoformat(prev["date"])).days
        if days > GAP_DAYS:
            gaps.append({"from": prev["date"], "to": nxt["date"], "days": days})
    return {
        "runs": len(runs),
        "first_run": runs[0]["date"],
        "last_run": runs[-1]["date"],
        "total_km": round(sum(a["distance_km"] for a in runs), 1),
        "km_by_year": {y: round(k, 1) for y, k in sorted(by_year.items())},
        "biggest_week": {"week_start": top_week[0].isoformat(), "km": round(top_week[1], 1)},
        "longest_run": {"date": longest["date"], "km": longest["distance_km"], "name": longest.get("name")},
        "breaks_over_3_weeks": gaps[-10:],
    }


def search(activities: list[dict], *, min_km: float | None = None, max_km: float | None = None,
           workout_type: str | None = None, sort: str = "date", limit: int = 15) -> list[dict]:
    runs = _runs(activities)
    if min_km is not None:
        runs = [a for a in runs if a["distance_km"] >= min_km]
    if max_km is not None:
        runs = [a for a in runs if a["distance_km"] <= max_km]
    if workout_type:
        runs = [a for a in runs if a.get("workout_type") == workout_type]
    keys = {"date": lambda a: a["date"], "pace": lambda a: a.get("avg_pace_s_per_km") or 9999,
            "distance": lambda a: a["distance_km"]}
    runs.sort(key=keys.get(sort, keys["date"]), reverse=sort in ("date", "distance"))
    return runs[:limit]
