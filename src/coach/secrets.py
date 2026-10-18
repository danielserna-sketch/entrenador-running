"""Lectura/escritura de secretos JSON en AWS Secrets Manager, con caché por invocación."""

from __future__ import annotations

import json
import time

import boto3

_cache: dict[str, tuple[float, dict]] = {}
_TTL_SECONDS = 300


def _client():
    return boto3.client("secretsmanager")


def get_secret(name: str) -> dict:
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return hit[1]
    raw = _client().get_secret_value(SecretId=name)["SecretString"]
    value = json.loads(raw)
    _cache[name] = (time.time(), value)
    return value


def put_secret(name: str, value: dict) -> None:
    _client().put_secret_value(SecretId=name, SecretString=json.dumps(value))
    _cache[name] = (time.time(), value)
