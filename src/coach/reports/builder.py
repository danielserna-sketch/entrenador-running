"""Arma el reporte HTML del plan (versión nueva en cada actualización) y lo guarda en S3."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from coach.analytics import cadence as cadence_mod
from coach.analytics import compliance, load
from coach.methodology import rules
from coach.reports import charts
from coach.storage.repo import Repo

TEMPLATES = Path(__file__).parent / "templates"
PHASE_COLORS = {"base": "#93c5fd", "build": "#fbbf24", "specialty": "#f87171", "taper": "#a7f3d0"}
WEEKDAY_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DISTANCE_LABELS = {"5k": "5K", "10k": "10K", "half": "Media maratón", "marathon": "Maratón"}
TYPE_LABELS = {"easy": "Rodaje", "long": "Fondo", "intervals": "Series", "tempo": "Tempo", "test": "Test",
               "recovery": "Recuperación", "technique": "Técnica"}


def _fmt_time(seconds: int | None) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_context(repo: Repo, today: date, reason: str) -> tuple[dict, dict[str, bytes]]:
    profile = repo.get_profile()
    macro = repo.get_macro()
    if not macro:
        raise ValueError("Todavía no hay plan generado.")
    zones = repo.get_zones()
    start = date.fromisoformat(macro["start"])
    race_date = date.fromisoformat(macro["race_date"])
    history_start = start - timedelta(days=42)
    activities = repo.activities(history_start, today)
    saved_weeks = {w["week_start"]: w for w in repo.weeks()}

    timeline, total = [], macro["total_weeks"]
    for phase in rules.PHASES:
        n = macro["phase_weeks"].get(phase, 0)
        if n:
            timeline.append({"label": rules.PHASE_LABELS[phase], "weeks": n,
                             "pct": round(100 * n / total, 2), "color": PHASE_COLORS[phase]})
    current_week = next((w for w in macro["weeks"]
                         if date.fromisoformat(w["week_start"]) <= today < date.fromisoformat(w["week_start"]) + timedelta(days=7)),
                        None)
    marker_pct = round(100 * (current_week["index"] - 0.5) / total, 2) if current_week else (0 if today < start else 100)

    weeks_view, chart_weeks = [], []
    for w in macro["weeks"]:
        monday = date.fromisoformat(w["week_start"])
        actual = load.weekly_summary(activities, monday) if monday <= today else None
        detail = saved_weeks.get(w["week_start"])
        sessions_view = []
        if detail:
            week_acts = [a for a in activities if monday <= date.fromisoformat(a["date"]) < monday + timedelta(days=7)]
            for r in compliance.match_week(detail["sessions"], week_acts, today):
                s, a = r["session"], r["activity"]
                ref = s or a
                d = date.fromisoformat(ref["date"])
                sessions_view.append({
                    "day": f"{WEEKDAY_SHORT[d.weekday()]} {d.day}",
                    "title": s["title"] if s else (a.get("name") or "No planeado"),
                    "type": TYPE_LABELS.get(s["type"], s["type"]) if s else "Extra",
                    "zone": s.get("main_zone") if s else a.get("main_zone"),
                    "duration": s.get("duration_min") if s else a.get("duration_min"),
                    "tss": s.get("tss") if s else a.get("tss"),
                    "actual_tss": a.get("tss") if a else None,
                    "color": compliance.HEX.get(r["color"]) if r["color"] else None,
                    "color_label": r["color"],
                    "cadence": s.get("cadence") if s else None,
                })
        weeks_view.append({**w, "actual_tss": actual["tss"] if actual else None, "sessions": sessions_view,
                           "summary": detail.get("summary") if detail else None,
                           "is_current": current_week is not None and w["index"] == current_week["index"],
                           "color": PHASE_COLORS[w["phase"]]})
        chart_weeks.append({"label": f"S{w['index']}", "planned": w["target_tss"],
                            "actual": actual["tss"] if actual else None})

    images: dict[str, bytes] = {"tss": charts.tss_planned_vs_actual(chart_weeks)}
    series = load.fitness_series(activities, history_start, min(today, race_date))
    if len(series) > 7:
        images["fitness"] = charts.fitness(series[-90:])
    trend = cadence_mod.cadence_trend([a for a in activities if a.get("sport") == "running"])
    if len(trend) >= 3:
        images["cadence"] = charts.cadence(trend[-30:])

    milestones = []
    for w in weeks_view:
        for s in w["sessions"]:
            if s["type"] == "Test" and not s["actual_tss"]:
                milestones.append({"date": s["day"], "label": "Test de 20 minutos"})
    distance_label = DISTANCE_LABELS.get(macro["distance"], macro["distance"])
    milestones.append({"date": race_date.isoformat(), "label": f"Carrera: {distance_label}"})

    last = repo.last_report_meta()
    context = {
        "athlete": profile.get("name") or "Atleta",
        "goal": {
            "distance": distance_label,
            "race_name": profile.get("race_name"),
            "race_date": race_date.isoformat(),
            "goal_time": _fmt_time(profile.get("goal_time_s")),
            "weeks_left": max(0, (race_date - today).days // 7),
        },
        "today": today.isoformat(),
        "current_week": current_week,
        "timeline": timeline,
        "marker_pct": marker_pct,
        "weeks": weeks_view,
        "zones": zones,
        "milestones": milestones,
        "reason": reason,
        "changes": macro.get("changelog", [])[-5:],
        "warnings": macro.get("warnings", []),
        "previous_version": last["number"] if last else None,
        "has_fitness": "fitness" in images,
        "has_cadence": "cadence" in images,
        "compliance_legend": [(compliance.HEX[c], c, d) for c, d in (
            (compliance.GREEN, "80-120%"), (compliance.YELLOW, "50-79% / 121-150%"),
            (compliance.ORANGE, "<50% / >150%"), (compliance.RED, "No realizado"),
            (compliance.GRAY, "No planeado"))],
    }
    return context, images


def render(context: dict, image_src: dict[str, str]) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("plan.html.j2").render(**context, img=image_src)


def build_report(repo: Repo, today: date, reason: str) -> dict:
    """Devuelve {"number", "html_email" (con cid:), "html_file" (imágenes base64), "images"}."""
    import base64

    context, images = build_context(repo, today, reason)
    number = repo.next_report_number()
    context["version"] = number
    html_email = render(context, {k: f"cid:{k}" for k in images})
    html_file = render(context, {k: "data:image/png;base64," + base64.b64encode(v).decode() for k, v in images.items()})
    key = f"reports/plan_v{number:03d}.html"
    repo.put_object(key, html_file, "text/html; charset=utf-8")
    repo.save_report_meta(number, {"reason": reason, "date": today.isoformat(), "s3_key": key})
    return {"number": number, "html_email": html_email, "html_file": html_file, "images": images, "s3_key": key}
