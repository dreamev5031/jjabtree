from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
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

TARGET_NAME = "피지오겔 데일리 모이스쳐 테라피 페이셜 크림, 75ml, 1개"
TARGET_MEDIA_ID = "17895639444590243"
TARGET_PERMALINK = "https://www.instagram.com/p/DbuhF1OG7hT/"
TARGET_IMAGE_URL = "https://autocard-production-726e.up.railway.app/media/uploads/피지오겔-데일리-모이스쳐-테라피-페이셜-크림-75ml-1개-567a9d867c6f43b1846c780e725027f3.jpg"
TARGET_TRIGGER = "링크"
OUTPUT = Path("jjabtree-live-mapping-result.json")


class PrepareError(RuntimeError):
    pass


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, Any]]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if raw is not None:
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=raw, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(response.status)
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    except Exception as exc:
        raise PrepareError("http_request_failed") from exc
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def write_result(result: dict[str, Any]) -> None:
    safe = {
        **result,
        "secretsPrinted": False,
        "accessTokensPrinted": False,
        "rawWebhookPayloadPrinted": False,
    }
    OUTPUT.write_text(json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(safe, ensure_ascii=False))


def set_status(base_url: str, admin_key: str, product_id: int, status_value: str) -> None:
    raw = json.dumps({"status": status_value}, separators=(",", ":")).encode("utf-8")
    response_status, _ = request_json(
        "PATCH",
        f"{base_url}/api/admin/products/{product_id}/status",
        headers={"X-App-Key": admin_key},
        raw=raw,
    )
    if response_status != 200:
        raise PrepareError(f"mapped_product_{status_value}_failed")


def download_image() -> tuple[bytes, str, str]:
    encoded = urllib.parse.quote(TARGET_IMAGE_URL, safe=":/?=&%")
    request = urllib.request.Request(encoded, headers={"User-Agent": "jjabtree-live-preparation"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            content_type = (response.headers.get_content_type() or "image/jpeg").lower()
    except Exception as exc:
        raise PrepareError("target_image_download_failed") from exc
    if not data or len(data) > 8 * 1024 * 1024:
        raise PrepareError("target_image_invalid_size")
    if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise PrepareError("target_image_invalid_type")
    extension = mimetypes.guess_extension(content_type) or ".jpg"
    if extension == ".jpe":
        extension = ".jpg"
    return data, f"live-target{extension}", content_type


def multipart_body(fields: dict[str, str], file_data: bytes, filename: str, content_type: str) -> tuple[bytes, str]:
    boundary = f"----jjabtree-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        file_data,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


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
    admin_key = variables.get("ADMIN_APP_KEY", "").strip()
    if not domain or not admin_key:
        raise PrepareError("production_configuration_missing")
    base_url = f"https://{domain}"

    status, products_body = request_json(
        "GET", f"{base_url}/api/admin/products", headers={"X-App-Key": admin_key}
    )
    products = products_body.get("products")
    if status != 200 or not isinstance(products, list):
        raise PrepareError("admin_products_failed")
    existing = next(
        (
            item for item in products
            if isinstance(item, dict) and str(item.get("ig_media_id") or "") == TARGET_MEDIA_ID
        ),
        None,
    )
    created = False
    subscription_ok: bool | None = None
    if existing is None:
        image_data, filename, content_type = download_image()
        raw, boundary = multipart_body(
            {
                "product_name": TARGET_NAME,
                "purchase_link": TARGET_PERMALINK,
                "trigger_phrase": TARGET_TRIGGER,
                "ig_media_id": TARGET_MEDIA_ID,
                "ig_permalink": TARGET_PERMALINK,
            },
            image_data,
            filename,
            content_type,
        )
        create_status, create_body = request_json(
            "POST",
            f"{base_url}/api/admin/products",
            headers={
                "X-App-Key": admin_key,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            raw=raw,
        )
        if create_status != 201:
            raise PrepareError(f"product_create_failed:{create_status}")
        existing = create_body.get("product")
        subscription = create_body.get("webhook_subscription")
        subscription_ok = bool(subscription.get("ok")) if isinstance(subscription, dict) else None
        created = True
    if not isinstance(existing, dict):
        raise PrepareError("mapped_product_missing")

    product_id = int(existing.get("id") or 0)
    if product_id <= 0:
        raise PrepareError("mapped_product_id_invalid")
    if str(existing.get("status") or "") != "active":
        set_status(base_url, admin_key, product_id, "active")

    check_status, check_body = request_json(
        "POST",
        f"{base_url}/api/admin/products/{product_id}/media-check",
        headers={"X-App-Key": admin_key},
        raw=b"{}",
    )
    if check_status != 200:
        set_status(base_url, admin_key, product_id, "inactive")
        write_result({
            "ok": False,
            "safeErrorCode": f"media_check_http_{check_status}",
            "serviceName": service_name,
            "productId": product_id,
            "productName": TARGET_NAME,
            "instagramMediaIdAbbreviated": f"{TARGET_MEDIA_ID[:6]}…{TARGET_MEDIA_ID[-4:]}",
            "status": "inactive",
            "created": created,
        })
        raise PrepareError(f"media_check_failed:{check_status}")

    check = check_body.get("check") if isinstance(check_body.get("check"), dict) else {}
    checked = check_body.get("product") if isinstance(check_body.get("product"), dict) else {}
    media_status = str(check.get("status") or checked.get("media_check_status") or "unknown")
    if media_status != "ok":
        set_status(base_url, admin_key, product_id, "inactive")
        write_result({
            "ok": False,
            "safeErrorCode": "media_check_not_ok",
            "serviceName": service_name,
            "productId": product_id,
            "productName": TARGET_NAME,
            "instagramMediaIdAbbreviated": f"{TARGET_MEDIA_ID[:6]}…{TARGET_MEDIA_ID[-4:]}",
            "triggerPhrase": TARGET_TRIGGER,
            "status": "inactive",
            "mediaCheckStatus": media_status,
            "mediaCheckHttpStatus": check.get("http_status"),
            "mediaCheckErrorCode": check.get("error_code"),
            "mediaCheckErrorSubcode": check.get("error_subcode"),
            "created": created,
            "webhookSubscriptionOk": subscription_ok,
        })
        raise PrepareError("media_check_not_ok")
    if str(checked.get("trigger_phrase") or "").strip() != TARGET_TRIGGER:
        set_status(base_url, admin_key, product_id, "inactive")
        raise PrepareError("trigger_phrase_mismatch")
    if str(checked.get("ig_media_id") or "").strip() != TARGET_MEDIA_ID:
        set_status(base_url, admin_key, product_id, "inactive")
        raise PrepareError("media_id_mismatch")
    if str(checked.get("status") or "") != "active":
        raise PrepareError("mapped_product_not_active")

    result = {
        "ok": True,
        "serviceName": service_name,
        "productId": product_id,
        "productName": TARGET_NAME,
        "instagramMediaIdAbbreviated": f"{TARGET_MEDIA_ID[:6]}…{TARGET_MEDIA_ID[-4:]}",
        "triggerPhrase": TARGET_TRIGGER,
        "status": "active",
        "mediaCheckStatus": "ok",
        "created": created,
        "webhookSubscriptionOk": subscription_ok,
        "publicReplyTemplateCount": 7,
        "gptApiUsed": False,
    }
    write_result(result)
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## jjabtree live mapping preparation\n")
            handle.write(f"- product_id: {product_id}\n")
            handle.write(f"- product_name: {TARGET_NAME}\n")
            handle.write(f"- instagram_media_id: {result['instagramMediaIdAbbreviated']}\n")
            handle.write("- trigger_phrase: 링크\n")
            handle.write("- status: active\n")
            handle.write("- media_check_status: ok\n")
            handle.write("- secrets_printed: false\n")


if __name__ == "__main__":
    main()
