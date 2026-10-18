"""Cumplimiento por colores (nomenclatura TrainingPeaks usada por Corriendo conCiencia)."""

from __future__ import annotations

from datetime import date

from coach.analytics.zones import zone_for_hr, zone_for_pace
from coach.methodology import rules

GREEN, YELLOW, ORANGE, RED, GRAY = "verde", "amarillo", "naranja", "rojo", "gris"
EMOJI = {GREEN: "🟢", YELLOW: "🟡", ORANGE: "🟠", RED: "🔴", GRAY: "⚪"}
HEX = {GREEN: "#2e9e4f", YELLOW: "#e8b923", ORANGE: "#e8792b", RED: "#d23c3c", GRAY: "#9aa0a6"}


def color_for_ratio(ratio: float) -> str:
    lo, hi = rules.COMPLIANCE_GREEN
    if lo <= ratio <= hi:
        return GREEN
    if rules.COMPLIANCE_YELLOW_LOW <= ratio < lo or hi < ratio <= rules.COMPLIANCE_YELLOW_HIGH:
        return YELLOW
    return ORANGE


SEVERITY = [GREEN, YELLOW, ORANGE, RED]
EASY_SESSION_TYPES = ("easy", "long", "recovery", "technique")


def intensity_color(planned: dict, actual: dict, zones: dict | None) -> str | None:
    """Primero lento: un rodaje suave hecho más rápido de lo planeado no puede salir verde.

    Compara la zona de la FC promedio (o del ritmo, si no hay FC) con la zona principal planeada.
    Una zona por encima es amarillo; dos o más, naranja. Solo aplica a sesiones suaves, porque en
    series el promedio mezcla trabajo y recuperación."""
    if not zones or planned.get("type") not in EASY_SESSION_TYPES:
        return None
    target = planned.get("main_zone") or "Z2"
    zone = None
    if actual.get("avg_hr") and zones.get("hr"):
        zone = zone_for_hr(actual["avg_hr"], zones)
    elif actual.get("avg_pace_s_per_km") and zones.get("pace"):
        zone = zone_for_pace(actual["avg_pace_s_per_km"], zones)
    if zone not in rules.ZONE_ORDER or target not in rules.ZONE_ORDER:
        return None
    diff = rules.ZONE_ORDER.index(zone) - rules.ZONE_ORDER.index(target)
    return GREEN if diff <= 0 else YELLOW if diff == 1 else ORANGE


def _worse(a: str | None, b: str | None) -> str | None:
    if a not in SEVERITY or b not in SEVERITY:
        return a or b
    return max(a, b, key=SEVERITY.index)


def session_compliance(planned: dict | None, actual: dict | None, today: date | None = None,
                       zones: dict | None = None) -> dict:
    """Compara una sesión planeada con la actividad realizada.

    Volumen: TSS, luego duración, luego distancia. Con zonas, también intensidad: el color final es
    el peor de los dos.
    """
    result = _volume_compliance(planned, actual, today)
    if planned and actual and result["color"] and (color := intensity_color(planned, actual, zones)):
        result = {**result, "volume_color": result["color"], "intensity_color": color,
                  "color": _worse(result["color"], color)}
    return result


def _volume_compliance(planned: dict | None, actual: dict | None, today: date | None = None) -> dict:
    today = today or date.today()
    if planned and not actual:
        if date.fromisoformat(planned["date"]) < today:
            return {"color": RED, "ratio": 0.0, "metric": None}
        return {"color": None, "ratio": None, "metric": None}  # pendiente
    if actual and not planned:
        return {"color": GRAY, "ratio": None, "metric": None}

    for metric, p_key, a_key in (("tss", "tss", "tss"), ("duration", "duration_min", "duration_min"),
                                 ("distance", "distance_km", "distance_km")):
        p = planned.get(p_key)
        a = actual.get(a_key)
        if a_key == "duration_min" and a is None and actual.get("duration_s"):
            a = actual["duration_s"] / 60
        if p and a is not None:
            ratio = float(a) / float(p)
            return {"color": color_for_ratio(ratio), "ratio": round(ratio, 2), "metric": metric}
    return {"color": GREEN, "ratio": None, "metric": None}


def match_week(planned_sessions: list[dict], activities: list[dict], today: date | None = None,
               zones: dict | None = None) -> list[dict]:
    """Empareja sesiones planeadas con actividades de carrera del mismo día."""
    by_day: dict[str, list[dict]] = {}
    for a in activities:
        if a.get("sport", "running") == "running":
            by_day.setdefault(a["date"], []).append(a)
    results = []
    for s in planned_sessions:
        acts = by_day.get(s["date"], [])
        actual = acts.pop(0) if acts else None
        results.append({"session": s, "activity": actual, **session_compliance(s, actual, today, zones)})
    for leftovers in by_day.values():
        for a in leftovers:
            results.append({"session": None, "activity": a, **session_compliance(None, a, today)})
    return results


def summarize(results: list[dict]) -> dict:
    counts = {c: 0 for c in (GREEN, YELLOW, ORANGE, RED, GRAY)}
    for r in results:
        if r["color"]:
            counts[r["color"]] += 1
    return counts
