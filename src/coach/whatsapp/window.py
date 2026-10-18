"""Regla de la ventana de servicio de 24 h de WhatsApp.

Dentro de las 24 h desde el último mensaje del usuario se puede enviar texto libre.
Fuera de ella solo se permiten plantillas aprobadas por Meta.
"""

from __future__ import annotations

import time

WINDOW_SECONDS = 24 * 3600
SAFETY_MARGIN_SECONDS = 10 * 60


def is_open(last_user_message_ts: float | None, now: float | None = None) -> bool:
    if last_user_message_ts is None:
        return False
    now = now or time.time()
    return now - last_user_message_ts < WINDOW_SECONDS - SAFETY_MARGIN_SECONDS
