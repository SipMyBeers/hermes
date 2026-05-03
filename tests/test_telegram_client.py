from unittest.mock import patch, MagicMock
from hermes.telegram.client import TelegramClient


def test_send_message_posts_to_correct_url():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.telegram.client.httpx.post", return_value=fake_resp) as p:
        c = TelegramClient(token="123:A", chat_id=42)
        c.send_message("hi")
    assert p.call_args.args[0].endswith("/bot123:A/sendMessage")
    body = p.call_args.kwargs["json"]
    assert body["chat_id"] == 42
    assert body["text"] == "hi"


def test_get_updates_returns_results():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": True, "result": [{"update_id": 1, "message": {}}]}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.telegram.client.httpx.get", return_value=fake_resp):
        c = TelegramClient(token="123:A", chat_id=42)
        u = c.get_updates(offset=0, timeout=1)
    assert u == [{"update_id": 1, "message": {}}]
