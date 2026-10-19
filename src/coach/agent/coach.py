"""Bucle del agente, independiente del proveedor de LLM."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from coach.agent import athlete_profile, prompts
from coach.agent.tools import TOOLS, ToolContext, run_tool
from coach.garmin.provider import GarminProvider
from coach.llm.base import ChatMessage, ImageInput, LLMClient, ToolResult
from coach.planning.service import PlanService
from coach.storage.repo import Repo

log = logging.getLogger(__name__)

MAX_STEPS = 12
# Historial: mensajes crudos desde un ancla. Al pasar de COMPACT_AT, los viejos se resumen con el modelo
# rápido y quedan KEEP_RAW. Entre compactaciones el inicio del historial no cambia, así que se cachea.
COMPACT_AT = 24
KEEP_RAW = 10
FIRST_LOAD = 60  # sin ancla todavía: cuántos mensajes recientes se leen (y se resumen) la primera vez
REFUSAL_TEXT = "No puedo ayudarte con eso. ¿Hablamos de tu entrenamiento?"


def _ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


@dataclass
class Coach:
    repo: Repo
    llm: LLMClient
    garmin_factory: Callable[[], GarminProvider]
    now: datetime
    # Canal de WhatsApp: los pone el worker. Sin ellos, el agente no puede pedir confirmación ni enviar gráficas.
    request_confirmation: Callable | None = None
    send_image: Callable | None = None

    @property
    def today(self) -> date:
        return self.now.date()

    def _system(self):
        ficha = athlete_profile.build_ficha(self.repo, self.today).to_prompt()
        summary = athlete_profile.normalize_memory(self.repo.get_memory())["summary"]
        conversation = self.repo.get_conversation().get("summary", "")
        return prompts.build_system(ficha, summary, conversation)

    def _context(self, system, *, confirmed: bool = False) -> ToolContext:
        plan = PlanService(self.repo, self.llm, system, self.garmin_factory, self.today)
        return ToolContext(repo=self.repo, plan=plan, garmin_factory=self.garmin_factory, today=self.today,
                           confirmed=confirmed, request_confirmation=self.request_confirmation,
                           send_image=self.send_image)

    def run(self, user_text: str, *, history: list[ChatMessage] | None = None, tier: str = "main",
            images: list[ImageInput] | None = None) -> str:
        """Ejecuta un turno completo: el modelo puede llamar herramientas hasta dar la respuesta final."""
        started = time.perf_counter()
        system = self._system()
        ctx = self._context(system)
        turn = ChatMessage("user", prompts.turn_context(self.now) + user_text, images=list(images or []))
        messages = list(history or []) + [turn]
        session = self.llm.session(system, messages, [t.spec for t in TOOLS], tier=tier)  # type: ignore[arg-type]
        text = ""
        usage = {"steps": 0, "input": 0, "cache_read": 0, "output": 0,
                 "prep_ms": _ms(started), "llm_ms": 0, "tools_ms": 0, "max_tools_per_step": 0,
                 "slowest_tool": None, "slowest_tool_ms": 0}
        try:
            for _ in range(MAX_STEPS):
                t0 = time.perf_counter()
                step = session.step()
                usage["llm_ms"] += _ms(t0)
                usage["steps"] += 1
                for key in ("input", "cache_read", "output"):
                    usage[key] += step.usage.get(key) or 0
                if step.stop_reason == "refusal":
                    return REFUSAL_TEXT
                text = step.text or text
                if not step.tool_calls:
                    break
                results = []
                usage["max_tools_per_step"] = max(usage["max_tools_per_step"], len(step.tool_calls))
                for call in step.tool_calls:
                    t0 = time.perf_counter()
                    content, is_error = run_tool(ctx, call.name, call.arguments)
                    elapsed = _ms(t0)
                    usage["tools_ms"] += elapsed
                    if elapsed > usage["slowest_tool_ms"]:
                        usage["slowest_tool"], usage["slowest_tool_ms"] = call.name, elapsed
                    log.info("tool %s error=%s ms=%s", call.name, is_error, elapsed)
                    results.append(ToolResult(call.id, content, is_error))
                session.add_tool_results(results)
            else:
                text = text or "Me tomó demasiados pasos responder. ¿Puedes concretar un poco la pregunta?"
        finally:
            # Una línea JSON por turno para medir costo y tiempos en CloudWatch Logs Insights.
            usage["total_ms"] = _ms(started)
            log.info("llm_usage %s", json.dumps({"provider": getattr(self.llm, "provider", "?"), "tier": tier,
                                                 **usage}))
            garmin = ctx.plan._garmin
            if garmin is not None and hasattr(garmin, "close"):
                garmin.close()
        return text.strip()

    def _history(self) -> list[ChatMessage]:
        state = self.repo.get_conversation()
        msgs = self.repo.messages_since(state.get("anchor"), limit=FIRST_LOAD)
        if len(msgs) > COMPACT_AT:
            msgs = self._compact(state, msgs)
        return [ChatMessage(m["role"], m["text"]) for m in msgs]

    def _compact(self, state: dict, msgs: list[dict]) -> list[dict]:
        """Resume los mensajes viejos y mueve el ancla. Si falla, sigue con los más recientes."""
        old, keep = msgs[:-KEEP_RAW], msgs[-KEEP_RAW:]
        try:
            summary = athlete_profile.summarize_conversation(self.llm, state.get("summary", ""), old)
        except Exception:
            log.exception("No se pudo compactar el historial")
            return msgs[-COMPACT_AT:]
        self.repo.save_conversation({"anchor": keep[0]["ts"], "summary": summary,
                                     "updated": self.today.isoformat()})
        log.info("Historial compactado: %s mensajes resumidos", len(old))
        return keep

    def reply_to_user(self, user_text: str, *, images: list[ImageInput] | None = None,
                      stored_text: str | None = None) -> str:
        """`stored_text` es lo que queda en el historial (p. ej. "[foto] pie"); el modelo ve `user_text`."""
        history = self._history()
        self.repo.add_message("user", stored_text or user_text)
        answer = self.run(user_text, history=history, images=images)
        self.repo.add_message("assistant", answer)
        return answer

    def execute_confirmed(self, pending: dict) -> str:
        """Ejecuta la acción que el atleta confirmó con el botón y deja que el agente le informe."""
        system = self._system()
        ctx = self._context(system, confirmed=True)
        content, is_error = run_tool(ctx, pending["tool"], pending["args"])
        garmin = ctx.plan._garmin
        if garmin is not None and hasattr(garmin, "close"):
            garmin.close()
        note = (f"[El atleta confirmó: {pending.get('summary', pending['tool'])}. "
                f"Resultado{' (error)' if is_error else ''}: {content}] Infórmale en breve qué pasó.")
        return self.reply_to_user(note, stored_text=note)
