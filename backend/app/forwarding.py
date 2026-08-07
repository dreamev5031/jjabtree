from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx

logger = logging.getLogger(__name__)


class CommentEventLike(Protocol):
    comment_id: str
    media_id: str
    text: str
    commenter_id: str | None
    commenter_username: str | None


@dataclass(frozen=True)
class ForwardResult:
    ok: bool
    status_code: int | None = None
    error: str | None = None


def event_payload(event: CommentEventLike, *, account_id: str, raw_event_hash: str, event_time: str | int | None = None) -> dict[str, Any]:
    return {
        "source_project": "jjabtree-gateway",
        "instagram_account_id": account_id,
        "instagram_comment_id": event.comment_id,
        "instagram_media_id": event.media_id,
        "instagram_user_id": event.commenter_id,
        "username": event.commenter_username,
        "text": event.text[:2000] if event.text else None,
        "event_time": event_time,
        "raw_event_hash": raw_event_hash,
        "field": "comments",
    }


def sign(secret: str, timestamp: str, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class AutocardForwarder:
    def __init__(
        self,
        *,
        endpoint: str,
        secret: str,
        account_id: str,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self.base_url = endpoint.rstrip("/")
        self.endpoint = self.base_url + "/api/internal/instagram/comment-events"
        self.probe_endpoint = self.base_url + "/api/internal/instagram/forward-probe"
        self.secret = secret
        self.account_id = account_id
        self.client_factory = client_factory

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.secret and self.account_id)

    def _headers(self, raw: bytes, event_id: str, timestamp: str | None = None) -> dict[str, str]:
        timestamp = timestamp or str(int(time.time()))
        return {
            "Content-Type": "application/json",
            "X-Autocard-Timestamp": timestamp,
            "X-Autocard-Signature": sign(self.secret, timestamp, raw),
            "X-Autocard-Event-Id": event_id,
        }

    async def forward(self, event: CommentEventLike, *, raw_event_hash: str, event_time: str | int | None = None) -> ForwardResult:
        if not self.configured:
            return ForwardResult(False, error="not_configured")
        payload = event_payload(event, account_id=self.account_id, raw_event_hash=raw_event_hash, event_time=event_time)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        event_id = f"ig-comment:{event.comment_id}:{raw_event_hash[:16]}"
        try:
            async with self.client_factory(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
                response = await client.post(self.endpoint, content=raw, headers=self._headers(raw, event_id))
        except httpx.RequestError as exc:
            logger.warning("autocard forward failed: type=%s", exc.__class__.__name__)
            return ForwardResult(False, error=exc.__class__.__name__)
        if response.status_code not in {200, 202}:
            logger.warning("autocard forward rejected: status=%s", response.status_code)
            return ForwardResult(False, status_code=response.status_code, error="rejected")
        return ForwardResult(True, status_code=response.status_code)

    async def probe(self) -> ForwardResult:
        if not self.configured:
            return ForwardResult(False, error="not_configured")
        raw = json.dumps(
            {"kind": "autocard-forward-secret-probe", "source_project": "jjabtree-gateway"},
            separators=(",", ":"),
        ).encode("utf-8")
        event_id = f"forward-probe:{uuid.uuid4()}"
        try:
            async with self.client_factory(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
                response = await client.post(self.probe_endpoint, content=raw, headers=self._headers(raw, event_id))
        except httpx.RequestError as exc:
            logger.warning("autocard probe failed: type=%s", exc.__class__.__name__)
            return ForwardResult(False, error=exc.__class__.__name__)
        if response.status_code != 200:
            return ForwardResult(False, status_code=response.status_code, error="rejected")
        try:
            matched = response.json().get("matched") is True
        except ValueError:
            matched = False
        return ForwardResult(matched, status_code=response.status_code, error=None if matched else "invalid_response")
