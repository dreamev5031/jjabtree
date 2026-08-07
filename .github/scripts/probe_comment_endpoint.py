from __future__ import annotations

import importlib.util
import json
import urllib.parse
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "credential_apply",
    SCRIPT_DIR / "apply_instagram_credential_envelope.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("credential_apply_module_unavailable")
apply = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apply)

OUTPUT = Path("comment-endpoint-probe.json")


def error_meta(body: dict[str, Any]) -> dict[str, Any]:
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return {
        "code": str(error.get("code")) if error.get("code") is not None else None,
        "subcode": str(error.get("error_subcode")) if error.get("error_subcode") is not None else None,
        "type": str(error.get("type") or "")[:80] or None,
        "isTransient": bool(error.get("is_transient")),
    }


def query_token_request(base: str, path: str, token: str) -> tuple[int, dict[str, Any]]:
    params = urllib.parse.urlencode({"fields": "id", "limit": "1", "access_token": token})
    return apply.request_json("GET", f"{base}/{path}?{params}")


def main() -> None:
    service_id, _ = apply.remediate.discover_service()
    variables = apply.load_variables(service_id)
    payload = apply.decrypt_envelope(variables.get("WEBHOOK_FORWARD_SECRET", "").strip())
    token = str(payload.get("instagramAccessToken") or "").strip()
    account_id = str(payload.get("instagramBusinessAccountId") or "").strip()
    version = variables.get("IG_API_VERSION", "v24.0").strip() or "v24.0"
    instagram_base = f"https://graph.instagram.com/{version}"
    facebook_base = f"https://graph.facebook.com/{version}"

    variants: dict[str, dict[str, Any]] = {}
    status, body = apply.graph_json(
        "GET", instagram_base, f"{apply.TARGET_MEDIA_ID}/comments", token,
        params={"fields": "id", "limit": "1"},
    )
    variants["instagramBearer"] = {"status": status, **error_meta(body)}

    status, body = query_token_request(
        instagram_base, f"{apply.TARGET_MEDIA_ID}/comments", token
    )
    variants["instagramQueryToken"] = {"status": status, **error_meta(body)}

    status, body = apply.graph_json(
        "GET", facebook_base, f"{apply.TARGET_MEDIA_ID}/comments", token,
        params={"fields": "id", "limit": "1"},
    )
    variants["facebookBearer"] = {"status": status, **error_meta(body)}

    status, body = apply.graph_json(
        "GET", instagram_base, f"{account_id}/media", token,
        params={"fields": "id,permalink", "limit": "1"},
    )
    variants["accountMedia"] = {"status": status, **error_meta(body)}

    result = {
        "ok": any(item["status"] == 200 for key, item in variants.items() if key != "accountMedia"),
        "variants": variants,
        "secretsPrinted": False,
        "accessTokensPrinted": False,
        "rawResponsesPrinted": False,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
