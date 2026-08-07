from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import replace

import httpx
import pytest

from app.config import ConfigurationError, Settings
from app.forwarding import AutocardForwarder, event_payload, sign
from app.main import create_app
from app.security import verify_meta_signature
from app.webhooks import CommentEvent, process_comment_event


def test_meta_signature_requires_secret_and_matches_raw_body():
    body = b'{"entry":[]}'
    secret = "meta-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, signature, secret)
    assert not verify_meta_signature(body, signature, None)
    assert not verify_meta_signature(body, None, secret)
    assert not verify_meta_signature(body + b" ", signature, secret)


def test_dedicated_verify_token_is_not_admin_key(monkeypatch, tmp_path):
    monkeypatch.setenv("IG_ACCESS_TOKEN", "token")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "account")
    monkeypatch.setenv("ADMIN_APP_KEY", "admin")
    monkeypatch.setenv("META_APP_SECRET", "secret")
    monkeypatch.delenv("META_WEBHOOK_VERIFY_TOKEN", raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_forward_signature_and_minimal_payload():
    event = CommentEvent("comment-1", "media-1", "링크", "user-1", "tester")
    payload = event_payload(event, account_id="account-1", raw_event_hash="a" * 64)
    assert set(payload) == {
        "source_project", "instagram_account_id", "instagram_comment_id", "instagram_media_id",
        "instagram_user_id", "username", "text", "event_time", "raw_event_hash", "field",
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = sign("forward-secret", "100", raw)
    assert signature == "sha256=" + hmac.new(b"forward-secret", b"100." + raw, hashlib.sha256).hexdigest()


def test_forwarder_posts_signed_raw_body():
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(202, json={"status": "pending"})
    transport = httpx.MockTransport(handler)
    forwarder = AutocardForwarder(
        endpoint="https://autocard.example",
        secret="forward-secret",
        account_id="account-1",
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )
    event = CommentEvent("comment-1", "media-1", "링크", "user-1", "tester")
    result = asyncio.run(forwarder.forward(event, raw_event_hash="b" * 64))
    assert result.ok
    request = captured[0]
    timestamp = request.headers["X-Autocard-Timestamp"]
    assert request.headers["X-Autocard-Signature"] == sign("forward-secret", timestamp, request.content)
    assert request.headers["X-Autocard-Event-Id"].startswith("ig-comment:comment-1:")
    assert request.url.path == "/api/internal/instagram/comment-events"


def test_forward_failure_does_not_block_legacy_processing(settings, monkeypatch):
    from app.database import Database

    database = Database(settings.db_path)
    database.initialize()
    database.create_product({
        "product_name": "테스트 상품",
        "purchase_link": "https://example.com/product",
        "trigger_phrase": "링크",
        "photo_url": "/uploads/test.jpg",
        "ig_media_id": "media-1",
        "ig_permalink": "https://instagram.com/reel/test",
    })
    monkeypatch.setenv("AUTOCARD_INTERNAL_BASE_URL", "https://autocard.example")
    monkeypatch.setenv("WEBHOOK_FORWARD_SECRET", "forward-secret")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "account-1")

    async def fail_forward(self, *args, **kwargs):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(AutocardForwarder, "forward", fail_forward)

    class Instagram:
        def __init__(self):
            self.dm = 0
            self.reply = 0
        async def send_private_reply(self, comment_id, message):
            self.dm += 1
        async def send_public_reply(self, comment_id, message):
            self.reply += 1

    instagram = Instagram()
    asyncio.run(process_comment_event(CommentEvent("comment-1", "media-1", "링크"), database=database, instagram=instagram))
    assert instagram.dm == 1
    assert instagram.reply == 1


def test_webhook_invalid_signature_rejected_and_valid_signature_returns_200(settings):
    app = create_app(settings)
    body = json.dumps({"object": "instagram", "entry": []}, separators=(",", ":")).encode()
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        invalid = client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "Content-Type": "application/json"})
        signature = "sha256=" + hmac.new(settings.meta_app_secret.encode(), body, hashlib.sha256).hexdigest()
        valid = client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"})
    assert invalid.status_code == 403
    assert valid.status_code == 200
    assert valid.json()["comment_events"] == 0
