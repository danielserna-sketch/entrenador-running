import hashlib
import hmac
import json

from coach.whatsapp import signature, window
from coach.whatsapp.client import parse_incoming, split_message, template_param


def test_signature():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert signature.is_valid(body, good, "secret")
    assert not signature.is_valid(body, good, "otro")
    assert not signature.is_valid(body, None, "secret")


def test_window():
    now = 1_000_000.0
    assert window.is_open(now - 3600, now)
    assert not window.is_open(now - 24 * 3600, now)
    assert not window.is_open(None, now)


def test_split_and_template_param():
    long = "\n\n".join(["x" * 3000, "y" * 3000])
    chunks = split_message(long)
    assert len(chunks) == 2 and all(len(c) <= 4000 for c in chunks)
    assert "\n" not in template_param("hola\nmundo\t   de      verdad")


def test_parse_incoming():
    payload = {"entry": [{"changes": [{"value": {
        "messages": [{"id": "wamid.1", "from": "573001112233", "type": "text", "text": {"body": "hola"}}],
        "statuses": [{"id": "x", "status": "delivered"}]}}]}]}
    msgs = parse_incoming(payload)
    assert msgs == [{"id": "wamid.1", "from": "573001112233", "type": "text", "text": "hola", "timestamp": None}]


def test_webhook_handler(aws, monkeypatch):
    import boto3

    from coach.handlers import webhook

    sqs = boto3.client("sqs")
    url = sqs.create_queue(QueueName="in.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    monkeypatch.setenv("QUEUE_URL", url)
    from coach.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(webhook.secrets, "get_secret",
                        lambda name: {"app_secret": "s", "verify_token": "vt", "access_token": "t",
                                      "phone_number_id": "1"})
    webhook._sqs = None

    ok = webhook.handler({"requestContext": {"http": {"method": "GET"}},
                          "queryStringParameters": {"hub.mode": "subscribe", "hub.verify_token": "vt",
                                                    "hub.challenge": "123"}}, None)
    assert ok["body"] == "123"

    def post(sender, msg_id):
        body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
            {"id": msg_id, "from": sender, "type": "text", "text": {"body": "hola"}}]}}]}]})
        sig = "sha256=" + hmac.new(b"s", body.encode(), hashlib.sha256).hexdigest()
        return webhook.handler({"requestContext": {"http": {"method": "POST"}}, "body": body,
                                "headers": {"X-Hub-Signature-256": sig}}, None)

    assert post("573001112233", "m1")["statusCode"] == 200
    assert post("573001112233", "m1")["statusCode"] == 200  # duplicado: no se encola
    assert post("579999999999", "m2")["statusCode"] == 200  # número no permitido
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)["Messages"]
    assert len(msgs) == 1
    bad = webhook.handler({"requestContext": {"http": {"method": "POST"}}, "body": "{}",
                           "headers": {"x-hub-signature-256": "sha256=bad"}}, None)
    assert bad["statusCode"] == 401
    get_settings.cache_clear()


def test_mark_read_sends_typing_indicator_and_falls_back():
    from coach.whatsapp.client import WhatsAppClient

    sent = []

    class FlakyHttp:
        def post(self, url, json, headers):
            sent.append(json)

            class R:
                status_code = 400 if "typing_indicator" in json else 200
                text = "bad"

                def json(self):
                    return {}

                def raise_for_status(self):
                    raise RuntimeError("400")

            return R()

    wa = WhatsAppClient(creds={"access_token": "t", "phone_number_id": "1"}, http=FlakyHttp())
    wa.mark_read("wamid.1")
    assert sent[0]["typing_indicator"] == {"type": "text"} and "typing_indicator" not in sent[1]
    assert sent[1]["status"] == "read" and len(sent) == 2


def test_worker_warmup_does_nothing(monkeypatch):
    from coach.handlers import agent_worker

    monkeypatch.setattr(agent_worker, "WhatsAppClient", lambda: (_ for _ in ()).throw(AssertionError("no")))
    assert agent_worker.handler({"warmup": True}, None) == {"warmup": True}
