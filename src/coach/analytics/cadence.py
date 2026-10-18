"""Cadencia: rpm/ppm y objetivo de metrónomo (+2..3 rpm sobre la sesión equivalente anterior)."""

from __future__ import annotations

from datetime import date, timedelta

from coach.methodology import rules


def spm_to_rpm(spm: float) -> float:
    return spm / 2


def rpm_to_bpm(rpm: float) -> int:
    return round(rpm * 2)


def metronome_target(previous_rpm: float, step: int | None = None) -> dict:
    step = step or rules.CADENCE_STEP_RPM[0]
    target_rpm = round(previous_rpm) + step
    return {
        "previous_rpm": round(previous_rpm, 1),
        "target_rpm": target_rpm,
        "metronome_bpm": rpm_to_bpm(target_rpm),
        "garmin_alert": "Every Other Beat (cada 2 ciclos), solo vibración",
    }


def previous_equivalent_cadence(activities: list[dict], zone: str, before: date) -> float | None:
    """Cadencia (rpm) de la última sesión con zona predominante `zone` en los 14 días previos."""
    window_start = before - timedelta(days=14)
    candidates = [
        a for a in activities
        if a.get("main_zone") == zone and a.get("avg_cadence_spm")
        and window_start <= date.fromisoformat(a["date"]) < before
    ]
    if not candidates:
        return None
    last = max(candidates, key=lambda a: a["date"])
    return spm_to_rpm(float(last["avg_cadence_spm"]))


def cadence_trend(activities: list[dict]) -> list[dict]:
    return [
        {"date": a["date"], "rpm": round(spm_to_rpm(float(a["avg_cadence_spm"])), 1),
         "stride_m": a.get("avg_stride_m")}
        for a in sorted(activities, key=lambda a: a["date"]) if a.get("avg_cadence_spm")
    ]
