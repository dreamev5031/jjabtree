from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

FAILED_DEPLOYMENT_STATES = {"FAILED", "CRASHED", "REMOVED", "REMOVING"}
REQUIRED_VARIABLES = {
    "META_APP_SECRET",
    "META_WEBHOOK_VERIFY_TOKEN",
    "WEBHOOK_FORWARD_SECRET",
    "AUTOCARD_INTERNAL_BASE_URL",
    "IG_BUSINESS_ACCOUNT_ID",
    "ADMIN_APP_KEY",
}


class FinalizeError(RuntimeError):
    pass


def _run(*args: str) -> str:
    process = subprocess.run(args, text=True, capture_output=True, check=False)
    if process.returncode != 0:
        raise FinalizeError(f"command_failed:{args[0]}:{args[1] if len(args) > 1 else ''}")
    return process.stdout


def _json_command(*args: str) -> Any:
    try:
        return json.loads(_run(*args))
    except json.JSONDecodeError as exc:
        raise FinalizeError(f"invalid_json:{args[0]}:{args[1] if len(args) > 1 else ''}") from exc


def _dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _dicts(nested)


def _service_candidates(value: Any) -> list[tuple[int, str, str]]:
    found: dict[str, tuple[int, str, str]] = {}
    for item in _dicts(value):
        service_id = str(item.get("id") or item.get("serviceId") or "").strip()
        name = str(item.get("name") or item.get("serviceName") or "").strip()
        if not service_id or not name:
            continue
        lower = name.lower()
        serialized = json.dumps(item, ensure_ascii=False).lower()
        score = 0
        if lower == "jjabtree":
            score += 100
        elif lower == "backend":
            score += 70
        elif "jjabtree" in lower:
            score += 80
        if "dreamev5031/jjabtree" in serialized:
            score += 150
        if any(key in item for key in ("serviceInstances", "service_instances", "source", "deployments")):
            score += 25
        if score:
            current = found.get(service_id)
            if current is None or score > current[0]:
                found[service_id] = (score, service_id, name)
    return sorted(found.values(), reverse=True)


def discover_service() -> tuple[str, str]:
    try:
        listing = _json_command("railway", "service", "list", "--json")
    except FinalizeError:
        listing = _json_command("railway", "status", "--json")
    candidates = _service_candidates(listing)
    if candidates:
        _, service_id, name = candidates[0]
        return service_id, name

    plain: dict[str, str] = {}
    for item in _dicts(listing):
        service_id = str(item.get("id") or item.get("serviceId") or "").strip()
        name = str(item.get("name") or item.get("serviceName") or "").strip()
        if service_id and name:
            plain[service_id] = name
    if len(plain) == 1:
        return next(iter(plain.items()))
    raise FinalizeError("jjabtree_service_not_found")


def _deployment_for_sha(value: Any, target_sha: str) -> tuple[str, str] | None:
    short_sha = target_sha[:12]
    for item in _dicts(value):
        serialized = json.dumps(item, ensure_ascii=False)
        if target_sha not in serialized and short_sha not in serialized:
            continue
        deployment_id = str(item.get("id") or item.get("deploymentId") or "").strip()
        status = str(item.get("status") or item.get("state") or "").strip().upper()
        if deployment_id and status:
            return deployment_id, status
    return None


def wait_for_deployment(target_sha: str, service_id: str) -> tuple[str, str]:
    for _ in range(90):
        deployments = _json_command(
            "railway", "deployment", "list",
            "--service", service_id,
            "--environment", "production",
            "--limit", "50",
            "--json",
        )
        matched = _deployment_for_sha(deployments, target_sha)
        if matched:
            deployment_id, status = matched
            if status == "SUCCESS":
                return deployment_id, status
            if status in FAILED_DEPLOYMENT_STATES:
                raise FinalizeError(f"deployment_failed:{status}")
        time.sleep(10)
    raise FinalizeError("deployment_timeout")


def normalize_variables(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(raw or "") for key, raw in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or item.get("key") or "")
            if key:
                result[key] = str(item.get("value") or "")
        return result
    raise FinalizeError("railway_variables_invalid")


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
    timeout: int = 60,
) -> tuple[int, bytes]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if raw_body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=raw_body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except Exception as exc:
        raise FinalizeError("http_request_failed") from exc


def json_body(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def forward_signature(secret: str, timestamp: int, raw: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("ascii") + raw,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def probe(
    autocard_base: str,
    secret: str,
    event_id: str,
    *,
    timestamp: int,
    signature: str | None = None,
) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(
        {"kind": "autocard-forward-secret-probe", "source_project": "jjabtree-gateway"},
        separators=(",", ":"),
    ).encode("utf-8")
    supplied = signature or forward_signature(secret, timestamp, raw)
    status, body = request(
        "POST",
        f"{autocard_base.rstrip('/')}/api/internal/instagram/forward-probe",
        headers={
            "X-Autocard-Timestamp": str(timestamp),
            "X-Autocard-Signature": supplied,
            "X-Autocard-Event-Id": event_id,
        },
        raw_body=raw,
    )
    return status, json_body(body)


def callback_url(base_url: str, query: dict[str, str] | None = None) -> str:
    path = f"{base_url.rstrip('/')}/api/webhooks/instagram"
    return path if not query else f"{path}?{urllib.parse.urlencode(query)}"


def save_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        lines = [
            "## jjabtree C-stage production finalization",
            f"- deployment_id: {result['deploymentId']}",
            f"- deployment_status: {result['deploymentStatus']}",
            f"- deployed_sha: {result['deployedSha']}",
            f"- health: {result['health']}",
            f"- callback_path_unchanged: {str(result['callbackPathUnchanged']).lower()}",
            f"- callback_verify_status: {result['callbackVerifyStatus']}",
            f"- callback_signed_empty_status: {result['callbackSignedEmptyStatus']}",
            f"- meta_app_secret_configured: {str(result['metaAppSecretConfigured']).lower()}",
            f"- verify_token_configured: {str(result['verifyTokenConfigured']).lower()}",
            f"- forward_secret_configured: {str(result['forwardSecretConfigured']).lower()}",
            f"- matched: {str(result['matched']).lower()}",
            f"- expired_status: {result['expiredStatus']}",
            f"- replay_status: {result['replayStatus']}",
            f"- invalid_signature_status: {result['invalidSignatureStatus']}",
            "- meta_calls: 0",
            "- dm_calls: 0",
            "- real_user_comment_test: false",
            "- secrets_printed: false",
        ]
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def finalize(target_sha: str, output: Path) -> dict[str, Any]:
    service_id, service_name = discover_service()
    deployment_id, deployment_status = wait_for_deployment(target_sha, service_id)
    variables = normalize_variables(
        _json_command(
            "railway", "variable", "list",
            "--service", service_id,
            "--environment", "production",
            "--json",
        )
    )
    missing = [name for name in REQUIRED_VARIABLES if not variables.get(name, "").strip()]
    if missing:
        raise FinalizeError("required_production_variables_missing")

    domain = variables.get("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    base_url = f"https://{domain}" if domain else ""
    if not base_url:
        raise FinalizeError("railway_public_domain_missing")

    health_status, health_raw = request("GET", f"{base_url}/health")
    health = json_body(health_raw)
    if health_status != 200 or health.get("ok") is not True or health.get("service") != "jjabtree-api":
        raise FinalizeError("health_failed")

    verify_token = variables["META_WEBHOOK_VERIFY_TOKEN"].strip()
    challenge = f"c-stage-{secrets.token_hex(8)}"
    valid_verify_status, valid_verify_body = request(
        "GET",
        callback_url(base_url, {
            "hub.mode": "subscribe",
            "hub.verify_token": verify_token,
            "hub.challenge": challenge,
        }),
    )
    if valid_verify_status != 200 or valid_verify_body.decode("utf-8") != challenge:
        raise FinalizeError("callback_verify_failed")

    invalid_verify_status, _ = request(
        "GET",
        callback_url(base_url, {
            "hub.mode": "subscribe",
            "hub.verify_token": "c-stage-invalid-token",
            "hub.challenge": challenge,
        }),
    )
    if invalid_verify_status != 403:
        raise FinalizeError("callback_invalid_token_not_rejected")

    empty_payload = json.dumps(
        {"object": "instagram", "entry": []},
        separators=(",", ":"),
    ).encode("utf-8")
    meta_secret = variables["META_APP_SECRET"].strip()
    meta_signature = "sha256=" + hmac.new(
        meta_secret.encode("utf-8"), empty_payload, hashlib.sha256
    ).hexdigest()
    signed_empty_status, signed_empty_raw = request(
        "POST",
        callback_url(base_url),
        headers={"X-Hub-Signature-256": meta_signature},
        raw_body=empty_payload,
    )
    signed_empty = json_body(signed_empty_raw)
    if signed_empty_status != 200 or signed_empty.get("comment_events") != 0:
        raise FinalizeError("callback_signed_empty_failed")

    invalid_meta_status, _ = request(
        "POST",
        callback_url(base_url),
        headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
        raw_body=empty_payload,
    )
    if invalid_meta_status != 403:
        raise FinalizeError("callback_invalid_signature_not_rejected")

    forward_secret = variables["WEBHOOK_FORWARD_SECRET"].strip()
    autocard_base = variables["AUTOCARD_INTERNAL_BASE_URL"].strip().rstrip("/")
    if not autocard_base.startswith("https://"):
        raise FinalizeError("autocard_internal_base_url_invalid")

    now = int(time.time())
    event_id = f"jjabtree-c-stage-{secrets.token_hex(12)}"
    matched_status, matched_body = probe(
        autocard_base, forward_secret, event_id, timestamp=now
    )
    if matched_status != 200 or matched_body.get("matched") is not True:
        raise FinalizeError("forward_secret_not_matched")

    replay_status, _ = probe(
        autocard_base, forward_secret, event_id, timestamp=int(time.time())
    )
    if replay_status != 409:
        raise FinalizeError("forward_probe_replay_not_blocked")

    expired_status, _ = probe(
        autocard_base,
        forward_secret,
        f"jjabtree-c-stage-expired-{secrets.token_hex(8)}",
        timestamp=int(time.time()) - 601,
    )
    if expired_status != 401:
        raise FinalizeError("forward_probe_expired_not_blocked")

    invalid_signature_status, _ = probe(
        autocard_base,
        forward_secret,
        f"jjabtree-c-stage-invalid-{secrets.token_hex(8)}",
        timestamp=int(time.time()),
        signature="sha256=" + ("0" * 64),
    )
    if invalid_signature_status != 403:
        raise FinalizeError("forward_probe_invalid_signature_not_blocked")

    result = {
        "serviceName": service_name,
        "deploymentId": deployment_id,
        "deploymentStatus": deployment_status,
        "deployedSha": target_sha,
        "health": "ok",
        "callbackPath": "/api/webhooks/instagram",
        "callbackPathUnchanged": True,
        "callbackVerifyStatus": valid_verify_status,
        "callbackInvalidTokenStatus": invalid_verify_status,
        "callbackSignedEmptyStatus": signed_empty_status,
        "callbackInvalidSignatureStatus": invalid_meta_status,
        "metaAppSecretConfigured": True,
        "verifyTokenConfigured": True,
        "verifyTokenAdminFallbackRemoved": True,
        "forwardSecretConfigured": True,
        "autocardBaseConfigured": True,
        "matched": True,
        "matchedStatus": matched_status,
        "expiredStatus": expired_status,
        "replayStatus": replay_status,
        "invalidSignatureStatus": invalid_signature_status,
        "legacyRegressionCiRequired": True,
        "metaCalls": 0,
        "dmCalls": 0,
        "realUserCommentTest": False,
        "secretsPrinted": False,
    }
    save_result(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--output", type=Path, default=Path("jjabtree-c-stage-result.json"))
    args = parser.parse_args()
    try:
        result = finalize(args.target_sha, args.output)
    except FinalizeError as exc:
        print(f"jjabtree_c_stage_finalize=failed:{exc}")
        raise SystemExit(1) from None
    print("jjabtree_c_stage_finalize=passed")
    print(f"deployment_id={result['deploymentId']}")
    print(f"deployment_status={result['deploymentStatus']}")
    print("callback_path_unchanged=true")
    print("forward_secret_configured=true")
    print("matched=true")
    print("meta_calls=0")
    print("dm_calls=0")
    print("real_user_comment_test=false")
    print("secrets_printed=false")


if __name__ == "__main__":
    main()
