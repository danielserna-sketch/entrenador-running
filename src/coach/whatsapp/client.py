"""Cliente mínimo de la WhatsApp Cloud API de Meta (Graph API)."""

from __future__ import annotations

import logging
import re

import httpx

from coach import secrets
from coach.config import get_settings
from coach.whatsapp import window

log = logging.getLogger(__name__)

MAX_TEXT = 4000  # límite de Meta: 4096
MAX_TEMPLATE_PARAM = 900


class WhatsAppClient:
    def __init__(self, creds: dict | None = None, http: httpx.Client | None = None):
        settings = get_settings()
        creds = creds or secrets.get_secret(settings.secret_whatsapp)
        self.token = creds["access_token"]
        self.phone_number_id = creds["phone_number_id"]
        self.base = f"https://graph.facebook.com/{settings.graph_api_version}/{self.phone_number_id}"
        self.template_name = settings.template_name
        self.template_lang = settings.template_lang
        self.http = http or httpx.Client(timeout=20)

    def _post(self, payload: dict) -> dict:
        resp = self.http.post(f"{self.base}/messages", json={"messaging_product": "whatsapp", **payload},
                              headers={"Authorization": f"Bearer {self.token}"})
        if resp.status_code >= 400:
            log.error("WhatsApp API %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()

    def send_text(self, to: str, text: str) -> None:
        for chunk in split_message(text):
            self._post({"to": to, "type": "text", "text": {"body": chunk, "preview_url": False}})

    def send_template(self, to: str, summary: str) -> None:
        """Plantilla utility aprobada, con un único parámetro de cuerpo ({{1}})."""
        self._post({
            "to": to,
            "type": "template",
            "template": {
                "name": self.template_name,
                "language": {"code": self.template_lang},
                "components": [{"type": "body", "parameters": [
                    {"type": "text", "text": template_param(summary)}]}],
            },
        })

    def mark_read(self, message_id: str) -> None:
        try:
            self._post({"status": "read", "message_id": message_id})
        except Exception:  # noqa: BLE001
            log.info("No se pudo marcar como leído %s", message_id)

    def send_smart(self, to: str, text: str, short_summary: str, last_user_ts: float | None) -> str:
        """Texto libre si la ventana de 24 h está abierta; si no, plantilla con un resumen."""
        if window.is_open(last_user_ts):
            self.send_text(to, text)
            return "text"
        self.send_template(to, short_summary)
        return "template"


def split_message(text: str, limit: int = MAX_TEXT) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def template_param(text: str) -> str:
    """Meta no permite saltos de línea, tabs ni más de 4 espacios seguidos en parámetros."""
    clean = re.sub(r"[\n\t]+", " · ", text)
    clean = re.sub(r" {4,}", "   ", clean).strip()
    return clean[:MAX_TEMPLATE_PARAM]


def parse_incoming(payload: dict) -> list[dict]:
    """Extrae los mensajes entrantes del webhook. Ignora estados (sent/delivered/read)."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                kind = msg.get("type")
                text = None
                if kind == "text":
                    text = msg["text"]["body"]
                elif kind == "interactive":
                    inter = msg.get("interactive", {})
                    text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title")
                elif kind == "button":
                    text = msg.get("button", {}).get("text")
                out.append({"id": msg["id"], "from": msg["from"], "type": kind, "text": text,
                            "timestamp": msg.get("timestamp")})
    return out
