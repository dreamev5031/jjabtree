from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException, Request, status

from .config import Settings


def require_admin_key(settings: Settings):
    async def dependency(x_app_key: str | None = Header(default=None)) -> None:
        if not x_app_key or not hmac.compare_digest(x_app_key, settings.admin_app_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효한 X-App-Key가 필요합니다.",
            )

    return dependency


def verify_meta_signature(request_body: bytes, signature: str | None, app_secret: str | None) -> bool:
    if not app_secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), request_body, hashlib.sha256).hexdigest()
    received = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
