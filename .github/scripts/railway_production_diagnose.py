from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

TRACKED_VARIABLES = (
    "META_APP_SECRET",
    "META_WEBHOOK_VERIFY_TOKEN",
    "WEBHOOK_FORWARD_SECRET",
    "IG_ACCESS_TOKEN",
    "IG_BUSINESS_ACCOUNT_ID",
    "IG_GRAPH_API_VERSION",
    "ADMIN_APP_KEY",
    "DB_PATH",
    "AUTOCARD_INTERNAL_BASE_URL",
)

SECRET_VARIABLES = {
    "META_APP_SECRET",
    "META_WEBHOOK_VERIFY_TOKEN",
    "WEBHOOK_FORWARD_SECRET",
    "IG_ACCESS_TOKEN",
    "ADMIN_APP_KEY",
    "RAILWAY_TOKEN",
}

ERROR_HINTS = (
    "error",
    "failed",
    "failure",
    "traceback",
    "exception",
    "configurationerror",
    "healthcheck",
    "exit code",
    "exited",
    "missing",
    "not found",
    "no such file",
    "module",
    "import",
    "permission denied",
    "address already in use",
)


class DiagnoseError(RuntimeError):
    pass


def run_command(*args: str) -> tuple[int, str, str]:
    process = subprocess.run(args, text=True, capture_output=True, check=False)
    return process.returncode, process.stdout, process.stderr


def json_command(*args: str) -> Any:
    code, stdout, _ = run_command(*args)
    if code != 0:
        raise DiagnoseError(f"command_failed:{args[0]}:{args[1] if len(args) > 1 else ''}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DiagnoseError(f"invalid_json:{args[0]}:{args[1] if len(args) > 1 else ''}") from exc


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_dicts(nested)


def service_candidates(value: Any) -> list[tuple[int, str, str]]:
    found: dict[str, tuple[int, str, str]] = {}
    for item in walk_dicts(value):
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
        listing = json_command("railway", "service", "list", "--json")
    except DiagnoseError:
        listing = json_command("railway", "status", "--json")
    candidates = service_candidates(listing)
    if candidates:
        _, service_id, name = candidates[0]
        return service_id, name
    plain: dict[str, str] = {}
    for item in walk_dicts(listing):
        service_id = str(item.get("id") or item.get("serviceId") or "").strip()
        name = str(item.get("name") or item.get("serviceName") or "").strip()
        if service_id and name:
            plain[service_id] = name
    if len(plain) == 1:
        return next(iter(plain.items()))
    raise DiagnoseError("jjabtree_service_not_found")


def deployment_for_sha(value: Any, target_sha: str) -> tuple[str, str] | None:
    short_sha = target_sha[:12]
    for item in walk_dicts(value):
        serialized = json.dumps(item, ensure_ascii=False)
        if target_sha not in serialized and short_sha not in serialized:
            continue
        deployment_id = str(item.get("id") or item.get("deploymentId") or "").strip()
        status = str(item.get("status") or item.get("state") or "").strip().upper()
        if deployment_id and status:
            return deployment_id, status
    return None


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
    raise DiagnoseError("railway_variables_invalid")


def redact(text: str, variables: dict[str, str]) -> str:
    sanitized = text
    values = sorted(
        {value for value in variables.values() if value and len(value) >= 4},
        key=len,
        reverse=True,
    )
    for value in values:
        sanitized = sanitized.replace(value, "***")
    for name in SECRET_VARIABLES:
        sanitized = re.sub(
            rf"(?i)({re.escape(name)}\s*[:=]\s*)([^\s,;]+)",
            r"\1***",
            sanitized,
        )
    sanitized = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1***", sanitized)
    sanitized = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", sanitized)
    return sanitized


def parse_log_output(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            lines.append(stripped)
            continue
        if isinstance(parsed, dict):
            message = (
                parsed.get("message")
                or parsed.get("msg")
                or parsed.get("text")
                or parsed.get("log")
            )
            if message is not None:
                lines.append(str(message))
            else:
                lines.append(json.dumps(parsed, ensure_ascii=False, sort_keys=True))
        else:
            lines.append(str(parsed))
    return lines


def fetch_logs(
    deployment_id: str,
    service_id: str,
    *,
    build: bool,
) -> tuple[bool, list[str], str | None]:
    mode = "--build" if build else "--deployment"
    attempts = (
        (
            "railway", "logs", deployment_id, mode, "--lines", "500", "--json",
            "--service", service_id, "--environment", "production",
        ),
        (
            "railway", "logs", mode, "--lines", "500", "--json",
            "--service", service_id, "--environment", "production", deployment_id,
        ),
        (
            "railway", "logs", deployment_id, mode, "--lines", "500", "--json",
        ),
    )
    last_error: str | None = None
    for args in attempts:
        code, stdout, stderr = run_command(*args)
        if code == 0:
            return True, parse_log_output(stdout), None
        last_error = f"railway_logs_exit_{code}"
        if stderr and "unknown" not in stderr.lower() and "usage" not in stderr.lower():
            break
    return False, [], last_error


def select_error_messages(lines: list[str]) -> list[str]:
    selected: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(hint in lower for hint in ERROR_HINTS):
            compact = " ".join(line.split())
            if compact not in selected:
                selected.append(compact[:1000])
        if len(selected) >= 40:
            break
    return selected


def extract_exit_code(lines: list[str]) -> int | None:
    patterns = (
        r"exit(?:ed)?(?:\s+with)?(?:\s+status|\s+code)?\s*[:=]?\s*(-?\d+)",
        r"process completed with exit code\s+(-?\d+)",
    )
    for line in reversed(lines):
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def extract_missing_variables(lines: list[str]) -> list[str]:
    missing: set[str] = set()
    patterns = (
        r"필수 환경변수\s+([A-Z][A-Z0-9_]+)",
        r"(?:missing|required environment variable|environment variable)\s+([A-Z][A-Z0-9_]+)",
    )
    for line in lines:
        for pattern in patterns:
            for match in re.finditer(pattern, line, re.IGNORECASE):
                missing.add(match.group(1).upper())
    return sorted(missing)


def classify_stage(build_lines: list[str], deploy_lines: list[str]) -> str:
    deploy_text = "\n".join(deploy_lines).lower()
    build_text = "\n".join(build_lines).lower()
    if deploy_lines and any(
        hint in deploy_text
        for hint in (
            "traceback",
            "configurationerror",
            "healthcheck",
            "starting container",
            "uvicorn",
            "exited",
            "exit code",
        )
    ):
        return "start"
    if build_lines and any(
        hint in build_text
        for hint in (
            "failed to build",
            "dockerfile",
            "pip install",
            "error:",
            "build failed",
        )
    ):
        return "build"
    if deploy_lines:
        return "start"
    if build_lines:
        return "build"
    return "unknown"


def diagnose(target_sha: str, output: Path) -> dict[str, Any]:
    service_id, service_name = discover_service()
    deployments = json_command(
        "railway", "deployment", "list",
        "--service", service_id,
        "--environment", "production",
        "--limit", "100",
        "--json",
    )
    matched = deployment_for_sha(deployments, target_sha)
    if not matched:
        raise DiagnoseError("target_deployment_not_found")
    deployment_id, deployment_status = matched

    variables = normalize_variables(
        json_command(
            "railway", "variable", "list",
            "--service", service_id,
            "--environment", "production",
            "--json",
        )
    )
    configured = {
        name: bool(variables.get(name, "").strip())
        for name in TRACKED_VARIABLES
    }

    build_ok, build_lines_raw, build_error = fetch_logs(
        deployment_id, service_id, build=True
    )
    deploy_ok, deploy_lines_raw, deploy_error = fetch_logs(
        deployment_id, service_id, build=False
    )
    build_lines = [redact(line, variables) for line in build_lines_raw]
    deploy_lines = [redact(line, variables) for line in deploy_lines_raw]
    combined = build_lines + deploy_lines
    result = {
        "targetSha": target_sha,
        "serviceName": service_name,
        "railwayAccess": True,
        "railwayTokenConfigured": bool(os.environ.get("RAILWAY_TOKEN", "").strip()),
        "deploymentId": deployment_id,
        "deploymentStatus": deployment_status,
        "variablesConfigured": configured,
        "forwardPairConfigured": (
            configured["WEBHOOK_FORWARD_SECRET"]
            and configured["AUTOCARD_INTERNAL_BASE_URL"]
        ),
        "autocardUrlHttps": variables.get("AUTOCARD_INTERNAL_BASE_URL", "").strip().startswith("https://"),
        "buildLogAvailable": build_ok,
        "deploymentLogAvailable": deploy_ok,
        "buildLogError": build_error,
        "deploymentLogError": deploy_error,
        "failureStage": classify_stage(build_lines, deploy_lines),
        "exitCode": extract_exit_code(combined),
        "missingVariablesFromLogs": extract_missing_variables(combined),
        "errorMessages": select_error_messages(combined),
        "buildLogTail": build_lines[-120:],
        "deploymentLogTail": deploy_lines[-160:],
        "secretsPrinted": False,
    }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## jjabtree Railway production diagnosis\n")
            handle.write(f"- target_sha: {target_sha}\n")
            handle.write(f"- deployment_id: {deployment_id}\n")
            handle.write(f"- deployment_status: {deployment_status}\n")
            handle.write(f"- failure_stage: {result['failureStage']}\n")
            handle.write(f"- exit_code: {result['exitCode']}\n")
            handle.write(f"- build_log_available: {str(build_ok).lower()}\n")
            handle.write(f"- deployment_log_available: {str(deploy_ok).lower()}\n")
            for name, present in configured.items():
                handle.write(f"- {name.lower()}_configured: {str(present).lower()}\n")
            handle.write("- secrets_printed: false\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-sha", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("jjabtree-railway-diagnosis.json"),
    )
    args = parser.parse_args()
    try:
        result = diagnose(args.target_sha, args.output)
    except DiagnoseError as exc:
        safe = {
            "targetSha": args.target_sha,
            "railwayTokenConfigured": bool(os.environ.get("RAILWAY_TOKEN", "").strip()),
            "diagnosisError": str(exc),
            "secretsPrinted": False,
        }
        args.output.write_text(
            json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"jjabtree_railway_diagnose=failed:{exc}")
        raise SystemExit(1)
    print(
        "jjabtree_railway_diagnose=complete:"
        f"deployment={result['deploymentId']}:"
        f"status={result['deploymentStatus']}:"
        f"stage={result['failureStage']}"
    )


if __name__ == "__main__":
    main()
