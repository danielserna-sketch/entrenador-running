"""Umbrales y zonas a partir del test de 20 minutos (metodología Corriendo conCiencia)."""

from __future__ import annotations

from datetime import date

from coach.methodology import rules


def pace_to_str(sec_per_km: float) -> str:
    sec = round(sec_per_km)
    return f"{sec // 60}:{sec % 60:02d}"


def pace_from_str(value: str) -> float:
    parts = [int(p) for p in value.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Pace inválido: {value}")


def thresholds_from_test(avg_pace_s_per_km: float | None, avg_hr: float | None) -> dict:
    """Pace umbral = pace promedio × 1.05; FC umbral = FC promedio × 0.95."""
    out: dict = {}
    if avg_pace_s_per_km:
        out["threshold_pace_s"] = round(avg_pace_s_per_km * rules.THRESHOLD_PACE_FACTOR, 1)
    if avg_hr:
        out["threshold_hr"] = round(avg_hr * rules.THRESHOLD_HR_FACTOR, 1)
    return out


def hr_zones(threshold_hr: float) -> list[dict]:
    zones = []
    for name, lo, hi in rules.FRIEL_HR_ZONES:
        zones.append({"zone": name, "min_bpm": round(threshold_hr * lo), "max_bpm": round(threshold_hr * hi) - 1})
    zones[0]["min_bpm"] = 0
    return zones


def pace_zones(threshold_pace_s: float) -> list[dict]:
    """Rangos de pace por zona. `fast_s` es el límite rápido (menos seg/km), `slow_s` el lento."""
    return [
        {
            "zone": name,
            "fast_s": round(threshold_pace_s * fast),
            "slow_s": round(threshold_pace_s * slow),
            "fast": pace_to_str(threshold_pace_s * fast),
            "slow": pace_to_str(threshold_pace_s * slow),
        }
        for name, fast, slow in rules.FRIEL_PACE_ZONES
    ]


def build_zones(threshold_pace_s: float | None, threshold_hr: float | None, *, source: str,
                test_date: date | None = None) -> dict:
    return {
        "threshold_pace_s": threshold_pace_s,
        "threshold_pace": pace_to_str(threshold_pace_s) if threshold_pace_s else None,
        "threshold_hr": threshold_hr,
        "hr": hr_zones(threshold_hr) if threshold_hr else [],
        "pace": pace_zones(threshold_pace_s) if threshold_pace_s else [],
        "source": source,
        "updated": (test_date or date.today()).isoformat(),
    }


def zone_for_hr(hr: float, zones: dict) -> str | None:
    for z in zones.get("hr", []):
        if z["min_bpm"] <= hr <= z["max_bpm"]:
            return z["zone"]
    return "Z5c" if zones.get("hr") and hr > zones["hr"][-1]["min_bpm"] else None


def zone_for_pace(pace_s: float, zones: dict) -> str | None:
    for z in zones.get("pace", []):
        if z["fast_s"] <= pace_s <= z["slow_s"]:
            return z["zone"]
    return None


def detect_threshold_change(current: dict | None, candidate: dict, min_change: float = 0.02) -> dict | None:
    """Devuelve los cambios si el nuevo umbral difiere ≥ 2% (SUPUESTO) del vigente."""
    if not current:
        return candidate or None
    changes = {}
    for key in ("threshold_pace_s", "threshold_hr"):
        new, old = candidate.get(key), current.get(key)
        if new and (not old or abs(new - old) / old >= min_change):
            changes[key] = {"old": old, "new": new}
    return changes or None
