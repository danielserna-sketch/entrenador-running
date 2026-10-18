"""Calendario de Garmin: lectura cruzada con el plan y los puntuales, y cambios de agenda."""

from datetime import date

import pytest

from coach.agent.tools import (
    ToolContext,
    change_garmin_schedule,
    get_performance_metrics,
    get_scheduled_workouts,
)
from coach.garmin.provider import GarminAuthError
from coach.planning.service import PlanService

TODAY = date(2026, 10, 10)  # sábado
MONDAY = date(2026, 10, 12)


class FakeCalendarGarmin:
    def __init__(self, entries, fail=None):
        self.entries, self.fail = entries, fail
        self.unscheduled, self.rescheduled = [], []

    def scheduled_workouts(self, start, end):
        if self.fail:
            raise self.fail
        return [e for e in self.entries if start.isoformat() <= e["date"] <= end.isoformat()]

    def unschedule_workout(self, schedule_id):
        self.unscheduled.append(schedule_id)

    def reschedule_workout(self, schedule_id, day):
        self.rescheduled.append((schedule_id, day.isoformat()))

    def performance(self, day):
        return {"vo2max": {"fecha": "2026-10-09", "vo2max": 52.0},
                "estado_entrenamiento": {"trainingStatusFeedbackPhrase": "PRODUCTIVE_1"}}


def make_ctx(repo, garmin, today=TODAY):
    plan = PlanService(repo, None, None, lambda: garmin, today)
    return ToolContext(repo=repo, plan=plan, garmin_factory=lambda: garmin, today=today)


def entry(workout_id, schedule_id, day, title):
    return {"schedule_id": schedule_id, "workout_id": workout_id, "date": day, "title": title}


@pytest.fixture
def seeded(repo):
    """Un plan con una sesión el martes, un puntual el jueves y una sesión solo en Garmin el sábado."""
    repo.save_week({"week_start": MONDAY.isoformat(), "sessions": [
        {"date": "2026-10-13", "title": "Rodaje Z2", "garmin_workout_id": "W1"}], "planned_tss": 0})
    repo.save_adhoc_workout({"date": "2026-10-15", "title": "Series 6x800"}, "W2")
    return repo


def garmin_calendar():
    return FakeCalendarGarmin([
        entry("W1", "S1", "2026-10-13", "Rodaje Z2"),
        entry("W2", "S2", "2026-10-15", "Series 6x800"),
        entry("W3", "S3", "2026-10-17", "Fondo 14k"),
    ])


def test_scheduled_merges_plan_puntual_and_garmin_only(seeded):
    ctx = make_ctx(seeded, garmin_calendar())
    out = get_scheduled_workouts(ctx, {"start": "2026-10-12", "end": "2026-10-18"})
    origins = {s["titulo"]: s["origen"] for s in out["sesiones"]}
    assert origins == {"Rodaje Z2": "plan", "Series 6x800": "puntual", "Fondo 14k": "solo_garmin"}
    assert all(s["en_garmin"] for s in out["sesiones"])
    assert [s["fecha"] for s in out["sesiones"]] == ["2026-10-13", "2026-10-15", "2026-10-17"]
    assert "aviso" not in out


def test_scheduled_marks_local_session_missing_from_garmin(seeded):
    garmin = FakeCalendarGarmin([entry("W2", "S2", "2026-10-15", "Series 6x800")])
    out = get_scheduled_workouts(make_ctx(seeded, garmin), {"start": "2026-10-12", "end": "2026-10-18"})
    plan_session = next(s for s in out["sesiones"] if s["origen"] == "plan")
    assert plan_session["en_garmin"] is False


def test_scheduled_keeps_local_data_when_garmin_fails(seeded):
    garmin = FakeCalendarGarmin([], fail=RuntimeError("timeout"))
    out = get_scheduled_workouts(make_ctx(seeded, garmin), {"start": "2026-10-12", "end": "2026-10-18"})
    assert len(out["sesiones"]) == 2
    assert "aviso" in out


def test_scheduled_propagates_auth_error(seeded):
    garmin = FakeCalendarGarmin([], fail=GarminAuthError("expired"))
    with pytest.raises(GarminAuthError):
        get_scheduled_workouts(make_ctx(seeded, garmin), {"start": "2026-10-12", "end": "2026-10-18"})


def test_unschedule_removes_only_from_calendar(seeded):
    garmin = garmin_calendar()
    out = change_garmin_schedule(make_ctx(seeded, garmin),
                                 {"date": "2026-10-15", "action": "unschedule"})
    assert out["ok"] and out["del_plan"] is False
    assert garmin.unscheduled == ["S2"]
    assert seeded.adhoc_workouts(date(2026, 10, 15), date(2026, 10, 15))[0]["garmin_workout_id"] == "W2"


def test_reschedule_moves_adhoc_record(seeded):
    garmin = garmin_calendar()
    out = change_garmin_schedule(make_ctx(seeded, garmin),
                                 {"date": "2026-10-15", "action": "reschedule", "new_date": "2026-10-16"})
    assert out["nueva_fecha"] == "2026-10-16"
    assert garmin.rescheduled == [("S2", "2026-10-16")]
    moved = seeded.adhoc_workouts(date(2026, 10, 16), date(2026, 10, 16))
    assert [m["garmin_workout_id"] for m in moved] == ["W2"]
    assert seeded.adhoc_workouts(date(2026, 10, 15), date(2026, 10, 15)) == []


def test_reschedule_plan_session_within_week_updates_plan(seeded):
    garmin = garmin_calendar()
    change_garmin_schedule(make_ctx(seeded, garmin),
                           {"date": "2026-10-13", "action": "reschedule", "new_date": "2026-10-14"})
    week = seeded.get_week(MONDAY)
    assert week["sessions"][0]["date"] == "2026-10-14"
    assert garmin.rescheduled == [("S1", "2026-10-14")]


def test_reschedule_plan_session_to_other_week_is_rejected(seeded):
    garmin = garmin_calendar()
    out = change_garmin_schedule(make_ctx(seeded, garmin),
                                 {"date": "2026-10-13", "action": "reschedule", "new_date": "2026-10-20"})
    assert "modify_session" in out["error"]
    assert garmin.rescheduled == []


def test_change_rejects_past_date_and_missing_workout(seeded):
    ctx = make_ctx(seeded, garmin_calendar())
    assert "pasada" in change_garmin_schedule(ctx, {"date": "2026-10-15", "action": "reschedule",
                                                    "new_date": "2026-10-09"})["error"]
    assert "No hay entrenamiento" in change_garmin_schedule(ctx, {"date": "2026-10-16", "action": "unschedule"})["error"]


def test_performance_reports_missing_metrics(seeded):
    out = get_performance_metrics(make_ctx(seeded, garmin_calendar()), {})
    assert out["vo2max"] == {"fecha": "2026-10-09", "vo2max": 52.0}
    assert "prediccion_carrera_s" not in out
    assert set(out["sin_dato"]) == {"prediccion_carrera_s", "umbral_lactato"}
