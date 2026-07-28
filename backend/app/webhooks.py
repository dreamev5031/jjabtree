from __future__ import annotations

import logging
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from .database import Database
from .instagram import InstagramClient

logger = logging.getLogger(__name__)

DM_TEMPLATES = (
    "프로필 링크 들어가서 {번호}번 확인해보세요 🙌",
    "요청하신 상품은 링크의 {번호}번이에요!",
    "{번호}번 상품이에요, 프로필 링크 확인해주세요~",
    "링크 프로필에서 {번호}번 보시면 돼요 😊",
    "{번호}번으로 안내드릴게요, 프로필 링크 확인!",
)

PUBLIC_REPLY_TEMPLATES = (
    "DM 보냈어요 확인해주세요 💌",
    "DM 확인해보세요! 📩",
    "DM 드렸어요~ 확인 부탁드려요 😊",
    "요청하신 내용은 DM으로 보내드렸어요 🙌",
    "메시지함을 확인해주세요 ✨",
)


@dataclass(frozen=True)
class CommentEvent:
    comment_id: str
    media_id: str
    text: str
    commenter_id: str | None = None
    commenter_username: str | None = None


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def trigger_matches(comment_text: str, trigger_phrase: str) -> bool:
    trigger = normalize_text(trigger_phrase)
    return bool(trigger) and trigger in normalize_text(comment_text)


def format_product_number(product_id: int) -> str:
    return str(product_id).zfill(3)


def _entry_changes(entry: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield both webhook payload layouts used by Meta Instagram products.

    Some configurations wrap events in ``entry[].changes[]`` while others put
    ``field`` and ``value`` directly on each ``entry`` object.
    """
    changes = entry.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                yield change

    if "field" in entry and "value" in entry:
        yield entry


def extract_comment_events(payload: dict[str, Any]) -> list[CommentEvent]:
    events: list[CommentEvent] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in _entry_changes(entry):
            if change.get("field") not in {"comments", "live_comments"}:
                continue
            raw_value = change.get("value")
            values: Iterable[Any] = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                if not isinstance(value, dict):
                    continue
                media = value.get("media") if isinstance(value.get("media"), dict) else {}
                author = value.get("from") if isinstance(value.get("from"), dict) else {}
                if not author and isinstance(value.get("user"), dict):
                    author = value["user"]
                comment_id = value.get("id") or value.get("comment_id")
                media_id = media.get("id") or value.get("media_id")
                text = value.get("text") or value.get("message") or ""
                if comment_id and media_id:
                    events.append(
                        CommentEvent(
                            comment_id=str(comment_id),
                            media_id=str(media_id),
                            text=str(text),
                            commenter_id=str(author.get("id")) if author.get("id") else None,
                            commenter_username=author.get("username"),
                        )
                    )
    return events


async def process_comment_event(
    event: CommentEvent,
    *,
    database: Database,
    instagram: InstagramClient,
) -> None:
    product = database.get_active_product_by_media(event.media_id)
    if not product:
        logger.info("매칭되는 활성 상품 없음: media_id=%s", event.media_id)
        return

    if not trigger_matches(event.text, product["trigger_phrase"]):
        logger.info(
            "트리거 불일치: comment_id=%s, product_id=%s", event.comment_id, product["id"]
        )
        return

    claimed = database.claim_comment(
        comment_id=event.comment_id,
        product_id=product["id"],
        product_name=product["product_name"],
        commenter_id=event.commenter_id,
        commenter_username=event.commenter_username,
        comment_text=event.text,
    )
    if not claimed:
        logger.info("중복 댓글 이벤트 건너뜀: comment_id=%s", event.comment_id)
        return

    product_number = format_product_number(product["id"])
    dm_message = secrets.choice(DM_TEMPLATES).format(번호=product_number)
    try:
        await instagram.send_private_reply(event.comment_id, dm_message)
    except Exception as exc:  # noqa: BLE001 - webhook worker must never crash the server
        error = str(exc)
        database.complete_comment(
            event.comment_id,
            status="failed",
            dm_message=dm_message,
            error_message=error[:1000],
        )
        database.complete_public_reply(event.comment_id, status="skipped")
        logger.exception("Instagram DM 발송 실패: comment_id=%s", event.comment_id)
        return

    database.complete_comment(event.comment_id, status="sent", dm_message=dm_message)
    logger.info("Instagram DM 발송 완료: comment_id=%s, product_id=%s", event.comment_id, product["id"])

    reply_message = secrets.choice(PUBLIC_REPLY_TEMPLATES)
    try:
        await instagram.send_public_reply(event.comment_id, reply_message)
    except Exception as exc:  # noqa: BLE001 - reply failure must not fail webhook processing
        error = str(exc)
        database.complete_public_reply(
            event.comment_id,
            status="failed",
            reply_message=reply_message,
            error_message=error[:1000],
        )
        logger.exception("Instagram 공개 답글 발송 실패: comment_id=%s", event.comment_id)
        return

    database.complete_public_reply(
        event.comment_id,
        status="sent",
        reply_message=reply_message,
    )
    logger.info("Instagram 공개 답글 발송 완료: comment_id=%s", event.comment_id)
