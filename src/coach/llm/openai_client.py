"""Proveedor OpenAI usando el SDK oficial `openai` (Chat Completions + function calling).

Los modelos se configuran con LLM_MODEL_MAIN / LLM_MODEL_FAST; los valores por defecto se
pueden cambiar sin tocar el código.
"""

from __future__ import annotations

import json

from openai import OpenAI

from coach.llm.base import ChatMessage, StepResult, SystemPrompt, Tier, ToolCall, ToolResult, ToolSpec

DEFAULT_MODELS = {"main": "gpt-5", "fast": "gpt-5-mini"}
MAX_TOKENS = 16000


class OpenAILLM:
    provider = "openai"

    def __init__(self, api_key: str, model_main: str = "", model_fast: str = "", client=None):
        self.client = client or OpenAI(api_key=api_key, max_retries=3)
        self.models = {"main": model_main or DEFAULT_MODELS["main"], "fast": model_fast or DEFAULT_MODELS["fast"]}

    @staticmethod
    def _system_text(system: SystemPrompt) -> str:
        # El estático va primero: OpenAI cachea automáticamente prefijos repetidos.
        return system.static + ("\n\n" + system.dynamic if system.dynamic else "")

    def session(self, system: SystemPrompt, history: list[ChatMessage], tools: list[ToolSpec],
                tier: Tier = "main") -> OpenAISession:
        return OpenAISession(self, system, history, tools, tier)

    def complete_json(self, system: SystemPrompt, prompt: str, schema: dict, tier: Tier = "main") -> dict:
        response = self.client.chat.completions.create(
            model=self.models[tier],
            max_completion_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": self._system_text(system)},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "respuesta", "schema": schema, "strict": False}},
        )
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise RuntimeError(f"El modelo rechazó la solicitud: {message.refusal}")
        return json.loads(message.content)


def _openai_content(m: ChatMessage):
    if not m.images:
        return m.text
    parts = [{"type": "text", "text": m.text}]
    parts += [{"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{img.data_b64}"}}
              for img in m.images]
    return parts


class OpenAISession:
    def __init__(self, llm: OpenAILLM, system: SystemPrompt, history: list[ChatMessage],
                 tools: list[ToolSpec], tier: Tier):
        self.llm = llm
        self.model = llm.models[tier]
        self.tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        self.messages: list[dict] = [{"role": "system", "content": llm._system_text(system)}]
        self.messages += [{"role": m.role, "content": _openai_content(m)} for m in history]

    def step(self) -> StepResult:
        kwargs = {"model": self.model, "messages": self.messages, "max_completion_tokens": MAX_TOKENS}
        if self.tools:
            kwargs["tools"] = self.tools
        response = self.llm.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        # prompt_tokens incluye los cacheados; cache_read dice cuántos salieron de la caché.
        details = getattr(response.usage, "prompt_tokens_details", None)
        usage = {"input": response.usage.prompt_tokens, "output": response.usage.completion_tokens,
                 "cache_read": getattr(details, "cached_tokens", 0) or 0}
        if getattr(message, "refusal", None):
            return StepResult(text="", stop_reason="refusal", usage=usage)

        self.messages.append(message.model_dump(exclude_none=True))
        calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        stop = "tool_use" if calls else ("max_tokens" if choice.finish_reason == "length" else "end")
        return StepResult(text=(message.content or "").strip(), tool_calls=calls, stop_reason=stop, usage=usage)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            content = f"ERROR: {r.content}" if r.is_error else r.content
            self.messages.append({"role": "tool", "tool_call_id": r.call_id, "content": content})
