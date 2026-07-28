from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# app.main intentionally fails fast when required production variables are missing.
os.environ.setdefault("IG_ACCESS_TOKEN", "test-token")
os.environ.setdefault("IG_BUSINESS_ACCOUNT_ID", "17841400000000000")
os.environ.setdefault("ADMIN_APP_KEY", "test-admin-key")
os.environ.setdefault("PUBLIC_SITE_URL", "https://jjabtree.pages.dev")

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        ig_access_token="test-token",
        ig_business_account_id="17841400000000000",
        admin_app_key="test-admin-key",
        db_path=tmp_path / "jjabtree.db",
        upload_dir=tmp_path / "uploads",
        graph_api_version="v25.0",
        graph_api_base_url="https://graph.facebook.com",
        webhook_verify_token="verify-me",
        meta_app_secret=None,
        cors_origins=["*"],
        public_site_url="https://jjabtree.pages.dev",
    )


@pytest.fixture()
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
