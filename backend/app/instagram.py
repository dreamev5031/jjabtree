from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class InstagramAPIError(RuntimeError):
    pass


@dataclass
class SubscriptionResult:
    ok: bool
    detail: str


@dataclass(frozen=True)
class MediaExistenceResult:
    status: Literal["ok", "missing", "error"]
    detail: str
    http_status: int | None = None
    error_code: int | None = None
    error_subcode: int | None = None


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
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self.auth_headers,
                    params={"fields": fields, "limit": min(max(limit, 1), 100)},
                )
        except httpx.RequestError as exc:
            raise InstagramAPIError(f"최근 게시물 조회 실패: Instagram API 연결 오류: {exc}") from exc

        if response.is_error:
            raise InstagramAPIError(self._error_message(response, "최근 게시물 조회 실패"))
        payload = response.json()
        media = payload.get("data", [])
        return [self._normalize_media(item) for item in media]

    async def check_media_exists(self, media_id: str) -> MediaExistenceResult:
        """Check one owned media object without treating transient API failures as deletion."""
        url = f"{self.api_root}/{media_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self.auth_headers,
                    params={"fields": "id,permalink"},
                )
        except httpx.RequestError as exc:
            return MediaExistenceResult(
                status="error",
                detail=f"Instagram API 연결 오류: {exc}",
            )

        return self.classify_media_check_response(response, media_id)

    @staticmethod
    def classify_media_check_response(
        response: httpx.Response,
        media_id: str,
    ) -> MediaExistenceResult:
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                return MediaExistenceResult(
                    status="error",
                    detail="Instagram API가 올바른 JSON을 반환하지 않았습니다.",
                    http_status=response.status_code,
                )

            returned_id = str(payload.get("id") or "")
            if returned_id:
                return MediaExistenceResult(
                    status="ok",
                    detail=f"Instagram 미디어 {returned_id} 확인 완료",
                    http_status=response.status_code,
                )
            return MediaExistenceResult(
                status="error",
                detail=f"Instagram API 성공 응답에 미디어 ID가 없습니다: {media_id}",
                http_status=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else {}
        error = error if isinstance(error, dict) else {}
        error_code = InstagramClient._as_int(error.get("code"))
        error_subcode = InstagramClient._as_int(error.get("error_subcode"))
        message = str(error.get("message") or response.text[:500] or "알 수 없는 오류")

        # Meta commonly returns either HTTP 404 or Graph error code 100/subcode 33
        # for an object that no longer exists. All other failures are deliberately
        # treated as transient/indeterminate to avoid false deletion warnings.
        is_missing = response.status_code == 404 or (
            error_code == 100 and error_subcode == 33
        )
        if is_missing:
            return MediaExistenceResult(
                status="missing",
                detail=message,
                http_status=response.status_code,
                error_code=error_code,
                error_subcode=error_subcode,
            )

        return MediaExistenceResult(
            status="error",
            detail=message,
            http_status=response.status_code,
            error_code=error_code,
            error_subcode=error_subcode,
        )

    async def subscribe_comments(self) -> SubscriptionResult:
        """Subscribe the professional account to app-level comment webhooks.

        Meta webhooks are account-level, not per-media. This call is best-effort because
        some app/login configurations require subscription only in the App Dashboard.
        """
        url = f"{self.api_root}/{self.settings.ig_business_account_id}/subscribed_apps"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    headers=self.auth_headers,
                    data={"subscribed_fields": "comments"},
                )
        except httpx.RequestError as exc:
            detail = f"댓글 웹훅 계정 구독 요청 실패: Instagram API 연결 오류: {exc}"
            logger.warning(detail)
            return SubscriptionResult(ok=False, detail=detail)

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

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                try:
                    modern = await client.post(
                        modern_url,
                        headers={**self.auth_headers, "Content-Type": "application/json"},
                        json=modern_payload,
                    )
                    if not modern.is_error:
                        return modern.json()
                    modern_error = self._error_message(modern, "messages 방식 DM 발송 실패")
                except httpx.RequestError as exc:
                    modern_error = f"messages 방식 DM 발송 연결 오류: {exc}"

                logger.warning("%s; legacy endpoint로 재시도합니다.", modern_error)
                legacy_url = f"{self.api_root}/{comment_id}/private_replies"
                legacy = await client.post(
                    legacy_url,
                    headers=self.auth_headers,
                    data={"message": message},
                )
                if not legacy.is_error:
                    return legacy.json()
        except httpx.RequestError as exc:
            raise InstagramAPIError(f"{modern_error} / private_replies 연결 오류: {exc}") from exc

        legacy_error = self._error_message(legacy, "private_replies 방식 DM 발송 실패")
        raise InstagramAPIError(f"{modern_error} / {legacy_error}")

    async def send_public_reply(self, comment_id: str, message: str) -> dict[str, Any]:
        """Post a public reply below the triggering Instagram comment."""
        url = f"{self.api_root}/{comment_id}/replies"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    headers=self.auth_headers,
                    data={"message": message},
                )
        except httpx.RequestError as exc:
            raise InstagramAPIError(f"공개 답글 발송 실패: Instagram API 연결 오류: {exc}") from exc

        if response.is_error:
            raise InstagramAPIError(self._error_message(response, "공개 답글 발송 실패"))
        return response.json()

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
    def _as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _error_message(response: httpx.Response, prefix: str) -> str:
        try:
            payload = response.json()
            detail = payload.get("error", {}).get("message") or payload
        except ValueError:
            detail = response.text[:500]
        return f"{prefix} ({response.status_code}): {detail}"