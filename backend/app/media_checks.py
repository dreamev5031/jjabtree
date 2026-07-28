from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from .database import Database
from .instagram import InstagramClient, MediaExistenceResult

logger = logging.getLogger(__name__)


async def check_one_product_media(
    product: dict[str, Any],
    *,
    database: Database,
    instagram: InstagramClient,
) -> tuple[MediaExistenceResult, dict[str, Any] | None]:
    result = await instagram.check_media_exists(str(product["ig_media_id"]))

    if result.status in {"ok", "missing"}:
        updated = database.update_media_check_status(product["id"], result.status)
    else:
        # Transient, permission, token, rate-limit and server errors never overwrite
        # the last known media status. This is the key false-positive safeguard.
        updated = database.get_product(product["id"])

    return result, updated


async def check_all_active_media(
    *,
    database: Database,
    instagram: InstagramClient,
) -> dict[str, int]:
    summary = {"checked": 0, "ok": 0, "missing": 0, "skipped": 0, "failed": 0}
    products = database.list_active_products_for_media_check()

    for product in products:
        try:
            result, _ = await check_one_product_media(
                product,
                database=database,
                instagram=instagram,
            )
        except Exception:  # noqa: BLE001 - one product must never stop the daily job
            summary["failed"] += 1
            logger.exception(
                "릴스 존재 여부 확인 중 예외 발생: product_id=%s media_id=%s",
                product.get("id"),
                product.get("ig_media_id"),
            )
            continue

        summary["checked"] += 1
        if result.status == "ok":
            summary["ok"] += 1
        elif result.status == "missing":
            summary["missing"] += 1
            logger.warning(
                "연결된 릴스 삭제 의심: product_id=%s media_id=%s detail=%s",
                product["id"],
                product["ig_media_id"],
                result.detail,
            )
        else:
            summary["skipped"] += 1
            logger.warning(
                "릴스 확인 일시 오류로 상태 유지: product_id=%s media_id=%s detail=%s",
                product["id"],
                product["ig_media_id"],
                result.detail,
            )

    logger.info("활성 상품 릴스 확인 완료: %s", summary)
    return summary


async def run_scheduled_media_checks(
    *,
    database: Database,
    instagram: InstagramClient,
) -> None:
    try:
        await check_all_active_media(database=database, instagram=instagram)
    except Exception:  # noqa: BLE001 - scheduler failures must not stop the API server
        logger.exception("일일 릴스 존재 여부 확인 작업 실패")


def serialize_media_check_result(result: MediaExistenceResult) -> dict[str, Any]:
    return asdict(result)