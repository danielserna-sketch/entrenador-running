"""Tipos neutrales e interfaz común para los proveedores de LLM (Anthropic, OpenAI).

El agente solo habla con esta interfaz. Cada proveedor mantiene internamente su propio
formato de mensajes durante un turno (así se conservan, por ejemplo, los bloques de
razonamiento de Claude entre llamadas a herramientas).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Tier = Literal["main", "fast"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    text: str


@dataclass
class SystemPrompt:
    """`static` es estable entre llamadas (se cachea); `dynamic` cambia (fecha, perfil, estado)."""

    static: str
    dynamic: str = ""


@dataclass
class StepResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"  # end | tool_use | max_tokens | refusal
    usage: dict = field(default_factory=dict)


class LLMSession(Protocol):
    def step(self) -> StepResult:
        """Una llamada al modelo con el estado actual de la conversación."""

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Agrega los resultados de las herramientas pedidas en el último paso."""


class LLMClient(Protocol):
    provider: str

    def session(self, system: SystemPrompt, history: list[ChatMessage], tools: list[ToolSpec],
                tier: Tier = "main") -> LLMSession: ...

    def complete_json(self, system: SystemPrompt, prompt: str, schema: dict, tier: Tier = "main") -> dict:
        """Respuesta única que cumple el JSON Schema dado."""
