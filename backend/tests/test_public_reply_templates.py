from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.database import Database
from app.webhooks import (
    FORWARD_ACCEPTED,
    FORWARD_FAILED,
    PUBLIC_REPLY_TEMPLATES,
    CommentEvent,
    choose_public_reply_template,
    process_comment_event,
    validate_public_reply_template,
    validate_public_reply_templates,
)


def create_test_product(database: Database) -> dict:
    return database.create_product(
        {
            "product_name": "테스트 상품",
            "purchase_link": "https://example.com/product",
            "trigger_phrase": "링크",
            "photo_url": "/uploads/test.jpg",
            "ig_media_id": "media-1",
            "ig_permalink": "https://instagram.com/reel/test",
        }
    )


def test_all_public_reply_templates_include_dm_and_request_inbox_notice():
    validated = validate_public_reply_templates(PUBLIC_REPLY_TEMPLATES)
    assert len(validated) == len(PUBLIC_REPLY_TEMPLATES)
    assert len(validated) >= 5
    for template in validated:
        assert "DM" in template or "dm" in template or "디엠" in template
        assert "요청함" in template or "메시지 요청함" in template
        assert "http://" not in template
        assert "https://" not in template


def test_public_reply_template_validation_rejects_invalid_values():
    with pytest.raises(ValueError, match="empty_public_reply_template"):
        validate_public_reply_template(" ")
    with pytest.raises(ValueError, match="public_reply_dm_notice_required"):
        validate_public_reply_template("메시지 요청함을 확인해주세요.")
    with pytest.raises(ValueError, match="public_reply_request_inbox_notice_required"):
        validate_public_reply_template("DM으로 보내드렸어요.")
    with pytest.raises(ValueError, match="public_reply_link_forbidden"):
        validate_public_reply_template("DM으로 보냈어요. 요청함 확인: https://example.com")


def test_public_reply_random_selection_is_injectable():
    selected = choose_public_reply_template(chooser=lambda values: values[-1])
    assert selected == PUBLIC_REPLY_TEMPLATES[-1]


def test_forwarded_comment_sends_public_reply_once_and_no_legacy_dm(settings, monkeypatch):
    database = Database(settings.db_path)
    database.initialize()
    create_test_product(database)
    instagram = AsyncMock()

    async def accepted(_event):
        return FORWARD_ACCEPTED

    monkeypatch.setattr("app.webhooks._forward_verified_event", accepted)
    event = CommentEvent("comment-forwarded", "media-1", "링크", "user-1", "tester")

    asyncio.run(process_comment_event(event, database=database, instagram=instagram))
    asyncio.run(process_comment_event(event, database=database, instagram=instagram))

    assert instagram.send_private_reply.await_count == 0
    assert instagram.send_public_reply.await_count == 1
    reply = instagram.send_public_reply.await_args.args[1]
    assert reply in PUBLIC_REPLY_TEMPLATES
    assert "DM" in reply or "디엠" in reply
    assert "요청함" in reply

    log = database.list_dm_logs()[0]
    assert log["status"] == "ignored"
    assert log["error_message"] == "private_reply_delegated_to_autocard"
    assert log["reply_status"] == "sent"


def test_forward_failure_blocks_dm_and_public_reply(settings, monkeypatch):
    database = Database(settings.db_path)
    database.initialize()
    create_test_product(database)
    instagram = AsyncMock()

    async def failed(_event):
        return FORWARD_FAILED

    monkeypatch.setattr("app.webhooks._forward_verified_event", failed)
    event = CommentEvent("comment-forward-failed", "media-1", "링크", "user-1", "tester")

    asyncio.run(process_comment_event(event, database=database, instagram=instagram))

    assert instagram.send_private_reply.await_count == 0
    assert instagram.send_public_reply.await_count == 0
    log = database.list_dm_logs()[0]
    assert log["status"] == "failed"
    assert log["error_message"] == "autocard_forward_failed"
    assert log["reply_status"] == "skipped"
