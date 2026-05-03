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


def test_send_message_retries_plain_on_400():
    """When Markdown parsing trips Telegram's 400, retry with no parse_mode."""
    bad_resp = MagicMock()
    bad_resp.status_code = 400
    bad_resp.raise_for_status = MagicMock()  # not called on retry path
    good_resp = MagicMock()
    good_resp.status_code = 200
    good_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    good_resp.raise_for_status = MagicMock()
    with patch("hermes.telegram.client.httpx.post",
               side_effect=[bad_resp, good_resp]) as p:
        c = TelegramClient(token="123:A", chat_id=42)
        c.send_message("__weird path/foo.py__")
    assert p.call_count == 2
    # First call had parse_mode, second did not.
    first_body = p.call_args_list[0].kwargs["json"]
    second_body = p.call_args_list[1].kwargs["json"]
    assert "parse_mode" in first_body
    assert "parse_mode" not in second_body


def test_send_message_propagates_non_400_errors():
    """A 500 should still raise (we only fall back on 400 parse failures)."""
    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.raise_for_status.side_effect = Exception("server error")
    with patch("hermes.telegram.client.httpx.post", return_value=err_resp):
        c = TelegramClient(token="123:A", chat_id=42)
        import pytest
        with pytest.raises(Exception, match="server error"):
            c.send_message("hello")
