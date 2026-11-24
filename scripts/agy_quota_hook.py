#!/usr/bin/env python3
"""
AGY Quota Auto-Switch Hook (v2.0.0)
Handles PreInvocation, PostInvocation, and Stop lifecycle events
according to the official Antigravity CLI Hook Contract.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
MANAGER_SCRIPT = PLUGIN_DIR / "scripts" / "account_manager.py"

GEMINI_DIR = Path(os.path.expanduser("~/.gemini"))
CONFIG_FILE = GEMINI_DIR / "agy_auth_config.json"
RETRY_FILE = GEMINI_DIR / ".agy_retry_count"
ERROR_FILE = GEMINI_DIR / ".agy_last_quota_error"

ERROR_STATE_TTL = 300.0  # Ignore stale quota error markers older than 5 minutes

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

QUOTA_ERROR_PATTERNS = [
    r"\b429\b",
    r"403.*quota",
    r"resource exhausted",
    r"quota exceeded",
    r"rate limit",
    r"resource_exhausted",
    r"usage limit reached",
    r"limit reached for all.*models",
    r"access resets at",
    r"quota.*exhaust",
    r"too many requests",
]


def log(msg: str) -> None:
    print(f"[AGY-QuotaHook] {msg}", file=sys.stderr)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def load_config() -> dict:
    defaults = {"auto_switch": True, "max_retries": 3}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            defaults.update(data)
        except Exception:
            pass
    return defaults


def get_retry_count() -> int:
    if RETRY_FILE.exists():
        try:
            return int(RETRY_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return 0


def set_retry_count(n: int) -> None:
    try:
        RETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        RETRY_FILE.write_text(str(n), encoding="utf-8")
    except Exception:
        pass


def reset_retry() -> None:
    for f in (RETRY_FILE, ERROR_FILE):
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass


def record_quota_error() -> None:
    try:
        ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
        ERROR_FILE.write_text(
            json.dumps({"quota_error": True, "timestamp": time.time()}),
            encoding="utf-8",
        )
    except Exception:
        pass


def is_quota_error(text: object) -> bool:
    if not text:
        return False
    text_lower = str(text).lower()
    return any(re.search(pattern, text_lower, re.IGNORECASE) for pattern in QUOTA_ERROR_PATTERNS)


def switch_next() -> str | None:
    """Invoke account_manager.py switch to rotate to the highest-quota account."""
    if not MANAGER_SCRIPT.exists():
        log(f"Manager script not found: {MANAGER_SCRIPT}")
        return None
    try:
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, str(MANAGER_SCRIPT), "switch"],
            capture_output=True,
            text=True,
            timeout=25,
            env=env,
        )
        output = strip_ansi(result.stdout + result.stderr)
        match = re.search(r"Switched to:\s*(\S+)", output)
        if match:
            return match.group(1)
        if "Switched to:" in output:
            return "next account"
        return None
    except Exception as e:
        log(f"Account switch failed: {e}")
        return None


def _read_stdin_json() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _system_message_step(msg: str) -> dict:
    return {
        "systemMessage": {
            "systemMessage": msg,
            "metadata": {"user_markdown": msg},
        }
    }


def handle_pre() -> None:
    """PreInvocation: switch before the next turn if a recent quota error was recorded."""
    _read_stdin_json()
    config = load_config()
    if not config.get("auto_switch", True):
        print(json.dumps({}))
        return

    if ERROR_FILE.exists():
        try:
            state = json.loads(ERROR_FILE.read_text(encoding="utf-8"))
            ts = float(state.get("timestamp", 0))
            if state.get("quota_error") and (time.time() - ts <= ERROR_STATE_TTL):
                log("Recent quota error detected; pre-switching account...")
                new_account = switch_next()
                reset_retry()
                if new_account:
                    msg = f"🔄 Quota pre-check: switched active account to `{new_account}`."
                    log(msg)
                    print(json.dumps({"injectSteps": [_system_message_step(msg)]}))
                    return
            else:
                reset_retry()
        except Exception:
            reset_retry()

    print(json.dumps({}))


def handle_post() -> None:
    """PostInvocation: inspect invocation error/response fields for quota exhaustion."""
    context = _read_stdin_json()
    config = load_config()
    if not config.get("auto_switch", True):
        print(json.dumps({}))
        return

    # Only inspect response/error fields (never lastUserInput) to avoid false positives
    candidate_texts = [
        context.get("error"),
        context.get("prompt_response"),
        context.get("output"),
        context.get("response"),
    ]
    matched = any(is_quota_error(t) for t in candidate_texts if t)

    if not matched:
        print(json.dumps({}))
        return

    retries = get_retry_count()
    max_retries = int(config.get("max_retries", 3))

    if retries >= max_retries:
        log(f"Max retries ({max_retries}) reached.")
        reset_retry()
        print(json.dumps({}))
        return

    new_account = switch_next()
    if new_account:
        if ERROR_FILE.exists():
            try:
                ERROR_FILE.unlink()
            except OSError:
                pass
        set_retry_count(retries + 1)
        msg = f"🔄 Quota exhausted. Switched to `{new_account}` (retry {retries + 1}/{max_retries})."
        log(msg)
        print(
            json.dumps(
                {
                    "injectSteps": [_system_message_step(msg)],
                    "terminationBehavior": "force_continue",
                }
            )
        )
    else:
        record_quota_error()
        log("Failed to switch account.")
        print(json.dumps({}))


def handle_stop() -> None:
    """Stop: auto-switch and continue when the execution loop stops due to a quota error."""
    context = _read_stdin_json()
    config = load_config()
    if not config.get("auto_switch", True):
        print(json.dumps({}))
        return

    termination_reason = context.get("terminationReason", "")
    err_text = context.get("error", "")

    if termination_reason != "ERROR" or not is_quota_error(err_text):
        if termination_reason == "NO_TOOL_CALL":
            reset_retry()
        print(json.dumps({}))
        return

    if context.get("continuationActive"):
        print(json.dumps({}))
        return

    retries = get_retry_count()
    max_retries = int(config.get("max_retries", 3))

    if retries >= max_retries:
        log(f"Max retries ({max_retries}) reached on Stop hook.")
        reset_retry()
        print(json.dumps({}))
        return

    new_account = switch_next()
    if new_account:
        if ERROR_FILE.exists():
            try:
                ERROR_FILE.unlink()
            except OSError:
                pass
        set_retry_count(retries + 1)
        msg = (
            f"🔄 Quota exhausted. Automatically switched to `{new_account}` "
            f"(attempt {retries + 1}/{max_retries})."
        )
        log(msg)
        print(json.dumps({"decision": "continue", "reason": msg}))
    else:
        record_quota_error()
        log("Failed to switch account on Stop hook.")
        print(json.dumps({}))


def main() -> None:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "post"
    if mode == "pre":
        handle_pre()
    elif mode == "stop":
        handle_stop()
    else:
        handle_post()


if __name__ == "__main__":
    main()
