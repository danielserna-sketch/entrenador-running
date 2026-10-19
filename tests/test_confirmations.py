"""Botones de confirmación, fotos entrantes y gráficas salientes."""

import json
from datetime import date
from types import SimpleNamespace

from coach.agent import tools
from coach.agent.tools import ToolContext, run_tool
from coach.handlers import agent_worker
from coach.llm.anthropic_client import _anthropic_content, _merge_roles
from coach.llm.base import ChatMessage, ImageInput
from coach.llm.openai_client import _openai_content
from coach.whatsapp.client import WhatsAppClient, parse_incoming

TODAY = date(2026, 10, 18)


def _payload(message):
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}


def test_parse_button_reply_keeps_id():
    msg = parse_incoming(_payload({"id": "w1", "from": "57", "type": "interactive", "interactive": {
        "type": "button_reply", "button_reply": {"id": "confirm:abc", "title": "Confirmar"}}}))[0]
    assert msg["text"] == "Confirmar" and msg["button_id"] == "confirm:abc"


def test_parse_image_keeps_media_mime_and_caption():
    msg = parse_incoming(_payload({"id": "w2", "from": "57", "type": "image",
                                   "image": {"id": "m9", "mime_type": "image/png", "caption": "mira"}}))[0]
    assert msg["media_id"] == "m9" and msg["mime_type"] == "image/png" and msg["caption"] == "mira"
    assert msg["text"] is None and "button_id" not in msg


class RecordingHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))

        class R:
            status_code = 200
            text = ""

            def json(self):
                return {"id": "media-7"}

        return R()


def test_send_buttons_payload_shape():
    http = RecordingHttp()
    wa = WhatsAppClient(creds={"access_token": "t", "phone_number_id": "1"}, http=http)
    wa.send_buttons("57", "Mover el fondo", [("confirm:x", "Confirmar"), ("cancel:x", "Cancelar")])
    body = http.calls[0][1]["json"]
    assert body["type"] == "interactive" and body["interactive"]["type"] == "button"
    assert body["interactive"]["action"]["buttons"][0] == {"type": "reply",
                                                           "reply": {"id": "confirm:x", "title": "Confirmar"}}


def test_upload_and_send_image():
    http = RecordingHttp()
    wa = WhatsAppClient(creds={"access_token": "t", "phone_number_id": "1"}, http=http)
    media_id = wa.upload_media(b"png", "image/png")
    wa.send_image("57", media_id, "Tu cadencia")
    upload_url, upload = http.calls[0]
    assert upload_url.endswith("/1/media") and upload["files"]["file"][2] == "image/png"
    assert http.calls[1][1]["json"]["image"] == {"id": "media-7", "caption": "Tu cadencia"}


def _ctx(**kwargs):
    repo = SimpleNamespace(activities=lambda start, end: [])
    plan = SimpleNamespace(_garmin=None, garmin=None)
    return ToolContext(repo=repo, plan=plan, garmin_factory=None, today=TODAY, **kwargs)


def test_garmin_change_waits_for_confirmation(monkeypatch):
    asked = []
    touched = []
    monkeypatch.setitem(tools.TOOLS_BY_NAME, "modify_session",
                        tools.Tool(tools.TOOLS_BY_NAME["modify_session"].spec, lambda ctx, a: touched.append(a)))
    ctx = _ctx(request_confirmation=lambda name, args, summary: asked.append((name, summary)))
    content, is_error = run_tool(ctx, "modify_session", {"date": "2026-10-25", "action": "remove",
                                                         "resumen": "Quitar la serie del domingo"})
    assert not is_error and "pendiente" in content
    assert asked == [("modify_session", "Quitar la serie del domingo")] and touched == []


def test_garmin_change_refused_without_channel(monkeypatch):
    content, is_error = run_tool(_ctx(), "change_garmin_schedule", {"date": "2026-10-25", "action": "unschedule",
                                                                    "resumen": "x"})
    assert is_error and "confirmación" in content


def test_confirmed_change_runs_handler(monkeypatch):
    ran = []
    monkeypatch.setitem(tools.TOOLS_BY_NAME, "modify_session",
                        tools.Tool(tools.TOOLS_BY_NAME["modify_session"].spec, lambda ctx, a: ran.append(a) or {"ok": 1}))
    content, is_error = run_tool(_ctx(confirmed=True), "modify_session", {"date": "2026-10-25", "action": "remove"})
    assert not is_error and ran and json.loads(content) == {"ok": 1}


def test_pending_action_is_taken_only_once(repo):
    repo.put_pending("p1", {"tool": "modify_session", "args": {"date": "2026-10-25"}}, 600)
    assert repo.take_pending("p1")["tool"] == "modify_session"
    assert repo.take_pending("p1") is None


class FakeWA:
    def __init__(self):
        self.sent = []

    def send_text(self, to, text):
        self.sent.append(text)


class FakeRepo:
    def __init__(self, pending):
        self.pending = pending
        self.taken = []

    def take_pending(self, pid):
        self.taken.append(pid)
        return self.pending


class FakeButtonCoach:
    def __init__(self, pending):
        self.repo = FakeRepo(pending)
        self.executed = []

    def execute_confirmed(self, pending):
        self.executed.append(pending)
        return "Listo, quité la serie."


def test_confirm_button_executes_once_and_reports():
    import time

    wa, coach = FakeWA(), FakeButtonCoach({"tool": "modify_session", "args": {}, "summary": "x",
                                          "creado": int(time.time())})
    agent_worker.handle_button(wa, coach, "57", "confirm:p1")
    assert coach.executed and wa.sent == ["Listo, quité la serie."]


def test_cancel_button_changes_nothing():
    wa, coach = FakeWA(), FakeButtonCoach({"tool": "modify_session", "args": {}, "creado": 0})
    agent_worker.handle_button(wa, coach, "57", "cancel:p1")
    assert coach.executed == [] and wa.sent == [agent_worker.CANCEL_MSG]


def test_expired_confirmation_does_not_execute():
    wa, coach = FakeWA(), FakeButtonCoach({"tool": "modify_session", "args": {}, "creado": 0})
    agent_worker.handle_button(wa, coach, "57", "confirm:p1")
    assert coach.executed == [] and wa.sent == [agent_worker.EXPIRED_MSG]


def test_second_tap_finds_nothing():
    wa, coach = FakeWA(), FakeButtonCoach(None)
    agent_worker.handle_button(wa, coach, "57", "confirm:p1")
    assert coach.executed == [] and wa.sent == [agent_worker.EXPIRED_MSG]


def test_images_only_in_current_turn_message():
    image = ImageInput("image/png", "AAA")
    assert _openai_content(ChatMessage("user", "hola")) == "hola"
    parts = _openai_content(ChatMessage("user", "mira", images=[image]))
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    blocks = _anthropic_content(ChatMessage("user", "mira", images=[image]))
    assert blocks[0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}
    assert blocks[1] == {"type": "text", "text": "mira"}


def test_merge_roles_keeps_images_when_joining_user_messages():
    merged = _merge_roles([{"role": "user", "content": [{"type": "text", "text": "a"}]},
                           {"role": "user", "content": "b"}])
    assert merged == [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]


def test_send_chart_reports_missing_data_and_sends_when_available(monkeypatch):
    sent = []
    ctx = _ctx(send_image=lambda png, caption: sent.append((png, caption)))
    assert "error" in tools.send_chart(ctx, {"kind": "fitness"})

    from coach.analytics import load
    from coach.reports import charts

    ctx.repo.activities = lambda start, end: [{"date": "2026-10-10", "sport": "running"}]
    monkeypatch.setattr(load, "fitness_series", lambda acts, start, end: [{"d": i} for i in range(10)])
    monkeypatch.setattr(charts, "fitness", lambda series: b"png-bytes")
    assert tools.send_chart(ctx, {"kind": "fitness"}) == {"ok": True, "enviada": "fitness"}
    assert sent[0][0] == b"png-bytes" and "forma" in sent[0][1]


def test_send_chart_without_channel_is_refused():
    assert "error" in tools.send_chart(_ctx(), {"kind": "cadence"})


def _card(route):
    return {"title": "Rodaje suave", "date_label": "dom 18 oct", "distance_km": 6.1, "pace": "6:44",
            "duration_min": 41, "avg_hr": 129, "zones": [{"zone": 1, "seconds": 2340}, {"zone": 2, "seconds": 120}],
            "route": route, "comment": "Z1 controlada y ritmo parejo."}


def test_workout_card_renders_png_with_and_without_route():
    from coach.reports.workout_card import render_workout_card

    route = [(4.6, -74.08), (4.61, -74.07), (4.615, -74.075), (4.612, -74.079)]
    for points in (route, []):
        png = render_workout_card(_card(points))
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000


def test_last_workout_card_is_sent_with_route(monkeypatch):
    sent = []
    ctx = _ctx(send_image=lambda png, caption: sent.append((png, caption)))
    ctx.repo.activities = lambda start, end: [{"activity_id": "a1", "date": "2026-10-18", "name": "Rodaje suave",
                                               "distance_km": 6.1, "duration_min": 41, "avg_pace_s_per_km": 404,
                                               "avg_hr": 129}]
    ctx.repo.get_activity = lambda aid: {"detail": {"hr_time_in_zones": [{"zone": 1, "seconds": 2340}]}}
    ctx.plan.garmin = SimpleNamespace(activity_route=lambda aid: [(4.6, -74.08), (4.61, -74.07)])
    assert tools.share_last_workout_card(ctx, {"comment": "Buen ritmo"}) == {"ok": True, "actividad": "Rodaje suave"}
    assert sent[0][0][:4] == b"\x89PNG" and sent[0][1] == "Tu último entreno"


def test_workout_card_survives_missing_gps(monkeypatch):
    sent = []
    ctx = _ctx(send_image=lambda png, caption: sent.append(png))
    ctx.repo.activities = lambda start, end: [{"activity_id": "a2", "date": "2026-10-18", "name": "Fondo",
                                               "distance_km": 12, "duration_min": 70, "avg_pace_s_per_km": 350}]
    ctx.repo.get_activity = lambda aid: None

    def no_gps(aid):
        raise RuntimeError("sin GPS")

    ctx.plan.garmin = SimpleNamespace(activity_route=no_gps)
    assert tools.share_last_workout_card(ctx, {"comment": "Bien"})["ok"] is True and sent
