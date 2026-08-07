from __future__ import annotations

import hashlib
import logging
import os
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .database import Database
from .forwarding import AutocardForwarder
from .instagram import InstagramClient

logger = logging.getLogger(__name__)
DEFAULT_PUBLIC_SITE_URL = "https://jjabtree.pages.dev"
MAX_PUBLIC_REPLY_LENGTH = 180

LEGACY_DM_TEMPLATES = (
    "요청하신 상품은 여기서 확인하세요: {페이지링크} ({번호}번)",
    "{페이지링크} 에서 {번호}번 상품 확인해보세요!",
    "여기 확인해보세요 👉 {페이지링크} ({번호}번)",
    "{번호}번 상품이에요! {페이지링크} 에서 바로 보실 수 있어요",
    "메시지 확인 완료! {페이지링크} ({번호}번)",
)
PUBLIC_REPLY_TEMPLATES = (
    "DM으로 보내드렸어요. 안 보이면 메시지 요청함도 확인해주세요.",
    "디엠 발송했어요. 보이지 않으면 요청함을 확인해주세요.",
    "요청하신 내용은 DM으로 보냈습니다. 안 뜨면 메시지 요청함을 봐주세요.",
    "DM으로 안내드렸어요. 확인되지 않으면 요청함도 확인해주세요.",
    "디엠 보내드렸습니다. 안 보일 경우 메시지 요청함을 확인해주세요.",
    "요청하신 링크는 DM으로 발송했어요. 안 보이면 요청함도 살펴봐주세요.",
    "DM 전송 완료했습니다. 받은편지함에 없으면 메시지 요청함을 확인해주세요.",
)

FORWARD_NOT_CONFIGURED = "not_configured"
FORWARD_ACCEPTED = "accepted"
FORWARD_FAILED = "failed"


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


def public_site_url_from_env() -> str:
    return (os.environ.get("PUBLIC_SITE_URL", DEFAULT_PUBLIC_SITE_URL).strip() or DEFAULT_PUBLIC_SITE_URL).rstrip("/")


def validate_public_reply_template(template: str) -> str:
    text = template.strip()
    if not text:
        raise ValueError("empty_public_reply_template")
    folded = text.casefold()
    if "dm" not in folded and "디엠" not in text:
        raise ValueError("public_reply_dm_notice_required")
    if "요청함" not in text and "메시지 요청함" not in text:
        raise ValueError("public_reply_request_inbox_notice_required")
    if "http://" in folded or "https://" in folded or "www." in folded:
        raise ValueError("public_reply_link_forbidden")
    if len(text) > MAX_PUBLIC_REPLY_LENGTH:
        raise ValueError("public_reply_template_too_long")
    return text


def validate_public_reply_templates(templates: Sequence[str]) -> tuple[str, ...]:
    if not templates:
        raise ValueError("public_reply_templates_required")
    validated = tuple(validate_public_reply_template(template) for template in templates)
    if len(set(validated)) != len(validated):
        raise ValueError("duplicate_public_reply_template")
    return validated


def choose_public_reply_template(
    templates: Sequence[str] = PUBLIC_REPLY_TEMPLATES,
    *,
    chooser: Callable[[Sequence[str]], str] = secrets.choice,
) -> str:
    validated = validate_public_reply_templates(templates)
    return validate_public_reply_template(chooser(validated))


validate_public_reply_templates(PUBLIC_REPLY_TEMPLATES)


def _entry_changes(entry: dict[str, Any]) -> Iterable[dict[str, Any]]:
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
                    events.append(CommentEvent(
                        comment_id=str(comment_id),
                        media_id=str(media_id),
                        text=str(text),
                        commenter_id=str(author.get("id")) if author.get("id") else None,
                        commenter_username=str(author.get("username")) if author.get("username") else None,
                    ))
    return events


def _forwarder_from_env() -> AutocardForwarder | None:
    endpoint = os.environ.get("AUTOCARD_INTERNAL_BASE_URL", "").strip()
    secret = os.environ.get("WEBHOOK_FORWARD_SECRET", "").strip()
    account_id = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "").strip()
    if not endpoint or not secret or not account_id:
        return None
    return AutocardForwarder(endpoint=endpoint, secret=secret, account_id=account_id)


async def _forward_verified_event(event: CommentEvent) -> str:
    forwarder = _forwarder_from_env()
    if forwarder is None:
        return FORWARD_NOT_CONFIGURED
    canonical = "\x1f".join([
        event.comment_id,
        event.media_id,
        event.commenter_id or "",
        event.commenter_username or "",
        event.text,
    ]).encode("utf-8")
    raw_event_hash = hashlib.sha256(canonical).hexdigest()
    result = await forwarder.forward(event, raw_event_hash=raw_event_hash)
    if not result.ok:
        logger.warning("autocard forwarding deferred/failed: status=%s error=%s", result.status_code, result.error)
        return FORWARD_FAILED
    return FORWARD_ACCEPTED


async def process_comment_event(
    event: CommentEvent,
    *,
    database: Database,
    instagram: InstagramClient,
    public_site_url: str | None = None,
) -> None:
    # This function is reached only after the main webhook route validates Meta's raw-body signature.
    # When Autocard forwarding is configured, Autocard exclusively owns Private Reply DM delivery.
    # jjabtree keeps the public reply responsibility and never falls back to a second DM.
    try:
        forward_status = await _forward_verified_event(event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("autocard forwarding failed without exposing details: type=%s", exc.__class__.__name__)
        forward_status = FORWARD_FAILED if _forwarder_from_env() is not None else FORWARD_NOT_CONFIGURED

    product = database.get_active_product_by_media(event.media_id)
    if not product:
        logger.info("매칭되는 활성 상품 없음: media_id=%s", event.media_id)
        return
    if not trigger_matches(event.text, product["trigger_phrase"]):
        logger.info("트리거 불일치: comment_id=%s, product_id=%s", event.comment_id, product["id"])
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

    if forward_status == FORWARD_FAILED:
        database.complete_comment(
            event.comment_id,
            status="failed",
            error_message="autocard_forward_failed",
        )
        database.complete_public_reply(event.comment_id, status="skipped")
        logger.warning("Autocard forwarding failed; DM and public reply blocked: comment_id=%s", event.comment_id)
        return

    if forward_status == FORWARD_ACCEPTED:
        database.complete_comment(
            event.comment_id,
            status="ignored",
            error_message="private_reply_delegated_to_autocard",
        )
    else:
        product_number = format_product_number(product["id"])
        page_url = (public_site_url or public_site_url_from_env()).rstrip("/")
        dm_message = secrets.choice(LEGACY_DM_TEMPLATES).format(번호=product_number, 페이지링크=page_url)
        try:
            await instagram.send_private_reply(event.comment_id, dm_message)
        except Exception as exc:  # noqa: BLE001
            database.complete_comment(event.comment_id, status="failed", dm_message=dm_message, error_message=str(exc)[:1000])
            database.complete_public_reply(event.comment_id, status="skipped")
            logger.exception("Instagram DM 발송 실패: comment_id=%s", event.comment_id)
            return
        database.complete_comment(event.comment_id, status="sent", dm_message=dm_message)
        logger.info("Instagram DM 발송 완료: comment_id=%s, product_id=%s", event.comment_id, product["id"])

    reply_message = choose_public_reply_template()
    try:
        await instagram.send_public_reply(event.comment_id, reply_message)
    except Exception as exc:  # noqa: BLE001
        database.complete_public_reply(event.comment_id, status="failed", reply_message=reply_message, error_message=str(exc)[:1000])
        logger.exception("Instagram 공개 답글 발송 실패: comment_id=%s", event.comment_id)
        return
    database.complete_public_reply(event.comment_id, status="sent", reply_message=reply_message)
    logger.info("Instagram 공개 답글 발송 완료: comment_id=%s", event.comment_id)
