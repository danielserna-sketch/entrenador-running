"""Cumplimiento por colores (nomenclatura TrainingPeaks usada por Corriendo conCiencia)."""

from __future__ import annotations

from datetime import date

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


def session_compliance(planned: dict | None, actual: dict | None, today: date | None = None) -> dict:
    """Compara una sesión planeada con la actividad realizada.

    Métrica en orden de preferencia: TSS, duración, distancia.
    """
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


def match_week(planned_sessions: list[dict], activities: list[dict], today: date | None = None) -> list[dict]:
    """Empareja sesiones planeadas con actividades de carrera del mismo día."""
    by_day: dict[str, list[dict]] = {}
    for a in activities:
        if a.get("sport", "running") == "running":
            by_day.setdefault(a["date"], []).append(a)
    results = []
    for s in planned_sessions:
        acts = by_day.get(s["date"], [])
        actual = acts.pop(0) if acts else None
        results.append({"session": s, "activity": actual, **session_compliance(s, actual, today)})
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
