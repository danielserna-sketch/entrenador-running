"""Desacople aeróbico (deriva cardiaca): cuánto empeora la relación ritmo/FC en la segunda mitad de un
rodaje suave y constante. Un valor bajo sugiere buena base aeróbica. El umbral de 5% es una convención de
entrenadores, no un valor validado (ver rules.DECOUPLING_OK)."""

from __future__ import annotations

from coach.methodology import rules


def _efficiency(laps: list[dict]) -> float:
    """Velocidad por latido, ponderada por duración: (m/s) / ppm."""
    speed = sum(lap["duration_s"] * 1000 / lap["pace_s_per_km"] for lap in laps)
    beats = sum(lap["duration_s"] * lap["avg_hr"] for lap in laps)
    return speed / beats


def aerobic_decoupling(splits: list[dict]) -> float | None:
    """Fracción en que cae la eficiencia de la primera a la segunda mitad (0.04 = 4%). None si no aplica."""
    laps = [lap for lap in splits if lap.get("duration_s") and lap.get("pace_s_per_km") and lap.get("avg_hr")]
    total = sum(lap["duration_s"] for lap in laps)
    if len(laps) < 4 or total < rules.DECOUPLING_MIN_MINUTES * 60:
        return None
    first, second, elapsed = [], [], 0.0
    for lap in laps:
        (first if elapsed + lap["duration_s"] / 2 <= total / 2 else second).append(lap)
        elapsed += lap["duration_s"]
    if not first or not second:
        return None
    ef1, ef2 = _efficiency(first), _efficiency(second)
    return round((ef1 - ef2) / ef1, 3)
