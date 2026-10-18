"""Implementación de GarminProvider con la librería no oficial `garminconnect` (>= 0.3).

Autenticación: los tokens (JSON de `client.dumps()`) se generan una sola vez en local con
`scripts/garmin_login.py` (permite MFA) y se guardan en Secrets Manager. Aquí solo se
reutilizan. El refresh token rota, así que después de cada uso se vuelve a guardar si cambió.
"""

from __future__ import annotations

import logging
from datetime import date

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from coach.garmin import mappers, token_store
from coach.garmin.provider import GarminAuthError

log = logging.getLogger(__name__)


class UnofficialGarmin:
    def __init__(self, tokens: str | None = None):
        self._tokens = tokens or token_store.load_tokens()
        self.api = Garmin()
        try:
            self.api.login(tokenstore=self._tokens)
        except GarminConnectAuthenticationError as e:
            raise GarminAuthError(str(e)) from e
        self._persist_tokens()

    def _persist_tokens(self) -> None:
        current = self.api.client.dumps()
        if current != self._tokens:
            token_store.save_tokens(current)
            self._tokens = current

    def close(self) -> None:
        self._persist_tokens()

    # ---- lectura ------------------------------------------------------------
    def activities(self, start: date, end: date) -> list[dict]:
        raw = self._call(self.api.get_activities_by_date, start.isoformat(), end.isoformat())
        return [mappers.map_activity(a) for a in raw or []]

    def activity_detail(self, activity_id: int | str) -> dict:
        aid = str(activity_id)
        splits = self._call(self.api.get_activity_splits, aid)
        zones = self._call(self.api.get_activity_hr_in_timezones, aid)
        return {"splits": mappers.map_splits(splits), "hr_time_in_zones": mappers.map_hr_zones(zones)}

    def daily_wellness(self, day: date) -> dict:
        d = day.isoformat()
        sleep = self._safe(self.api.get_sleep_data, d)
        hrv = self._safe(self.api.get_hrv_data, d)
        stats = self._safe(self.api.get_stats, d)
        readiness = self._safe(self.api.get_training_readiness, d)
        return mappers.map_daily(day, sleep, hrv, stats, readiness)

    # ---- escritura ----------------------------------------------------------
    def create_workout(self, workout: dict) -> str:
        resp = self._call(self.api.upload_workout, workout)
        return str(resp["workoutId"])

    def schedule_workout(self, workout_id: str, day: date) -> None:
        self._call(self.api.schedule_workout, workout_id, day.isoformat())

    def delete_workout(self, workout_id: str) -> None:
        self._call(self.api.delete_workout, workout_id)

    def unschedule_workout(self, schedule_id: str) -> None:
        self._call(self.api.unschedule_workout, schedule_id)

    def reschedule_workout(self, schedule_id: str, day: date) -> None:
        self._call(self.api.reschedule_workout, schedule_id, day.isoformat())

    def scheduled_workouts(self, start: date, end: date) -> list[dict]:
        raw = self._call(self.api.get_scheduled_workouts_by_period, start.isoformat(), end.isoformat())
        return [mappers.map_scheduled(w) for w in raw or []]

    def performance(self, day: date) -> dict:
        d = day.isoformat()
        return mappers.map_performance(
            max_metrics=self._safe(self.api.get_max_metrics, d),
            race=self._safe(self.api.get_race_predictions),
            status=self._safe(self.api.get_training_status, d),
            lactate=self._safe(lambda: self.api.get_lactate_threshold(latest=True)),
        )

    def update_running_hr_zones(self, threshold_hr: float, hr_zones: list[dict]) -> bool:
        """Carga en Garmin las zonas de FC por umbral láctico.

        Garmin maneja 5 zonas: se mapean Z1-Z4 de Friel y Z5a-Z5c se agrupan en la zona 5.
        """
        current = self._call(self.api.get_heart_rate_zones_v2) or []
        entries = current if isinstance(current, list) else current.get("heartRateZones", [])
        running = next((z for z in entries if str(z.get("sport", "")).upper() == "RUNNING"), None)
        if not running:
            running = next((z for z in entries if str(z.get("sport", "")).upper() == "DEFAULT"), None)
            if not running:
                return False
            running = {**running, "sport": "RUNNING"}
        floors = {z["zone"]: z["min_bpm"] for z in hr_zones}
        running.update({
            "trainingMethod": "LACTATE_THRESHOLD",
            "lactateThresholdHeartRateUsed": round(threshold_hr),
            "zone1Floor": max(floors["Z2"] - 20, 60),
            "zone2Floor": floors["Z2"],
            "zone3Floor": floors["Z3"],
            "zone4Floor": floors["Z4"],
            "zone5Floor": floors["Z5a"],
        })
        self._call(self.api.update_heart_rate_zones, [running])
        return True

    # ---- utilidades ---------------------------------------------------------
    def _call(self, fn, *args):
        try:
            return fn(*args)
        except GarminConnectAuthenticationError as e:
            raise GarminAuthError(str(e)) from e
        except GarminConnectTooManyRequestsError:
            log.warning("Garmin rate limit en %s", getattr(fn, "__name__", fn))
            raise

    def _safe(self, fn, *args):
        """Para métricas opcionales (sin dato ese día o reloj sin soporte)."""
        try:
            return self._call(fn, *args)
        except GarminAuthError:
            raise
        except Exception as e:  # noqa: BLE001
            log.info("Sin dato de %s: %s", getattr(fn, "__name__", fn), e)
            return None
