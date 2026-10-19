"""Proveedor Anthropic (Claude) usando el SDK oficial `anthropic`.

- Caché de prompt: herramientas + system estático (persona y metodología) quedan en el prefijo
  cacheado; el system dinámico (ficha del atleta y resúmenes) tiene un segundo punto de caché.
- Fallback del lado del servidor ante rechazos (`fallbacks: "default"`) para Sonnet/Opus.
- Los bloques de la respuesta se agregan tal cual al historial del turno para conservar
  el razonamiento entre llamadas a herramientas.
"""

from __future__ import annotations

import json
import logging

import anthropic

from coach.llm.base import ChatMessage, StepResult, SystemPrompt, Tier, ToolCall, ToolResult, ToolSpec

log = logging.getLogger(__name__)

DEFAULT_MODELS = {"main": "claude-sonnet-5-5", "fast": "claude-haiku-5-5"}
DEFAULT_EFFORT = {"main": "medium", "fast": "low"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key: str, model_main: str = "", model_fast: str = "", client=None):
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.models = {"main": model_main or DEFAULT_MODELS["main"], "fast": model_fast or DEFAULT_MODELS["fast"]}

    def _system_blocks(self, system: SystemPrompt) -> list[dict]:
        blocks = [{"type": "text", "text": system.static, "cache_control": {"type": "ephemeral"}}]
        if system.dynamic:
            # Segundo punto de caché: la ficha del atleta cambia poco entre turnos.
            blocks.append({"type": "text", "text": system.dynamic, "cache_control": {"type": "ephemeral"}})
        return blocks

    def _create(self, tier: Tier, **kwargs):
        model = self.models[tier]
        output_config = {"effort": DEFAULT_EFFORT[tier], **kwargs.pop("output_config", {})}
        # Haiku no tiene fallback del lado del servidor.
        if "haiku" not in model:
            return self.client.beta.messages.create(
                model=model, max_tokens=MAX_TOKENS, output_config=output_config,
                betas=[FALLBACK_BETA], fallbacks="default", **kwargs,
            )
        return self.client.messages.create(model=model, max_tokens=MAX_TOKENS, output_config=output_config,
                                           **kwargs)

    def session(self, system: SystemPrompt, history: list[ChatMessage], tools: list[ToolSpec],
                tier: Tier = "main") -> AnthropicSession:
        return AnthropicSession(self, system, history, tools, tier)

    def complete_json(self, system: SystemPrompt, prompt: str, schema: dict, tier: Tier = "main") -> dict:
        response = self._create(
            tier,
            system=self._system_blocks(system),
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("El modelo rechazó la solicitud")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)


class AnthropicSession:
    def __init__(self, llm: AnthropicLLM, system: SystemPrompt, history: list[ChatMessage],
                 tools: list[ToolSpec], tier: Tier):
        self.llm = llm
        self.tier = tier
        self.system = llm._system_blocks(system)
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                      for t in tools]
        self.messages: list[dict] = _merge_roles([{"role": m.role, "content": _anthropic_content(m)}
                                                  for m in history])

    def step(self) -> StepResult:
        kwargs = {"system": self.system, "messages": self.messages}
        if self.tools:
            kwargs["tools"] = self.tools
        response = self.llm._create(self.tier, **kwargs)
        usage = {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
            "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }
        if response.stop_reason == "refusal":
            return StepResult(text="", stop_reason="refusal", usage=usage)

        self.messages.append({"role": "assistant", "content": response.content})
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
                 for b in response.content if b.type == "tool_use"]
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(response.stop_reason, "end")
        return StepResult(text=text, tool_calls=calls, stop_reason=stop, usage=usage)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # Todos los resultados en un único mensaje de usuario.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]})


def _anthropic_content(m: ChatMessage):
    if not m.images:
        return m.text
    # Las imágenes van antes del texto, como recomienda la documentación.
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.data_b64}}
              for img in m.images]
    return blocks + [{"type": "text", "text": m.text}]


def _as_blocks(content) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _merge_roles(messages: list[dict]) -> list[dict]:
    """La API exige alternar user/assistant y empezar por user."""
    merged: list[dict] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            prev, new = merged[-1]["content"], m["content"]
            if isinstance(prev, str) and isinstance(new, str):
                merged[-1]["content"] = prev + "\n\n" + new
            else:
                merged[-1]["content"] = _as_blocks(prev) + _as_blocks(new)
        else:
            merged.append(dict(m))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged
