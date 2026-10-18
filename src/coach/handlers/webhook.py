"""Lambda detrás de API Gateway (HTTP API v2): webhook de WhatsApp.

GET  → verificación del webhook (hub.challenge).
POST → valida firma, filtra por número permitido, deduplica y encola en SQS FIFO.
Responde rápido (Meta reintenta si no recibe 200 a tiempo); el trabajo pesado lo hace agent_worker.
"""

from __future__ import annotations

import base64
import json
import logging

import boto3

from coach import secrets
from coach.config import get_settings
from coach.storage.repo import Repo
from coach.whatsapp import signature
from coach.whatsapp.client import parse_incoming

log = logging.getLogger()
log.setLevel(logging.INFO)

_sqs = None


def _resp(status: int, body: str = "") -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "text/plain"}, "body": body}


def handler(event, context):
    settings = get_settings()
    creds = secrets.get_secret(settings.secret_whatsapp)
    method = event.get("requestContext", {}).get("http", {}).get("method", "POST")

    if method == "GET":
        params = event.get("queryStringParameters") or {}
        if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == creds["verify_token"]:
            return _resp(200, params.get("hub.challenge", ""))
        return _resp(403, "forbidden")

    raw = event.get("body") or ""
    raw_bytes = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not signature.is_valid(raw_bytes, headers.get("x-hub-signature-256"), creds["app_secret"]):
        log.warning("Firma inválida en webhook")
        return _resp(401, "invalid signature")

    payload = json.loads(raw_bytes)
    repo = Repo()
    global _sqs
    _sqs = _sqs or boto3.client("sqs")
    for msg in parse_incoming(payload):
        if settings.allowed_phone and msg["from"] != settings.allowed_phone:
            log.info("Mensaje ignorado de número no permitido")
            continue
        if not repo.put_if_absent(f"DEDUP#{msg['id']}", {"seen": True}, ttl_days=2):
            continue
        _sqs.send_message(
            QueueUrl=settings.queue_url,
            MessageBody=json.dumps({"kind": "user_message", **msg}),
            MessageGroupId=msg["from"],
            MessageDeduplicationId=msg["id"],
        )
    return _resp(200, "ok")
