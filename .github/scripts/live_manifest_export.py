from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "jjabtree_finalize",
    SCRIPT_DIR / "jjabtree_c_stage_finalize.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("finalizer_module_unavailable")
finalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalizer)


def _safe_product(item: dict[str, Any]) -> dict[str, Any] | None:
    media_id = str(item.get("ig_media_id") or "").strip()
    if not media_id:
        return None
    return {
        "id": int(item.get("id") or 0),
        "productName": str(item.get("product_name") or "")[:200],
        "instagramMediaId": media_id,
        "triggerPhrase": str(item.get("trigger_phrase") or "")[:100],
        "status": str(item.get("status") or ""),
        "mediaCheckStatus": str(item.get("media_check_status") or ""),
        "instagramPermalinkConfigured": bool(str(item.get("ig_permalink") or "").strip()),
    }


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:16] if value.strip() else ""


def main() -> None:
    status_info = finalizer._json_command("railway", "status", "--json")
    service_id, service_name = finalizer.discover_service()
    variables = finalizer.normalize_variables(
        finalizer._json_command(
            "railway", "variable", "list",
            "--service", service_id,
            "--environment", "production",
            "--json",
        )
    )
    domain = variables.get("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    admin_key = variables.get("ADMIN_APP_KEY", "").strip()
    if not domain:
        raise RuntimeError("railway_public_domain_missing")
    if not admin_key:
        raise RuntimeError("admin_key_missing")
    base_url = f"https://{domain}"

    health_status, health_raw = finalizer.request("GET", f"{base_url}/health")
    health = finalizer.json_body(health_raw)
    if health_status != 200 or health.get("ok") is not True:
        raise RuntimeError("health_failed")

    public_status, public_raw = finalizer.request(
        "GET", f"{base_url}/api/public/products"
    )
    public_body = finalizer.json_body(public_raw)
    public_products = public_body.get("products")
    if public_status != 200 or not isinstance(public_products, list):
        raise RuntimeError("public_products_failed")

    admin_status, admin_raw = finalizer.request(
        "GET",
        f"{base_url}/api/admin/products",
        headers={"X-App-Key": admin_key},
    )
    admin_body = finalizer.json_body(admin_raw)
    admin_products = admin_body.get("products")
    if admin_status != 200 or not isinstance(admin_products, list):
        raise RuntimeError("admin_products_failed")

    safe_public = [safe for item in public_products if isinstance(item, dict) and (safe := _safe_product(item))]
    safe_admin = [safe for item in admin_products if isinstance(item, dict) and (safe := _safe_product(item))]
    project_id = str(status_info.get("id") or status_info.get("projectId") or "") if isinstance(status_info, dict) else ""
    project_name = str(status_info.get("name") or status_info.get("projectName") or "") if isinstance(status_info, dict) else ""

    result = {
        "ok": True,
        "railwayProjectId": project_id,
        "railwayProjectName": project_name,
        "railwayServiceId": service_id,
        "serviceName": service_name,
        "instagramAccountHash": _hash_identifier(variables.get("IG_BUSINESS_ACCOUNT_ID", "")),
        "publicBaseUrl": base_url,
        "health": "ok",
        "callbackPath": "/api/webhooks/instagram",
        "activeProducts": safe_public,
        "activeProductCount": len(safe_public),
        "adminProducts": safe_admin,
        "adminProductCount": len(safe_admin),
        "secretsPrinted": False,
        "accessTokensPrinted": False,
        "rawWebhookPayloadPrinted": False,
    }
    output = Path("jjabtree-live-manifest.json")
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))

    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## jjabtree live manifest\n")
            handle.write(f"- health: {result['health']}\n")
            handle.write(f"- active_product_count: {result['activeProductCount']}\n")
            handle.write(f"- admin_product_count: {result['adminProductCount']}\n")
            handle.write(f"- instagram_account_hash: {result['instagramAccountHash']}\n")
            handle.write("- secrets_printed: false\n")
            handle.write("- raw_webhook_payload_printed: false\n")


if __name__ == "__main__":
    main()
