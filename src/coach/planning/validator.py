"""Valida una semana generada por el LLM contra las reglas del método. Devuelve errores legibles."""

from __future__ import annotations

import itertools
from datetime import date, timedelta

from coach.methodology import rules
from coach.planning.garmin_workout_builder import estimated_duration_s

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
HARD_ZONES = {"Z4", "Z5a", "Z5b", "Z5c"}
TSS_TOLERANCE = 0.15


def _iter_steps(steps: list[dict]):
    for s in steps:
        if s.get("kind") == "repeat":
            for _ in range(int(s.get("times", 1))):
                yield from _iter_steps(s.get("steps", []))
        else:
            yield s


def session_tss(session: dict, zones: dict | None) -> float:
    total = 0.0
    for step in _iter_steps(session["steps"]):
        secs = estimated_duration_s([step], zones)
        total += secs / 3600 * rules.ZONE_IF.get(step.get("zone") or "Z1", 0.65) ** 2 * 100
    return round(total, 1)


def session_zones(session: dict) -> set[str]:
    return {s.get("zone") for s in _iter_steps(session["steps"]) if s.get("zone")}


def is_hard(session: dict) -> bool:
    return session.get("type") in ("intervals", "tempo", "test") or bool(session_zones(session) & HARD_ZONES)


def validate_week(week: dict, *, week_start: date, phase: str, target_tss: float, sessions_max: int,
                  days_available: list[str], longest_session_min: int | None, needs_test: bool,
                  zones: dict | None) -> list[str]:
    errors: list[str] = []
    sessions = week.get("sessions", [])
    week_end = week_start + timedelta(days=6)

    if len(sessions) > sessions_max:
        errors.append(f"Hay {len(sessions)} sesiones; el máximo para este nivel es {sessions_max}.")
    if not sessions:
        errors.append("La semana no tiene sesiones.")

    dates = []
    for s in sessions:
        try:
            d = date.fromisoformat(s["date"])
        except (KeyError, ValueError):
            errors.append(f"Sesión '{s.get('title')}' con fecha inválida.")
            continue
        dates.append(d)
        if not week_start <= d <= week_end:
            errors.append(f"La sesión '{s['title']}' ({d}) está fuera de la semana {week_start}–{week_end}.")
        if days_available and WEEKDAYS[d.weekday()] not in [x.lower() for x in days_available]:
            errors.append(f"La sesión '{s['title']}' cae en {WEEKDAYS[d.weekday()]}, que no está disponible.")
        bad = session_zones(s) - rules.PHASE_ALLOWED_ZONES[phase] - ({"Z4", "Z5a"} if s.get("type") == "test" else set())
        if bad:
            errors.append(f"La sesión '{s['title']}' usa {sorted(bad)}, no permitidas en la fase {phase}.")
        minutes = estimated_duration_s(s["steps"], zones) / 60
        is_race = s.get("title", "").lower().startswith("carrera")
        if longest_session_min and not is_race and minutes > longest_session_min * 1.1:
            errors.append(f"La sesión '{s['title']}' dura ~{minutes:.0f} min; el máximo es {longest_session_min}.")
    if len(dates) != len(set(dates)):
        errors.append("Hay dos sesiones el mismo día.")

    ordered = sorted((s for s in sessions if "date" in s), key=lambda s: s["date"])
    for a, b in itertools.pairwise(ordered):
        if is_hard(a) and is_hard(b) and (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days == 1:
            errors.append(f"Dos sesiones intensas seguidas: '{a['title']}' y '{b['title']}'.")

    total = sum(session_tss(s, zones) for s in sessions)
    if target_tss and abs(total - target_tss) / target_tss > TSS_TOLERANCE:
        errors.append(f"TSS semanal estimado {total:.0f} vs objetivo {target_tss:.0f} (tolerancia ±15%).")

    if needs_test and not any(s.get("type") == "test" for s in sessions):
        errors.append("Falta el test de 20 minutos para calcular umbrales y zonas.")
    return errors
