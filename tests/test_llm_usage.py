"""Registro de uso de tokens por turno (línea base de costo)."""

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from coach.agent.coach import Coach
from coach.llm.base import StepResult
from coach.llm.openai_client import OpenAISession


class OneStepLLM:
    provider = "fake"

    def session(self, system, history, tools, tier="main"):
        return SimpleNamespace(step=lambda: StepResult(text="Hola", usage={"input": 900, "cache_read": 600,
                                                                              "output": 40}),
                               add_tool_results=lambda results: None)


def test_turn_logs_token_usage(repo, caplog):
    coach = Coach(repo=repo, llm=OneStepLLM(), garmin_factory=lambda: None,
                  now=datetime(2026, 10, 18, 9, 0, tzinfo=ZoneInfo("America/Bogota")))
    with caplog.at_level(logging.INFO, logger="coach.agent.coach"):
        assert coach.run("hola") == "Hola"
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("llm_usage"))
    assert json.loads(line.removeprefix("llm_usage ")) == {"provider": "fake", "tier": "main", "steps": 1,
                                                           "input": 900, "cache_read": 600, "output": 40}


def test_openai_reports_cached_tokens():
    usage = SimpleNamespace(prompt_tokens=5000, completion_tokens=80,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=4096))
    message = SimpleNamespace(content="ok", tool_calls=None, refusal=None, model_dump=lambda **kw: {"role": "assistant"})
    response = SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=message, finish_reason="stop")])
    llm = SimpleNamespace(models={"main": "gpt-5"},
                          client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                              create=lambda **kw: response))),
                          _system_text=lambda system: "sys")
    session = OpenAISession(llm, None, [], [], "main")
    assert session.step().usage == {"input": 5000, "output": 80, "cache_read": 4096}
