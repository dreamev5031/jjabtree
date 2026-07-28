from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx

from app.database import Database
from app.instagram import InstagramClient, MediaExistenceResult
from app.media_checks import check_all_active_media, check_one_product_media


def create_product(database: Database, *, media_id: str, name: str = "테스트 상품"):
    return database.create_product(
        {
            "product_name": name,
            "purchase_link": "https://example.com/product",
            "trigger_phrase": "링크",
            "photo_url": "/uploads/test.jpg",
            "ig_media_id": media_id,
            "ig_permalink": "https://instagram.com/reel/test",
        }
    )


def graph_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://graph.facebook.com/v25.0/media-1"),
    )


def test_media_check_response_classification_is_conservative():
    ok = InstagramClient.classify_media_check_response(
        graph_response(200, {"id": "media-1", "permalink": "https://instagram.com/reel/x"}),
        "media-1",
    )
    not_found = InstagramClient.classify_media_check_response(
        graph_response(404, {"error": {"message": "Object not found"}}),
        "media-1",
    )
    graph_missing = InstagramClient.classify_media_check_response(
        graph_response(
            400,
            {
                "error": {
                    "message": "Unsupported get request",
                    "code": 100,
                    "error_subcode": 33,
                }
            },
        ),
        "media-1",
    )
    rate_limited = InstagramClient.classify_media_check_response(
        graph_response(
            429,
            {"error": {"message": "Application request limit reached", "code": 4}},
        ),
        "media-1",
    )
    server_error = InstagramClient.classify_media_check_response(
        graph_response(500, {"error": {"message": "Temporary server error", "code": 2}}),
        "media-1",
    )

    assert ok.status == "ok"
    assert not_found.status == "missing"
    assert graph_missing.status == "missing"
    assert rate_limited.status == "error"
    assert server_error.status == "error"


def test_daily_check_only_updates_active_products(settings):
    database = Database(settings.db_path)
    database.initialize()
    active = create_product(database, media_id="media-active")
    inactive = create_product(database, media_id="media-inactive")
    database.update_product_status(inactive["id"], "inactive")

    instagram = AsyncMock()
    instagram.check_media_exists.return_value = MediaExistenceResult(
        status="missing",
        detail="Object not found",
        http_status=404,
    )

    summary = asyncio.run(check_all_active_media(database=database, instagram=instagram))

    assert summary == {"checked": 1, "ok": 0, "missing": 1, "skipped": 0, "failed": 0}
    instagram.check_media_exists.assert_awaited_once_with("media-active")

    active_after = database.get_product(active["id"])
    inactive_after = database.get_product(inactive["id"])
    assert active_after["media_check_status"] == "missing"
    assert active_after["media_checked_at"] is not None
    assert inactive_after["media_check_status"] == "unchecked"
    assert inactive_after["media_checked_at"] is None


def test_transient_error_preserves_last_known_status(settings):
    database = Database(settings.db_path)
    database.initialize()
    product = create_product(database, media_id="media-transient")
    previous = database.update_media_check_status(product["id"], "ok")

    instagram = AsyncMock()
    instagram.check_media_exists.return_value = MediaExistenceResult(
        status="error",
        detail="Rate limit",
        http_status=429,
        error_code=4,
    )

    result, updated = asyncio.run(
        check_one_product_media(product, database=database, instagram=instagram)
    )

    assert result.status == "error"
    assert updated["media_check_status"] == "ok"
    assert updated["media_checked_at"] == previous["media_checked_at"]


def test_manual_media_check_endpoint_updates_product(client):
    database = client.app.state.database
    product = create_product(database, media_id="media-manual")
    client.app.state.instagram.check_media_exists = AsyncMock(
        return_value=MediaExistenceResult(
            status="missing",
            detail="Object not found",
            http_status=404,
        )
    )

    response = client.post(
        f"/api/admin/products/{product['id']}/media-check",
        headers={"X-App-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["check"]["status"] == "missing"
    assert payload["product"]["media_check_status"] == "missing"
    assert payload["product"]["media_checked_at"] is not None
    client.app.state.instagram.check_media_exists.assert_awaited_once_with("media-manual")


def test_manual_transient_error_does_not_mark_missing(client):
    database = client.app.state.database
    product = create_product(database, media_id="media-manual-error")
    client.app.state.instagram.check_media_exists = AsyncMock(
        return_value=MediaExistenceResult(
            status="error",
            detail="Temporary server error",
            http_status=500,
            error_code=2,
        )
    )

    response = client.post(
        f"/api/admin/products/{product['id']}/media-check",
        headers={"X-App-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["check"]["status"] == "error"
    assert payload["product"]["media_check_status"] == "unchecked"
    assert payload["product"]["media_checked_at"] is None