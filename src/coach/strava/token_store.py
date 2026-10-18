"""Tokens OAuth de Strava en Secrets Manager:
{"client_id", "client_secret", "refresh_token", "access_token", "expires_at"}."""

from __future__ import annotations

from coach import secrets
from coach.config import get_settings


def load_tokens() -> dict:
    return dict(secrets.get_secret(get_settings().secret_strava))


def save_tokens(tokens: dict) -> None:
    secrets.put_secret(get_settings().secret_strava, tokens)
