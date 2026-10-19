"""Acceso a DynamoDB (single-table) y S3.

Esquema: PK = "USER#<id>", SK por tipo de entidad:
  PROFILE, MEMORY, META#last_user_msg, META#zones
  ACTIVITY#<yyyy-mm-dd>#<activity_id>
  DAILY#<yyyy-mm-dd>
  MACRO (macrociclo vigente)
  PLAN#<yyyy-mm-dd lunes>  (microciclo semanal)
  NOTE#<iso ts>
  MSG#<iso ts>  (historial, con TTL)
  ALERT#<tipo>#<yyyy-mm-dd>
  REPORT#<n>
  DEDUP#<whatsapp message id> (con TTL)
  STRAVA_ACTIVITY#<yyyy-mm-dd>#<strava id>  (historial de Strava, aparte de ACTIVITY# de Garmin)
  META#strava_sync, META#strava_records
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from coach.config import get_settings

MSG_TTL_DAYS = 90


def to_dynamo(value: Any) -> Any:
    """Convierte floats a Decimal (DynamoDB no acepta float) de forma recursiva."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def from_dynamo(value: Any) -> Any:
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class Repo:
    def __init__(self, table=None, s3=None, user_id: str | None = None):
        settings = get_settings()
        self.table = table or boto3.resource("dynamodb").Table(settings.table_name)
        self.s3 = s3 or boto3.client("s3")
        self.bucket = settings.bucket_name
        self.pk = f"USER#{user_id or settings.user_id}"

    # ---- genéricos -------------------------------------------------------
    def put(self, sk: str, data: dict, ttl_days: int | None = None) -> None:
        item = {"PK": self.pk, "SK": sk, **to_dynamo(data)}
        if ttl_days:
            item["ttl"] = int(time.time()) + ttl_days * 86400
        self.table.put_item(Item=item)

    def get(self, sk: str) -> dict | None:
        item = self.table.get_item(Key={"PK": self.pk, "SK": sk}).get("Item")
        return _strip(item) if item else None

    def query_prefix(self, prefix: str, *, start: str | None = None, end: str | None = None,
                     newest_first: bool = False, limit: int | None = None, with_sk: bool = False) -> list[dict]:
        if start or end:
            cond = Key("PK").eq(self.pk) & Key("SK").between(
                f"{prefix}{start or ''}", f"{prefix}{end or ''}￿"
            )
        else:
            cond = Key("PK").eq(self.pk) & Key("SK").begins_with(prefix)
        kwargs: dict[str, Any] = {"KeyConditionExpression": cond, "ScanIndexForward": not newest_first}
        if limit:
            kwargs["Limit"] = limit
        items: list[dict] = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend({**_strip(i), "sk": i["SK"]} if with_sk else _strip(i) for i in resp["Items"])
            if limit and len(items) >= limit:
                return items[:limit]
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def put_if_absent(self, sk: str, data: dict, ttl_days: int | None = None) -> bool:
        """Escribe solo si no existe. Devuelve False si ya existía (útil para deduplicar)."""
        item = {"PK": self.pk, "SK": sk, **to_dynamo(data)}
        if ttl_days:
            item["ttl"] = int(time.time()) + ttl_days * 86400
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(SK)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    # ---- acciones pendientes de confirmación ----------------------------
    def put_pending(self, pending_id: str, data: dict, ttl_seconds: int) -> None:
        item = {"PK": self.pk, "SK": f"PENDING#{pending_id}", **to_dynamo(data),
                "ttl": int(time.time()) + ttl_seconds}
        self.table.put_item(Item=item)

    def take_pending(self, pending_id: str) -> dict | None:
        """Lo borra y lo devuelve en una sola operación: solo el primer toque lo obtiene."""
        resp = self.table.delete_item(Key={"PK": self.pk, "SK": f"PENDING#{pending_id}"},
                                      ReturnValues="ALL_OLD")
        item = resp.get("Attributes")
        return _strip(item) if item else None

    # ---- perfil, memoria, zonas -----------------------------------------
    def get_profile(self) -> dict:
        return self.get("PROFILE") or {}

    def update_profile(self, changes: dict) -> dict:
        profile = self.get_profile()
        profile.update({k: v for k, v in changes.items() if v is not None})
        self.put("PROFILE", profile)
        return profile

    def get_memory(self) -> dict:
        return self.get("MEMORY") or {"facts": [], "summary": ""}

    def save_memory(self, memory: dict) -> None:
        self.put("MEMORY", memory)

    def get_zones(self) -> dict | None:
        return self.get("META#zones")

    def save_zones(self, zones: dict) -> None:
        self.put("META#zones", zones)

    # ---- datos de entrenamiento -----------------------------------------
    def save_activity(self, activity: dict) -> bool:
        """Guarda la actividad. Devuelve True si es nueva."""
        sk = f"ACTIVITY#{activity['date']}#{activity['activity_id']}"
        is_new = self.get(sk) is None
        self.put(sk, activity)
        return is_new

    def activities(self, start: date, end: date) -> list[dict]:
        return self.query_prefix("ACTIVITY#", start=start.isoformat(), end=end.isoformat())

    def get_activity(self, activity_id: str | int) -> dict | None:
        for a in self.query_prefix("ACTIVITY#", newest_first=True, limit=200):
            if str(a.get("activity_id")) == str(activity_id):
                return a
        return None

    def save_daily(self, day: dict) -> None:
        self.put(f"DAILY#{day['date']}", day)

    def dailies(self, start: date, end: date) -> list[dict]:
        return self.query_prefix("DAILY#", start=start.isoformat(), end=end.isoformat())

    def add_note(self, note: dict) -> None:
        self.put(f"NOTE#{datetime.now(UTC).isoformat()}", note)

    def notes(self, since: date) -> list[dict]:
        return self.query_prefix("NOTE#", start=since.isoformat())

    # ---- Strava (historial) ----------------------------------------------
    def save_strava_activity(self, activity: dict) -> bool:
        """Guarda o actualiza una actividad de Strava sin perder el detalle ya traído. True si es nueva."""
        sk = f"STRAVA_ACTIVITY#{activity['date']}#{activity['activity_id']}"
        current = self.get(sk)
        if current and current.get("detailed") and not activity.get("detailed"):
            activity = {**current, **{k: v for k, v in activity.items() if v is not None}}
        self.put(sk, activity)
        return current is None

    def strava_activities(self, start: date | None = None, end: date | None = None) -> list[dict]:
        return self.query_prefix("STRAVA_ACTIVITY#", start=start.isoformat() if start else None,
                                 end=end.isoformat() if end else None)

    def get_strava_activity(self, day: str, activity_id: str) -> dict | None:
        return self.get(f"STRAVA_ACTIVITY#{day}#{activity_id}")

    def get_strava_sync(self) -> dict:
        return self.get("META#strava_sync") or {"listed_until": 0, "pending": [], "backfill_done": False}

    def save_strava_sync(self, state: dict) -> None:
        self.put("META#strava_sync", state)

    def get_records(self) -> dict | None:
        return self.get("META#strava_records")

    def save_records(self, records: dict) -> None:
        self.put("META#strava_records", records)

    # ---- plan -------------------------------------------------------------
    def get_macro(self) -> dict | None:
        return self.get("MACRO")

    def save_macro(self, macro: dict) -> None:
        self.put("MACRO", macro)

    def get_week(self, monday: date) -> dict | None:
        return self.get(f"PLAN#{monday.isoformat()}")

    def save_week(self, week: dict) -> None:
        self.put(f"PLAN#{week['week_start']}", week)

    def weeks(self) -> list[dict]:
        return self.query_prefix("PLAN#")

    # ---- entrenamientos puntuales (fuera del plan semanal) -----------------
    def save_adhoc_workout(self, session: dict, workout_id: str) -> None:
        self.put(f"ADHOC#{session['date']}#{workout_id}", {**session, "garmin_workout_id": workout_id})

    def adhoc_workouts(self, start: date, end: date) -> list[dict]:
        return self.query_prefix("ADHOC#", start=start.isoformat(), end=end.isoformat())

    def delete_adhoc_workout(self, day: str, workout_id: str) -> None:
        self.table.delete_item(Key={"PK": self.pk, "SK": f"ADHOC#{day}#{workout_id}"})

    # ---- conversación ----------------------------------------------------
    def add_message(self, role: str, text: str) -> None:
        self.put(f"MSG#{datetime.now(UTC).isoformat()}", {"role": role, "text": text}, ttl_days=MSG_TTL_DAYS)
        if role == "user":
            self.put("META#last_user_msg", {"ts": time.time()})

    def recent_messages(self, limit: int = 20) -> list[dict]:
        return list(reversed(self.query_prefix("MSG#", newest_first=True, limit=limit)))

    def messages_since(self, anchor: str | None, limit: int = 60) -> list[dict]:
        """Mensajes con su marca de tiempo ('ts'), desde el ancla incluida; sin ancla, los últimos `limit`.
        El ancla puede ser una fecha (YYYY-MM-DD) o la marca de tiempo de un mensaje."""
        if anchor:
            items = self.query_prefix("MSG#", start=anchor, with_sk=True)
        else:
            items = list(reversed(self.query_prefix("MSG#", newest_first=True, limit=limit, with_sk=True)))
        return [{**{k: v for k, v in i.items() if k != "sk"}, "ts": i["sk"].removeprefix("MSG#")} for i in items]

    def get_conversation(self) -> dict:
        """Ancla del historial y resumen de los mensajes anteriores a ella."""
        return self.get("META#conversation") or {}

    def save_conversation(self, state: dict) -> None:
        self.put("META#conversation", state)

    def last_user_message_ts(self) -> float | None:
        meta = self.get("META#last_user_msg")
        return float(meta["ts"]) if meta else None

    # ---- alertas y reportes ----------------------------------------------
    def alert_sent_recently(self, kind: str, since: date) -> bool:
        return bool(self.query_prefix(f"ALERT#{kind}#", start=since.isoformat()))

    def mark_alert(self, kind: str, day: date, detail: dict) -> None:
        self.put(f"ALERT#{kind}#{day.isoformat()}", detail, ttl_days=60)

    def next_report_number(self) -> int:
        last = self.query_prefix("REPORT#", newest_first=True, limit=1)
        return (int(last[0]["number"]) + 1) if last else 1

    def save_report_meta(self, number: int, meta: dict) -> None:
        self.put(f"REPORT#{number:05d}", {"number": number, **meta})

    def last_report_meta(self) -> dict | None:
        last = self.query_prefix("REPORT#", newest_first=True, limit=1)
        return last[0] if last else None

    # ---- S3 ---------------------------------------------------------------
    def put_object(self, key: str, body: bytes | str, content_type: str = "application/json") -> None:
        if isinstance(body, str):
            body = body.encode()
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)


def _strip(item: dict) -> dict:
    return from_dynamo({k: v for k, v in item.items() if k not in ("PK", "SK", "ttl")})
