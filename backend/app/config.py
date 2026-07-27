from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"필수 환경변수 {name}가 설정되지 않았습니다. README의 환경변수 설정을 확인하세요."
        )
    return value


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


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
    meta_app_secret: str | None
    cors_origins: list[str]

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.environ.get("DB_PATH", "./data/jjabtree.db")).expanduser()
        upload_default = str(db_path.parent / "uploads")
        admin_key = _required("ADMIN_APP_KEY")
        version = os.environ.get("IG_GRAPH_API_VERSION", "v25.0").strip() or "v25.0"
        if not version.startswith("v"):
            version = f"v{version}"

        return cls(
            ig_access_token=_required("IG_ACCESS_TOKEN"),
            ig_business_account_id=_required("IG_BUSINESS_ACCOUNT_ID"),
            admin_app_key=admin_key,
            db_path=db_path,
            upload_dir=Path(os.environ.get("UPLOAD_DIR", upload_default)).expanduser(),
            graph_api_version=version,
            graph_api_base_url=os.environ.get(
                "IG_GRAPH_API_BASE_URL", "https://graph.facebook.com"
            ).rstrip("/"),
            webhook_verify_token=os.environ.get(
                "META_WEBHOOK_VERIFY_TOKEN", admin_key
            ).strip(),
            meta_app_secret=os.environ.get("META_APP_SECRET") or None,
            cors_origins=_csv("CORS_ORIGINS", "*"),
        )
