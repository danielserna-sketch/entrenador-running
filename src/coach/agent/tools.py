"""Herramientas del agente: definición neutral (nombre, descripción, JSON Schema) + handler."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from coach.analytics import alerts, compliance, history, load, wellness
from coach.analytics import zones as zones_mod
from coach.garmin.provider import GarminProvider
from coach.garmin.sync import enrich_activity
from coach.llm.base import ToolSpec
from coach.planning import periodization
from coach.planning.garmin_workout_builder import build_workout
from coach.planning.microcycle import WEEK_SCHEMA
from coach.planning.service import PlanService
from coach.storage.repo import Repo

log = logging.getLogger(__name__)

SESSION_SCHEMA = WEEK_SCHEMA["properties"]["sessions"]["items"]


@dataclass
class ToolContext:
    repo: Repo
    plan: PlanService
    garmin_factory: Callable[[], GarminProvider]
    today: date

    @property
    def garmin(self) -> GarminProvider:
        return self.plan.garmin


@dataclass
class Tool:
    spec: ToolSpec
    handler: Callable[[ToolContext, dict], Any]


def _obj(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or [],
            "additionalProperties": False}


def _activity_brief(a: dict) -> dict:
    keys = ["activity_id", "date", "name", "type", "distance_km", "duration_min", "avg_pace_s_per_km",
            "avg_hr", "avg_cadence_spm", "tss", "if", "main_zone", "aerobic_te", "elevation_gain_m"]
    out = {k: a.get(k) for k in keys if a.get(k) is not None}
    if a.get("avg_pace_s_per_km"):
        out["avg_pace"] = zones_mod.pace_to_str(a["avg_pace_s_per_km"])
    return out


# ---------------------------------------------------------------------------- lectura
def get_recent_activities(ctx: ToolContext, args: dict) -> Any:
    days = int(args.get("days", 14))
    acts = ctx.repo.activities(ctx.today - timedelta(days=days), ctx.today)
    return [_activity_brief(a) for a in acts]


def get_activity_detail(ctx: ToolContext, args: dict) -> Any:
    activity = ctx.repo.get_activity(args["activity_id"])
    if not activity:
        return {"error": "No encontré esa actividad. Usa get_recent_activities para ver los ids."}
    out = _activity_brief(activity)
    out["detail"] = activity.get("detail") or ctx.garmin.activity_detail(args["activity_id"])
    return out


def get_wellness(ctx: ToolContext, args: dict) -> Any:
    days = int(args.get("days", 7))
    dailies = ctx.repo.dailies(ctx.today - timedelta(days=35), ctx.today)
    rows = [d for d in dailies if date.fromisoformat(d["date"]) > ctx.today - timedelta(days=days)]
    # Si el reloj no envía readiness, no mostramos ese campo vacío.
    rows = [{k: v for k, v in d.items() if not (k == "training_readiness" and v is None)} for d in rows]
    return {"snapshot": wellness.wellness_snapshot(dailies, ctx.today), "days": rows}


def get_training_load(ctx: ToolContext, args: dict) -> Any:
    acts = ctx.repo.activities(ctx.today - timedelta(days=120), ctx.today)
    series = load.fitness_series(acts, ctx.today - timedelta(days=120), ctx.today)
    monday = periodization.monday_of(ctx.today)
    return {
        "hoy": series[-1] if series else None,
        "ctl_hace_4_semanas": series[-29]["ctl"] if len(series) > 29 else None,
        "acwr": load.acwr(acts, ctx.today),
        "monotonia_7d": load.monotony(acts, ctx.today),
        "semanas": [load.weekly_summary(acts, monday - timedelta(weeks=i)) for i in range(5, -1, -1)],
    }


def get_profile(ctx: ToolContext, args: dict) -> Any:
    return {"perfil": ctx.repo.get_profile(), "zonas": ctx.repo.get_zones(), "memoria": ctx.repo.get_memory()}


def get_plan(ctx: ToolContext, args: dict) -> Any:
    macro = ctx.repo.get_macro()
    if not macro:
        return {"plan": None, "faltan_datos_perfil": ctx.plan.missing_profile_fields()}
    monday = date.fromisoformat(args["week_start"]) if args.get("week_start") else periodization.monday_of(ctx.today)
    monday = periodization.monday_of(monday)
    week = ctx.repo.get_week(monday)
    return {
        "macrociclo": {k: macro.get(k) for k in ("start", "race_date", "distance", "total_weeks", "phase_weeks",
                                                   "warnings")},
        "semanas": [{k: w[k] for k in ("index", "week_start", "phase_label", "kind", "target_tss")}
                    for w in macro["weeks"]],
        "semana_consultada": week or "Aún no generada (se genera el domingo anterior o con generate_week).",
    }


def get_compliance(ctx: ToolContext, args: dict) -> Any:
    monday = periodization.monday_of(date.fromisoformat(args["week_start"])) if args.get("week_start") \
        else periodization.monday_of(ctx.today)
    week = ctx.repo.get_week(monday)
    if not week:
        return {"error": "No hay semana planeada para esa fecha."}
    acts = ctx.repo.activities(monday, monday + timedelta(days=6))
    results = compliance.match_week(week["sessions"], acts, ctx.today)
    return {
        "semana": monday.isoformat(),
        "resumen": compliance.summarize(results),
        "sesiones": [{"fecha": (r["session"] or r["activity"])["date"],
                      "planeado": (r["session"] or {}).get("title"),
                      "realizado": (r["activity"] or {}).get("name"),
                      "color": r["color"], "emoji": compliance.EMOJI.get(r["color"], "⏳"),
                      "ratio": r["ratio"], "metrica": r["metric"]} for r in results],
    }


def check_alerts(ctx: ToolContext, args: dict) -> Any:
    acts = ctx.repo.activities(ctx.today - timedelta(days=42), ctx.today)
    dailies = ctx.repo.dailies(ctx.today - timedelta(days=35), ctx.today)
    notes = ctx.repo.notes(ctx.today - timedelta(days=7))
    monday = periodization.monday_of(ctx.today)
    week = ctx.repo.get_week(monday)
    comp = compliance.match_week(week["sessions"], [a for a in acts if a["date"] >= monday.isoformat()],
                                 ctx.today) if week else []
    return [a.__dict__ for a in alerts.evaluate(acts, dailies, notes, comp, ctx.today)]


# ---------------------------------------------------------------------------- escritura
def update_athlete_profile(ctx: ToolContext, args: dict) -> Any:
    changes = dict(args)
    best = changes.pop("best_times", None)
    profile = ctx.repo.get_profile()
    if best:
        merged = {**profile.get("best_times", {}), **{k: v for k, v in best.items() if v}}
        changes["best_times"] = merged
    return {"perfil": ctx.repo.update_profile(changes)}


def log_note(ctx: ToolContext, args: dict) -> Any:
    note = {"date": args.get("date") or ctx.today.isoformat(), **{k: v for k, v in args.items() if k != "date"}}
    ctx.repo.add_note(note)
    return {"ok": True, "nota": note}


def save_memory(ctx: ToolContext, args: dict) -> Any:
    memory = ctx.repo.get_memory()
    facts = memory.get("facts", [])
    facts.append({"date": ctx.today.isoformat(), "fact": args["fact"]})
    memory["facts"] = facts[-60:]
    ctx.repo.save_memory(memory)
    return {"ok": True, "total_hechos": len(memory["facts"])}


def recalculate_zones(ctx: ToolContext, args: dict) -> Any:
    """Desde un test de 20 min: por actividad (usa su pace y FC promedio) o valores manuales."""
    pace_s = zones_mod.pace_from_str(args["test_avg_pace"]) if args.get("test_avg_pace") else None
    hr = args.get("test_avg_hr")
    test_date = date.fromisoformat(args["test_date"]) if args.get("test_date") else ctx.today
    if args.get("activity_id"):
        activity = ctx.repo.get_activity(args["activity_id"])
        if not activity:
            return {"error": "Actividad no encontrada."}
        laps = (activity.get("detail") or {}).get("splits") or []
        test_laps = [lap for lap in laps if lap.get("duration_s") and 1100 <= lap["duration_s"] <= 1300]
        source = test_laps[0] if test_laps else None
        pace_s = pace_s or (source or {}).get("pace_s_per_km") or activity.get("avg_pace_s_per_km")
        hr = hr or (source or {}).get("avg_hr") or activity.get("avg_hr")
        test_date = date.fromisoformat(activity["date"])
    th = zones_mod.thresholds_from_test(pace_s, hr)
    if not th:
        return {"error": "Necesito el pace promedio y/o la FC promedio del test de 20 minutos."}
    current = ctx.repo.get_zones()
    change = zones_mod.detect_threshold_change(current, th)
    new = zones_mod.build_zones(th.get("threshold_pace_s"), th.get("threshold_hr"), source="test 20 min",
                                test_date=test_date)
    ctx.repo.save_zones(new)
    pushed = False
    if new.get("threshold_hr") and args.get("push_to_garmin", True):
        try:
            pushed = ctx.garmin.update_running_hr_zones(new["threshold_hr"], new["hr"])  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            pushed = f"no se pudieron cargar en Garmin: {e}"
    # Recalcular TSS de las últimas 6 semanas con las zonas nuevas.
    for a in ctx.repo.activities(ctx.today - timedelta(days=42), ctx.today):
        ctx.repo.save_activity(enrich_activity(a, new))
    ctx.plan.add_changelog(f"Cambio de umbral: pace {new.get('threshold_pace')} min/km, "
                           f"FC {new.get('threshold_hr')}. Zonas recalculadas.")
    return {"zonas": new, "cambio": change, "zonas_fc_en_garmin": pushed}


def generate_training_plan(ctx: ToolContext, args: dict) -> Any:
    return ctx.plan.create_plan(publish=args.get("publish_to_garmin", True),
                                send_email=args.get("send_email", True))


def generate_week(ctx: ToolContext, args: dict) -> Any:
    monday = periodization.monday_of(date.fromisoformat(args["week_start"])) if args.get("week_start") \
        else periodization.monday_of(ctx.today + timedelta(days=7))
    week = ctx.plan.generate_week(monday, adjustments=args.get("adjustments"),
                                  publish=args.get("publish_to_garmin", True))
    from coach.planning.service import _week_brief

    return _week_brief(week)


def modify_session(ctx: ToolContext, args: dict) -> Any:
    """Mover, eliminar o reemplazar una sesión de la semana (y reflejarlo en Garmin)."""
    day = date.fromisoformat(args["date"])
    monday = periodization.monday_of(day)
    week = ctx.repo.get_week(monday)
    if not week:
        return {"error": "No hay semana planeada en esa fecha."}
    session = next((s for s in week["sessions"] if s["date"] == args["date"]), None)
    if not session:
        return {"error": f"No hay sesión el {args['date']}."}
    action = args["action"]
    if action == "move" and (not args.get("new_date")
                             or periodization.monday_of(date.fromisoformat(args["new_date"])) != monday):
        return {"error": "Para mover indica new_date dentro de la misma semana."}
    zones = ctx.repo.get_zones()
    if session.get("garmin_workout_id"):
        try:
            ctx.garmin.delete_workout(session["garmin_workout_id"])
        except Exception:  # noqa: BLE001
            log.warning("No se pudo borrar el workout %s de Garmin", session["garmin_workout_id"])
    week["sessions"].remove(session)
    if action == "move":
        session["date"] = args["new_date"]
        week["sessions"].append(session)
    elif action == "replace":
        from coach.planning import validator

        session = {**args["session"], "date": args.get("new_date") or args["date"]}
        session["tss"] = validator.session_tss(session, zones)
        session["duration_min"] = round(validator.estimated_duration_s(session["steps"], zones) / 60)
        week["sessions"].append(session)
    if action in ("move", "replace") and date.fromisoformat(session["date"]) >= ctx.today:
        workout_id = ctx.garmin.create_workout(build_workout(session, zones))
        ctx.garmin.schedule_workout(workout_id, date.fromisoformat(session["date"]))
        session["garmin_workout_id"] = workout_id
    week["sessions"].sort(key=lambda s: s["date"])
    week["planned_tss"] = round(sum(s.get("tss") or 0 for s in week["sessions"]), 1)
    ctx.repo.save_week(week)
    ctx.plan.add_changelog(f"Sesión del {args['date']}: {action}. {args.get('reason', '')}".strip())
    return {"ok": True, "semana": [{"date": s["date"], "title": s["title"]} for s in week["sessions"]]}


def create_and_schedule_workout(ctx: ToolContext, args: dict) -> Any:
    zones = ctx.repo.get_zones()
    session = args["session"]
    workout_id = ctx.garmin.create_workout(build_workout(session, zones))
    ctx.garmin.schedule_workout(workout_id, date.fromisoformat(session["date"]))
    return {"ok": True, "garmin_workout_id": workout_id}


def send_plan_report(ctx: ToolContext, args: dict) -> Any:
    return ctx.plan.send_report(args.get("reason") or "Lo pediste por WhatsApp.")


def sync_garmin_now(ctx: ToolContext, args: dict) -> Any:
    from coach.garmin.sync import sync

    result = sync(ctx.garmin, ctx.repo, ctx.today, days_back=int(args.get("days_back", 2)))
    return {"actividades_nuevas": [_activity_brief(a) for a in result["new_activities"]]}


# ---------------------------------------------------------------------------- Strava (historial)
def _duration_str(seconds: float) -> str:
    seconds = round(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _with_strings(value: Any) -> Any:
    """Agrega tiempos y ritmos legibles (h:mm:ss, m:ss/km) junto a los segundos."""
    if isinstance(value, list):
        return [_with_strings(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: _with_strings(v) for k, v in value.items()}
    if value.get("time_s"):
        out["time"] = _duration_str(value["time_s"])
    for key in ("pace_s_per_km", "avg_pace_s_per_km", "easy_pace_s_per_km"):
        if value.get(key):
            out[key.replace("_s_per_km", "")] = zones_mod.pace_to_str(value[key])
    return out


def _no_strava_data(ctx: ToolContext) -> dict | None:
    if ctx.repo.query_prefix("STRAVA_ACTIVITY#", limit=1):
        return None
    return {"error": "Todavía no hay historial de Strava. Hay que cargar el archivo exportado con "
                     "scripts/import_strava_export.py (o conectar la API con scripts/strava_login.py)."}


def _strava_status(ctx: ToolContext) -> dict:
    state = ctx.repo.get_strava_sync()
    export = ctx.repo.get("META#strava_export") or {}
    out = {"historial_hasta": export.get("last_activity")}
    if state.get("listed_until"):
        out.update({"api_historial_completo": bool(state.get("backfill_done")),
                    "carreras_pendientes_de_detalle": len(state.get("pending") or [])})
    elif export:
        out["fuente"] = f"archivo de Strava importado el {export.get('imported_on')}"
    return out


def get_personal_records(ctx: ToolContext, args: dict) -> Any:
    if missing := _no_strava_data(ctx):
        return missing
    records = ctx.repo.get_records() or {"distances": {}}
    distances = records["distances"]
    if args.get("distance"):
        distances = {k: v for k, v in distances.items() if k == args["distance"]}
    return {"records": _with_strings(distances), "actualizado": records.get("updated"),
            "marcas_del_perfil": ctx.repo.get_profile().get("best_times"), **_strava_status(ctx)}


def get_training_history(ctx: ToolContext, args: dict) -> Any:
    if missing := _no_strava_data(ctx):
        return missing
    start = date.fromisoformat(args["start"]) if args.get("start") else None
    end = date.fromisoformat(args["end"]) if args.get("end") else None
    acts = ctx.repo.strava_activities(start, end)
    group = args.get("group_by") or ("year" if start is None else "month")
    return {"resumen": history.history_summary(acts),
            "por_periodo": _with_strings(history.pace_trends(acts, group)), **_strava_status(ctx)}


def search_past_activities(ctx: ToolContext, args: dict) -> Any:
    if missing := _no_strava_data(ctx):
        return missing
    start = date.fromisoformat(args["start"]) if args.get("start") else None
    end = date.fromisoformat(args["end"]) if args.get("end") else None
    found = history.search(ctx.repo.strava_activities(start, end), min_km=args.get("min_km"),
                           max_km=args.get("max_km"), workout_type=args.get("workout_type"),
                           sort=args.get("sort", "date"), limit=int(args.get("limit", 15)))
    keys = ["activity_id", "date", "name", "distance_km", "duration_s", "avg_pace_s_per_km", "avg_hr",
            "elevation_gain_m", "workout_type", "main_zone", "tss"]
    out = []
    for a in found:
        brief = {k: a[k] for k in keys if a.get(k) is not None}
        if a.get("duration_s"):
            brief["duration"] = _duration_str(a["duration_s"])
        if args.get("include_splits") and a.get("splits"):
            brief["splits"] = a["splits"]
        out.append(_with_strings(brief))
    return out


def sync_strava_now(ctx: ToolContext, args: dict) -> Any:
    from coach.strava.client import StravaClient
    from coach.strava.sync import sync

    client = StravaClient()
    try:
        return sync(client, ctx.repo, ctx.today, max_seconds=120)
    finally:
        client.close()


# ---------------------------------------------------------------------------- registro
_SESSION = SESSION_SCHEMA
TOOLS: list[Tool] = [
    Tool(ToolSpec("get_profile", "Perfil del atleta, zonas vigentes y memoria guardada.", _obj()), get_profile),
    Tool(ToolSpec("get_recent_activities", "Actividades recientes con distancia, pace, FC, cadencia, TSS, IF y zona.",
                  _obj({"days": {"type": "integer", "description": "Días hacia atrás (defecto 14)"}})),
         get_recent_activities),
    Tool(ToolSpec("get_activity_detail", "Detalle de una actividad: splits/laps y tiempo en zonas de FC.",
                  _obj({"activity_id": {"type": "string"}}, ["activity_id"])), get_activity_detail),
    Tool(ToolSpec("get_wellness", "Sueño, HRV, FC en reposo, Body Battery y estrés con líneas base.",
                  _obj({"days": {"type": "integer"}})), get_wellness),
    Tool(ToolSpec("get_training_load", "Forma (CTL), fatiga (ATL), frescura (TSB), ACWR, monotonía y TSS por semana.",
                  _obj()), get_training_load),
    Tool(ToolSpec("get_plan", "Macrociclo (fases y TSS objetivo por semana) y el detalle de una semana.",
                  _obj({"week_start": {"type": "string", "description": "YYYY-MM-DD (defecto: semana actual)"}})),
         get_plan),
    Tool(ToolSpec("get_compliance", "Cumplimiento por colores de una semana (planeado vs realizado).",
                  _obj({"week_start": {"type": "string"}})), get_compliance),
    Tool(ToolSpec("check_alerts", "Evalúa señales de alerta: ACWR, HRV, FC reposo, sueño, sesiones perdidas, dolor.",
                  _obj()), check_alerts),
    Tool(ToolSpec("update_athlete_profile", "Guarda o actualiza datos del perfil del atleta.", _obj({
        "name": {"type": "string"},
        "goal_distance": {"type": "string", "enum": ["5k", "10k", "half", "marathon"]},
        "race_name": {"type": "string"},
        "race_date": {"type": "string", "description": "YYYY-MM-DD"},
        "goal_time_s": {"type": "integer", "description": "Marca objetivo en segundos"},
        "best_times": {"type": "object", "description": "Mejores marcas recientes en segundos",
                       "properties": {k: {"type": "integer"} for k in ("5k", "10k", "half", "marathon")},
                       "additionalProperties": False},
        "days_available": {"type": "array", "items": {"type": "string", "enum": [
            "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]}},
        "max_hours_week": {"type": "number"},
        "longest_session_min": {"type": "integer"},
        "injuries": {"type": "string"},
        "has_power_meter": {"type": "boolean"},
        "notes": {"type": "string"},
    })), update_athlete_profile),
    Tool(ToolSpec("log_note", "Registra sensaciones del atleta: RPE (1-10), dolor (0-10) y zona, sueño, estrés o texto.",
                  _obj({"date": {"type": "string"}, "rpe": {"type": "integer"}, "pain": {"type": "integer"},
                        "pain_location": {"type": "string"}, "text": {"type": "string"},
                        "activity_id": {"type": "string"}})), log_note),
    Tool(ToolSpec("save_memory", "Guarda un dato duradero del atleta para recordarlo en el futuro.",
                  _obj({"fact": {"type": "string"}}, ["fact"])), save_memory),
    Tool(ToolSpec("recalculate_zones",
                  "Recalcula umbrales y zonas Friel a partir de un test de 20 min (pace×1.05, FC×0.95). "
                  "Usa activity_id del test o valores manuales.",
                  _obj({"activity_id": {"type": "string"}, "test_avg_pace": {"type": "string", "description": "m:ss"},
                        "test_avg_hr": {"type": "number"}, "test_date": {"type": "string"},
                        "push_to_garmin": {"type": "boolean"}})), recalculate_zones),
    Tool(ToolSpec("generate_training_plan",
                  "Crea el plan completo (diagnóstico, macrociclo y primera semana), lo publica en Garmin "
                  "y envía el reporte HTML por correo. Requiere perfil con distancia, fecha y días.",
                  _obj({"publish_to_garmin": {"type": "boolean"}, "send_email": {"type": "boolean"}})),
         generate_training_plan),
    Tool(ToolSpec("generate_week", "Genera (o regenera con ajustes) el microciclo de una semana y lo publica en Garmin.",
                  _obj({"week_start": {"type": "string"}, "adjustments": {"type": "string"},
                        "publish_to_garmin": {"type": "boolean"}})), generate_week),
    Tool(ToolSpec("modify_session", "Mueve, elimina o reemplaza una sesión de la semana y actualiza Garmin.",
                  _obj({"date": {"type": "string"}, "action": {"type": "string", "enum": ["move", "remove", "replace"]},
                        "new_date": {"type": "string"}, "session": _SESSION, "reason": {"type": "string"}},
                       ["date", "action"])), modify_session),
    Tool(ToolSpec("create_and_schedule_workout", "Crea un entrenamiento estructurado puntual y lo agenda en Garmin.",
                  _obj({"session": _SESSION}, ["session"])), create_and_schedule_workout),
    Tool(ToolSpec("send_plan_report", "Envía por correo el reporte HTML actualizado del plan.",
                  _obj({"reason": {"type": "string"}})), send_plan_report),
    Tool(ToolSpec("get_personal_records",
                  "Récords personales desde Strava por distancia (400m a 50k): de siempre, últimos 12 meses, "
                  "año actual, top 3 y mejor por año.",
                  _obj({"distance": {"type": "string", "enum": [
                      "400m", "half_mile", "1k", "mile", "2mile", "5k", "10k", "15k", "10mile", "20k", "half",
                      "30k", "marathon", "50k"]}})), get_personal_records),
    Tool(ToolSpec("get_training_history",
                  "Historial completo de Strava: totales, km por año, semana más grande, fondo más largo, pausas "
                  "y, por mes o año, volumen, ritmo medio, ritmo suave, eficiencia aeróbica y carreras.",
                  _obj({"start": {"type": "string", "description": "YYYY-MM-DD (defecto: desde el inicio)"},
                        "end": {"type": "string", "description": "YYYY-MM-DD"},
                        "group_by": {"type": "string", "enum": ["month", "year"]}})), get_training_history),
    Tool(ToolSpec("search_past_activities",
                  "Busca carreras pasadas en el historial de Strava por distancia, tipo y fechas, "
                  "p. ej. el fondo más largo o la carrera más rápida de un año.",
                  _obj({"start": {"type": "string"}, "end": {"type": "string"},
                        "min_km": {"type": "number"}, "max_km": {"type": "number"},
                        "workout_type": {"type": "string", "enum": ["normal", "carrera", "fondo", "entrenamiento"]},
                        "sort": {"type": "string", "enum": ["date", "pace", "distance"]},
                        "limit": {"type": "integer"}, "include_splits": {"type": "boolean"}})),
         search_past_activities),
    Tool(ToolSpec("sync_strava_now", "Sincroniza ya mismo el historial de Strava (actividades nuevas y detalle).",
                  _obj()), sync_strava_now),
    Tool(ToolSpec("sync_garmin_now", "Sincroniza ya mismo las actividades y el bienestar desde Garmin.",
                  _obj({"days_back": {"type": "integer"}})), sync_garmin_now),
]
TOOLS_BY_NAME = {t.spec.name: t for t in TOOLS}


def run_tool(ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        return f"Herramienta desconocida: {name}", True
    try:
        result = tool.handler(ctx, args)
        return json.dumps(result, ensure_ascii=False, default=str), False
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}", True
