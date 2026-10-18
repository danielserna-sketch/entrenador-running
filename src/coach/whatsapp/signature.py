"""Verificación de la firma X-Hub-Signature-256 que Meta añade a cada webhook."""

from __future__ import annotations

import hashlib
import hmac


def is_valid(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))
