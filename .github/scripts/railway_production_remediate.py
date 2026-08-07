from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from railway_production_diagnose import (
    DiagnoseError,
    discover_service,
    json_command,
    normalize_variables,
    walk_dicts,
)

TERMINAL_FAILURE = {"FAILED", "CRASHED", "REMOVED"}
PENDING = {"BUILDING", "DEPLOYING", "INITIALIZING", "WAITING", "QUEUED", "REMOVING"}


def command_with_input(args: tuple[str, ...], value: str) -> tuple[int, str, str]:
    process = subprocess.run(
        args,
        input=value,
        text=True,
        capture_output=True,
        check=False,
    )
    return process.returncode, process.stdout, process.stderr


def deployment_rows(value: Any) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for item in walk_dicts(value):
        deployment_id = str(item.get("id") or item.get("deploymentId") or "").strip()
        status = str(item.get("status") or item.get("state") or "").strip().upper()
        if deployment_id and status:
            rows.setdefault(
                deployment_id,
                {
                    "id": deployment_id,
                    "status": status,
                    "sha": str(
                        item.get("commitSha")
                        or item.get("commit_sha")
                        or item.get("sha")
                        or item.get("source")
                        or ""
                    ),
                },
            )
    return list(rows.values())


def list_deployments(service_id: str) -> list[dict[str, str]]:
    value = json_command(
        "railway",
        "deployment",
        "list",
        "--service",
        service_id,
        "--environment",
        "production",
        "--limit",
        "100",
        "--json",
    )
    return deployment_rows(value)


def extract_deployment_id(raw: str) -> str | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    for item in walk_dicts(parsed):
        deployment_id = str(item.get("id") or item.get("deploymentId") or "").strip()
        if deployment_id:
            return deployment_id
    return None


def set_autocard_url(service_id: str, expected_url: str) -> None:
    args = (
        "railway",
        "variable",
        "set",
        "AUTOCARD_INTERNAL_BASE_URL",
        "--stdin",
        "--service",
        service_id,
        "--environment",
        "production",
        "--skip-deploys",
        "--json",
    )
    code, _, _ = command_with_input(args, expected_url)
    if code != 0:
        fallback = (
            "railway",
            "variable",
            "set",
            f"AUTOCARD_INTERNAL_BASE_URL={expected_url}",
            "--service",
            service_id,
            "--environment",
            "production",
            "--skip-deploys",
            "--json",
        )
        process = subprocess.run(fallback, text=True, capture_output=True, check=False)
        if process.returncode != 0:
            raise DiagnoseError("autocard_url_variable_set_failed")


def trigger_redeploy(service_id: str) -> str | None:
    commands = (
        (
            "railway",
            "redeploy",
            "--service",
            service_id,
            "--environment",
            "production",
            "--yes",
            "--json",
        ),
        (
            "railway",
            "service",
            "redeploy",
            "--service",
            service_id,
            "--environment",
            "production",
            "--yes",
            "--json",
        ),
    )
    for args in commands:
        process = subprocess.run(args, text=True, capture_output=True, check=False)
        if process.returncode == 0:
            return extract_deployment_id(process.stdout)
    raise DiagnoseError("railway_redeploy_failed")


def wait_for_new_deployment(
    service_id: str,
    before_ids: set[str],
    hinted_id: str | None,
) -> tuple[str, str]:
    candidate_id = hinted_id
    for _ in range(90):
        rows = list_deployments(service_id)
        if candidate_id:
            matching = next((row for row in rows if row["id"] == candidate_id), None)
        else:
            matching = next((row for row in rows if row["id"] not in before_ids), None)
            if matching:
                candidate_id = matching["id"]
        if matching:
            status = matching["status"]
            if status == "SUCCESS":
                return matching["id"], status
            if status in TERMINAL_FAILURE:
                raise DiagnoseError(f"redeploy_failed:{status}")
            if status not in PENDING:
                raise DiagnoseError(f"redeploy_unknown_status:{status}")
        time.sleep(5)
    raise DiagnoseError("redeploy_timeout")


def health_check(public_domain: str) -> tuple[int | None, bool]:
    if not public_domain:
        return None, False
    url = "https://" + public_domain.strip().strip("/") + "/health"
    for _ in range(24):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                body = response.read(2048).decode("utf-8", errors="replace")
                return response.status, response.status == 200 and "jjabtree" in body.lower()
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5)
    return None, False


def remediate(expected_url: str, output: Path) -> dict[str, Any]:
    service_id, service_name = discover_service()
    before_variables = normalize_variables(
        json_command(
            "railway",
            "variable",
            "list",
            "--service",
            service_id,
            "--environment",
            "production",
            "--json",
        )
    )
    if not before_variables.get("WEBHOOK_FORWARD_SECRET", "").strip():
        raise DiagnoseError("webhook_forward_secret_not_configured")

    before_rows = list_deployments(service_id)
    before_ids = {row["id"] for row in before_rows}

    set_autocard_url(service_id, expected_url)

    after_variables = normalize_variables(
        json_command(
            "railway",
            "variable",
            "list",
            "--service",
            service_id,
            "--environment",
            "production",
            "--json",
        )
    )
    actual_url = after_variables.get("AUTOCARD_INTERNAL_BASE_URL", "").strip().rstrip("/")
    expected = expected_url.rstrip("/")
    if actual_url != expected:
        raise DiagnoseError("autocard_url_mismatch_after_set")

    hinted_id = trigger_redeploy(service_id)
    deployment_id, deployment_status = wait_for_new_deployment(
        service_id,
        before_ids,
        hinted_id,
    )

    final_variables = normalize_variables(
        json_command(
            "railway",
            "variable",
            "list",
            "--service",
            service_id,
            "--environment",
            "production",
            "--json",
        )
    )
    health_status, health_ok = health_check(
        final_variables.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    )
    if not health_ok:
        raise DiagnoseError("health_check_failed")

    result = {
        "serviceName": service_name,
        "railwayAccess": True,
        "railwayTokenConfigured": bool(os.environ.get("RAILWAY_TOKEN", "").strip()),
        "webhookForwardSecretConfigured": bool(
            final_variables.get("WEBHOOK_FORWARD_SECRET", "").strip()
        ),
        "autocardInternalBaseUrlConfigured": bool(
            final_variables.get("AUTOCARD_INTERNAL_BASE_URL", "").strip()
        ),
        "autocardInternalBaseUrlMatched": (
            final_variables.get("AUTOCARD_INTERNAL_BASE_URL", "").strip().rstrip("/")
            == expected
        ),
        "deploymentId": deployment_id,
        "deploymentStatus": deployment_status,
        "healthStatus": health_status,
        "health": "ok",
        "metaCalls": 0,
        "dmCalls": 0,
        "realUserCommentTest": False,
        "secretsPrinted": False,
    }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--autocard-url", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("jjabtree-railway-remediation.json"),
    )
    args = parser.parse_args()
    try:
        result = remediate(args.autocard_url, args.output)
    except DiagnoseError as exc:
        safe = {
            "railwayTokenConfigured": bool(os.environ.get("RAILWAY_TOKEN", "").strip()),
            "remediationError": str(exc),
            "metaCalls": 0,
            "dmCalls": 0,
            "realUserCommentTest": False,
            "secretsPrinted": False,
        }
        args.output.write_text(
            json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"jjabtree_railway_remediate=failed:{exc}")
        raise SystemExit(1)
    print(
        "jjabtree_railway_remediate=complete:"
        f"deployment={result['deploymentId']}:"
        f"status={result['deploymentStatus']}:health=ok"
    )


if __name__ == "__main__":
    main()
