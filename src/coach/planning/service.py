"""Orquestación del plan: crear plan, generar semana, publicar en Garmin, enviar reporte."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta

from coach.agent.athlete_profile import REQUIRED_PROFILE
from coach.analytics import compliance, load, wellness
from coach.garmin.provider import GarminProvider
from coach.llm.base import LLMClient, SystemPrompt
from coach.methodology import rules
from coach.planning import assessment, microcycle, periodization, readiness
from coach.planning.garmin_workout_builder import build_workout
from coach.reports import builder, email_sender
from coach.storage.repo import Repo

log = logging.getLogger(__name__)



class PlanService:
    def __init__(self, repo: Repo, llm: LLMClient, system: SystemPrompt,
                 garmin_factory: Callable[[], GarminProvider], today: date):
        self.repo = repo
        self.llm = llm
        self.system = system
        self.garmin_factory = garmin_factory
        self.today = today
        self._garmin: GarminProvider | None = None

    @property
    def garmin(self) -> GarminProvider:
        if self._garmin is None:
            self._garmin = self.garmin_factory()
        return self._garmin

    def ensure_zones(self) -> dict | None:
        """Zonas con la FC que el atleta tiene en Garmin (ver assessment.ensure_zones)."""
        try:
            garmin = self.garmin
        except Exception as e:  # noqa: BLE001
            log.warning("Sin Garmin para leer zonas: %s", e)
            garmin = None
        return assessment.ensure_zones(self.repo, garmin, self.today)

    # ---- plan completo ----------------------------------------------------
    def missing_profile_fields(self) -> list[str]:
        profile = self.repo.get_profile()
        return [f for f in REQUIRED_PROFILE if not profile.get(f)]

    def create_plan(self, publish: bool = True, send_email: bool = True) -> dict:
        missing = self.missing_profile_fields()
        if missing:
            return {"ok": False, "missing_profile_fields": missing}
        profile = self.repo.get_profile()
        zones = self.ensure_zones()
        activities = self.repo.activities(self.today - timedelta(days=90), self.today)
        diag = assessment.assess(profile, activities, zones, self.today, self.repo.get_records())
        level = diag["half_level"] or {}
        macro = periodization.build_macrocycle(
            race_date=date.fromisoformat(profile["race_date"]),
            start=periodization.plan_start(self.today),
            distance=diag["distance"],
            start_weekly_tss=diag["start_weekly_tss"],
            weekly_hours_target=level.get("weekly_hours") or profile.get("max_hours_week"),
        )
        macro["assessment"] = diag
        macro["changelog"] = [{"date": self.today.isoformat(), "text": "Plan creado."}]
        self.repo.save_macro(macro)

        week = self.generate_week(date.fromisoformat(macro["start"]), publish=publish)
        result = {"ok": True, "assessment": diag, "macro": _macro_summary(macro), "first_week": _week_brief(week)}
        if send_email:
            result["report"] = self.send_report("Plan creado: ciclos, primera semana y zonas.")
        return result

    # ---- semana -----------------------------------------------------------
    def week_context(self, monday: date, adjustments: str | None = None) -> dict:
        macro = self.repo.get_macro()
        if not macro:
            raise ValueError("No hay plan. Primero crea el plan.")
        info = periodization.week_info(macro, monday)
        if not info:
            raise ValueError(f"La semana {monday} no está dentro del plan.")
        profile = self.repo.get_profile()
        zones = self.repo.get_zones()
        diag = macro.get("assessment", {})
        prev_monday = monday - timedelta(days=7)
        prev_week = self.repo.get_week(prev_monday)
        activities = self.repo.activities(prev_monday - timedelta(days=21), self.today)
        prev_summary = None
        if prev_week:
            prev_acts = [a for a in activities if prev_monday <= date.fromisoformat(a["date"]) < monday]
            results = compliance.match_week(prev_week["sessions"], prev_acts, self.today, zones)
            prev_summary = {
                "colores": compliance.summarize(results),
                "tss_planeado": prev_week.get("planned_tss"),
                "tss_real": load.weekly_summary(activities, prev_monday)["tss"],
                "sesiones": [{"fecha": r["session"]["date"] if r["session"] else r["activity"]["date"],
                              "titulo": (r["session"] or {}).get("title"), "color": r["color"],
                              "ratio": r["ratio"], "intensidad": r.get("intensity_color")} for r in results],
            }
        dailies = self.repo.dailies(self.today - timedelta(days=35), self.today)
        # El test va cuando no hay zonas o están viejas, en la semana 1 o en una de descarga.
        needs_test = assessment.needs_threshold_test(zones, self.today) and (
            info["index"] == 1 or info["kind"] == "descarga")
        level = diag.get("half_level") or {}
        notes = self.repo.notes(self.today - timedelta(days=10))
        adjust = readiness.assess(dailies, notes, self.today)
        if adjust["reason"]:
            info = {**info, "target_tss": round(info["target_tss"] * adjust["tss_factor"])}
        sessions_max = diag.get("sessions_max", 3)
        workout_zones = assessment.with_easy_ceiling(zones, profile, self.repo.get_records())
        return {
            "week": info,
            "distance": macro["distance"],
            "sessions_recommended": diag.get("sessions_recommended", 3),
            "sessions_max": sessions_max,
            "max_quality": max(rules.max_quality_sessions(info["phase"], sessions_max) + adjust["quality_delta"], 0),
            "easy_ceiling_s": (workout_zones or {}).get("easy_ceiling_s"),
            "readiness": adjust,
            "days_available": profile.get("days_available", []),
            "longest_session_min": level.get("longest_session_min") or profile.get("longest_session_min"),
            "needs_test": needs_test,
            "zones_table": {"pace": (zones or {}).get("pace"), "hr": (zones or {}).get("hr")} if zones else None,
            "previous_week": prev_summary,
            "wellness": wellness.wellness_snapshot(dailies, self.today),
            "notes": notes,
            "race": {"date": macro["race_date"], "distance": macro["distance"]},
            "adjustments": adjustments,
        }

    def generate_week(self, monday: date, adjustments: str | None = None, publish: bool = True) -> dict:
        zones = self.ensure_zones()
        ctx = self.week_context(monday, adjustments)
        activities = self.repo.activities(monday - timedelta(days=28), self.today)
        old = self.repo.get_week(monday)
        week = microcycle.generate_week(self.llm, self.system, ctx, zones, activities)
        week["generated"] = self.today.isoformat()
        if week["validation_errors"]:
            # Primero lento y seguro: una semana que incumple las reglas no llega al reloj.
            week["needs_review"] = True
            log.warning("Semana %s con errores; no se publica: %s", monday, week["validation_errors"])
            if old:
                return {**old, "needs_review": True, "rejected_errors": week["validation_errors"]}
            self.repo.save_week(week)
            return week
        if publish:
            if old:
                self.unpublish_week(old)
            self.publish_week(week, zones)
        self.repo.save_week(week)
        if old:
            self.add_changelog(f"Semana {week['index']} regenerada" + (f": {adjustments}" if adjustments else "."))
        return week

    def publish_week(self, week: dict, zones: dict | None) -> None:
        zones = assessment.with_easy_ceiling(zones, self.repo.get_profile(), self.repo.get_records())
        for s in week["sessions"]:
            if date.fromisoformat(s["date"]) < self.today:
                continue
            try:
                workout_id = self.garmin.create_workout(build_workout(s, zones))
                self.garmin.schedule_workout(workout_id, date.fromisoformat(s["date"]))
                s["garmin_workout_id"] = workout_id
            except Exception as e:
                log.exception("No se pudo publicar %s en Garmin", s["title"])
                s["garmin_error"] = str(e)

    def unpublish_week(self, week: dict) -> None:
        for s in week.get("sessions", []):
            if s.get("garmin_workout_id") and date.fromisoformat(s["date"]) >= self.today:
                try:
                    self.garmin.delete_workout(s["garmin_workout_id"])
                except Exception:  # noqa: BLE001
                    log.warning("No se pudo borrar el workout %s", s["garmin_workout_id"])

    # ---- reporte y bitácora --------------------------------------------------
    def add_changelog(self, text: str) -> None:
        macro = self.repo.get_macro()
        if macro:
            macro.setdefault("changelog", []).append({"date": self.today.isoformat(), "text": text})
            self.repo.save_macro(macro)

    def send_report(self, reason: str) -> dict:
        report = builder.build_report(self.repo, self.today, reason)
        subject = f"🏃 Tu plan de entrenamiento · v{report['number']} · {self.today.isoformat()}"
        try:
            message_id = email_sender.send_report(report, subject)
            return {"sent": True, "version": report["number"], "message_id": message_id}
        except Exception as e:
            log.exception("No se pudo enviar el reporte")
            return {"sent": False, "version": report["number"], "error": str(e), "s3_key": report["s3_key"]}


def _macro_summary(macro: dict) -> dict:
    return {k: macro[k] for k in ("start", "race_date", "total_weeks", "phase_weeks", "warnings")}


def _week_brief(week: dict) -> dict:
    return {
        "week_start": week["week_start"], "phase": week["phase_label"], "summary": week["summary"],
        "planned_tss": week["planned_tss"], "validation_errors": week["validation_errors"],
        "semana_segura": week.get("fallback", False),
        "aviso": ("La semana generada incumplía las reglas y no se publicó en Garmin; avísale al atleta."
                  if week.get("needs_review") else None),
        "sessions": [{"date": s["date"], "title": s["title"], "type": s["type"], "duration_min": s["duration_min"],
                      "tss": s["tss"], "cadence": s.get("cadence"), "garmin": bool(s.get("garmin_workout_id"))}
                     for s in week["sessions"]],
    }
