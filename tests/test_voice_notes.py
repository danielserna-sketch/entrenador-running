import base64
import json
from types import SimpleNamespace
from typing import ClassVar

from coach.handlers import agent_worker
from coach.whatsapp.client import WhatsAppClient, parse_incoming
from coach.whatsapp.transcribe import MAX_VOICE_SECONDS, transcribe_voice


def test_parse_incoming_audio_keeps_media_id():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.a", "from": "573001112233", "type": "audio",
         "audio": {"id": "media-1", "mime_type": "audio/ogg; codecs=opus", "voice": True}}]}}]}]}
    msgs = parse_incoming(payload)
    assert msgs == [{"id": "wamid.a", "from": "573001112233", "type": "audio", "text": None,
                     "timestamp": None, "media_id": "media-1"}]


def test_download_media_resolves_url_then_fetches_bytes():
    calls = []

    class Resp:
        def __init__(self, data=None, content=b""):
            self._data, self.content = data, content

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    class FakeHttp:
        def get(self, url, headers):
            calls.append((url, headers))
            return Resp({"url": "https://cdn.example/x"}) if "cdn" not in url else Resp(content=b"OGG")

    wa = WhatsAppClient(creds={"access_token": "t", "phone_number_id": "1"}, http=FakeHttp())
    assert wa.download_media("media-1") == b"OGG"
    assert calls[0][0].endswith("/media-1") and calls[0][1] == {"Authorization": "Bearer t"}
    assert calls[1] == ("https://cdn.example/x", {"Authorization": "Bearer t"})


def test_transcribe_voice_returns_text_and_duration():
    seen = {}

    class FakeTranscriptions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(text="  me duele la rodilla ", duration=12.5)

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    text, seconds = transcribe_voice(b"ogg", client=client)
    assert text == "me duele la rodilla" and seconds == 12.5
    assert seen["language"] == "es" and seen["file"][0] == "nota.ogg"


class FakeWA:
    instances: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.sent = []
        self.read = []
        self.downloads = []
        FakeWA.instances.append(self)

    def mark_read(self, message_id):
        self.read.append(message_id)

    def download_media(self, media_id):
        self.downloads.append(media_id)
        if media_id == "broken":
            raise RuntimeError("Meta caído")
        return b"ogg"

    def send_text(self, to, text):
        self.sent.append(text)


class FakeCoach:
    def __init__(self):
        self.asked = []
        self.images = []
        self.stored = []

    def reply_to_user(self, text, *, images=None, stored_text=None):
        self.asked.append(text)
        self.images.append(images or [])
        self.stored.append(stored_text or text)
        return "respuesta"


def _run(monkeypatch, body, transcript=("me duele la rodilla", 20.0)):
    FakeWA.instances.clear()
    coach = FakeCoach()
    monkeypatch.setattr(agent_worker, "WhatsAppClient", FakeWA)
    monkeypatch.setattr(agent_worker, "make_coach", lambda task: coach)
    monkeypatch.setattr(agent_worker, "transcribe_voice", lambda audio: transcript)
    event = {"Records": [{"messageId": "m", "body": json.dumps({"from": "573001112233", **body})}]}
    result = agent_worker.handler(event, None)
    return result, FakeWA.instances[0], coach


def test_voice_note_is_transcribed_and_answered(monkeypatch):
    result, wa, coach = _run(monkeypatch, {"id": "w1", "type": "audio", "media_id": "m1", "text": None})
    assert result == {"batchItemFailures": []}
    assert wa.downloads == ["m1"]
    assert len(coach.asked) == 1
    assert coach.asked[0].startswith(agent_worker.VOICE_PROMPT)
    assert coach.asked[0].endswith("me duele la rodilla")
    assert wa.sent == ["🎙️ Entendí: me duele la rodilla", "respuesta"]


def test_voice_note_too_long_is_not_sent_to_agent(monkeypatch):
    _, wa, coach = _run(monkeypatch, {"id": "w2", "type": "audio", "media_id": "m2", "text": None},
                        transcript=("hola", MAX_VOICE_SECONDS + 1))
    assert coach.asked == [] and len(wa.sent) == 1 and "3 minutos" in wa.sent[0]


def test_voice_note_download_failure_tells_user(monkeypatch):
    _, wa, coach = _run(monkeypatch, {"id": "w3", "type": "audio", "media_id": "broken", "text": None})
    assert coach.asked == [] and "No pude escuchar" in wa.sent[0]


def test_unsupported_media_still_gets_text_only_reply(monkeypatch):
    _, wa, coach = _run(monkeypatch, {"id": "w4", "type": "sticker", "text": None})
    assert coach.asked == [] and wa.sent == [agent_worker.NON_TEXT_MSG]


def test_photo_is_sent_to_agent_once_and_stored_as_caption(monkeypatch):
    _, wa, coach = _run(monkeypatch, {"id": "w5", "type": "image", "media_id": "img1",
                                      "mime_type": "image/png", "caption": "¿qué marca es esta?"})
    assert wa.downloads == ["img1"]
    assert coach.asked == ["¿qué marca es esta?"]
    assert coach.stored == ["[foto] ¿qué marca es esta?"]
    image = coach.images[0][0]
    assert image.media_type == "image/png" and image.data_b64 == base64.b64encode(b"ogg").decode()
    assert wa.sent == ["respuesta"]


def test_photo_too_big_is_refused(monkeypatch):
    big = b"x" * (agent_worker.IMAGE_MAX_BYTES + 1)
    monkeypatch.setattr(FakeWA, "download_media", lambda self, media_id: big)
    _, wa, coach = _run(monkeypatch, {"id": "w6", "type": "image", "media_id": "img2", "caption": ""})
    assert coach.asked == [] and wa.sent == [agent_worker.IMAGE_TOO_BIG_MSG]
