from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class InstagramAPIError(RuntimeError):
    pass


@dataclass
class SubscriptionResult:
    ok: bool
    detail: str


class InstagramClient:
    def __init__(self, settings: Settings, timeout: float = 20.0):
        self.settings = settings
        self.timeout = timeout

    @property
    def api_root(self) -> str:
        return f"{self.settings.graph_api_base_url}/{self.settings.graph_api_version}"

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.ig_access_token}"}

    async def list_recent_media(self, limit: int = 24) -> list[dict[str, Any]]:
        fields = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"
        url = f"{self.api_root}/{self.settings.ig_business_account_id}/media"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                url,
                headers=self.auth_headers,
                params={"fields": fields, "limit": min(max(limit, 1), 100)},
            )
        if response.is_error:
            raise InstagramAPIError(self._error_message(response, "최근 게시물 조회 실패"))
        payload = response.json()
        media = payload.get("data", [])
        return [self._normalize_media(item) for item in media]

    async def subscribe_comments(self) -> SubscriptionResult:
        """Subscribe the professional account to app-level comment webhooks.

        Meta webhooks are account-level, not per-media. This call is best-effort because
        some app/login configurations require subscription only in the App Dashboard.
        """
        url = f"{self.api_root}/{self.settings.ig_business_account_id}/subscribed_apps"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                headers=self.auth_headers,
                data={"subscribed_fields": "comments"},
            )
        if response.is_error:
            detail = self._error_message(response, "댓글 웹훅 계정 구독 요청 실패")
            logger.warning(detail)
            return SubscriptionResult(ok=False, detail=detail)
        return SubscriptionResult(ok=True, detail="댓글 웹훅 계정 구독 요청 완료")

    async def send_private_reply(self, comment_id: str, message: str) -> dict[str, Any]:
        """Send a private reply to an Instagram comment.

        The modern messages endpoint is attempted first. The legacy private_replies
        endpoint is retained as a compatibility fallback for Facebook Login based apps.
        """
        modern_url = f"{self.api_root}/{self.settings.ig_business_account_id}/messages"
        modern_payload = {
            "recipient": {"comment_id": comment_id},
            "message": {"text": message},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            modern = await client.post(
                modern_url,
                headers={**self.auth_headers, "Content-Type": "application/json"},
                json=modern_payload,
            )
            if not modern.is_error:
                return modern.json()

            modern_error = self._error_message(modern, "messages 방식 DM 발송 실패")
            logger.warning("%s; legacy endpoint로 재시도합니다.", modern_error)

            legacy_url = f"{self.api_root}/{comment_id}/private_replies"
            legacy = await client.post(
                legacy_url,
                headers=self.auth_headers,
                data={"message": message},
            )
            if not legacy.is_error:
                return legacy.json()

        legacy_error = self._error_message(legacy, "private_replies 방식 DM 발송 실패")
        raise InstagramAPIError(f"{modern_error} / {legacy_error}")

    @staticmethod
    def _normalize_media(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item.get("id", "")),
            "caption": item.get("caption") or "",
            "media_type": item.get("media_type") or "UNKNOWN",
            "image_url": item.get("thumbnail_url") or item.get("media_url") or "",
            "permalink": item.get("permalink") or "",
            "timestamp": item.get("timestamp") or "",
        }

    @staticmethod
    def _error_message(response: httpx.Response, prefix: str) -> str:
        try:
            payload = response.json()
            detail = payload.get("error", {}).get("message") or payload
        except ValueError:
            detail = response.text[:500]
        return f"{prefix} ({response.status_code}): {detail}"
