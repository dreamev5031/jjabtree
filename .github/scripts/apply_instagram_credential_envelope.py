from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
ENVELOPE_PATH = ROOT_DIR / ".github" / "secure" / "instagram-credential-envelope.json"
OUTPUT = Path("jjabtree-credential-apply-result.json")
TARGET_PROJECT_ID = "ddce0da6-2fed-4e1d-8237-5cb218ee5864"
TARGET_SERVICE_ID = "156a6a4f-51dc-426b-aca0-89098619fd00"
TARGET_MEDIA_ID = "17895639444590243"
TARGET_PRODUCT_ID = 6
CONTEXT = b"autocard-to-jjabtree-instagram-credentials-v1"

SPEC = importlib.util.spec_from_file_location(
    "railway_remediate",
    SCRIPT_DIR / "railway_production_remediate.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("railway_remediate_module_unavailable")
remediate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remediate)


class ApplyError(RuntimeError):
    pass


def save(result: dict[str, Any]) -> None:
    safe = {
        **result,
        "plaintextPrinted": False,
        "secretsPrinted": False,
        "accessTokensPrinted": False,
        "rawWebhookPayloadPrinted": False,
    }
    OUTPUT.write_text(json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(safe, ensure_ascii=False))
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## jjabtree encrypted Instagram credential apply\n")
            for key in (
                "accountReadVerified", "commentsReadVerified", "messagesReadVerified",
                "webhookSubscriptionVerified", "railwayVariablesUpdated", "deploymentStatus",
                "health", "mappingStatus", "mediaCheckStatus", "accountHashMatched",
            ):
                if key in safe:
                    handle.write(f"- {key}: {safe[key]}\n")
            if "safeErrorCode" in safe:
                handle.write(f"- safeErrorCode: {safe['safeErrorCode']}\n")
            handle.write("- plaintext_printed: false\n")
            handle.write("- secrets_printed: false\n")


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
    request = urllib.request.Request(url, data=raw, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    except Exception as exc:
        raise ApplyError("http_request_failed") from exc
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def graph_json(
    method: str,
    base: str,
    path: str,
    token: str,
    *,
    params: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    query = urllib.parse.urlencode(params or {})
    url = f"{base}/{path.lstrip('/')}"
    if query:
        url += "?" + query
    return request_json(method, url, headers={"Authorization": f"Bearer {token}"})


def load_variables(service_id: str) -> dict[str, str]:
    return remediate.normalize_variables(
        remediate.json_command(
            "railway", "variable", "list",
            "--service", service_id,
            "--environment", "production",
            "--json",
        )
    )


def set_secret_variable(service_id: str, name: str, value: str) -> None:
    args = (
        "railway", "variable", "set", name, "--stdin",
        "--service", service_id,
        "--environment", "production",
        "--skip-deploys", "--json",
    )
    code, _, _ = remediate.command_with_input(args, value)
    if code != 0:
        raise ApplyError(f"variable_set_failed:{name}")


def decrypt_envelope(forward_secret: str) -> dict[str, Any]:
    try:
        envelope = json.loads(ENVELOPE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError("credential_envelope_invalid") from exc
    if envelope.get("version") != 1:
        raise ApplyError("credential_envelope_version_invalid")
    if envelope.get("targetProjectId") != TARGET_PROJECT_ID or envelope.get("targetServiceId") != TARGET_SERVICE_ID:
        raise ApplyError("credential_envelope_target_mismatch")
    now = int(time.time())
    if now < int(envelope.get("issuedAt") or 0) - 300 or now > int(envelope.get("expiresAt") or 0):
        raise ApplyError("credential_envelope_expired")
    try:
        aad = base64.urlsafe_b64decode(envelope["aad"])
        nonce = base64.urlsafe_b64decode(envelope["nonce"])
        ciphertext = base64.urlsafe_b64decode(envelope["ciphertext"])
    except (KeyError, ValueError) as exc:
        raise ApplyError("credential_envelope_encoding_invalid") from exc
    expected_aad = CONTEXT + b"|" + TARGET_PROJECT_ID.encode("ascii") + b"|" + TARGET_SERVICE_ID.encode("ascii")
    if aad != expected_aad:
        raise ApplyError("credential_envelope_aad_mismatch")
    salt = hashlib.sha256(CONTEXT + TARGET_PROJECT_ID.encode("ascii")).digest()
    key = hashlib.pbkdf2_hmac("sha256", forward_secret.encode("utf-8"), salt, 250_000, dklen=32)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ApplyError("credential_envelope_authentication_failed") from exc
    if not isinstance(payload, dict):
        raise ApplyError("credential_payload_invalid")
    if payload.get("targetProjectId") != TARGET_PROJECT_ID or payload.get("targetServiceId") != TARGET_SERVICE_ID:
        raise ApplyError("credential_payload_target_mismatch")
    if int(payload.get("expiresAt") or 0) < now:
        raise ApplyError("credential_payload_expired")
    return payload


def admin_json(
    method: str,
    base_url: str,
    admin_key: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if method != "GET" else None
    return request_json(
        method,
        f"{base_url}{path}",
        headers={"X-App-Key": admin_key},
        raw=raw,
    )


def main() -> None:
    service_id, service_name = remediate.discover_service()
    if service_id != TARGET_SERVICE_ID:
        raise ApplyError("railway_service_target_mismatch")
    variables = load_variables(service_id)
    forward_secret = variables.get("WEBHOOK_FORWARD_SECRET", "").strip()
    admin_key = variables.get("ADMIN_APP_KEY", "").strip()
    domain = variables.get("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    if not forward_secret or not admin_key or not domain:
        raise ApplyError("production_configuration_missing")
    base_url = f"https://{domain}"
    payload = decrypt_envelope(forward_secret)
    token = str(payload.get("instagramAccessToken") or "").strip()
    account_id = str(payload.get("instagramBusinessAccountId") or "").strip()
    if not token or not account_id:
        raise ApplyError("credential_payload_missing_values")

    api_version = variables.get("IG_API_VERSION", "v24.0").strip() or "v24.0"
    graph_base = variables.get("IG_GRAPH_BASE_URL", "https://graph.instagram.com").strip().rstrip("/")
    graph = f"{graph_base}/{api_version}"

    account_status, account_body = graph_json(
        "GET", graph, account_id, token, params={"fields": "id"}
    )
    account_ok = account_status == 200 and str(account_body.get("id") or "") == account_id
    comments_status, _ = graph_json(
        "GET", graph, f"{TARGET_MEDIA_ID}/comments", token,
        params={"fields": "id", "limit": "1"},
    )
    comments_ok = comments_status == 200
    messages_status, _ = graph_json(
        "GET", graph, f"{account_id}/conversations", token,
        params={"platform": "instagram", "fields": "id", "limit": "1"},
    )
    messages_ok = messages_status == 200
    subscription_status, subscription_body = graph_json(
        "POST", graph, f"{account_id}/subscribed_apps", token,
        params={"subscribed_fields": "comments,live_comments"},
    )
    subscription_ok = subscription_status == 200 and subscription_body.get("success") is True
    if not all((account_ok, comments_ok, messages_ok, subscription_ok)):
        save({
            "ok": False,
            "safeErrorCode": "credential_capability_gate_failed",
            "serviceName": service_name,
            "accountReadVerified": account_ok,
            "commentsReadVerified": comments_ok,
            "messagesReadVerified": messages_ok,
            "webhookSubscriptionVerified": subscription_ok,
            "accountHttpStatus": account_status,
            "commentsHttpStatus": comments_status,
            "messagesHttpStatus": messages_status,
            "subscriptionHttpStatus": subscription_status,
            "railwayVariablesUpdated": False,
        })
        raise ApplyError("credential_capability_gate_failed")

    before_ids = {row["id"] for row in remediate.list_deployments(service_id)}
    set_secret_variable(service_id, "IG_ACCESS_TOKEN", token)
    set_secret_variable(service_id, "IG_BUSINESS_ACCOUNT_ID", account_id)
    after_variables = load_variables(service_id)
    if after_variables.get("IG_BUSINESS_ACCOUNT_ID", "").strip() != account_id:
        raise ApplyError("account_variable_verification_failed")
    hinted = remediate.trigger_redeploy(service_id)
    deployment_id, deployment_status = remediate.wait_for_new_deployment(service_id, before_ids, hinted)
    health_status, health_ok = remediate.health_check(domain)
    if not health_ok:
        raise ApplyError("post_deploy_health_failed")

    media_status, media_body = admin_json(
        "POST", base_url, admin_key,
        f"/api/admin/products/{TARGET_PRODUCT_ID}/media-check",
    )
    check = media_body.get("check") if isinstance(media_body.get("check"), dict) else {}
    product = media_body.get("product") if isinstance(media_body.get("product"), dict) else {}
    media_ok = media_status == 200 and check.get("status") == "ok"
    if not media_ok:
        save({
            "ok": False,
            "safeErrorCode": "post_deploy_media_check_failed",
            "serviceName": service_name,
            "accountReadVerified": True,
            "commentsReadVerified": True,
            "messagesReadVerified": True,
            "webhookSubscriptionVerified": True,
            "railwayVariablesUpdated": True,
            "deploymentStatus": deployment_status,
            "health": "ok",
            "mediaCheckStatus": check.get("status") or "unknown",
            "mediaCheckHttpStatus": check.get("http_status") or media_status,
            "mappingStatus": product.get("status") or "unknown",
        })
        raise ApplyError("post_deploy_media_check_failed")

    if product.get("status") != "active":
        activate_status, activate_body = admin_json(
            "PATCH", base_url, admin_key,
            f"/api/admin/products/{TARGET_PRODUCT_ID}/status",
            {"status": "active"},
        )
        product = activate_body.get("product") if isinstance(activate_body.get("product"), dict) else {}
        if activate_status != 200 or product.get("status") != "active":
            raise ApplyError("mapping_activation_failed")

    final_variables = load_variables(service_id)
    account_hash = hashlib.sha256(final_variables.get("IG_BUSINESS_ACCOUNT_ID", "").strip().encode("utf-8")).hexdigest()[:16]
    source_hash = str(json.loads(ENVELOPE_PATH.read_text(encoding="utf-8")).get("sourceAccountHash") or "")
    result = {
        "ok": True,
        "serviceName": service_name,
        "accountReadVerified": True,
        "commentsReadVerified": True,
        "messagesReadVerified": True,
        "webhookSubscriptionVerified": True,
        "railwayVariablesUpdated": True,
        "deploymentId": deployment_id,
        "deploymentStatus": deployment_status,
        "healthStatus": health_status,
        "health": "ok",
        "mappingProductId": TARGET_PRODUCT_ID,
        "mappingStatus": "active",
        "mediaCheckStatus": "ok",
        "instagramMediaIdAbbreviated": f"{TARGET_MEDIA_ID[:6]}…{TARGET_MEDIA_ID[-4:]}",
        "accountHashMatched": account_hash == source_hash,
        "accountHash": account_hash,
        "sourceAccountHash": source_hash,
        "privateReplyCalls": 0,
        "realUserCommentTest": False,
    }
    if not result["accountHashMatched"]:
        raise ApplyError("post_apply_account_hash_mismatch")
    save(result)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if not OUTPUT.exists():
            save({"ok": False, "safeErrorCode": str(exc)[:240], "railwayVariablesUpdated": False})
        raise
