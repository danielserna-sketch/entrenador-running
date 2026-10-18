"""Bucle del agente, independiente del proveedor de LLM."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from coach.agent import prompts
from coach.agent.tools import TOOLS, ToolContext, run_tool
from coach.garmin.provider import GarminProvider
from coach.llm.base import ChatMessage, LLMClient, ToolResult
from coach.planning import periodization
from coach.planning.service import PlanService
from coach.storage.repo import Repo

log = logging.getLogger(__name__)

MAX_STEPS = 12
HISTORY_MESSAGES = 20
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
        week = self.repo.get_week(periodization.monday_of(self.today))
        current = None
        if week:
            current = {"semana": week.get("index"), "fase": week.get("phase_label"), "tipo": week.get("kind"),
                       "sesiones": [{"fecha": s["date"], "titulo": s["title"], "tss": s.get("tss")}
                                    for s in week["sessions"]]}
        return prompts.build_system(self.today, self.now, self.repo.get_profile(), self.repo.get_memory(),
                                    self.repo.get_zones(), current)

    def _context(self, system) -> ToolContext:
        plan = PlanService(self.repo, self.llm, system, self.garmin_factory, self.today)
        return ToolContext(repo=self.repo, plan=plan, garmin_factory=self.garmin_factory, today=self.today)

    def run(self, user_text: str, *, history: list[ChatMessage] | None = None, tier: str = "main") -> str:
        """Ejecuta un turno completo: el modelo puede llamar herramientas hasta dar la respuesta final."""
        system = self._system()
        ctx = self._context(system)
        messages = list(history or []) + [ChatMessage("user", user_text)]
        session = self.llm.session(system, messages, [t.spec for t in TOOLS], tier=tier)  # type: ignore[arg-type]
        text = ""
        try:
            for _ in range(MAX_STEPS):
                step = session.step()
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
            garmin = ctx.plan._garmin
            if garmin is not None and hasattr(garmin, "close"):
                garmin.close()
        return text.strip()

    def reply_to_user(self, user_text: str) -> str:
        history = [ChatMessage(m["role"], m["text"]) for m in self.repo.recent_messages(HISTORY_MESSAGES)]
        self.repo.add_message("user", user_text)
        answer = self.run(user_text, history=history)
        self.repo.add_message("assistant", answer)
        return answer
