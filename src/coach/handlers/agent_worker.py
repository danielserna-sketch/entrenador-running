"""Lambda consumidora de la cola SQS FIFO: procesa los mensajes del atleta y responde por WhatsApp."""

from __future__ import annotations

import json
import logging

from coach.garmin.provider import GarminAuthError
from coach.handlers.common import make_coach
from coach.whatsapp.client import WhatsAppClient
from coach.whatsapp.transcribe import MAX_VOICE_SECONDS, transcribe_voice

log = logging.getLogger()

GARMIN_AUTH_MSG = ("⚠️ Tu sesión de Garmin expiró. Corre `scripts/garmin_login.py` en tu computador "
                   "para renovarla; mientras tanto trabajo con los datos que ya tengo.")
NON_TEXT_MSG = "Por ahora solo entiendo mensajes de texto y notas de voz 🙂. ¿Me lo escribes?"
ERROR_MSG = "Tuve un problema procesando tu mensaje. Inténtalo de nuevo en un momento."
VOICE_ERROR_MSG = "No pude escuchar tu nota de voz. ¿Me la escribes?"
VOICE_TOO_LONG_MSG = "Tu nota dura más de 3 minutos. ¿Me la resumes en texto o la mandas en partes?"


def handle_voice(wa: WhatsAppClient, to: str, media_id: str) -> str | None:
    """Transcribe la nota y avisa al atleta qué entendió. Devuelve el texto, o None si ya respondió."""
    try:
        transcript, seconds = transcribe_voice(wa.download_media(media_id))
    except Exception:
        log.exception("Nota de voz no transcrita")
        wa.send_text(to, VOICE_ERROR_MSG)
        return None
    if seconds > MAX_VOICE_SECONDS:
        wa.send_text(to, VOICE_TOO_LONG_MSG)
        return None
    if not transcript:
        wa.send_text(to, VOICE_ERROR_MSG)
        return None
    wa.send_text(to, f"🎙️ Entendí: {transcript}")
    return transcript


def handler(event, context):
    if event.get("warmup"):
        # Ping programado: solo mantiene la Lambda caliente (los imports ya se cargaron).
        return {"warmup": True}
    wa = WhatsAppClient()
    failures = []
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        to = body["from"]
        try:
            wa.mark_read(body["id"])
            text = body.get("text")
            if body.get("type") == "audio":
                text = handle_voice(wa, to, body["media_id"])
                if text is None:
                    continue
            if not text:
                wa.send_text(to, NON_TEXT_MSG)
                continue
            answer = make_coach("chat").reply_to_user(text)
            wa.send_text(to, answer)
        except GarminAuthError:
            log.exception("Garmin auth")
            wa.send_text(to, GARMIN_AUTH_MSG)
        except Exception:
            log.exception("Error procesando mensaje")
            try:
                wa.send_text(to, ERROR_MSG)
            except Exception:  # noqa: BLE001
                failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
