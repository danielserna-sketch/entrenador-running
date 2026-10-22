"""El resumen de las 5:00 sincroniza Garmin antes de escribir, sin repetir análisis de actividades."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from coach.handlers import garmin_sync, proactive, proactive_jobs


def test_morning_brief_syncs_garmin_first_and_skips_analysis(monkeypatch):
    calls = []
    monkeypatch.setattr(garmin_sync, "handler", lambda event, context: calls.append(("sync", event)) or {"ok": True})
    monkeypatch.setattr(proactive_jobs, "make_coach", lambda task: SimpleNamespace(
        repo=MagicMock(get_profile=MagicMock(return_value={"x": 1}))))
    monkeypatch.setattr(proactive, "morning_brief", lambda coach: calls.append(("brief", None)) or "hola")
    monkeypatch.setattr(proactive, "deliver", lambda coach, text: "whatsapp")

    result = proactive_jobs.handler({"job": "morning_brief"}, None)

    assert [name for name, _ in calls] == ["sync", "brief"]
    assert calls[0][1]["skip_analysis"] is True
    assert result == {"ok": True, "job": "morning_brief", "delivery": "whatsapp"}


def test_morning_brief_still_sends_when_sync_fails(monkeypatch):
    def boom(event, context):
        raise RuntimeError("garmin caído")

    monkeypatch.setattr(garmin_sync, "handler", boom)
    monkeypatch.setattr(proactive_jobs, "make_coach", lambda task: SimpleNamespace(
        repo=MagicMock(get_profile=MagicMock(return_value={"x": 1}))))
    monkeypatch.setattr(proactive, "morning_brief", lambda coach: "hola")
    monkeypatch.setattr(proactive, "deliver", lambda coach, text: "whatsapp")

    result = proactive_jobs.handler({"job": "morning_brief"}, None)

    assert result["delivery"] == "whatsapp"
