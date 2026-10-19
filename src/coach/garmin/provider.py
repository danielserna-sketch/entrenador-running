"""Interfaz de acceso a Garmin.

Hoy la implementa `UnofficialGarmin` (librería `garminconnect`). Si se migra a la API
oficial de Garmin, basta con otra implementación de esta interfaz.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class GarminAuthError(RuntimeError):
    """Los tokens de Garmin expiraron o fueron rechazados: hay que correr scripts/garmin_login.py."""


class GarminProvider(Protocol):
    def activities(self, start: date, end: date) -> list[dict]:
        """Actividades normalizadas (ver mappers.map_activity)."""

    def activity_detail(self, activity_id: int | str) -> dict:
        """Splits/laps y tiempo en zonas de una actividad."""

    def activity_route(self, activity_id: int | str) -> list[tuple[float, float]]:
        """Puntos (lat, lon) del recorrido de una actividad; vacío si no tiene GPS."""

    def daily_wellness(self, day: date) -> dict:
        """Sueño, HRV, FC reposo, Body Battery, estrés y training readiness del día."""

    def create_workout(self, workout: dict) -> str:
        """Crea un workout estructurado (formato Garmin) y devuelve su id."""

    def schedule_workout(self, workout_id: str, day: date) -> None:
        """Agenda un workout existente en el calendario de Garmin."""

    def delete_workout(self, workout_id: str) -> None: ...

    def scheduled_workouts(self, start: date, end: date) -> list[dict]:
        """Entrenamientos agendados en el calendario de Garmin entre dos fechas (ver mappers.map_scheduled)."""

    def unschedule_workout(self, schedule_id: str) -> None:
        """Quita un entrenamiento del calendario sin borrar la plantilla."""

    def reschedule_workout(self, schedule_id: str, day: date) -> None:
        """Mueve un entrenamiento agendado a otra fecha."""

    def performance(self, day: date) -> dict:
        """VO2max, predicciones de carrera, estado de entrenamiento y umbral de lactato (None si no hay dato)."""
