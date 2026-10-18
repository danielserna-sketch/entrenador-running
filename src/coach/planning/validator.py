"""Valida una semana generada por el LLM contra las reglas del método. Devuelve errores legibles.

Además de las reglas de calendario y carga, hace cumplir el principio "primero lento": proporción mínima
de tiempo en zona baja, límite de sesiones de calidad, fondos en Z1-Z2 y series bien preparadas.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

from coach.methodology import rules
from coach.planning.garmin_workout_builder import estimated_duration_s

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
HARD_ZONES = rules.HARD_ZONES
TSS_TOLERANCE = 0.15
QUALITY_TYPES = ("intervals", "tempo", "test")


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


def is_race(session: dict) -> bool:
    return session.get("title", "").lower().startswith("carrera")


def intensity_level(session: dict) -> str:
    """'easy', 'moderate' (algo de Z3) o 'hard' (series, tempo, test o Z4+)."""
    zones = session_zones(session)
    if session.get("type") in QUALITY_TYPES or zones & HARD_ZONES:
        return "hard"
    return "moderate" if zones & rules.MODERATE_ZONES else "easy"


def is_quality(session: dict) -> bool:
    """Cuenta como sesión de calidad todo lo que no es puramente Z1-Z2 (Z3 incluida)."""
    return intensity_level(session) != "easy"


def is_hard(session: dict) -> bool:
    return intensity_level(session) == "hard"


def zone_time_split(sessions: list[dict], zones: dict | None) -> dict[str, float]:
    """Segundos estimados en zona fácil (Z1-Z2), moderada (Z3) e intensa (Z4+). Sin zona cuenta como fácil."""
    split = {"easy": 0.0, "moderate": 0.0, "hard": 0.0}
    for session in sessions:
        for step in _iter_steps(session["steps"]):
            zone = step.get("zone") or "Z1"
            key = "hard" if zone in HARD_ZONES else "moderate" if zone in rules.MODERATE_ZONES else "easy"
            split[key] += estimated_duration_s([step], zones)
    return split


def easy_share(sessions: list[dict], zones: dict | None) -> float | None:
    split = zone_time_split(sessions, zones)
    total = sum(split.values())
    return split["easy"] / total if total else None


def interval_km(session: dict, zones: dict | None) -> float:
    """Kilómetros de trabajo en Z3 o más (series y tempo), estimando la distancia de los pasos por tiempo."""
    km = 0.0
    for step in _iter_steps(session["steps"]):
        if step.get("zone") not in rules.MODERATE_ZONES | HARD_ZONES or step.get("kind") in ("warmup", "cooldown"):
            continue
        if step.get("distance_km"):
            km += step["distance_km"]
            continue
        pace = next((z for z in (zones or {}).get("pace", []) if z["zone"] == step.get("zone")), None)
        if pace and step.get("duration_min"):
            km += step["duration_min"] * 60 / ((pace["fast_s"] + pace["slow_s"]) / 2)
    return round(km, 1)


def _long_run_error(session: dict, phase: str, zones: dict | None) -> str | None:
    main = [s for s in _iter_steps(session["steps"]) if s.get("kind") not in ("warmup", "cooldown")]
    fast = [s for s in main if s.get("zone") and s["zone"] not in rules.LONG_RUN_ZONES]
    if not fast:
        return None
    if phase == "specialty":
        share = estimated_duration_s(fast, zones) / max(estimated_duration_s(main, zones), 1)
        if share <= rules.LONG_RUN_FAST_SHARE_SPECIALTY:
            return None
        return (f"El fondo '{session['title']}' tiene {share:.0%} fuera de Z1-Z2; en el específico el máximo es "
                f"{rules.LONG_RUN_FAST_SHARE_SPECIALTY:.0%}.")
    return f"El fondo '{session['title']}' debe ir en Z1-Z2 (usa {sorted({s['zone'] for s in fast})})."


def _warmup_error(session: dict, zones: dict | None) -> str | None:
    if session.get("type") not in ("intervals", "tempo") or is_race(session):
        return None
    first = session["steps"][0] if session["steps"] else {}
    minutes = estimated_duration_s([first], zones) / 60 if first.get("kind") == "warmup" else 0
    if minutes < rules.QUALITY_WARMUP_MIN:
        return (f"La sesión '{session['title']}' necesita al menos {rules.QUALITY_WARMUP_MIN} min de "
                "calentamiento muy suave al inicio.")
    return None


def validate_week(week: dict, *, week_start: date, phase: str, target_tss: float, sessions_max: int,
                  days_available: list[str], longest_session_min: int | None, needs_test: bool,
                  zones: dict | None, distance: str | None = None, max_quality: int | None = None) -> list[str]:
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
        if longest_session_min and not is_race(s) and minutes > longest_session_min * 1.1:
            errors.append(f"La sesión '{s['title']}' dura ~{minutes:.0f} min; el máximo es {longest_session_min}.")
        if s.get("type") == "long" and (error := _long_run_error(s, phase, zones)):
            errors.append(error)
        if error := _warmup_error(s, zones):
            errors.append(error)
        if distance in rules.MAX_INTERVAL_KM and not is_race(s) and s.get("type") != "test":
            km = interval_km(s, zones)
            if km > rules.MAX_INTERVAL_KM[distance]:
                errors.append(f"La sesión '{s['title']}' tiene ~{km} km de series; el máximo para {distance} es "
                              f"{rules.MAX_INTERVAL_KM[distance]} km.")
    if len(dates) != len(set(dates)):
        errors.append("Hay dos sesiones el mismo día.")

    ordered = sorted((s for s in sessions if "date" in s), key=lambda s: s["date"])
    for a, b in itertools.pairwise(ordered):
        if is_quality(a) and is_quality(b) and (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days == 1:
            errors.append(f"Dos sesiones intensas seguidas: '{a['title']}' y '{b['title']}'.")

    # Primero lento: el test y la carrera son evaluaciones y no cuentan en la distribución ni en el límite.
    training = [s for s in sessions if s.get("type") != "test" and not is_race(s)]
    limit = rules.max_quality_sessions(phase, sessions_max) if max_quality is None else max_quality
    quality = [s for s in training if is_quality(s)]
    if len(quality) > limit:
        errors.append(f"Hay {len(quality)} sesiones de calidad (Z3 o más); el máximo esta semana es {limit}. "
                      "El resto deben ser rodajes en Z1-Z2.")
    share = easy_share(training, zones)
    minimum = rules.EASY_SHARE_MIN_BY_PHASE[phase]
    if share is not None and share < minimum:
        errors.append(f"Solo {share:.0%} del tiempo es en Z1-Z2; el mínimo en la fase {phase} es {minimum:.0%}. "
                      "Alarga los rodajes suaves o acorta el trabajo intenso.")

    total = sum(session_tss(s, zones) for s in sessions)
    if target_tss and abs(total - target_tss) / target_tss > TSS_TOLERANCE:
        errors.append(f"TSS semanal estimado {total:.0f} vs objetivo {target_tss:.0f} (tolerancia ±15%).")

    if needs_test and not any(s.get("type") == "test" for s in sessions):
        errors.append("Falta el test de 20 minutos para calcular umbrales y zonas.")
    return errors
