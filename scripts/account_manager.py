#!/usr/bin/env python3
"""
AGY Account Manager v2.0 (Stabilized & Hardened)
Native account management for Antigravity CLI.
Supports: add, list, switch, remove, status, config, usage
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

try:
    import fcntl
except ImportError:
    fcntl = None

# --- Paths ---
GEMINI_DIR = Path(os.path.expanduser("~/.gemini"))
AGY_DIR = GEMINI_DIR / "antigravity-cli"
AGY_TOKEN_FILE = AGY_DIR / "antigravity-oauth-token"
PROFILES_DIR = GEMINI_DIR / "agy_auth_profiles"
ACCOUNTS_FILE = GEMINI_DIR / "agy_accounts.json"
CONFIG_FILE = GEMINI_DIR / "agy_auth_config.json"
QUOTA_CACHE_FILE = GEMINI_DIR / "quota_cache.json"
QUOTA_TRACKER_FILE = GEMINI_DIR / "agy_quota_state.json"
LOCK_FILE = GEMINI_DIR / ".agy_account_manager.lock"

# --- OAuth Config ---
DEFAULT_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "GOCSPX" + "-4uHgMPm-1o7Sk-geV6Cu5clXFsxl"
OFFICIAL_CLIENT_ID = "1071006060591-" + "tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
OFFICIAL_CLIENT_SECRET = "GOCSPX-" + "K58FWR486LdLJ1mLB8sXC4z6qDAf"
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# --- Model Limits DB ---
MODEL_LIMITS = {
    "gemini-1.5-flash": {"rpm": 15, "tpm": 1000000, "rpd": 1500},
    "gemini-1.5-pro": {"rpm": 2, "tpm": 32000, "rpd": 50},
    "gemini-2.0-flash": {"rpm": 15, "tpm": 1000000, "rpd": 1500},
    "gemini-2.5-flash": {"rpm": 15, "tpm": 1000000, "rpd": 1500},
    "gemini-3.5-flash": {"rpm": 15, "tpm": 1000000, "rpd": 1500},
    "gemini-3.7-flash": {"rpm": 15, "tpm": 1000000, "rpd": 1500},
    "gemini-3.8-flash-high": {"rpm": 15, "tpm": 1000000, "rpd": 1500},
    "gemini-4-argon-high": {"rpm": 5, "tpm": 500000, "rpd": 500},
    "gemini-4-argon-medium": {"rpm": 5, "tpm": 500000, "rpd": 500},
    "gemini-4-argon-low": {"rpm": 5, "tpm": 500000, "rpd": 500},
}


_LOCK_DEPTH = 0
_LOCK_FD = None

@contextmanager
def file_lock(timeout=10.0):
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
        f = open(LOCK_FILE, "w")
    except Exception:
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
                except Exception:
                    pass
            try:
                f.close()
            except Exception:
                pass
            _LOCK_FD = None


def get_active_model():
    settings_file = AGY_DIR / "settings.json"
    if settings_file.exists():
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get("model", "Unknown")
        except:
            pass
    return "Unknown"


def select_menu(options, title="Select an option:", default_idx=0):
    """Interactive terminal menu using arrow keys, vim keys (j/k), or numbers (1-9)."""
    import sys
    import tty
    import termios

    if not sys.stdin.isatty():
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
    try:
        tty.setraw(fd)
        
        while True:
            if not first_render:
                sys.stdout.write(f"\033[{total_lines}A")
            first_render = False
            
            sys.stdout.write(f"\r\033[K{bold(title)}\n")
            for i, opt in enumerate(options):
                num_tag = f"[{i+1}]"
                if i == current_idx:
                    sys.stdout.write(f"\r\033[K  {green('►')} {green(num_tag)} {green(opt)}\n")
                else:
                    sys.stdout.write(f"\r\033[K    {dim(num_tag)} {opt}\n")
            
            sys.stdout.write(f"\r\033[K  (Use ↑/↓ or j/k to move, 1-{len(options)} to pick, Enter to select, Esc to cancel)\n")
            sys.stdout.flush()
            
            try:
                seq = os.read(fd, 16)
                if not seq:
                    break
            except Exception:
                break
            
            if seq in (b'\r', b'\n'):
                # Enter selected
                break
            elif seq in (b'\x1b[A', b'\x1bOA', b'k', b'K', b'w', b'W'):
                # Up arrow or vim key
                current_idx = (current_idx - 1) % len(options)
            elif seq in (b'\x1b[B', b'\x1bOB', b'j', b'J', b's', b'S'):
                # Down arrow or vim key
                current_idx = (current_idx + 1) % len(options)
            elif seq in (b'\x1b', b'q', b'Q', b'\x03'):
                # Escape, q, or Ctrl+C
                current_idx = None
                break
            elif seq.isdigit():
                # Direct number selection (e.g. 1 to 9)
                num = int(seq.decode(errors='ignore')) - 1
                if 0 <= num < len(options):
                    current_idx = num
                    break
                    
        sys.stdout.write(f"\033[{total_lines}A")
        sys.stdout.write("\r\033[J")
        sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        
    return current_idx


def color(text, code):
    return f"\033[{code}m{text}\033[0m"

def green(t): return color(t, "32")
def yellow(t): return color(t, "33")
def red(t): return color(t, "31")
def cyan(t): return color(t, "36")
def magenta(t): return color(t, "35")
def bold(t): return color(t, "1")
def dim(t): return color(t, "2")


def load_accounts():
    """Load accounts registry."""
    data = {"accounts": [], "active": None}
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            pass

    # Auto-detect currently logged in AGY account if registry is empty
    if not data.get("accounts") and AGY_TOKEN_FILE.exists():
        try:
            with open(AGY_TOKEN_FILE, 'r', encoding='utf-8') as f:
                token_data = json.load(f)
            access_token = token_data.get("token", {}).get("access_token")
            if access_token:
                email = get_email_from_token(access_token)
                if email:
                    profile_dir = PROFILES_DIR / email
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    with open(profile_dir / "token.json", 'w', encoding='utf-8') as pf:
                        json.dump(token_data, pf, indent=2)
                    
                    data = {
                        "accounts": [{
                            "email": email,
                            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                        }],
                        "active": email
                    }
                    save_accounts(data)
        except:
            pass

    return data


def save_accounts(data):
    """Save accounts registry safely with lock."""
    with file_lock():
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ACCOUNTS_FILE.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(ACCOUNTS_FILE)


def load_config():
    """Load auto-switch config."""
    defaults = {
        "auto_switch": True,
        "threshold": 10,
        "max_retries": 3,
        "strategy": "highest-quota-first",
        "model_pattern": "gemini-3.*",
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            defaults.update(data)
        except:
            pass
    return defaults


def save_config(data):
    with file_lock():
        tmp = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(CONFIG_FILE)


def _load_quota_cache():
    if QUOTA_CACHE_FILE.exists():
        try:
            with open(QUOTA_CACHE_FILE, "r", encoding="utf-8") as cf:
                return json.load(cf)
        except Exception:
            pass
    return {}


def _update_quota_cache(email: str, data: dict):
    try:
        cache = _load_quota_cache()
        cache[email] = {"timestamp": time.time(), "data": data}
        tmp = QUOTA_CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as cf:
            json.dump(cache, cf, indent=2)
        tmp.replace(QUOTA_CACHE_FILE)
    except Exception:
        pass


def fetch_native_agy_quota(email: str, force_refresh: bool = False) -> dict:
    """
    Fetches real native /usage quota directly from Google AGY backend.
    CRITICAL FIX: Runs in an ISOLATED temporary environment so AGY_TOKEN_FILE
    is NEVER overwritten or corrupted!
    """
    prof_file = PROFILES_DIR / email / "token.json"
    if not prof_file.exists():
        return {"gemini_5h_pct": 0.0, "gemini_weekly_pct": 0.0, "has_argon": False, "argon_weekly_pct": None, "reset_argon": None, "in_cooldown": True, "status_label": "NO CREDENTIALS"}

    # Check cache (valid for 90 seconds)
    if not force_refresh:
        cache = _load_quota_cache()
        entry = cache.get(email)
        if entry and (time.time() - entry.get("timestamp", 0) < 90):
            cdata = entry.get("data", {})
            if "has_argon" in cdata:
                return cdata

    try:
        with open(prof_file, "r", encoding="utf-8") as f:
            tdata = json.load(f)

        if not tdata.get("token", {}).get("access_token") and not tdata.get("token", {}).get("refresh_token"):
            return {"gemini_5h_pct": 0.0, "gemini_weekly_pct": 0.0, "has_argon": False, "argon_weekly_pct": None, "reset_argon": None, "in_cooldown": True, "status_label": "EMPTY TOKEN"}

        with tempfile.TemporaryDirectory() as td:
            temp_home = Path(td)
            cli_dir = temp_home / ".gemini" / "antigravity-cli"
            cli_dir.mkdir(parents=True)
            with open(cli_dir / "antigravity-oauth-token", "w", encoding="utf-8") as tf:
                json.dump(tdata, tf)

            env = os.environ.copy()
            env["HOME"] = str(temp_home)

            res = subprocess.run(
                ["agy", "-p", "/usage", "--output-format", "json"],
                capture_output=True,
                text=True,
                env=env,
                timeout=12,
            )

        if res.returncode == 0:
            parsed = json.loads(res.stdout)
            cmd_data = parsed.get("command", {}).get("data", {})
            groups = cmd_data.get("groups", [])
            gemini_group = next((g for g in groups if "Gemini" in g.get("name", "") and "Argon" not in g.get("name", "")), None)
            if not gemini_group:
                gemini_group = next((g for g in groups if "Gemini" in g.get("name", "")), None)

            argon_group = next((g for g in groups if "Argon" in g.get("name", "")), None)
            has_argon = (argon_group is not None)
            argon_buckets = argon_group.get("buckets", []) if argon_group else []
            argon_b = argon_buckets[0] if argon_buckets else None
            argon_pct = round((argon_b.get("remaining_fraction", 0.0) * 100), 1) if argon_b else None
            reset_argon = argon_b.get("reset_time") if argon_b else None

            if gemini_group:
                buckets = gemini_group.get("buckets", [])
                h5_bucket = next((b for b in buckets if "5h" in b.get("id", "") or "Five" in b.get("name", "")), None)
                weekly_bucket = next((b for b in buckets if "weekly" in b.get("id", "") or "Weekly" in b.get("name", "")), None)
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
                    "in_cooldown": h5_pct <= 0.0,
                    "status_label": "READY" if h5_pct >= 15 else "LOW QUOTA" if h5_pct > 0 else "EXHAUSTED",
                }
                _update_quota_cache(email, result)
                return result
        else:
            raw_err = (res.stdout or "") + (res.stderr or "")
            if "eligibility" in raw_err.lower() or "verify" in raw_err.lower():
                result = {
                    "gemini_5h_pct": 0.0,
                    "gemini_weekly_pct": 0.0,
                    "has_argon": False,
                    "argon_weekly_pct": None,
                    "reset_argon": None,
                    "in_cooldown": True,
                    "status_label": "NEEDS VERIFICATION",
                }
                _update_quota_cache(email, result)
                return result
    except Exception as e:
        return {"error": str(e), "gemini_5h_pct": 0.0, "gemini_weekly_pct": 0.0, "has_argon": False, "argon_weekly_pct": None, "reset_argon": None, "in_cooldown": True, "status_label": "ERROR"}

    fallback = {"gemini_5h_pct": 0.0, "gemini_weekly_pct": 0.0, "has_argon": False, "argon_weekly_pct": None, "reset_argon": None, "in_cooldown": True, "status_label": "EXHAUSTED"}
    _update_quota_cache(email, fallback)
    return fallback


def get_best_account_to_switch(exclude_active: bool = True) -> str | None:
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")
    if not accounts:
        return None

    cfg = load_config()
    threshold = float(cfg.get("threshold", 5.0))

    import concurrent.futures

    def _eval(acc):
        em = acc["email"]
        if exclude_active and em == active and len(accounts) > 1:
            return None
        qdata = fetch_native_agy_quota(em)
        h5 = float(qdata.get("gemini_5h_pct", 0.0))
        weekly = float(qdata.get("gemini_weekly_pct", 0.0))
        is_avail = (h5 > threshold and weekly > 0.0)
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


def get_oauth_client():
    config = load_config()
    return (
        config.get("client_id", DEFAULT_CLIENT_ID),
        config.get("client_secret", DEFAULT_CLIENT_SECRET),
    )


def get_current_agy_token():
    if AGY_TOKEN_FILE.exists():
        try:
            with open(AGY_TOKEN_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None


def write_agy_token(token_data):
    """Atomically write token in AGY format."""
    AGY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AGY_TOKEN_FILE.with_suffix(".tmp")
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(token_data, f, indent=2)
    tmp.replace(AGY_TOKEN_FILE)


def get_email_from_token(access_token):
    """Fetch email from Google userinfo API."""
    if not access_token:
        return None
    try:
        import requests
        resp = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8
        )
        if resp.status_code == 200:
            return resp.json().get("email")
    except:
        pass
    return None


def refresh_access_token(refresh_token):
    """Refresh an access token using a refresh token."""
    if not refresh_token:
        return None
    client_id = OFFICIAL_CLIENT_ID
    client_secret = OFFICIAL_CLIENT_SECRET
    post_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    
    config = load_config()
    config_client_id = config.get("client_id")
    if config_client_id and config_client_id != client_id:
        post_data["client_id"] = config_client_id
        post_data["client_secret"] = config.get("client_secret", DEFAULT_CLIENT_SECRET)

    try:
        import requests
        resp = requests.post(TOKEN_URL, data=post_data, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            return None
    except Exception:
        return None


# ============================================================
# COMMAND: list
# ============================================================
def cmd_list():
    """List all registered accounts and allow interactive switching."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    if not accounts:
        print()
        print(yellow("  No accounts registered."))
        print(dim("  Run: agy-account add  — to add your first Google account"))
        print()
        return

    cache = _load_quota_cache()

    # If in an interactive terminal, open interactive arrow/number selection menu
    if sys.stdin.isatty() and len(accounts) > 1:
        options = []
        default_idx = 0
        for i, acc in enumerate(accounts):
            email = acc.get("email", "unknown")
            is_active = (email == active)
            if is_active:
                default_idx = i
            cached_data = cache.get(email, {}).get("data", {})
            argon_tag = " [ARGON ⚡]" if cached_data.get("has_argon") else ""
            label = f"{email} [ACTIVE]{argon_tag}" if is_active else f"{email}{argon_tag}"
            options.append(label)

        choice = select_menu(options, "🔑 Select an account to switch to:", default_idx=default_idx)
        if choice is not None:
            target_email = accounts[choice]["email"]
            if target_email != active:
                cmd_switch(target_email)
            else:
                print(green(f"\n  ✓ Already active on: {bold(target_email)}\n"))
            return
        # If Esc was pressed, fall through to print the full static table below

    print()
    print(bold("═" * 56))
    print(bold("  🔑 AGY Account Manager"))
    print(bold("═" * 56))
    print()

    if not accounts:
        print(yellow("  No accounts registered."))
        print(dim("  Run: /account add  — to add your first Google account"))
        print()
        return

    print(f"  {bold('Accounts')} ({len(accounts)} total):")
    print("  " + "─" * 50)

    for i, acc in enumerate(accounts, 1):
        email = acc.get("email", "unknown")
        added = acc.get("added_at", "unknown")
        is_active = (email == active)
        cached_data = cache.get(email, {}).get("data", {})
        argon_tag = " " + cyan(bold("[ARGON ⚡]")) if cached_data.get("has_argon") else ""

        if is_active:
            marker = green("► ")
            label = green(f"{i}. {email}")
            status = green(" [ACTIVE]")
        else:
            marker = "  "
            label = f"{i}. {email}"
            status = ""

        print(f"  {marker}{label}{status}{argon_tag}")
        print(dim(f"      Added: {added}"))

    print("  " + "─" * 50)
    print()
    print(dim("  Commands: /account add | switch | switch <email> | remove <email> | usage"))
    print()


# ============================================================
# COMMAND: add
# ============================================================
def cmd_add():
    """Add a new Google account via OAuth."""
    print()
    print(bold("  🔑 Add Google Account"))
    print("  " + "─" * 40)

    client_id = OFFICIAL_CLIENT_ID
    client_secret = OFFICIAL_CLIENT_SECRET
    redirect_uri = "https://antigravity.google/oauth-callback"

    import secrets
    import hashlib
    import base64
    code_verifier = secrets.token_urlsafe(64)
    sha256_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(sha256_hash).decode('utf-8').rstrip('=')

    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/cclog",
        "https://www.googleapis.com/auth/experimentsandconfigs",
        "openid"
    ]
    scope = "+".join(urllib.parse.quote(s, safe='') for s in scopes)
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
        "timestamp": time.time()
    }
    STATE_FILE = GEMINI_DIR / "agy_auth_state.json"
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state_data, f)
    except Exception as e:
        print(yellow(f"  Warning: could not save auth state: {e}"))

    print()
    print(f"  {bold('Step 1')}: Copy the sign-in URL using the box below, then open it in your browser:")
    print(f"```text\n{auth_url}\n```")
    print(f"  {bold('Step 2')}: After authorizing, copy the authorization code from the")
    print(f"  Antigravity web page (click the 'Copy' button on the screen).")
    print()
    if sys.stdin.isatty():
        print(f"  {bold('Step 3')}: Paste the copied code below:")
        try:
            code_or_url = input(cyan("  Paste code here: ")).strip()
            if code_or_url:
                cmd_code(code_or_url)
        except (KeyboardInterrupt, EOFError):
            print(yellow("\n  Cancelled."))
    else:
        print(f"  {bold('Step 3')}: Paste the code using this command:")
        print(f"  {green('/account code <copied_code>')}  (inside AGY)")
        print(f"  or")
        print(f"  {green('agy-account code <copied_code>')}  (in your terminal)")
        print()


# ============================================================
# COMMAND: code
# ============================================================
def cmd_code(code_or_url):
    """Exchange code for tokens and save profile."""
    if not code_or_url:
        print(red("  ✗ Please provide the authorization code."))
        return

    code = code_or_url
    if "code=" in code_or_url:
        try:
            parsed = urllib.parse.urlparse(code_or_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code = params["code"][0]
        except Exception as e:
            print(yellow(f"  Failed to parse URL, using raw input as code: {e}"))

    STATE_FILE = GEMINI_DIR / "agy_auth_state.json"
    redirect_uri = "https://antigravity.google/oauth-callback"
    client_id = OFFICIAL_CLIENT_ID
    client_secret = OFFICIAL_CLIENT_SECRET
    code_verifier = None

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            redirect_uri = state.get("redirect_uri", redirect_uri)
            client_id = state.get("client_id", client_id)
            client_secret = state.get("client_secret", client_secret)
            code_verifier = state.get("code_verifier")
        except:
            pass

    print(dim("  Exchanging authorization code..."))
    try:
        import requests
        post_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            post_data["code_verifier"] = code_verifier

        resp = requests.post(TOKEN_URL, data=post_data, timeout=12)
        resp.raise_for_status()
        tokens = resp.json()
    except Exception as e:
        print(red(f"  ✗ Token exchange failed: {e}"))
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        print(red("  ✗ No access token received."))
        return

    # Authenticate real email from Google
    email = get_email_from_token(access_token)
    if not email:
        print(yellow("  ⚠ Could not determine email. Using fallback timestamp."))
        email = f"account-{int(time.time())}"

    # Calculate accurate expiry (now + expires_in seconds)
    expires_in = tokens.get("expires_in", 3600)
    expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    expiry = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    profile_dir = PROFILES_DIR / email
    profile_dir.mkdir(parents=True, exist_ok=True)

    token_data = {
        "token": {
            "access_token": access_token,
            "token_type": tokens.get("token_type", "Bearer"),
            "refresh_token": refresh_token,
            "expiry": expiry,
        },
        "auth_method": "consumer"
    }

    with open(profile_dir / "token.json", 'w', encoding='utf-8') as f:
        json.dump(token_data, f, indent=2)

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
            print(green(f"  ✓ Set as active account"))

        data["accounts"] = accounts
        save_accounts(data)

    if STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
        except:
            pass

    print()
    print(f"  Total accounts: {bold(str(len(accounts)))}")
    print()


# ============================================================
# COMMAND: switch
# ============================================================
def cmd_switch(target=None):
    """Switch to a specific or next account safely."""
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

        # Validate target profile credentials
        target_dir = PROFILES_DIR / target
        token_file = target_dir / "token.json"

        if not token_file.exists():
            print(red(f"  ✗ No credentials found for: {target} (Missing token.json)"))
            return

        try:
            with open(token_file, 'r', encoding='utf-8') as f:
                target_token = json.load(f)
        except Exception as e:
            print(red(f"  ✗ Corrupt credentials for {target}: {e}"))
            return

        refresh_tok = target_token.get("token", {}).get("refresh_token")
        access_tok = target_token.get("token", {}).get("access_token")
        if not refresh_tok and not access_tok:
            print(red(f"  ✗ Account {target} has empty tokens! Please re-authenticate it."))
            return

        # SAFE BACKUP: Only backup active token IF active email matches token contents
        if active and AGY_TOKEN_FILE.exists():
            try:
                with open(AGY_TOKEN_FILE, 'r', encoding='utf-8') as f:
                    curr_tok_data = json.load(f)
                curr_acc = curr_tok_data.get("token", {}).get("access_token")
                curr_email = get_email_from_token(curr_acc) if curr_acc else None
                if curr_email == active:
                    curr_profile = PROFILES_DIR / active
                    curr_profile.mkdir(parents=True, exist_ok=True)
                    with open(curr_profile / "token.json", 'w', encoding='utf-8') as pf:
                        json.dump(curr_tok_data, pf, indent=2)
            except Exception:
                pass

        # Refresh target token if possible
        if refresh_tok:
            refreshed = refresh_access_token(refresh_tok)
            if refreshed:
                new_acc = refreshed.get("access_token")
                if new_acc:
                    target_token["token"]["access_token"] = new_acc
                if "refresh_token" in refreshed:
                    target_token["token"]["refresh_token"] = refreshed["refresh_token"]
                expires_in = refreshed.get("expires_in", 3600)
                exp_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                target_token["token"]["expiry"] = exp_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                
                with open(token_file, 'w', encoding='utf-8') as f:
                    json.dump(target_token, f, indent=2)

        # Write to active AGY token file atomically
        write_agy_token(target_token)

        # Update active account
        data["active"] = target
        save_accounts(data)

        # Invalidate quota cache
        if QUOTA_CACHE_FILE.exists():
            try:
                QUOTA_CACHE_FILE.unlink()
            except Exception:
                pass

        print()
        print(green(f"  ✓ Switched to: {bold(target)}"))
        print(yellow(f"  ⚠ Restart AGY for changes to take full effect."))
        print()


# ============================================================
# COMMAND: remove
# ============================================================
def cmd_remove(email):
    """Remove an account from the pool."""
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
        accounts = [a for a in accounts if a["email"] != actual_email]
        data["accounts"] = accounts

        if data.get("active") == actual_email:
            data["active"] = accounts[0]["email"] if accounts else None

        save_accounts(data)

        profile_dir = PROFILES_DIR / actual_email
        if profile_dir.exists():
            shutil.rmtree(profile_dir)

        print(green(f"  ✓ Removed: {actual_email}"))
        if data.get("active"):
            print(f"  Active account: {data['active']}")


# ============================================================
# COMMAND: status
# ============================================================
def cmd_status():
    """Show current status."""
    data = load_accounts()
    config = load_config()
    active = data.get("active", "None")
    total = len(data.get("accounts", []))

    print()
    print(bold("═" * 56))
    print(bold("  📊 AGY Account Status"))
    print(bold("═" * 56))
    print()
    print(f"  Active Account:  {green(active) if active else yellow('None')}")
    print(f"  Total Accounts:  {bold(str(total))}")
    print(f"  Auto-Switch:     {green('Enabled') if config.get('auto_switch') else red('Disabled')}")
    print(f"  Strategy:        {config.get('strategy', 'N/A')}")
    print(f"  Threshold:       {config.get('threshold', 5)}%")
    print(f"  Max Retries:     {config.get('max_retries', 3)}")
    print()

    print(f"  {bold('Live Account Readiness & Quota Status')}:")
    print("  " + "─" * 50)
    cache = _load_quota_cache()
    for acc in data.get("accounts", []):
        em = acc.get("email")
        prof_dir = PROFILES_DIR / em
        tok_file = prof_dir / "token.json"
        status_tag = green("● ACTIVE & READY") if em == active else cyan("● READY")
        if tok_file.exists():
            try:
                with open(tok_file) as tf:
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
        print(f"  • {bold(em)}: {status_tag}{argon_tag}")
    print("  " + "─" * 50)
    print()


# ============================================================
# COMMAND: config
# ============================================================
def cmd_config(args=None):
    """View or set auto-switch configuration."""
    config = load_config()

    if not args:
        print()
        print(bold("  ⚙ Auto-Switch Configuration"))
        print("  " + "─" * 40)
        for key, val in config.items():
            print(f"  {key}: {bold(str(val))}")
        print()
        print(dim("  To change: /account config <key> <value>"))
        print()
        return

    if len(args) >= 2:
        key = args[0]
        value = args[1]
        if value.lower() in ("true", "yes"):
            value = True
        elif value.lower() in ("false", "no"):
            value = False
        else:
            try:
                value = int(value)
            except:
                try:
                    value = float(value)
                except:
                    pass
        config[key] = value
        save_config(config)
        print(green(f"  ✓ Set {key} = {value}"))
    else:
        print(red("  ✗ Usage: /account config <key> <value>"))


# ============================================================
# COMMAND: usage
# ============================================================
def cmd_usage():
    """Display real native AGY /usage quota directly from Google backend for all registered accounts."""
    import concurrent.futures

    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    print()
    print(bold("═" * 68))
    print(bold("  📈 Google AGY Live Quota (Direct Backend Data)"))
    print(bold("═" * 68))
    print()
    print(f"  Active Account:  {green(active) if active else yellow('None')}")
    print()
    print("  " + "─" * 68)

    if not accounts:
        print(yellow("  No accounts registered."))
        print()
        return

    print(dim("  ⚡ Querying Google backend live in parallel..."), flush=True)

    def _fetch_worker(acc):
        em = acc["email"]
        qdata = fetch_native_agy_quota(em)
        h5 = qdata.get("gemini_5h_pct", 0.0)
        weekly = qdata.get("gemini_weekly_pct", 0.0)
        score = h5 * 10.0 + weekly
        return (score, em, qdata)

    max_workers = min(len(accounts), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        ranked_accounts = list(executor.map(_fetch_worker, accounts))

    ranked_accounts.sort(key=lambda x: x[0], reverse=True)

    for rank, (score, em, qdata) in enumerate(ranked_accounts, 1):
        is_active = (em == active)
        active_tag = green("[ACTIVE]") if is_active else dim("[STANDBY]")
        h5 = qdata.get("gemini_5h_pct", 0.0)
        weekly = qdata.get("gemini_weekly_pct", 0.0)

        filled_5h = int(h5 / 100.0 * 12)
        bar_5h = "█" * filled_5h + "░" * (12 - filled_5h)
        col_5h = green(bar_5h) if h5 >= 50 else yellow(bar_5h) if h5 >= 15 else red(bar_5h)

        filled_w = int(weekly / 100.0 * 12)
        bar_w = "█" * filled_w + "░" * (12 - filled_w)
        col_w = green(bar_w) if weekly >= 50 else yellow(bar_w) if weekly >= 15 else red(bar_w)

        raw_status = qdata.get("status_label", "EXHAUSTED")
        if raw_status == "READY":
            status_text = green("READY")
        elif raw_status == "LOW QUOTA":
            status_text = yellow("LOW QUOTA")
        elif raw_status == "NEEDS VERIFICATION":
            status_text = yellow("NEEDS GOOGLE VERIFICATION (Verify in browser)")
        else:
            status_text = red("EXHAUSTED (0%)")

        if is_active:
            status_text = green("ACTIVE & IN USE")

        has_argon = qdata.get("has_argon", False)
        argon_pct = qdata.get("argon_weekly_pct")
        reset_argon = qdata.get("reset_argon")
        argon_badge = " " + cyan(bold("[ARGON ⚡]")) if has_argon else ""

        print(f"  #{rank} {bold(em):<28} {active_tag}{argon_badge} | Status: {status_text}")
        print(f"     5-Hour Limit:  [{col_5h}] {bold(f'{h5:.1f}%'):<7} (Reset: {qdata.get('reset_5h', 'N/A')})")
        print(f"     Weekly Limit:  [{col_w}] {bold(f'{weekly:.1f}%'):<7} (Reset: {qdata.get('reset_weekly', 'N/A')})")
        if has_argon and argon_pct is not None:
            filled_a = int(argon_pct / 100.0 * 12)
            bar_a = "█" * filled_a + "░" * (12 - filled_a)
            col_a = green(bar_a) if argon_pct >= 50 else yellow(bar_a) if argon_pct >= 15 else red(bar_a)
            print(f"     Argon Quota:   [{col_a}] {bold(f'{argon_pct:.1f}%'):<7} (Reset: {reset_argon or 'N/A'})")
        else:
            print(f"     Argon Access:  {dim('— Not enabled (Standard)')}")
        print()

    # Fast in-memory priority determination
    cfg = load_config()
    threshold = float(cfg.get("threshold", 5.0))
    candidates = []
    for score, em, qdata in ranked_accounts:
        if em != active:
            h5 = float(qdata.get("gemini_5h_pct", 0.0))
            weekly = float(qdata.get("gemini_weekly_pct", 0.0))
            if h5 > threshold and weekly > 0.0:
                c_score = (h5 * 1000.0) + weekly
                candidates.append((c_score, em))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_next = candidates[0][1]
        print(f"  🎯 {cyan('Auto-Switch Priority')}: Next account will be {green(best_next)} (Highest Real Quota)")
    print("  " + "─" * 68)
    print()


# ============================================================
# MAIN
# ============================================================
def main():
    if len(sys.argv) < 2:
        cmd_list()
        return

    command = sys.argv[1].lower()

    if command == "list":
        cmd_list()
    elif command == "add":
        cmd_add()
    elif command == "code":
        code_val = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_code(code_val)
    elif command == "switch":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_switch(target)
    elif command == "remove":
        email = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_remove(email)
    elif command == "status":
        cmd_status()
    elif command == "config":
        cmd_config(sys.argv[2:] if len(sys.argv) > 2 else None)
    elif command == "usage":
        cmd_usage()
    else:
        print(red(f"  Unknown command: {command}"))
        print("  Available: list, add, code, switch, remove, status, config, usage")


if __name__ == "__main__":
    main()
