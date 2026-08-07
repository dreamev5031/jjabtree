from __future__ import annotations

import importlib.util
import json
import os
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

TARGET_PERMALINK = "https://www.instagram.com/p/DbuhF1OG7hT/"
OUTPUT = Path("resolved-target-media.json")


def normalize_permalink(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    return parsed.path.rstrip("/").lower()


def safe_error(body: dict[str, Any]) -> tuple[str | None, str | None]:
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = str(error.get("code")) if error.get("code") is not None else None
    subcode = str(error.get("error_subcode")) if error.get("error_subcode") is not None else None
    return code, subcode


def main() -> None:
    service_id, service_name = apply.remediate.discover_service()
    variables = apply.load_variables(service_id)
    forward_secret = variables.get("WEBHOOK_FORWARD_SECRET", "").strip()
    if not forward_secret:
        raise RuntimeError("forward_secret_missing")
    payload = apply.decrypt_envelope(forward_secret)
    token = str(payload.get("instagramAccessToken") or "").strip()
    account_id = str(payload.get("instagramBusinessAccountId") or "").strip()
    if not token or not account_id:
        raise RuntimeError("credential_payload_missing_values")

    api_version = variables.get("IG_API_VERSION", "v24.0").strip() or "v24.0"
    graph_base = variables.get("IG_GRAPH_BASE_URL", "https://graph.instagram.com").strip().rstrip("/")
    graph = f"{graph_base}/{api_version}"

    direct_status, direct_body = apply.graph_json(
        "GET", graph, apply.TARGET_MEDIA_ID, token,
        params={"fields": "id,permalink"},
    )
    direct_code, direct_subcode = safe_error(direct_body)

    status, body = apply.graph_json(
        "GET", graph, f"{account_id}/media", token,
        params={"fields": "id,permalink", "limit": "100"},
    )
    if status != 200:
        code, subcode = safe_error(body)
        result = {
            "ok": False,
            "safeErrorCode": "account_media_list_failed",
            "serviceName": service_name,
            "accountMediaHttpStatus": status,
            "accountMediaErrorCode": code,
            "accountMediaErrorSubcode": subcode,
            "directMediaHttpStatus": direct_status,
            "directMediaErrorCode": direct_code,
            "directMediaErrorSubcode": direct_subcode,
        }
    else:
        data = body.get("data") if isinstance(body.get("data"), list) else []
        target_path = normalize_permalink(TARGET_PERMALINK)
        matched = next(
            (
                item for item in data
                if isinstance(item, dict)
                and normalize_permalink(str(item.get("permalink") or "")) == target_path
            ),
            None,
        )
        resolved_id = str(matched.get("id") or "") if isinstance(matched, dict) else ""
        result = {
            "ok": bool(resolved_id),
            "safeErrorCode": None if resolved_id else "target_permalink_not_found_in_account_media",
            "serviceName": service_name,
            "scannedMediaCount": len(data),
            "targetPermalinkPath": target_path,
            "resolvedMediaId": resolved_id or None,
            "resolvedMediaIdAbbreviated": (
                f"{resolved_id[:6]}…{resolved_id[-4:]}" if len(resolved_id) > 10 else resolved_id or None
            ),
            "storedMediaIdMatched": resolved_id == apply.TARGET_MEDIA_ID if resolved_id else False,
            "directMediaHttpStatus": direct_status,
            "directMediaErrorCode": direct_code,
            "directMediaErrorSubcode": direct_subcode,
        }
    result.update({
        "secretsPrinted": False,
        "accessTokensPrinted": False,
        "rawWebhookPayloadPrinted": False,
    })
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "ok": result.get("ok"),
        "safeErrorCode": result.get("safeErrorCode"),
        "scannedMediaCount": result.get("scannedMediaCount"),
        "resolvedMediaIdAbbreviated": result.get("resolvedMediaIdAbbreviated"),
        "storedMediaIdMatched": result.get("storedMediaIdMatched"),
        "directMediaHttpStatus": result.get("directMediaHttpStatus"),
        "directMediaErrorCode": result.get("directMediaErrorCode"),
        "secretsPrinted": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
