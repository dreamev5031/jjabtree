from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import ConfigurationError, Settings
from app.database import Database
from app.webhooks import DM_TEMPLATES, CommentEvent, process_comment_event


def test_dm_templates_require_page_link_and_product_number():
    assert len(DM_TEMPLATES) == 5
    for template in DM_TEMPLATES:
        assert "{페이지링크}" in template
        assert "{번호}" in template


def test_public_site_url_is_normalized_and_validated(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://todaypicks.example/")
    assert Settings.from_env().public_site_url == "https://todaypicks.example"

    monkeypatch.setenv("PUBLIC_SITE_URL", "todaypicks.example")
    with pytest.raises(ConfigurationError, match="PUBLIC_SITE_URL"):
        Settings.from_env()


def test_dm_contains_clickable_https_url_and_zero_padded_number(settings):
    database = Database(settings.db_path)
    database.initialize()
    database.create_product(
        {
            "product_name": "테스트 상품",
            "purchase_link": "https://example.com/product",
            "trigger_phrase": "링크",
            "photo_url": "/uploads/test.jpg",
            "ig_media_id": "media-with-url",
            "ig_permalink": "https://instagram.com/reel/test",
        }
    )
    instagram = AsyncMock()
    event = CommentEvent(
        "comment-with-url",
        "media-with-url",
        "링크 주세요",
        "user-1",
        "tester",
    )

    asyncio.run(
        process_comment_event(
            event,
            database=database,
            instagram=instagram,
            public_site_url="https://todaypicks.example/",
        )
    )

    sent_dm = instagram.send_private_reply.await_args.args[1]
    assert "https://todaypicks.example" in sent_dm
    assert "https://todaypicks.example/" not in sent_dm
    assert "001번" in sent_dm

    log = database.list_dm_logs()[0]
    assert log["status"] == "sent"
    assert log["dm_message"] == sent_dm
