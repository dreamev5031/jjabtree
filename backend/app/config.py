from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"필수 환경변수 {name}가 설정되지 않았습니다. README의 환경변수 설정을 확인하세요.")
    return value


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _hour(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name}는 0부터 23 사이의 정수여야 합니다.") from exc
    if not 0 <= value <= 23:
        raise ConfigurationError(f"{name}는 0부터 23 사이의 정수여야 합니다.")
    return value


def _http_url(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip() or default
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{name}는 http(s)로 시작하는 완전한 URL이어야 합니다.")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    ig_access_token: str
    ig_business_account_id: str
    admin_app_key: str
    db_path: Path
    upload_dir: Path
    graph_api_version: str
    graph_api_base_url: str
    webhook_verify_token: str
    meta_app_secret: str
    webhook_forward_secret: str | None
    autocard_internal_base_url: str | None
    cors_origins: list[str]
    public_site_url: str
    media_check_hour: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.environ.get("DB_PATH", "./data/jjabtree.db")).expanduser()
        upload_default = str(db_path.parent / "uploads")
        version = os.environ.get("IG_GRAPH_API_VERSION", "v25.0").strip() or "v25.0"
        if not version.startswith("v"):
            version = f"v{version}"
        forward_secret = os.environ.get("WEBHOOK_FORWARD_SECRET", "").strip() or None
        forward_url = os.environ.get("AUTOCARD_INTERNAL_BASE_URL", "").strip() or None
        if bool(forward_secret) != bool(forward_url):
            logger.warning(
                "autocard forwarding disabled: WEBHOOK_FORWARD_SECRET and "
                "AUTOCARD_INTERNAL_BASE_URL must both be configured"
            )
            forward_secret = None
            forward_url = None
        elif forward_url:
            forward_url = _http_url("AUTOCARD_INTERNAL_BASE_URL", forward_url)

        return cls(
            ig_access_token=_required("IG_ACCESS_TOKEN"),
            ig_business_account_id=_required("IG_BUSINESS_ACCOUNT_ID"),
            admin_app_key=_required("ADMIN_APP_KEY"),
            db_path=db_path,
            upload_dir=Path(os.environ.get("UPLOAD_DIR", upload_default)).expanduser(),
            graph_api_version=version,
            graph_api_base_url=os.environ.get("IG_GRAPH_API_BASE_URL", "https://graph.facebook.com").rstrip("/"),
            webhook_verify_token=_required("META_WEBHOOK_VERIFY_TOKEN"),
            meta_app_secret=_required("META_APP_SECRET"),
            webhook_forward_secret=forward_secret,
            autocard_internal_base_url=forward_url,
            cors_origins=_csv("CORS_ORIGINS", "*"),
            public_site_url=_http_url("PUBLIC_SITE_URL", "https://jjabtree.pages.dev"),
            media_check_hour=_hour("MEDIA_CHECK_HOUR", 5),
        )
