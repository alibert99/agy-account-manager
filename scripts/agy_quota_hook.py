#!/usr/bin/env python3
"""
AGY Quota Auto-Switch Hook (unified)
Handles both PreInvocation and PostInvocation events.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = PLUGIN_DIR / "scripts"
MANAGER_SCRIPT = SCRIPTS_DIR / "account_manager.py"

GEMINI_DIR = Path(os.path.expanduser("~/.gemini"))
AGY_DIR = GEMINI_DIR / "antigravity-cli"
CONFIG_FILE = GEMINI_DIR / "agy_auth_config.json"
RETRY_FILE = GEMINI_DIR / ".agy_retry_count"
ERROR_FILE = GEMINI_DIR / ".agy_last_quota_error"

QUOTA_ERROR_PATTERNS = [
    r"429",
    r"403.*quota",
    r"Resource exhausted",
    r"Quota exceeded",
    r"rate limit",
    r"RESOURCE_EXHAUSTED",
    r"Usage limit reached",
    r"limit reached for all.*models",
    r"Access resets at",
    r"Keep trying.*Stop",
    r"quota.*exhaust",
    r"too many requests",
]


def log(msg):
    print(f"[AGY-QuotaHook] {msg}", file=sys.stderr)


def load_config():
    defaults = {"auto_switch": True, "max_retries": 3}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
            defaults.update(data)
        except:
            pass
    return defaults


def get_retry_count():
    if RETRY_FILE.exists():
        try:
            return int(RETRY_FILE.read_text().strip())
        except:
            pass
    return 0


def set_retry_count(n):
    try:
        RETRY_FILE.write_text(str(n))
    except:
        pass


def reset_retry():
    for f in [RETRY_FILE, ERROR_FILE]:
        if f.exists():
            try:
                f.unlink()
            except:
                pass


def is_quota_error(text):
    text_lower = text.lower()
    for pattern in QUOTA_ERROR_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def switch_next():
    """Call account_manager.py switch to go to next account."""
    if not MANAGER_SCRIPT.exists():
        log(f"Manager script not found: {MANAGER_SCRIPT}")
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(MANAGER_SCRIPT), "switch"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        match = re.search(r'Switched to:\s*(\S+)', output)
        if match:
            return match.group(1)
        if "Switched" in output:
            return "next account"
        return None
    except Exception as e:
        log(f"Switch failed: {e}")
        return None


def handle_post():
    """PostInvocation: check response for quota errors."""
    try:
        context = json.load(sys.stdin)
    except:
        print(json.dumps({"decision": "allow"}))
        return

    config = load_config()
    if not config.get("auto_switch", True):
        print(json.dumps({"decision": "allow"}))
        return

    response = context.get("prompt_response", context.get("output", json.dumps(context)))

    if not is_quota_error(response):
        reset_retry()
        print(json.dumps({"decision": "allow"}))
        return

    # Quota error!
    retries = get_retry_count()
    max_retries = config.get("max_retries", 3)

    if retries >= max_retries:
        log(f"Max retries ({max_retries}) reached.")
        reset_retry()
        print(json.dumps({"decision": "allow"}))
        return

    new_account = switch_next()
    if new_account:
        set_retry_count(retries + 1)
        msg = f"🔄 Quota exhausted. Switched to: {new_account}. Retry {retries+1}/{max_retries}"
        log(msg)
        print(json.dumps({"decision": "allow", "systemMessage": msg}))
    else:
        log("Failed to switch account.")
        print(json.dumps({"decision": "allow"}))


def handle_pre():
    """PreInvocation: check if previous request had quota error."""
    try:
        context = json.load(sys.stdin)
    except:
        context = {}

    config = load_config()
    if not config.get("auto_switch", True):
        print(json.dumps({"decision": "allow"}))
        return

    # Check if there's a lingering error state
    if ERROR_FILE.exists():
        try:
            state = json.loads(ERROR_FILE.read_text())
            if state.get("quota_error"):
                log("Previous quota error detected, pre-switching...")
                new_account = switch_next()
                if new_account:
                    msg = f"🔄 Pre-check: switched to {new_account} due to previous quota error."
                    log(msg)
                    reset_retry()
                    print(json.dumps({"decision": "allow", "systemMessage": msg}))
                    return
        except:
            pass

    print(json.dumps({"decision": "allow"}))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    if mode == "pre":
        handle_pre()
    else:
        handle_post()


if __name__ == "__main__":
    main()
