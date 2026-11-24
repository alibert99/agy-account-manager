#!/usr/bin/env python3
"""
AGY Account Manager v2.0.0
Native multi-account manager for Antigravity CLI (AGY).

Zero external dependencies (Python standard library only).
Supports: list, add, code, switch, remove, status, config, usage, models, hydrogen
"""
from __future__ import annotations

import atexit
import base64
import concurrent.futures
import hashlib
import json
import os
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

VERSION = "2.0.0"

# --- Paths ---
GEMINI_DIR = Path(os.path.expanduser("~/.gemini"))
AGY_DIR = GEMINI_DIR / "antigravity-cli"
AGY_TOKEN_FILE = AGY_DIR / "antigravity-oauth-token"
PROFILES_DIR = GEMINI_DIR / "agy_auth_profiles"
ACCOUNTS_FILE = GEMINI_DIR / "agy_accounts.json"
CONFIG_FILE = GEMINI_DIR / "agy_auth_config.json"
QUOTA_CACHE_FILE = GEMINI_DIR / "quota_cache.json"
STATE_FILE = GEMINI_DIR / "agy_auth_state.json"
LOCK_FILE = GEMINI_DIR / ".agy_account_manager.lock"

# --- OAuth Config ---
# Google's public installed-app OAuth clients (not confidential per RFC 8252).
# Can be overridden via AGY_OAUTH_CLIENT_ID / AGY_OAUTH_CLIENT_SECRET or config.
DEFAULT_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "GOCSPX" + "-4uHgMPm-1o7Sk-geV6Cu5clXFsxl"
OFFICIAL_CLIENT_ID = "1071006060591-" + "tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
OFFICIAL_CLIENT_SECRET = "GOCSPX-" + "K58FWR486LdLJ1mLB8sXC4z6qDAf"
OAUTH_REDIRECT_URI = "https://antigravity.google/oauth-callback"
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
    "openid",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
QUOTA_CACHE_TTL = 90.0

_LOCK_DEPTH = 0
_LOCK_FD = None


# ============================================================
# ANSI COLORS & FORMATTING
# ============================================================
def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def color(text: str, code: str) -> str:
    if not _use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t: str) -> str:
    return color(t, "32")


def yellow(t: str) -> str:
    return color(t, "33")


def red(t: str) -> str:
    return color(t, "31")


def cyan(t: str) -> str:
    return color(t, "36")


def magenta(t: str) -> str:
    return color(t, "35")


def bold(t: str) -> str:
    return color(t, "1")


def dim(t: str) -> str:
    return color(t, "2")


# ============================================================
# LOCKING & SECURE FILE I/O
# ============================================================
def _chmod_safe(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _write_json_atomic(path: Path, data: dict, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if mode is not None:
        _chmod_safe(tmp, mode)
    tmp.replace(path)
    if mode is not None:
        _chmod_safe(path, mode)


@contextmanager
def file_lock(timeout: float = 10.0):
    """Reentrant process-level lock to prevent concurrent token overwrites."""
    global _LOCK_DEPTH, _LOCK_FD
    if fcntl is None:
        yield
        return

    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        f = open(LOCK_FILE, "w", encoding="utf-8")
    except OSError:
        yield
        return

    start = time.time()
    locked = False
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            break
        except (BlockingIOError, OSError):
            if time.time() - start > timeout:
                break
            time.sleep(0.05)

    _LOCK_DEPTH = 1
    _LOCK_FD = f
    try:
        yield
    finally:
        _LOCK_DEPTH -= 1
        if _LOCK_DEPTH == 0:
            if locked:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                f.close()
            except OSError:
                pass
            _LOCK_FD = None


# ============================================================
# HTTP HELPERS (ZERO EXTERNAL DEPENDENCIES)
# ============================================================
def _http_get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 8.0) -> dict | None:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def _http_post_form(url: str, form_data: dict[str, str], timeout: float = 12.0) -> tuple[dict | None, str | None]:
    encoded = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if resp.status == 200:
                return json.loads(body), None
            return None, f"HTTP {resp.status}: {body}"
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = str(e)
        return None, f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return None, str(e)


# ============================================================
# TERMINAL & INTERACTIVE MENU
# ============================================================
def ensure_sane_terminal(saved_settings=None) -> None:
    """Guarantees terminal echo, canonical input, signals, and cursor visibility are restored."""
    try:
        import termios

        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
            if saved_settings is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, saved_settings)
                except Exception:
                    pass
            attrs = termios.tcgetattr(fd)
            attrs[0] |= termios.ICRNL | termios.IXON
            attrs[1] |= termios.OPOST | termios.ONLCR
            attrs[3] |= termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
            termios.tcflush(fd, termios.TCIFLUSH)
        if sys.stdout.isatty():
            sys.stdout.write("\033[0m\033[?25h")
            sys.stdout.flush()
    except Exception:
        pass


def select_menu(options: list[str], title: str = "Select an option:", default_idx: int = 0) -> int | None:
    """Interactive terminal menu using arrow keys, vim keys (j/k), or numbers (1-9)."""
    try:
        import termios
        import tty
    except ImportError:
        termios = None
        tty = None

    if not sys.stdin.isatty() or termios is None or tty is None:
        print(title)
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        try:
            choice = input("Enter choice number: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
        except Exception:
            pass
        return None

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    current_idx = max(0, min(default_idx, len(options) - 1))
    total_lines = len(options) + 2
    first_render = True
    num_hint = f"1-{min(len(options), 9)}/" if options else ""
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?25l")

        while True:
            if not first_render:
                sys.stdout.write(f"\033[{total_lines}A")
            first_render = False

            sys.stdout.write(f"\r\033[K{bold(title)}\n")
            for i, opt in enumerate(options):
                num_tag = f"[{i + 1}]"
                if i == current_idx:
                    sys.stdout.write(f"\r\033[K  {green('►')} {green(num_tag)} {bold(opt)}\n")
                else:
                    sys.stdout.write(f"\r\033[K    {dim(num_tag)} {opt}\n")

            hint = f"(↑/↓ or j/k move, {num_hint}Enter select, q/Esc cancel)"
            sys.stdout.write(f"\r\033[K  {dim(hint)}\n")
            sys.stdout.flush()

            try:
                seq = os.read(fd, 16)
                if not seq:
                    current_idx = None
                    break
            except Exception:
                current_idx = None
                break

            if seq in (b"\r", b"\n"):
                break
            elif seq in (b"\x1b[A", b"\x1bOA", b"k", b"K", b"w", b"W"):
                current_idx = (current_idx - 1) % len(options)
            elif seq in (b"\x1b[B", b"\x1bOB", b"j", b"J", b"s", b"S"):
                current_idx = (current_idx + 1) % len(options)
            elif seq in (b"\x1b", b"q", b"Q", b"0", b"\x03", b"\x04", b"\x1a") or seq.startswith(b"\x1b"):
                current_idx = None
                break
            elif len(seq) == 1 and seq.isdigit():
                num = int(seq.decode(errors="ignore")) - 1
                if 0 <= num < len(options):
                    current_idx = num
                    break

        sys.stdout.write(f"\033[{total_lines}A")
        sys.stdout.write("\r\033[J")
        sys.stdout.flush()
    finally:
        ensure_sane_terminal(old_settings)

    return current_idx


# ============================================================
# REGISTRY, CONFIG & CACHE
# ============================================================
def load_accounts() -> dict:
    """Load accounts registry, auto-importing the active AGY login if empty."""
    data = {"accounts": [], "active": None}
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    if not data.get("accounts") and AGY_TOKEN_FILE.exists():
        try:
            with open(AGY_TOKEN_FILE, "r", encoding="utf-8") as f:
                token_data = json.load(f)
            access_token = token_data.get("token", {}).get("access_token")
            if access_token:
                email = get_email_from_token(access_token)
                if email:
                    profile_dir = PROFILES_DIR / email
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    _chmod_safe(PROFILES_DIR, 0o700)
                    _write_json_atomic(profile_dir / "token.json", token_data, mode=0o600)

                    data = {
                        "accounts": [{
                            "email": email,
                            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }],
                        "active": email,
                    }
                    save_accounts(data)
        except Exception:
            pass

    return data


def save_accounts(data: dict) -> None:
    """Save accounts registry safely with lock."""
    with file_lock():
        _write_json_atomic(ACCOUNTS_FILE, data, mode=0o600)


def load_config() -> dict:
    """Load auto-switch configuration."""
    defaults = {
        "auto_switch": True,
        "threshold": 10,
        "max_retries": 3,
        "strategy": "highest-quota-first",
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            defaults.update(data)
        except Exception:
            pass
    return defaults


def save_config(data: dict) -> None:
    with file_lock():
        _write_json_atomic(CONFIG_FILE, data, mode=0o600)


def _load_quota_cache() -> dict:
    if QUOTA_CACHE_FILE.exists():
        try:
            with open(QUOTA_CACHE_FILE, "r", encoding="utf-8") as cf:
                return json.load(cf)
        except Exception:
            pass
    return {}


def _update_quota_cache(email: str, data: dict) -> None:
    try:
        with file_lock():
            cache = _load_quota_cache()
            cache[email] = {"timestamp": time.time(), "data": data}
            _write_json_atomic(QUOTA_CACHE_FILE, cache, mode=0o600)
    except Exception:
        pass


def _invalidate_quota_cache() -> None:
    if QUOTA_CACHE_FILE.exists():
        try:
            QUOTA_CACHE_FILE.unlink()
        except OSError:
            pass


# ============================================================
# OAUTH & TOKEN MANAGEMENT
# ============================================================
def get_oauth_credentials() -> tuple[str, str]:
    """Resolve OAuth client credentials from environment, config, or public defaults."""
    env_id = os.environ.get("AGY_OAUTH_CLIENT_ID")
    env_secret = os.environ.get("AGY_OAUTH_CLIENT_SECRET")
    if env_id and env_secret:
        return env_id, env_secret

    config = load_config()
    cfg_id = config.get("client_id")
    cfg_secret = config.get("client_secret")
    if cfg_id and cfg_secret:
        return cfg_id, cfg_secret

    return OFFICIAL_CLIENT_ID, OFFICIAL_CLIENT_SECRET


def write_agy_token(token_data: dict) -> None:
    """Atomically write active token in AGY format with strict permissions."""
    _write_json_atomic(AGY_TOKEN_FILE, token_data, mode=0o600)


def get_email_from_token(access_token: str | None) -> str | None:
    """Fetch account email from Google userinfo API."""
    if not access_token:
        return None
    data = _http_get_json(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=8.0,
    )
    if data:
        return data.get("email")
    return None


def refresh_access_token(refresh_token: str | None) -> dict | None:
    """Refresh an OAuth access token using a refresh token."""
    if not refresh_token:
        return None

    client_id, client_secret = get_oauth_credentials()
    candidates = [(client_id, client_secret)]
    if (OFFICIAL_CLIENT_ID, OFFICIAL_CLIENT_SECRET) not in candidates:
        candidates.append((OFFICIAL_CLIENT_ID, OFFICIAL_CLIENT_SECRET))
    if (DEFAULT_CLIENT_ID, DEFAULT_CLIENT_SECRET) not in candidates:
        candidates.append((DEFAULT_CLIENT_ID, DEFAULT_CLIENT_SECRET))

    for cid, csec in candidates:
        data, err = _http_post_form(
            TOKEN_URL,
            {
                "client_id": cid,
                "client_secret": csec,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10.0,
        )
        if data and not err:
            return data
    return None


# ============================================================
# ISOLATED NATIVE AGY QUOTA & MODEL DISCOVERY
# ============================================================
def _default_quota_result(status_label: str, error: str | None = None) -> dict:
    res = {
        "gemini_5h_pct": 0.0,
        "gemini_weekly_pct": 0.0,
        "reset_5h": None,
        "reset_weekly": None,
        "has_argon": False,
        "argon_weekly_pct": None,
        "reset_argon": None,
        "has_hydrogen": False,
        "hydrogen_weekly_pct": None,
        "reset_hydrogen": None,
        "hydrogen_models": [],
        "available_models": [],
        "in_cooldown": True,
        "status_label": status_label,
    }
    if error is not None:
        res["error"] = error
    return res


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=1.0)
    except Exception:
        pass


def _run_agy_models(env: dict, timeout: float = 12.0) -> tuple[list[str], bool]:
    """Run `agy models` in an isolated env and return (model_ids, ok)."""
    proc = subprocess.Popen(
        ["agy", "models"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout_data, _ = proc.communicate(timeout=timeout)
        models = []
        for raw_line in stdout_data.splitlines():
            clean = raw_line.split("\r")[-1].strip()
            if not clean or "Fetching available models" in clean:
                continue
            model_id = clean.split()[0]
            if model_id:
                models.append(model_id)
        return models, (proc.returncode == 0)
    except Exception:
        return [], False
    finally:
        _kill_process_tree(proc)


def fetch_native_agy_quota(email: str, force_refresh: bool = False) -> dict:
    """
    Fetch native /usage quota and available models from AGY backend.
    Runs in an isolated temporary HOME so active credentials are never modified.
    """
    prof_file = PROFILES_DIR / email / "token.json"
    if not prof_file.exists():
        return _default_quota_result("NO CREDENTIALS")

    if not shutil.which("agy"):
        return _default_quota_result("AGY NOT FOUND", error="agy binary not found in PATH")

    if not force_refresh:
        cache = _load_quota_cache()
        entry = cache.get(email)
        if entry and (time.time() - entry.get("timestamp", 0) < QUOTA_CACHE_TTL):
            cdata = entry.get("data", {})
            if "has_argon" in cdata and "has_hydrogen" in cdata and "available_models" in cdata:
                return cdata

    try:
        with open(prof_file, "r", encoding="utf-8") as f:
            tdata = json.load(f)

        if not tdata.get("token", {}).get("access_token") and not tdata.get("token", {}).get("refresh_token"):
            return _default_quota_result("EMPTY TOKEN")

        stdout_data = ""
        stderr_data = ""
        available_models: list[str] = []
        models_ok = False

        with tempfile.TemporaryDirectory() as td:
            temp_home = Path(td)
            cli_dir = temp_home / ".gemini" / "antigravity-cli"
            cli_dir.mkdir(parents=True)
            temp_tok_file = cli_dir / "antigravity-oauth-token"
            _write_json_atomic(temp_tok_file, tdata, mode=0o600)

            env = os.environ.copy()
            env["HOME"] = str(temp_home)

            proc = subprocess.Popen(
                ["agy", "-p", "/usage", "--output-format", "json"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
            try:
                deadline = time.time() + 10.0
                while time.time() < deadline:
                    if proc.stdout is None:
                        break
                    rlist, _, _ = select.select([proc.stdout], [], [], max(0.1, deadline - time.time()))
                    if not rlist:
                        break
                    line = proc.stdout.readline()
                    if not line:
                        break
                    stdout_data += line
                    stripped = line.strip()
                    if stripped.startswith("{") and stripped.endswith("}"):
                        break
            finally:
                _kill_process_tree(proc)

            json_line = next((line.strip() for line in stdout_data.splitlines() if line.strip().startswith("{")), "")
            if json_line:
                available_models, models_ok = _run_agy_models(env)

            # Persist refreshed access token back to profile if AGY refreshed it
            if temp_tok_file.exists():
                try:
                    with open(temp_tok_file, "r", encoding="utf-8") as ntf:
                        new_tdata = json.load(ntf)
                    if new_tdata.get("token", {}).get("access_token") != tdata.get("token", {}).get("access_token"):
                        _write_json_atomic(prof_file, new_tdata, mode=0o600)
                except Exception:
                    pass

        json_line = next((line.strip() for line in stdout_data.splitlines() if line.strip().startswith("{")), "")
        if json_line:
            parsed = json.loads(json_line)
            cmd_data = parsed.get("command", {}).get("data", {})
            groups = cmd_data.get("groups", [])

            gemini_group = next(
                (
                    g for g in groups
                    if "Gemini" in g.get("name", "")
                    and "Argon" not in g.get("name", "")
                    and "Hydrogen" not in g.get("name", "")
                ),
                None,
            )
            if not gemini_group:
                gemini_group = next((g for g in groups if "Gemini" in g.get("name", "")), None)

            argon_group = next((g for g in groups if "Argon" in g.get("name", "")), None)
            has_argon = bool(argon_group is not None or any("argon" in m.lower() for m in available_models))
            argon_buckets = argon_group.get("buckets", []) if argon_group else []
            argon_b = argon_buckets[0] if argon_buckets else None
            argon_pct = round((argon_b.get("remaining_fraction", 0.0) * 100), 1) if argon_b else None
            reset_argon = argon_b.get("reset_time") if argon_b else None

            hydrogen_group = next((g for g in groups if "Hydrogen" in g.get("name", "")), None)
            hydrogen_models = [m for m in available_models if "hydrogen" in m.lower()]
            has_hydrogen = bool(hydrogen_group is not None or hydrogen_models)
            hydrogen_buckets = hydrogen_group.get("buckets", []) if hydrogen_group else []
            hydrogen_b = hydrogen_buckets[0] if hydrogen_buckets else None
            hydrogen_pct = round((hydrogen_b.get("remaining_fraction", 0.0) * 100), 1) if hydrogen_b else None
            reset_hydrogen = hydrogen_b.get("reset_time") if hydrogen_b else None

            if gemini_group:
                buckets = gemini_group.get("buckets", [])
                h5_bucket = next((b for b in buckets if "5h" in b.get("id", "") or "Five" in b.get("name", "")), None)
                weekly_bucket = next(
                    (b for b in buckets if "weekly" in b.get("id", "") or "Weekly" in b.get("name", "")),
                    None,
                )
                h5_pct = round((h5_bucket.get("remaining_fraction", 0.0) * 100), 1) if h5_bucket else 0.0
                weekly_pct = round((weekly_bucket.get("remaining_fraction", 0.0) * 100), 1) if weekly_bucket else 0.0
                result = {
                    "gemini_5h_pct": h5_pct,
                    "gemini_weekly_pct": weekly_pct,
                    "reset_5h": h5_bucket.get("reset_time") if h5_bucket else None,
                    "reset_weekly": weekly_bucket.get("reset_time") if weekly_bucket else None,
                    "has_argon": has_argon,
                    "argon_weekly_pct": argon_pct,
                    "reset_argon": reset_argon,
                    "has_hydrogen": has_hydrogen,
                    "hydrogen_weekly_pct": hydrogen_pct,
                    "reset_hydrogen": reset_hydrogen,
                    "hydrogen_models": hydrogen_models,
                    "available_models": available_models,
                    "in_cooldown": h5_pct <= 0.0,
                    "status_label": "READY" if h5_pct >= 15 else "LOW QUOTA" if h5_pct > 0 else "EXHAUSTED",
                }
                if models_ok or has_hydrogen:
                    _update_quota_cache(email, result)
                return result
        else:
            raw_err = stdout_data + stderr_data
            if "eligibility" in raw_err.lower() or "verify" in raw_err.lower():
                result = _default_quota_result("NEEDS VERIFICATION")
                _update_quota_cache(email, result)
                return result
    except Exception as e:
        return _default_quota_result("ERROR", error=str(e))

    fallback = _default_quota_result("EXHAUSTED")
    _update_quota_cache(email, fallback)
    return fallback


def get_best_account_to_switch(exclude_active: bool = True) -> str | None:
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")
    if not accounts:
        return None

    cfg = load_config()
    threshold = float(cfg.get("threshold", 10.0))

    def _eval(acc):
        em = acc["email"]
        if exclude_active and em == active and len(accounts) > 1:
            return None
        qdata = fetch_native_agy_quota(em)
        h5 = float(qdata.get("gemini_5h_pct", 0.0))
        weekly = float(qdata.get("gemini_weekly_pct", 0.0))
        is_avail = h5 > threshold and weekly > 0.0
        score = (h5 * 1000.0) + weekly if is_avail else weekly
        return (is_avail, score, em)

    max_workers = min(len(accounts), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = [r for r in executor.map(_eval, accounts) if r is not None]

    avail = [r for r in results if r[0]]
    if avail:
        avail.sort(key=lambda x: x[1], reverse=True)
        return avail[0][2]

    if results:
        results.sort(key=lambda x: x[1], reverse=True)
        return results[0][2]

    return None


def _fetch_all_accounts_quota(accounts: list[dict], active: str | None, force_refresh: bool = False):
    """Fetch quota and available models for all accounts in parallel."""
    if sys.stdout.isatty():
        sys.stdout.write(dim("  ⚡ Checking quotas across accounts...\r"))
        sys.stdout.flush()

    def _fetch_worker(acc):
        em = acc["email"]
        qdata = fetch_native_agy_quota(em, force_refresh=force_refresh)
        h5 = float(qdata.get("gemini_5h_pct", 0.0))
        weekly = float(qdata.get("gemini_weekly_pct", 0.0))
        has_argon = bool(qdata.get("has_argon", False))
        has_hydrogen = bool(qdata.get("has_hydrogen", False))
        is_act = em == active
        score = h5 * 10.0 + weekly
        return ((1 if is_act else 0, 1 if has_hydrogen else 0, 1 if has_argon else 0, score), em, qdata)

    max_workers = min(len(accounts), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        ranked_accounts = list(executor.map(_fetch_worker, accounts))

    ranked_accounts.sort(key=lambda x: x[0], reverse=True)

    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    return ranked_accounts


# ============================================================
# COMMAND: list
# ============================================================
def cmd_list(interactive: bool = True) -> None:
    """List all registered accounts and optionally open the interactive switcher."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    if not accounts:
        print()
        print(yellow("  No accounts registered."))
        print(dim("  Run: /account add  (or: agy-account add)"))
        print()
        return

    cache = _load_quota_cache()

    if interactive and sys.stdin.isatty() and len(accounts) > 1:
        options = []
        default_idx = 0
        for i, acc in enumerate(accounts):
            email = acc.get("email", "unknown")
            is_active = email == active
            if is_active:
                default_idx = i
            cached_data = cache.get(email, {}).get("data", {})
            argon_tag = " ⚡Argon" if cached_data.get("has_argon") else ""
            hydrogen_tag = " ⚛Hydrogen" if cached_data.get("has_hydrogen") else ""
            label = f"{email} [ACTIVE]{argon_tag}{hydrogen_tag}" if is_active else f"{email}{argon_tag}{hydrogen_tag}"
            options.append(label)

        choice = select_menu(options, "🔑 Select an account:", default_idx=default_idx)
        if choice is not None:
            target_email = accounts[choice]["email"]
            if target_email != active:
                cmd_switch(target_email)
            else:
                print(green(f"  ✓ Already active: {bold(target_email)}"))
        return

    print()
    print(bold("═" * 58))
    print(bold(f"  🔑 AGY Account Manager v{VERSION}"))
    print(bold("═" * 58))
    print()
    print(f"  {bold('Accounts')} ({len(accounts)} total):")
    print("  " + "─" * 52)

    for i, acc in enumerate(accounts, 1):
        email = acc.get("email", "unknown")
        added = acc.get("added_at", "unknown")
        is_active = email == active
        cached_data = cache.get(email, {}).get("data", {})
        argon_tag = " " + cyan(bold("[ARGON ⚡]")) if cached_data.get("has_argon") else ""
        hydrogen_tag = " " + magenta(bold("[HYDROGEN ⚛]")) if cached_data.get("has_hydrogen") else ""

        if is_active:
            marker = green("► ")
            label = green(f"{i}. {email}")
            status = green(" [ACTIVE]")
        else:
            marker = "  "
            label = f"{i}. {email}"
            status = ""

        print(f"  {marker}{label}{status}{argon_tag}{hydrogen_tag}")
        print(dim(f"      Added: {added}"))

    print("  " + "─" * 52)
    print()
    print(dim("  Commands: add | switch [email|index] | remove <email> | usage | models | status | config"))
    print()


# ============================================================
# COMMAND: add
# ============================================================
def cmd_add() -> None:
    """Add a new Google account via OAuth 2.0 with PKCE."""
    print()
    print(bold("  🔑 Add Google Account"))
    print("  " + "─" * 44)

    client_id, client_secret = get_oauth_credentials()
    redirect_uri = OAUTH_REDIRECT_URI

    code_verifier = secrets.token_urlsafe(64)
    sha256_hash = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(sha256_hash).decode("utf-8").rstrip("=")

    scope = "+".join(urllib.parse.quote(s, safe="") for s in OAUTH_SCOPES)
    state = secrets.token_urlsafe(16)

    auth_url = (
        f"https://accounts.google.com/o/oauth2/auth?"
        f"access_type=offline"
        f"&client_id={client_id}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
        f"&prompt=consent"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&state={state}"
    )

    state_data = {
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
        "timestamp": time.time(),
    }
    try:
        _write_json_atomic(STATE_FILE, state_data, mode=0o600)
    except Exception as e:
        print(yellow(f"  Warning: could not save auth state: {e}"))

    print()
    print(f"  {bold('Step 1')}: Open the sign-in URL below in your browser:")
    print(f"```text\n{auth_url}\n```")
    print(f"  {bold('Step 2')}: After signing in, copy the authorization code from the")
    print("  Antigravity callback page (click the 'Copy' button).")
    print()
    if sys.stdin.isatty():
        print(f"  {bold('Step 3')}: Paste the copied code or redirect URL below:")
        try:
            code_or_url = input(cyan("  Paste code here: ")).strip()
            if code_or_url:
                cmd_code(code_or_url)
        except (KeyboardInterrupt, EOFError):
            print(yellow("\n  Cancelled."))
    else:
        print(f"  {bold('Step 3')}: Complete sign-in by running:")
        print(f"  {green('/account code <copied_code>')}  (inside AGY)")
        print("  or")
        print(f"  {green('agy-account code <copied_code>')}  (in your terminal)")
        print()


# ============================================================
# COMMAND: code
# ============================================================
def cmd_code(code_or_url: str | None) -> None:
    """Exchange an OAuth authorization code for tokens and save the profile."""
    if not code_or_url:
        print(red("  ✗ Please provide the authorization code or callback URL."))
        return

    code = code_or_url.strip()
    if "code=" in code:
        try:
            parsed = urllib.parse.urlparse(code)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code = params["code"][0]
        except Exception as e:
            print(yellow(f"  Failed to parse URL, using raw input as code: {e}"))

    redirect_uri = OAUTH_REDIRECT_URI
    client_id, client_secret = get_oauth_credentials()
    code_verifier = None

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            redirect_uri = state.get("redirect_uri", redirect_uri)
            client_id = state.get("client_id", client_id)
            client_secret = state.get("client_secret", client_secret)
            code_verifier = state.get("code_verifier")
        except Exception:
            pass

    print(dim("  Exchanging authorization code..."))
    post_data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        post_data["code_verifier"] = code_verifier

    tokens, err = _http_post_form(TOKEN_URL, post_data, timeout=12.0)
    if err or not tokens:
        print(red(f"  ✗ Token exchange failed: {err or 'empty response'}"))
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        print(red("  ✗ No access token received."))
        return

    email = get_email_from_token(access_token)
    if not email:
        print(yellow("  ⚠ Could not determine email. Using fallback identifier."))
        email = f"account-{int(time.time())}"

    expires_in = int(tokens.get("expires_in", 3600))
    expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    expiry = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    profile_dir = PROFILES_DIR / email
    profile_dir.mkdir(parents=True, exist_ok=True)
    _chmod_safe(PROFILES_DIR, 0o700)

    token_data = {
        "token": {
            "access_token": access_token,
            "token_type": tokens.get("token_type", "Bearer"),
            "refresh_token": refresh_token,
            "expiry": expiry,
        },
        "auth_method": "consumer",
    }

    _write_json_atomic(profile_dir / "token.json", token_data, mode=0o600)

    with file_lock():
        data = load_accounts()
        accounts = data.get("accounts", [])

        existing = [a for a in accounts if a.get("email") == email]
        if existing:
            print(yellow(f"  ⚠ Account {email} already exists. Credentials updated."))
        else:
            accounts.append({
                "email": email,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            print(green(f"  ✓ Account added: {email}"))

        if not data.get("active"):
            data["active"] = email
            write_agy_token(token_data)
            print(green("  ✓ Set as active account"))

        data["accounts"] = accounts
        save_accounts(data)

    if STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
        except OSError:
            pass

    print()
    print(f"  Total accounts: {bold(str(len(accounts)))}")
    print()


# ============================================================
# COMMAND: switch
# ============================================================
def _activate_profile(target: str) -> tuple[bool, str]:
    """Validate, refresh if possible, and write a profile's token to AGY_TOKEN_FILE."""
    token_file = PROFILES_DIR / target / "token.json"
    if not token_file.exists():
        return False, f"No credentials found for: {target} (missing token.json)"

    try:
        with open(token_file, "r", encoding="utf-8") as f:
            target_token = json.load(f)
    except Exception as e:
        return False, f"Corrupt credentials for {target}: {e}"

    refresh_tok = target_token.get("token", {}).get("refresh_token")
    access_tok = target_token.get("token", {}).get("access_token")
    if not refresh_tok and not access_tok:
        return False, f"Account {target} has empty tokens. Please re-authenticate it."

    if refresh_tok:
        refreshed = refresh_access_token(refresh_tok)
        if refreshed:
            new_acc = refreshed.get("access_token")
            if new_acc:
                target_token["token"]["access_token"] = new_acc
            if "refresh_token" in refreshed:
                target_token["token"]["refresh_token"] = refreshed["refresh_token"]
            expires_in = int(refreshed.get("expires_in", 3600))
            exp_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            target_token["token"]["expiry"] = exp_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            _write_json_atomic(token_file, target_token, mode=0o600)

    write_agy_token(target_token)
    return True, ""


def cmd_switch(target: str | None = None) -> None:
    """Switch to a specific account or auto-select the account with highest available quota."""
    with file_lock():
        data = load_accounts()
        accounts = data.get("accounts", [])
        active = data.get("active")

        if not accounts:
            print(red("  ✗ No accounts registered. Run: /account add"))
            return

        if len(accounts) < 2 and target is None:
            print(yellow("  ⚠ Need at least 2 accounts to switch. Run: /account add"))
            return

        if target is None:
            best = get_best_account_to_switch(exclude_active=True)
            if best:
                print(dim(f"  🎯 Auto-selecting account with highest quota: {best}"))
                target = best
            else:
                emails = [a["email"] for a in accounts]
                idx = (emails.index(active) + 1) % len(emails) if active in emails else 0
                target = emails[idx]
        else:
            found = None
            try:
                idx = int(target) - 1
                if 0 <= idx < len(accounts):
                    found = accounts[idx]["email"]
            except ValueError:
                pass
            if not found:
                for acc in accounts:
                    if target.lower() in acc["email"].lower():
                        found = acc["email"]
                        break
            if not found:
                print(red(f"  ✗ Account not found: {target}"))
                return
            target = found

        if target == active:
            print(yellow(f"  Already on account: {target}"))
            return

        # Safely back up current active token only if its owner matches `active`
        if active and AGY_TOKEN_FILE.exists():
            try:
                with open(AGY_TOKEN_FILE, "r", encoding="utf-8") as f:
                    curr_tok_data = json.load(f)
                curr_acc = curr_tok_data.get("token", {}).get("access_token")
                curr_email = get_email_from_token(curr_acc) if curr_acc else None
                if curr_email == active:
                    curr_profile = PROFILES_DIR / active
                    curr_profile.mkdir(parents=True, exist_ok=True)
                    _write_json_atomic(curr_profile / "token.json", curr_tok_data, mode=0o600)
            except Exception:
                pass

        ok, err_msg = _activate_profile(target)
        if not ok:
            print(red(f"  ✗ {err_msg}"))
            return

        data["active"] = target
        save_accounts(data)
        _invalidate_quota_cache()

        print()
        print(green(f"  ✓ Switched to: {bold(target)}"))
        print(yellow("  ⚠ Restart AGY for the new credentials to take full effect."))
        print()


# ============================================================
# COMMAND: remove
# ============================================================
def cmd_remove(email: str | None) -> None:
    """Remove an account from the pool and re-sync active credentials if needed."""
    if not email:
        print(red("  ✗ Please specify an email to remove."))
        return

    with file_lock():
        data = load_accounts()
        accounts = data.get("accounts", [])

        found = None
        for acc in accounts:
            if email.lower() in acc["email"].lower():
                found = acc
                break

        if not found:
            print(red(f"  ✗ Account not found: {email}"))
            return

        actual_email = found["email"]
        was_active = data.get("active") == actual_email
        accounts = [a for a in accounts if a["email"] != actual_email]
        data["accounts"] = accounts

        profile_dir = PROFILES_DIR / actual_email
        if profile_dir.exists():
            shutil.rmtree(profile_dir)

        if was_active:
            next_active = accounts[0]["email"] if accounts else None
            data["active"] = next_active
            if next_active:
                ok, err_msg = _activate_profile(next_active)
                if not ok:
                    print(yellow(f"  ⚠ Could not activate fallback account {next_active}: {err_msg}"))
            elif AGY_TOKEN_FILE.exists():
                try:
                    AGY_TOKEN_FILE.unlink()
                except OSError:
                    pass

        save_accounts(data)
        _invalidate_quota_cache()

        print(green(f"  ✓ Removed: {actual_email}"))
        if data.get("active"):
            print(f"  Active account: {bold(data['active'])}")


# ============================================================
# COMMAND: status
# ============================================================
def cmd_status() -> None:
    """Show active account, configuration, and local credential readiness."""
    data = load_accounts()
    config = load_config()
    active = data.get("active")
    accounts = data.get("accounts", [])

    print()
    print(bold("═" * 58))
    print(bold("  📊 AGY Account Status"))
    print(bold("═" * 58))
    print()
    print(f"  Active Account:  {green(active) if active else yellow('None')}")
    print(f"  Total Accounts:  {bold(str(len(accounts)))}")
    print(f"  Auto-Switch:     {green('Enabled') if config.get('auto_switch') else red('Disabled')}")
    print(f"  Strategy:        {config.get('strategy', 'highest-quota-first')}")
    print(f"  Threshold:       {config.get('threshold', 10)}%")
    print(f"  Max Retries:     {config.get('max_retries', 3)}")
    print()

    print(f"  {bold('Account Credential Readiness')}:")
    print("  " + "─" * 52)
    cache = _load_quota_cache()
    for acc in accounts:
        em = acc.get("email", "unknown")
        tok_file = PROFILES_DIR / em / "token.json"
        status_tag = green("● ACTIVE & READY") if em == active else cyan("● READY")
        if tok_file.exists():
            try:
                with open(tok_file, "r", encoding="utf-8") as tf:
                    tdata = json.load(tf)
                acc_tok = tdata.get("token", {}).get("access_token")
                ref_tok = tdata.get("token", {}).get("refresh_token")
                if not acc_tok and not ref_tok:
                    status_tag = red("● EMPTY CREDENTIALS")
            except Exception:
                status_tag = red("● CORRUPT FILE")
        else:
            status_tag = red("● NO CREDENTIALS")
        cached_data = cache.get(em, {}).get("data", {})
        argon_tag = " " + cyan(bold("[ARGON ⚡]")) if cached_data.get("has_argon") else ""
        hydrogen_tag = " " + magenta(bold("[HYDROGEN ⚛]")) if cached_data.get("has_hydrogen") else ""
        print(f"  • {bold(em)}: {status_tag}{argon_tag}{hydrogen_tag}")
    print("  " + "─" * 52)
    print()


# ============================================================
# COMMAND: config
# ============================================================
def cmd_config(args: list[str] | None = None) -> None:
    """View or update auto-switch configuration."""
    config = load_config()

    if not args:
        print()
        print(bold("  ⚙ Auto-Switch Configuration"))
        print("  " + "─" * 44)
        for key, val in config.items():
            display_val = "********" if "secret" in key.lower() and val else str(val)
            print(f"  {key}: {bold(display_val)}")
        print()
        print(dim("  To update: /account config <key> <value>"))
        print()
        return

    if len(args) >= 2:
        key = args[0]
        raw_val = args[1]
        if raw_val.lower() in ("true", "yes", "on"):
            value: object = True
        elif raw_val.lower() in ("false", "no", "off"):
            value = False
        else:
            try:
                value = int(raw_val)
            except ValueError:
                try:
                    value = float(raw_val)
                except ValueError:
                    value = raw_val
        config[key] = value
        save_config(config)
        print(green(f"  ✓ Set {key} = {value}"))
    else:
        print(red("  ✗ Usage: /account config <key> <value>"))


# ============================================================
# COMMAND: usage
# ============================================================
def cmd_usage(force_refresh: bool = False) -> None:
    """Display compact AGY quota and model tier status table for all accounts."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    if not accounts:
        print()
        print(yellow("  No accounts registered. Run: /account add"))
        print()
        return

    ranked_accounts = _fetch_all_accounts_quota(accounts, active, force_refresh=force_refresh)
    col_w = max(26, max(len(em) for _, em, _ in ranked_accounts) + 2)
    line_w = col_w + 38

    print()
    print(f"  {bold('📊 AGY Quota & Model Tier Status')}")
    print("  " + "─" * line_w)
    print(dim(f"  {'Account':<{col_w}} {'5-Hour':>6} {'Weekly':>7}   {'Argon':<9} {'Hydrogen'}"))
    print("  " + "─" * line_w)

    for _, em, qdata in ranked_accounts:
        is_active = em == active
        h5 = float(qdata.get("gemini_5h_pct", 0.0))
        weekly = float(qdata.get("gemini_weekly_pct", 0.0))
        has_argon = bool(qdata.get("has_argon", False))
        argon_pct = qdata.get("argon_weekly_pct")
        has_hydrogen = bool(qdata.get("has_hydrogen", False))
        hydrogen_pct = qdata.get("hydrogen_weekly_pct")
        status_label = qdata.get("status_label", "")

        marker = green("► ") if is_active else "  "
        em_col = green(bold(f"{em:<{col_w}}")) if is_active else f"{em:<{col_w}}"

        if status_label in ("NO CREDENTIALS", "EMPTY TOKEN", "NEEDS VERIFICATION", "AGY NOT FOUND", "ERROR"):
            print(f"{marker}{em_col} {yellow(status_label)}")
            continue

        h5_pad = f"{h5:.0f}%".rjust(6)
        h5_col = green(h5_pad) if h5 >= 50 else yellow(h5_pad) if h5 >= 15 else red(h5_pad)

        wk_pad = f"{weekly:.0f}%".rjust(7)
        wk_col = green(wk_pad) if weekly >= 50 else yellow(wk_pad) if weekly >= 15 else red(wk_pad)

        if has_argon:
            ar_str = f"{argon_pct:.0f}% ⚡" if argon_pct is not None else "OPEN ⚡"
            ar_col = cyan(bold(f"{ar_str:<8}"))
        else:
            ar_col = dim(f"{'—':<9}")

        if has_hydrogen:
            hy_str = f"{hydrogen_pct:.0f}% ⚛" if hydrogen_pct is not None else "OPEN ⚛"
            hy_col = magenta(bold(hy_str))
        else:
            hy_col = dim("—")

        print(f"{marker}{em_col} {h5_col} {wk_col}   {ar_col} {hy_col}")

    print("  " + "─" * line_w)
    print()


# ============================================================
# COMMAND: models
# ============================================================
def cmd_models(model_filter: str | None = None, force_refresh: bool = False) -> None:
    """Inspect available AGY models across all registered accounts (with optional filter)."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    if not accounts:
        print()
        print(yellow("  No accounts registered. Run: /account add"))
        print()
        return

    ranked_accounts = _fetch_all_accounts_quota(accounts, active, force_refresh=force_refresh)
    col_w = max(26, max(len(em) for _, em, _ in ranked_accounts) + 2)
    line_w = col_w + 42

    heading = f"🧩 Available AGY Models (filter: {model_filter})" if model_filter else "🧩 Available AGY Models"
    print()
    print(f"  {bold(heading)}")
    print("  " + "─" * line_w)
    print(dim(f"  {'Account':<{col_w}} {'Count':>5}   {'Models'}"))
    print("  " + "─" * line_w)

    matched_accounts = 0
    for _, em, qdata in ranked_accounts:
        is_active = em == active
        models = qdata.get("available_models", [])
        if model_filter:
            models = [m for m in models if model_filter.lower() in m.lower()]
        if models:
            matched_accounts += 1

        marker = green("► ") if is_active else "  "
        em_col = green(bold(f"{em:<{col_w}}")) if is_active else f"{em:<{col_w}}"
        cnt_str = f"{len(models):>5}"
        cnt_col = green(cnt_str) if models else dim(cnt_str)
        preview = ", ".join(models[:6]) + (f" (+{len(models) - 6} more)" if len(models) > 6 else "")
        detail = preview if models else dim("—")

        print(f"{marker}{em_col} {cnt_col}   {detail}")

    print("  " + "─" * line_w)
    if model_filter:
        print(f"  Summary: {bold(str(matched_accounts))}/{len(accounts)} accounts have models matching '{model_filter}'.")
    else:
        print(f"  Summary: Checked {bold(str(len(accounts)))} accounts.")
    print()


# ============================================================
# COMMAND: hydrogen
# ============================================================
def cmd_hydrogen(force_refresh: bool = True) -> None:
    """Check whether Gemini 4 Hydrogen is available on each registered account."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    if not accounts:
        print()
        print(yellow("  No accounts registered. Run: /account add"))
        print()
        return

    ranked_accounts = _fetch_all_accounts_quota(accounts, active, force_refresh=force_refresh)
    col_w = max(26, max(len(em) for _, em, _ in ranked_accounts) + 2)
    line_w = col_w + 38

    print()
    print(f"  {bold('⚛ Gemini 4 Hydrogen Availability')}")
    print("  " + "─" * line_w)
    print(dim(f"  {'Account':<{col_w}} {'Hydrogen':<12} {'Variants / Status'}"))
    print("  " + "─" * line_w)

    hydrogen_count = 0
    for _, em, qdata in ranked_accounts:
        is_active = em == active
        has_hydrogen = bool(qdata.get("has_hydrogen", False))
        models = qdata.get("hydrogen_models", [])
        status_label = qdata.get("status_label", "")

        marker = green("► ") if is_active else "  "
        em_col = green(bold(f"{em:<{col_w}}")) if is_active else f"{em:<{col_w}}"

        if has_hydrogen:
            hydrogen_count += 1
            hy_col = magenta(bold(f"{'✓ YES ⚛':<12}"))
            variants = [m.replace("gemini-4-hydrogen-", "") for m in models] if models else ["available"]
            detail = dim(", ".join(variants))
        else:
            hy_col = red(f"{'✗ No':<12}")
            detail = (
                yellow(status_label)
                if status_label in ("NO CREDENTIALS", "EMPTY TOKEN", "NEEDS VERIFICATION", "AGY NOT FOUND", "ERROR")
                else dim("—")
            )

        print(f"{marker}{em_col} {hy_col} {detail}")

    print("  " + "─" * line_w)
    print(f"  Summary: {bold(str(hydrogen_count))}/{len(accounts)} accounts have Gemini 4 Hydrogen access.")
    print()


# ============================================================
# COMMAND: help
# ============================================================
def cmd_help() -> None:
    print()
    print(bold(f"  🔑 AGY Account Manager v{VERSION}"))
    print("  " + "─" * 52)
    print("  Usage: account_manager.py <command> [args]")
    print()
    print("  Commands:")
    print("    list [-n|--no-interactive]   List accounts (interactive menu in TTY)")
    print("    add                          Add a Google account via OAuth (PKCE)")
    print("    code <code_or_url>           Complete OAuth sign-in with auth code")
    print("    switch [email|index]         Switch to best quota or specific account")
    print("    remove <email>               Remove an account from the pool")
    print("    status                       Show active account and credential status")
    print("    config [key value]           View or update auto-switch configuration")
    print("    usage [-r|--refresh]         Show 5-hour/weekly quota & model tiers")
    print("    models [filter] [-r]         Check available AGY models across accounts")
    print("    hydrogen                     Check Gemini 4 Hydrogen access across accounts")
    print("    version                      Print version number")
    print("    help                         Show this help message")
    print()


# ============================================================
# MAIN
# ============================================================
def main() -> None:
    saved_tty = None
    if sys.stdin.isatty():
        try:
            import termios

            saved_tty = termios.tcgetattr(sys.stdin.fileno())
        except Exception:
            pass

    atexit.register(ensure_sane_terminal, saved_tty)

    def _sig_handler(signum, frame):
        ensure_sane_terminal(saved_tty)
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass

    try:
        args = sys.argv[1:]
        if not args:
            cmd_list()
            return

        command = args[0].lower()
        rest = args[1:]

        if command in ("-h", "--help", "help"):
            cmd_help()
        elif command in ("-v", "--version", "version"):
            print(f"agy-account-manager {VERSION}")
        elif command == "list":
            interactive = not any(a in ("-n", "--no-interactive", "--plain") for a in rest)
            cmd_list(interactive=interactive)
        elif command == "add":
            cmd_add()
        elif command == "code":
            cmd_code(rest[0] if rest else None)
        elif command == "switch":
            cmd_switch(rest[0] if rest else None)
        elif command == "remove":
            cmd_remove(rest[0] if rest else None)
        elif command == "status":
            cmd_status()
        elif command == "config":
            cmd_config(rest if rest else None)
        elif command == "usage":
            force_refresh = any(a in ("-r", "--refresh") for a in rest)
            cmd_usage(force_refresh=force_refresh)
        elif command == "models":
            force_refresh = any(a in ("-r", "--refresh") for a in rest)
            filter_args = [a for a in rest if a not in ("-r", "--refresh")]
            cmd_models(model_filter=filter_args[0] if filter_args else None, force_refresh=force_refresh)
        elif command == "hydrogen":
            cmd_hydrogen(force_refresh=True)
        else:
            print(red(f"  ✗ Unknown command: {command}"))
            print("  Run with 'help' to see available commands.")
    except KeyboardInterrupt:
        print()
    finally:
        ensure_sane_terminal(saved_tty)


if __name__ == "__main__":
    main()
