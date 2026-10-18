"""Configuración central, leída de variables de entorno.

Los secretos (tokens, API keys) no viajan en variables de entorno: se leen de
Secrets Manager por nombre (ver `secrets.py`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Falta la variable de entorno {name}")
    return value


@dataclass(frozen=True)
class Settings:
    user_id: str = "daniel"
    timezone: str = "America/Bogota"

    table_name: str = "coach"
    bucket_name: str = "coach-data"
    queue_url: str = ""

    # Nombres de secretos en Secrets Manager
    secret_garmin_tokens: str = "coach/garmin-tokens"
    secret_whatsapp: str = "coach/whatsapp"  # {"access_token","app_secret","verify_token","phone_number_id"}
    secret_llm: str = "coach/llm"  # {"anthropic_api_key","openai_api_key"}

    # WhatsApp
    allowed_phone: str = ""  # número del atleta en formato E.164 sin '+', ej. 573001234567
    graph_api_version: str = "v21.0"
    template_name: str = "coach_update"
    template_lang: str = "es"

    # LLM: proveedor por defecto y por tarea
    llm_provider: str = "anthropic"
    llm_provider_proactive: str = ""  # vacío = igual que llm_provider
    llm_model_main: str = ""
    llm_model_fast: str = ""

    # Correo
    report_email: str = ""
    sender_email: str = ""


    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def provider_for(self, task: str) -> str:
        if task == "proactive" and self.llm_provider_proactive:
            return self.llm_provider_proactive
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings(
        user_id=_env("COACH_USER_ID", "daniel"),
        timezone=_env("COACH_TIMEZONE", "America/Bogota"),
        table_name=_env("TABLE_NAME", "coach"),
        bucket_name=_env("BUCKET_NAME", "coach-data"),
        queue_url=_env("QUEUE_URL", ""),
        secret_garmin_tokens=_env("SECRET_GARMIN_TOKENS", "coach/garmin-tokens"),
        secret_whatsapp=_env("SECRET_WHATSAPP", "coach/whatsapp"),
        secret_llm=_env("SECRET_LLM", "coach/llm"),
        allowed_phone=_env("ALLOWED_PHONE", ""),
        graph_api_version=_env("GRAPH_API_VERSION", "v21.0"),
        template_name=_env("WHATSAPP_TEMPLATE", "coach_update"),
        template_lang=_env("WHATSAPP_TEMPLATE_LANG", "es"),
        llm_provider=_env("LLM_PROVIDER", "anthropic"),
        llm_provider_proactive=_env("LLM_PROVIDER_PROACTIVE", ""),
        llm_model_main=_env("LLM_MODEL_MAIN", ""),
        llm_model_fast=_env("LLM_MODEL_FAST", ""),
        report_email=_env("REPORT_EMAIL", ""),
        sender_email=_env("SENDER_EMAIL", ""),
    )
