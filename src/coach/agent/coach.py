"""Bucle del agente, independiente del proveedor de LLM."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from coach.agent import athlete_profile, prompts
from coach.agent.tools import TOOLS, ToolContext, run_tool
from coach.garmin.provider import GarminProvider
from coach.llm.base import ChatMessage, LLMClient, ToolResult
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


@dataclass
class Coach:
    repo: Repo
    llm: LLMClient
    garmin_factory: Callable[[], GarminProvider]
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()

    def _system(self):
        ficha = athlete_profile.build_ficha(self.repo, self.today).to_prompt()
        summary = athlete_profile.normalize_memory(self.repo.get_memory())["summary"]
        conversation = self.repo.get_conversation().get("summary", "")
        return prompts.build_system(ficha, summary, conversation)

    def _context(self, system) -> ToolContext:
        plan = PlanService(self.repo, self.llm, system, self.garmin_factory, self.today)
        return ToolContext(repo=self.repo, plan=plan, garmin_factory=self.garmin_factory, today=self.today)

    def run(self, user_text: str, *, history: list[ChatMessage] | None = None, tier: str = "main") -> str:
        """Ejecuta un turno completo: el modelo puede llamar herramientas hasta dar la respuesta final."""
        system = self._system()
        ctx = self._context(system)
        messages = list(history or []) + [ChatMessage("user", prompts.turn_context(self.now) + user_text)]
        session = self.llm.session(system, messages, [t.spec for t in TOOLS], tier=tier)  # type: ignore[arg-type]
        text = ""
        usage = {"steps": 0, "input": 0, "cache_read": 0, "output": 0}
        try:
            for _ in range(MAX_STEPS):
                step = session.step()
                usage["steps"] += 1
                for key in ("input", "cache_read", "output"):
                    usage[key] += step.usage.get(key) or 0
                if step.stop_reason == "refusal":
                    return REFUSAL_TEXT
                text = step.text or text
                if not step.tool_calls:
                    break
                results = []
                for call in step.tool_calls:
                    content, is_error = run_tool(ctx, call.name, call.arguments)
                    log.info("tool %s error=%s", call.name, is_error)
                    results.append(ToolResult(call.id, content, is_error))
                session.add_tool_results(results)
            else:
                text = text or "Me tomó demasiados pasos responder. ¿Puedes concretar un poco la pregunta?"
        finally:
            # Una línea JSON por turno para medir costo en CloudWatch Logs Insights.
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

    def reply_to_user(self, user_text: str) -> str:
        history = self._history()
        self.repo.add_message("user", user_text)
        answer = self.run(user_text, history=history)
        self.repo.add_message("assistant", answer)
        return answer
