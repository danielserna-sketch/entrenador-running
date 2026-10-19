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
        self.graph = f"https://graph.facebook.com/{settings.graph_api_version}"
        self.base = f"{self.graph}/{self.phone_number_id}"
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

    def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> None:
        """Mensaje con hasta 3 botones de respuesta. Cada botón es (id, título ≤ 20 caracteres)."""
        self._post({"to": to, "type": "interactive", "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": bid, "title": title[:20]}}
                                   for bid, title in buttons[:3]]},
        }})

    def upload_media(self, data: bytes, mime_type: str = "image/png") -> str:
        """Sube un archivo a Meta y devuelve su media id (para enviarlo después)."""
        resp = self.http.post(f"{self.base}/media",
                              data={"messaging_product": "whatsapp", "type": mime_type},
                              files={"file": ("imagen", data, mime_type)},
                              headers={"Authorization": f"Bearer {self.token}"})
        if resp.status_code >= 400:
            log.error("WhatsApp upload %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()["id"]

    def send_image(self, to: str, media_id: str, caption: str = "") -> None:
        image = {"id": media_id, **({"caption": caption[:1024]} if caption else {})}
        self._post({"to": to, "type": "image", "image": image})

    def download_media(self, media_id: str) -> bytes:
        """Descarga un archivo recibido: primero pide su URL temporal a Meta y luego la baja."""
        headers = {"Authorization": f"Bearer {self.token}"}
        info = self.http.get(f"{self.graph}/{media_id}", headers=headers)
        info.raise_for_status()
        resp = self.http.get(info.json()["url"], headers=headers)
        resp.raise_for_status()
        return resp.content

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

    def mark_read(self, message_id: str, typing: bool = True) -> None:
        """Marca como leído y muestra "escribiendo…" (se quita al responder o tras ~25 s).
        Si la API rechaza el indicador, solo marca como leído."""
        payload = {"status": "read", "message_id": message_id}
        attempts = [{**payload, "typing_indicator": {"type": "text"}}, payload] if typing else [payload]
        for body in attempts:
            try:
                self._post(body)
                return
            except Exception:  # noqa: BLE001
                log.info("No se pudo marcar como leído %s (indicador=%s)", message_id, "typing_indicator" in body)

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
                button_id = None
                if kind == "interactive":
                    inter = msg.get("interactive", {})
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    text, button_id = reply.get("title"), reply.get("id")
                elif kind == "button":
                    text = msg.get("button", {}).get("text")
                item = {"id": msg["id"], "from": msg["from"], "type": kind, "text": text,
                        "timestamp": msg.get("timestamp")}
                if button_id:
                    item["button_id"] = button_id
                if kind in ("audio", "image"):
                    item["media_id"] = msg[kind]["id"]
                if kind == "image":
                    item["mime_type"] = msg["image"].get("mime_type", "image/jpeg")
                    item["caption"] = msg["image"].get("caption")
                out.append(item)
    return out
