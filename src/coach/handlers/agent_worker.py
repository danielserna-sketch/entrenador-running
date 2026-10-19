"""Lambda consumidora de la cola SQS FIFO: procesa los mensajes del atleta y responde por WhatsApp."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid

from coach.agent.coach import Coach
from coach.garmin.provider import GarminAuthError
from coach.handlers.common import make_coach
from coach.llm.base import ImageInput
from coach.whatsapp import window
from coach.whatsapp.client import WhatsAppClient
from coach.whatsapp.transcribe import MAX_VOICE_SECONDS, transcribe_voice

log = logging.getLogger()

GARMIN_AUTH_MSG = ("⚠️ Tu sesión de Garmin expiró. Corre `scripts/garmin_login.py` en tu computador "
                   "para renovarla; mientras tanto trabajo con los datos que ya tengo.")
NON_TEXT_MSG = "Por ahora entiendo mensajes de texto, notas de voz y fotos 🙂. ¿Me lo escribes?"
ERROR_MSG = "Tuve un problema procesando tu mensaje. Inténtalo de nuevo en un momento."
VOICE_ERROR_MSG = "No pude escuchar tu nota de voz. ¿Me la escribes?"
VOICE_TOO_LONG_MSG = "Tu nota dura más de 3 minutos. ¿Me la resumes en texto o la mandas en partes?"
VOICE_PROMPT = ("[Nota de voz transcrita. Ya le mostraste al atleta lo que entendiste: respóndela directamente, "
                "sin pedirle que confirme la transcripción.] ")
IMAGE_MAX_BYTES = 4_500_000  # Anthropic acepta hasta 5 MB por imagen
IMAGE_TOO_BIG_MSG = "La foto pesa demasiado. ¿Me la mandas más pequeña?"
PENDING_TTL_SECONDS = 600
CANCEL_MSG = "Cancelado. No hice ningún cambio."
EXPIRED_MSG = "Esa confirmación ya venció o ya se usó. Si sigues queriendo el cambio, pídemelo de nuevo."
IMAGE_WINDOW_MSG = "Fuera de la ventana de 24 h no puedo enviar imágenes por WhatsApp."


def _coach(wa: WhatsAppClient, to: str) -> Coach:
    """Coach con los canales de WhatsApp: botones de confirmación y envío de gráficas."""
    coach = make_coach("chat")
    coach.request_confirmation = lambda tool, args, summary: _request_confirmation(wa, to, coach, tool, args, summary)
    coach.send_image = lambda png, caption: _send_image(wa, to, coach, png, caption)
    return coach


def _request_confirmation(wa: WhatsAppClient, to: str, coach: Coach, tool: str, args: dict, summary: str) -> None:
    pending_id = uuid.uuid4().hex
    coach.repo.put_pending(pending_id, {"tool": tool, "args": args, "summary": summary,
                                        "creado": int(time.time())}, PENDING_TTL_SECONDS)
    wa.send_buttons(to, f"{summary}\n\n¿Lo confirmas?",
                    [(f"confirm:{pending_id}", "Confirmar"), (f"cancel:{pending_id}", "Cancelar")])


def _send_image(wa: WhatsAppClient, to: str, coach: Coach, png: bytes, caption: str) -> str | None:
    if not window.is_open(coach.repo.last_user_message_ts()):
        return IMAGE_WINDOW_MSG
    wa.send_image(to, wa.upload_media(png, "image/png"), caption)
    return None


def handle_button(wa: WhatsAppClient, coach: Coach, to: str, button_id: str) -> None:
    action, _, pending_id = button_id.partition(":")
    # take_pending borra el registro en la misma operación: un segundo toque ya no encuentra nada.
    pending = coach.repo.take_pending(pending_id)
    if action == "cancel":
        wa.send_text(to, CANCEL_MSG)
        return
    if not pending or time.time() - pending.get("creado", 0) > PENDING_TTL_SECONDS:
        wa.send_text(to, EXPIRED_MSG)
        return
    wa.send_text(to, coach.execute_confirmed(pending))


def handle_photo(wa: WhatsAppClient, coach: Coach, to: str, body: dict) -> None:
    data = wa.download_media(body["media_id"])
    if len(data) > IMAGE_MAX_BYTES:
        wa.send_text(to, IMAGE_TOO_BIG_MSG)
        return
    image = ImageInput(body.get("mime_type") or "image/jpeg", base64.b64encode(data).decode())
    caption = (body.get("caption") or "").strip()
    # La foto solo existe en este turno: el historial guarda el pie, nunca los bytes.
    answer = coach.reply_to_user(caption or "Te mandé una foto sin texto.", images=[image],
                                 stored_text=f"[foto] {caption}".strip())
    wa.send_text(to, answer)


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
            coach = _coach(wa, to)
            if body.get("button_id"):
                handle_button(wa, coach, to, body["button_id"])
                continue
            kind = body.get("type")
            if kind == "image":
                handle_photo(wa, coach, to, body)
                continue
            if kind == "audio":
                transcript = handle_voice(wa, to, body["media_id"])
                if transcript is None:
                    continue
                wa.send_text(to, coach.reply_to_user(VOICE_PROMPT + transcript, stored_text=transcript))
                continue
            if not body.get("text"):
                wa.send_text(to, NON_TEXT_MSG)
                continue
            wa.send_text(to, coach.reply_to_user(body["text"]))
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
