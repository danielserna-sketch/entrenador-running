"""Tokens de garth guardados en Secrets Manager como {"tokens": "<base64 de garth.dumps()>"}."""

from __future__ import annotations

from coach import secrets
from coach.config import get_settings


def load_tokens() -> str:
    return secrets.get_secret(get_settings().secret_garmin_tokens)["tokens"]


def save_tokens(tokens_b64: str) -> None:
    secrets.put_secret(get_settings().secret_garmin_tokens, {"tokens": tokens_b64})
