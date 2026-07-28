from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.webhooks import (
    PUBLIC_REPLY_TEMPLATES,
    CommentEvent,
    extract_comment_events,
    format_product_number,
    process_comment_event,
    trigger_matches,
)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "jjabtree-api"


def test_admin_requires_key(client):
    response = client.get("/api/admin/products")
    assert response.status_code == 401


def test_webhook_verification(client):
    response = client.get(
        "/api/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 200
    assert response.text == "12345"


def test_trigger_matching_is_case_and_space_tolerant():
    assert trigger_matches("  링 크   주세요 ", "링 크")
    assert trigger_matches("LINK 부탁해요", "link")
    assert not trigger_matches("가격 알려줘", "링크")


def test_product_number_is_zero_padded():
    assert format_product_number(1) == "001"
    assert format_product_number(12) == "012"
    assert format_product_number(123) == "123"
    assert format_product_number(1000) == "1000"


def test_extract_comment_webhook_event():
    payload = {
        "object": "instagram",
        "entry": [
            {
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "comment-1",
                            "text": "링크",
                            "media": {"id": "media-1"},
                            "from": {"id": "user-1", "username": "tester"},
                        },
                    }
                ]
            }
        ],
    }
    events = extract_comment_events(payload)
    assert events == [
        CommentEvent(
            comment_id="comment-1",
            media_id="media-1",
            text="링크",
            commenter_id="user-1",
            commenter_username="tester",
        )
    ]


def test_extract_direct_entry_webhook_event():
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "ig-account-1",
                "field": "comments",
                "value": {
                    "id": "comment-2",
                    "text": "링크 부탁해요",
                    "media": {"id": "media-2"},
                    "from": {"id": "user-2", "username": "tester2"},
                },
            }
        ],
    }

    assert extract_comment_events(payload) == [
        CommentEvent(
            comment_id="comment-2",
            media_id="media-2",
            text="링크 부탁해요",
            commenter_id="user-2",
            commenter_username="tester2",
        )
    ]


def create_test_product(database, *, media_id: str = "media-1"):
    return database.create_product(
        {
            "product_name": "테스트 상품",
            "purchase_link": "https://example.com/product",
            "trigger_phrase": "링크",
            "photo_url": "/uploads/test.jpg",
            "ig_media_id": media_id,
            "ig_permalink": "https://instagram.com/reel/test",
        }
    )


def test_duplicate_comment_sends_dm_and_public_reply_only_once(settings):
    from app.database import Database

    database = Database(settings.db_path)
    database.initialize()
    product = create_test_product(database)
    instagram = AsyncMock()
    event = CommentEvent("comment-1", "media-1", "링크 주세요", "user-1", "tester")

    asyncio.run(process_comment_event(event, database=database, instagram=instagram))
    asyncio.run(process_comment_event(event, database=database, instagram=instagram))

    assert product["id"] == 1
    assert instagram.send_private_reply.await_count == 1
    assert instagram.send_public_reply.await_count == 1

    sent_dm = instagram.send_private_reply.await_args.args[1]
    sent_reply = instagram.send_public_reply.await_args.args[1]
    assert "001번" in sent_dm
    assert sent_reply in PUBLIC_REPLY_TEMPLATES
    assert "001" not in sent_reply

    log = database.list_dm_logs()[0]
    assert log["status"] == "sent"
    assert "001번" in log["dm_message"]
    assert log["reply_status"] == "sent"
    assert log["reply_message"] == sent_reply
    assert log["reply_error_message"] is None
    assert log["replied_at"] is not None


def test_dm_failure_does_not_send_public_reply(settings):
    from app.database import Database

    database = Database(settings.db_path)
    database.initialize()
    create_test_product(database)
    instagram = AsyncMock()
    instagram.send_private_reply.side_effect = RuntimeError("DM API failure")
    event = CommentEvent("comment-dm-fail", "media-1", "링크", "user-1", "tester")

    asyncio.run(process_comment_event(event, database=database, instagram=instagram))

    assert instagram.send_private_reply.await_count == 1
    assert instagram.send_public_reply.await_count == 0
    log = database.list_dm_logs()[0]
    assert log["status"] == "failed"
    assert "DM API failure" in log["error_message"]
    assert log["reply_status"] == "skipped"
    assert log["reply_message"] is None


def test_public_reply_failure_keeps_dm_success_and_records_error(settings):
    from app.database import Database

    database = Database(settings.db_path)
    database.initialize()
    create_test_product(database)
    instagram = AsyncMock()
    instagram.send_public_reply.side_effect = RuntimeError("reply API failure")
    event = CommentEvent("comment-reply-fail", "media-1", "링크", "user-1", "tester")

    asyncio.run(process_comment_event(event, database=database, instagram=instagram))

    assert instagram.send_private_reply.await_count == 1
    assert instagram.send_public_reply.await_count == 1
    log = database.list_dm_logs()[0]
    assert log["status"] == "sent"
    assert log["reply_status"] == "failed"
    assert log["reply_message"] in PUBLIC_REPLY_TEMPLATES
    assert "reply API failure" in log["reply_error_message"]
    assert log["replied_at"] is not None
