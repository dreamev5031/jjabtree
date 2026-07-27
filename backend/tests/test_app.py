from __future__ import annotations

from unittest.mock import AsyncMock

from app.webhooks import CommentEvent, extract_comment_events, process_comment_event, trigger_matches


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


def test_duplicate_comment_sends_only_once(settings):
    from app.database import Database

    database = Database(settings.db_path)
    database.initialize()
    product = database.create_product(
        {
            "product_name": "테스트 상품",
            "purchase_link": "https://example.com/product",
            "trigger_phrase": "링크",
            "photo_url": "/uploads/test.jpg",
            "ig_media_id": "media-1",
            "ig_permalink": "https://instagram.com/reel/test",
        }
    )
    instagram = AsyncMock()
    event = CommentEvent("comment-1", "media-1", "링크 주세요", "user-1", "tester")

    import asyncio

    asyncio.run(process_comment_event(event, database=database, instagram=instagram))
    asyncio.run(process_comment_event(event, database=database, instagram=instagram))

    assert product["id"] == 1
    assert instagram.send_private_reply.await_count == 1
    logs = database.list_dm_logs()
    assert logs[0]["status"] == "sent"
