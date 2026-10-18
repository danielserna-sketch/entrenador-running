"""Mensajes proactivos: el agente analiza y escribe sin que el atleta pregunte."""

from __future__ import annotations

import logging
from datetime import timedelta

from coach.agent.coach import Coach
from coach.analytics import compliance
from coach.config import get_settings
from coach.planning import periodization
from coach.whatsapp.client import WhatsAppClient

log = logging.getLogger(__name__)

TASK_PREFIX = "[Tarea automática del sistema, no es un mensaje del atleta] "


def deliver(coach: Coach, text: str, wa: WhatsAppClient | None = None) -> str:
    """Envía respetando la ventana de 24 h y guarda el mensaje en el historial."""
    settings = get_settings()
    wa = wa or WhatsAppClient()
    summary = text.split("\n")[0][:300]
    mode = wa.send_smart(settings.allowed_phone, text, summary, coach.repo.last_user_message_ts())
    coach.repo.add_message("assistant", text)
    return mode


def run_task(coach: Coach, instruction: str) -> str:
    return coach.run(TASK_PREFIX + instruction, tier="main")


def post_activity(coach: Coach, activity: dict) -> str:
    monday = periodization.monday_of(coach.today)
    week = coach.repo.get_week(monday)
    planned = next((s for s in (week or {}).get("sessions", []) if s["date"] == activity["date"]), None)
    is_test = bool(planned and planned.get("type") == "test")
    color = compliance.session_compliance(planned, activity, coach.today) if planned else None
    instruction = (
        f"El atleta acaba de terminar una actividad (id {activity['activity_id']}, {activity.get('name')}). "
        f"Sesión planeada: {planned['title'] if planned else 'ninguna (no planeada)'}. "
        f"Cumplimiento: {color}. Analízala con get_activity_detail y get_compliance: cumplimiento por colores, "
        "zonas, pace, cadencia (vs. objetivo si había metrónomo) y cómo encaja en la semana. "
        "Termina con una recomendación concreta para la próxima sesión y pregunta su RPE (1-10)."
    )
    if is_test:
        instruction += (" Esta sesión era el TEST DE 20 MINUTOS: usa recalculate_zones con este activity_id, "
                        "explica los nuevos umbrales y si cambiaron frente a los anteriores, y envía el "
                        "reporte con send_plan_report (motivo: cambio de umbral).")
    return run_task(coach, instruction)


def morning_brief(coach: Coach) -> str:
    return run_task(coach, (
        "Escribe el resumen matutino: revisa get_wellness (sueño, HRV, Body Battery, readiness) y la sesión "
        "de hoy en get_plan. Si el bienestar está bajo, propone una adaptación concreta (bajar zona o duración, "
        "o mover la sesión). Si hoy no hay sesión, da una recomendación breve de recuperación o movilidad. "
        "Máximo 90 palabras."
    ))


def weekly_review(coach: Coach) -> str:
    next_monday = periodization.monday_of(coach.today) + timedelta(days=7)
    return run_task(coach, (
        "Es la revisión semanal del domingo. 1) Revisa get_compliance de esta semana, get_training_load y "
        "get_wellness. 2) Genera la próxima semana con generate_week (week_start "
        f"{next_monday.isoformat()}), indicando en 'adjustments' lo que deba cambiar según el cumplimiento "
        "(repetir, bajar carga o avanzar) y el bienestar. 3) Envía el reporte con send_plan_report "
        "(motivo: revisión semanal). 4) Escribe al atleta: balance de la semana con colores, qué viene y por qué."
    ))


def alerts_check(coach: Coach) -> str | None:
    """Evalúa reglas; solo escribe si hay alertas nuevas (con cooldown por tipo)."""
    from coach.agent.tools import ToolContext, check_alerts  # evita import circular
    from coach.planning.service import PlanService

    plan = PlanService(coach.repo, coach.llm, coach._system(), coach.garmin_factory, coach.today)
    ctx = ToolContext(repo=coach.repo, plan=plan, garmin_factory=coach.garmin_factory, today=coach.today)
    fresh = []
    for alert in check_alerts(ctx, {}):
        since = coach.today - timedelta(days=alert["cooldown_days"])
        if not coach.repo.alert_sent_recently(alert["kind"], since):
            fresh.append(alert)
    if not fresh:
        return None
    for alert in fresh:
        coach.repo.mark_alert(alert["kind"], coach.today, alert)
    titles = "; ".join(a["title"] for a in fresh)
    return run_task(coach, (
        f"Se detectaron estas alertas: {titles}. Datos: {fresh}. Explica al atleta qué significan con sus "
        "números, y propone un ajuste concreto. Si hace falta cambiar sesiones de esta semana, propónlo y pide "
        "confirmación antes de usar modify_session (salvo dolor fuerte: ahí sugiere descanso de inmediato)."
    ))

