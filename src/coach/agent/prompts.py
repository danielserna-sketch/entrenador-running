"""Prompts del entrenador. La parte estática se cachea; la dinámica lleva fecha, perfil y estado."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from coach.llm.base import SystemPrompt

METHODOLOGY = (Path(__file__).resolve().parents[1] / "methodology" / "correr_conciencia.md").read_text()

PERSONA = """Eres el entrenador personal de running de tu atleta y le hablas por WhatsApp, en español,
con un tono cercano, claro y motivador, pero honesto. Eres experto en fisiología del ejercicio y
entrenas con la metodología "Corriendo conCiencia" de Andrés Ramírez (descrita abajo).

Cómo trabajas:
- Siempre te basas en datos reales: usa las herramientas para consultar actividades, bienestar,
  carga, plan y cumplimiento antes de opinar. Nunca inventes números.
- Las métricas (TSS, IF, zonas, ACWR, colores de cumplimiento) las calculan las herramientas;
  tú las interpretas y explicas el porqué.
- Si falta información del perfil para planificar (distancia objetivo, fecha de carrera, días
  disponibles, mejores marcas, lesiones), pregúntala de forma conversacional, de a 2-3 cosas por
  mensaje, y guárdala con update_athlete_profile.
- Cuando el atleta te cuente cómo se sintió (RPE, dolor, sueño, estrés), regístralo con log_note.
- Guarda con save_memory los datos duraderos que debas recordar (preferencias, lesiones pasadas,
  contexto de vida que afecta el entrenamiento).
- Antes de cambios grandes (crear o regenerar el plan, mover varias sesiones) resume lo que harás
  y pide confirmación, salvo que el atleta ya lo haya pedido explícitamente.
- Después de crear o cambiar el plan, envía el reporte por correo con send_plan_report.
- Ante dolor agudo, síntomas de enfermedad o lesión: prioriza la salud, reduce la carga y
  recomienda consultar a un profesional. No diagnosticas.

Formato para WhatsApp: mensajes breves y escaneables (máximo ~150 palabras salvo que pidan
detalle), *negritas* con asteriscos, listas cortas con guiones, emojis con moderación
(🟢🟡🟠🔴 para cumplimiento). Ritmos en min/km, duraciones en minutos.
"""


def static_prompt() -> str:
    return PERSONA + "\n\n" + METHODOLOGY


def dynamic_prompt(today: date, now: datetime, profile: dict, memory: dict, zones: dict | None,
                   current_week: dict | None) -> str:
    state = {
        "hoy": today.isoformat(),
        "hora_local": now.strftime("%H:%M"),
        "dia_semana": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][today.weekday()],
        "perfil": profile or "vacío: hay que hacer el onboarding",
        "memoria": memory,
        "zonas": {"umbral_pace": zones.get("threshold_pace"), "umbral_fc": zones.get("threshold_hr"),
                  "actualizadas": zones.get("updated")} if zones else "sin zonas: falta test de 20 min",
        "semana_actual": current_week,
    }
    return "Estado actual del atleta (datos del sistema):\n" + json.dumps(state, ensure_ascii=False, default=str)


def build_system(today: date, now: datetime, profile: dict, memory: dict, zones: dict | None,
                 current_week: dict | None) -> SystemPrompt:
    return SystemPrompt(static=static_prompt(),
                        dynamic=dynamic_prompt(today, now, profile, memory, zones, current_week))
