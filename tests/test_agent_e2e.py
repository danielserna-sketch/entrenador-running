"""Flujo completo con LLM y Garmin simulados: onboarding → plan → Garmin → reporte por correo."""

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import boto3

from coach.agent.coach import Coach
from coach.llm.base import StepResult, ToolCall
from coach.reports import builder, email_sender


class FakeGarmin:
    def __init__(self):
        self.created, self.scheduled, self.deleted = [], [], []

    def activities(self, start, end):
        return []

    def activity_detail(self, activity_id):
        return {"splits": [], "hr_time_in_zones": []}

    def daily_wellness(self, day):
        return {"date": day.isoformat()}

    def create_workout(self, workout):
        self.created.append(workout)
        return str(1000 + len(self.created))

    def schedule_workout(self, workout_id, day):
        self.scheduled.append((workout_id, day.isoformat()))

    def delete_workout(self, workout_id):
        self.deleted.append(workout_id)

    def update_running_hr_zones(self, threshold_hr, zones):
        return True


class ScriptedSession:
    def __init__(self, steps):
        self.steps = steps
        self.results = []

    def step(self):
        return self.steps.pop(0)

    def add_tool_results(self, results):
        self.results.extend(results)


class FakeLLM:
    """Conversación guionada + generador de semanas válido para el validador."""

    provider = "fake"

    def __init__(self, steps):
        self.steps = steps
        self.sessions = []

    def session(self, system, history, tools, tier="main"):
        s = ScriptedSession(self.steps)
        self.sessions.append(s)
        return s

    def complete_json(self, system, prompt, schema, tier="main"):
        monday = date.fromisoformat(re.search(r"lunes (\d{4}-\d{2}-\d{2})", prompt).group(1))
        target = float(re.search(r"TSS objetivo: (\d+)", prompt).group(1))
        need_test = "OBLIGATORIO" in prompt

        def day(offset):
            return (monday + timedelta(days=offset)).isoformat()

        sessions = []
        if need_test:
            sessions.append({"date": day(1), "title": "Test 20 min", "type": "test", "description": "Test",
                             "cadence_focus": False, "steps": [
                                 {"kind": "warmup", "duration_min": 15, "zone": "Z1"},
                                 {"kind": "interval", "duration_min": 20, "zone": "Z4"},
                                 {"kind": "cooldown", "duration_min": 10, "zone": "Z1"}]})
        else:
            sessions.append({"date": day(1), "title": "Rodaje", "type": "easy", "description": "Suave",
                             "cadence_focus": True, "steps": [{"kind": "main", "duration_min": 40, "zone": "Z2"}]})
        used = 35 / 60 * 0.6 + 20 / 60 * 0.95**2 * 100 if need_test else 40 / 60 * 0.75**2 * 100
        remaining = max(target - used, 60)
        mid = remaining * 0.4 / (0.75**2 * 100) * 60
        long = remaining * 0.6 / (0.75**2 * 100) * 60
        sessions.append({"date": day(3), "title": "Rodaje Z2", "type": "easy", "description": "Z2",
                         "cadence_focus": True, "steps": [{"kind": "main", "duration_min": round(mid), "zone": "Z2"}]})
        sessions.append({"date": day(6), "title": "Fondo", "type": "long", "description": "Largo",
                         "cadence_focus": False, "steps": [{"kind": "main", "duration_min": round(long), "zone": "Z2"}]})
        return {"summary": "Semana de base.", "sessions": sessions, "strength_notes": "Core 2x semana"}


def test_onboarding_plan_and_report(repo, monkeypatch):
    garmin = FakeGarmin()
    steps = [
        StepResult(text="", stop_reason="tool_use", tool_calls=[ToolCall("1", "update_athlete_profile", {
            "name": "Daniel", "goal_distance": "half", "race_date": "2027-04-04", "goal_time_s": 5940,
            "best_times": {"10k": 2880}, "days_available": ["martes", "jueves", "domingo"]})]),
        StepResult(text="", stop_reason="tool_use", tool_calls=[ToolCall("2", "generate_training_plan", {})]),
        StepResult(text="¡Tu plan está listo! Te lo envié al correo.", stop_reason="end"),
    ]
    llm = FakeLLM(steps)
    now = datetime(2026, 10, 17, 9, 0, tzinfo=ZoneInfo("America/Bogota"))
    coach = Coach(repo=repo, llm=llm, garmin_factory=lambda: garmin, now=now)

    answer = coach.reply_to_user("Quiero correr la media de abril en sub 1:40")
    assert answer == "¡Tu plan está listo! Te lo envié al correo."

    results = llm.sessions[0].results
    plan_result = json.loads(results[1].content)
    assert not results[1].is_error, results[1].content
    assert plan_result["ok"] and plan_result["assessment"]["half_level"]["name"] == "SUB 1h40"
    assert plan_result["macro"]["total_weeks"] == 24
    assert plan_result["first_week"]["validation_errors"] == []
    assert plan_result["report"]["sent"] is True

    # Primera semana publicada en Garmin, con test de 20 min (no hay zonas todavía).
    assert len(garmin.created) == 3 and garmin.scheduled[0][1] == "2026-10-20"
    assert all(w["workoutName"].startswith("Agents - ") for w in garmin.created)
    assert garmin.created[0]["workoutName"].startswith("Agents - Test")
    assert repo.get_week(date(2026, 10, 19))["sessions"][0]["type"] == "test"

    # Reporte en S3 y mensajes en historial.
    keys = [o["Key"] for o in boto3.client("s3").list_objects_v2(Bucket="coach-test-bucket")["Contents"]]
    assert "reports/plan_v001.html" in keys
    assert [m["role"] for m in repo.recent_messages()] == ["user", "assistant"]


def test_report_html_and_mime(repo, zones):
    repo.update_profile({"name": "Daniel", "goal_distance": "half", "race_date": "2027-04-04",
                         "goal_time_s": 5940})
    repo.save_zones(zones)
    from coach.planning import periodization

    macro = periodization.build_macrocycle(date(2027, 4, 4), date(2026, 10, 19), "half", 180, 4.45)
    macro["changelog"] = [{"date": "2026-10-17", "text": "Plan creado."}]
    repo.save_macro(macro)
    repo.save_week({**macro["weeks"][0], "summary": "Base", "planned_tss": 100, "sessions": [
        {"date": "2026-10-20", "title": "Rodaje", "type": "easy", "steps": [], "tss": 40, "duration_min": 45,
         "main_zone": "Z2", "cadence": {"target_rpm": 82, "metronome_bpm": 164}}]})

    report = builder.build_report(repo, date(2026, 10, 21), "Prueba")
    html = report["html_email"]
    for text in ("Ciclos del plan", "estás aquí", "Tus zonas", "Z5c", "Metrónomo 164 bpm", "Semana 24",
                 "cid:tss", "Plan creado."):
        assert text in html, text
    assert "data:image/png;base64," in report["html_file"]
    mime = email_sender.build_mime(report, "coach@example.com", "atleta@example.com", "Plan")
    raw = mime.as_string()
    assert "Content-ID: <tss>" in raw and 'filename="plan_v001.html"' in raw
