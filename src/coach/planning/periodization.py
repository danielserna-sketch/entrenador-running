"""Macrociclo determinista: fases Base → Construcción → Específico → Afinamiento con TSS semanal.

Las proporciones, la progresión y las semanas de descarga son SUPUESTOS (ver methodology/rules.py).
"""

from __future__ import annotations

from datetime import date, timedelta

from coach.methodology import rules

TSS_PER_HOUR_EASY = 0.75**2 * 100  # ~56 TSS por hora a IF 0.75


def plan_start(today: date) -> date:
    """Los planes empiezan un lunes: hoy si es lunes, si no el próximo lunes."""
    return today if today.weekday() == 0 else today + timedelta(days=7 - today.weekday())


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_macrocycle(race_date: date, start: date, distance: str, start_weekly_tss: float,
                     weekly_hours_target: float | None = None) -> dict:
    start = monday_of(start)
    race_monday = monday_of(race_date)
    total_weeks = (race_monday - start).days // 7 + 1
    warnings: list[str] = []
    if total_weeks < 6:
        raise ValueError(f"Solo hay {total_weeks} semanas hasta la carrera: muy poco para un plan completo.")

    taper = min(rules.TAPER_WEEKS.get(distance, 1), total_weeks - 4)
    remaining = total_weeks - taper
    base = max(round(remaining * rules.PHASE_SPLIT["base"]), min(rules.MIN_BASE_WEEKS, remaining // 3))
    build = round(remaining * rules.PHASE_SPLIT["build"])
    specialty = remaining - base - build
    if specialty < 2:
        build -= 2 - specialty
        specialty = 2
    if distance == "half" and total_weeks < rules.HALF_PLAN_WEEKS:
        warnings.append(f"El método usa {rules.HALF_PLAN_WEEKS} semanas para media maratón; hay {total_weeks}. "
                        "Se acortó la base: más riesgo si vienes con poco volumen.")
    if base < rules.MIN_BASE_WEEKS:
        warnings.append("Base muy corta: prioriza no lesionarte antes que la marca.")

    phases = ["base"] * base + ["build"] * build + ["specialty"] * specialty + ["taper"] * taper
    peak_tss = (weekly_hours_target * TSS_PER_HOUR_EASY * 1.1) if weekly_hours_target else start_weekly_tss * 1.6
    peak_tss = max(peak_tss, start_weekly_tss)

    # Repartir la subida para llegar al pico al final del ciclo específico (no antes).
    pre_taper = total_weeks - taper
    increases = max(pre_taper - pre_taper // (rules.LOAD_WEEKS_PER_BLOCK + 1) - 1, 1)
    growth = (peak_tss / start_weekly_tss) ** (1 / increases) - 1
    growth = min(max(growth, 0.0), rules.MAX_WEEKLY_TSS_INCREASE * 0.8)

    weeks = []
    tss = start_weekly_tss
    last_load_tss = start_weekly_tss
    load_streak = 0
    for i, phase in enumerate(phases):
        monday = start + timedelta(weeks=i)
        if phase == "taper":
            taper_index = i - (total_weeks - taper)
            factor = 0.45 if i == total_weeks - 1 else (0.65 if taper_index == 0 else 0.55)
            target, kind = last_load_tss * factor, ("carrera" if i == total_weeks - 1 else "afinamiento")
        elif load_streak == rules.LOAD_WEEKS_PER_BLOCK:
            target, kind = last_load_tss * rules.RECOVERY_WEEK_FACTOR, "descarga"
            load_streak = 0
        else:
            if i > 0:
                tss = min(last_load_tss * (1 + growth), peak_tss)
            target, kind = tss, "carga"
            last_load_tss = tss
            load_streak += 1
        weeks.append({
            "index": i + 1,
            "week_start": monday.isoformat(),
            "phase": phase,
            "phase_label": rules.PHASE_LABELS[phase],
            "kind": kind,
            "target_tss": round(target),
            "target_hours": round(target / TSS_PER_HOUR_EASY, 1),
        })

    return {
        "start": start.isoformat(),
        "race_date": race_date.isoformat(),
        "distance": distance,
        "total_weeks": total_weeks,
        "phase_weeks": {"base": base, "build": build, "specialty": specialty, "taper": taper},
        "weeks": weeks,
        "warnings": warnings,
    }


def week_info(macro: dict, monday: date) -> dict | None:
    return next((w for w in macro["weeks"] if w["week_start"] == monday.isoformat()), None)
