"""Transcripción de notas de voz de WhatsApp con Whisper (OpenAI).

Siempre usa la clave de OpenAI, aunque el chat use Anthropic: Anthropic no ofrece transcripción.
El audio no se guarda: solo viaja a OpenAI y vuelve como texto.
"""

from __future__ import annotations

from openai import OpenAI

from coach import secrets
from coach.config import get_settings

MODEL = "whisper-1"
MAX_VOICE_SECONDS = 180  # notas más largas se rechazan para acotar costo y latencia


def transcribe_voice(audio: bytes, client: OpenAI | None = None) -> tuple[str, float]:
    """Devuelve (texto, duración en segundos)."""
    if client is None:
        key = secrets.get_secret(get_settings().secret_llm)["openai_api_key"]
        client = OpenAI(api_key=key)
    result = client.audio.transcriptions.create(
        model=MODEL,
        file=("nota.ogg", audio, "audio/ogg"),  # WhatsApp envía notas de voz en Ogg/Opus
        language="es",
        response_format="verbose_json",
    )
    return (result.text or "").strip(), float(result.duration or 0)
