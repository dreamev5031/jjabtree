from __future__ import annotations

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


def main() -> None:
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
    if not domain:
        raise RuntimeError("railway_public_domain_missing")
    base_url = f"https://{domain}"

    health_status, health_raw = finalizer.request("GET", f"{base_url}/health")
    health = finalizer.json_body(health_raw)
    if health_status != 200 or health.get("ok") is not True:
        raise RuntimeError("health_failed")

    products_status, products_raw = finalizer.request(
        "GET", f"{base_url}/api/public/products"
    )
    products_body = finalizer.json_body(products_raw)
    products = products_body.get("products")
    if products_status != 200 or not isinstance(products, list):
        raise RuntimeError("public_products_failed")

    safe_products: list[dict[str, Any]] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        media_id = str(item.get("ig_media_id") or "").strip()
        if not media_id:
            continue
        safe_products.append(
            {
                "id": int(item.get("id") or 0),
                "productName": str(item.get("product_name") or "")[:200],
                "instagramMediaId": media_id,
                "triggerPhrase": str(item.get("trigger_phrase") or "")[:100],
                "status": str(item.get("status") or ""),
            }
        )

    result = {
        "ok": True,
        "serviceName": service_name,
        "publicBaseUrl": base_url,
        "health": "ok",
        "callbackPath": "/api/webhooks/instagram",
        "activeProducts": safe_products,
        "activeProductCount": len(safe_products),
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
            handle.write("- secrets_printed: false\n")
            handle.write("- raw_webhook_payload_printed: false\n")


if __name__ == "__main__":
    main()
