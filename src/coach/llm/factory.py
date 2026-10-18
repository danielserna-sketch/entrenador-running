"""Crea el cliente LLM según la configuración (LLM_PROVIDER y, por tarea, LLM_PROVIDER_PROACTIVE)."""

from __future__ import annotations

from coach import secrets
from coach.config import get_settings
from coach.llm.base import LLMClient


def get_llm(task: str = "chat") -> LLMClient:
    settings = get_settings()
    provider = settings.provider_for(task)
    keys = secrets.get_secret(settings.secret_llm)
    if provider == "anthropic":
        from coach.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(keys["anthropic_api_key"], settings.llm_model_main, settings.llm_model_fast)
    if provider == "openai":
        from coach.llm.openai_client import OpenAILLM

        return OpenAILLM(keys["openai_api_key"], settings.llm_model_main, settings.llm_model_fast)
    raise ValueError(f"LLM_PROVIDER desconocido: {provider} (usa 'anthropic' u 'openai')")
