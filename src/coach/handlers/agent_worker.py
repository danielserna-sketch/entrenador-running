"""Lambda consumidora de la cola SQS FIFO: procesa los mensajes del atleta y responde por WhatsApp."""

from __future__ import annotations

import json
import logging

from coach.garmin.provider import GarminAuthError
from coach.handlers.common import make_coach
from coach.whatsapp.client import WhatsAppClient

log = logging.getLogger()

GARMIN_AUTH_MSG = ("⚠️ Tu sesión de Garmin expiró. Corre `scripts/garmin_login.py` en tu computador "
                   "para renovarla; mientras tanto trabajo con los datos que ya tengo.")
NON_TEXT_MSG = "Por ahora solo entiendo mensajes de texto 🙂. ¿Me lo escribes?"
ERROR_MSG = "Tuve un problema procesando tu mensaje. Inténtalo de nuevo en un momento."


def handler(event, context):
    wa = WhatsAppClient()
    failures = []
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        to = body["from"]
        try:
            wa.mark_read(body["id"])
            if not body.get("text"):
                wa.send_text(to, NON_TEXT_MSG)
                continue
            answer = make_coach("chat").reply_to_user(body["text"])
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
