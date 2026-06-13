#!/usr/bin/env python3
"""
AGY Account Manager v1.0
Native account management for Antigravity CLI.
Supports: add, list, switch, remove, status, config
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import http.server
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from datetime import datetime

# --- Paths ---
GEMINI_DIR = Path(os.path.expanduser("~/.gemini"))
AGY_DIR = GEMINI_DIR / "antigravity-cli"
AGY_TOKEN_FILE = AGY_DIR / "antigravity-oauth-token"
PROFILES_DIR = GEMINI_DIR / "agy_auth_profiles"
ACCOUNTS_FILE = GEMINI_DIR / "agy_accounts.json"
CONFIG_FILE = GEMINI_DIR / "agy_auth_config.json"
QUOTA_CACHE_FILE = GEMINI_DIR / "quota_cache.json"

# --- OAuth Config ---
# Google's public OAuth client for desktop apps (not secret)
DEFAULT_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "GOCSPX" + "-4uHgMPm-1o7Sk-geV6Cu5clXFsxl"
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def color(text, code):
    """ANSI color wrapper."""
    return f"\033[{code}m{text}\033[0m"

def green(t): return color(t, "32")
def yellow(t): return color(t, "33")
def red(t): return color(t, "31")
def cyan(t): return color(t, "36")
def bold(t): return color(t, "1")
def dim(t): return color(t, "2")


def load_accounts():
    """Load accounts registry."""
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"accounts": [], "active": None}


def save_accounts(data):
    """Save accounts registry."""
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_config():
    """Load auto-switch config."""
    defaults = {
        "auto_switch": True,
        "threshold": 10,
        "max_retries": 3,
        "strategy": "gemini3.1-series-only",
        "model_pattern": "gemini-3.1.*",
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
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_oauth_client():
    """Get OAuth client credentials from config or defaults."""
    config = load_config()
    return (
        config.get("client_id", DEFAULT_CLIENT_ID),
        config.get("client_secret", DEFAULT_CLIENT_SECRET),
    )


def get_current_agy_token():
    """Read current AGY token."""
    if AGY_TOKEN_FILE.exists():
        try:
            with open(AGY_TOKEN_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None


def write_agy_token(token_data):
    """Write token in AGY format."""
    with open(AGY_TOKEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(token_data, f)


def get_email_from_token(access_token):
    """Fetch email from Google userinfo API."""
    try:
        import requests
        resp = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("email")
    except:
        return None


def refresh_access_token(refresh_token):
    """Refresh an access token using a refresh token."""
    client_id, client_secret = get_oauth_client()
    try:
        import requests
        resp = requests.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(red(f"  Failed to refresh token: {e}"))
        return None


# ============================================================
# COMMAND: list
# ============================================================
def cmd_list():
    """List all registered accounts."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

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

        if is_active:
            marker = green("► ")
            label = green(f"{i}. {email}")
            status = green(" [ACTIVE]")
        else:
            marker = "  "
            label = f"{i}. {email}"
            status = ""

        print(f"  {marker}{label}{status}")
        print(dim(f"      Added: {added}"))

    print("  " + "─" * 50)
    print()
    print(dim("  Commands: /account add | switch | switch <email> | remove <email>"))
    print()


# ============================================================
# COMMAND: add
# ============================================================
def cmd_add():
    """Add a new Google account via OAuth."""
    print()
    print(bold("  🔑 Add Google Account"))
    print("  " + "─" * 40)

    client_id, client_secret = get_oauth_client()

    # Find available port
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('localhost', 0))
        port = sock.getsockname()[1]
        sock.close()
    except:
        port = 8080

    redirect_uri = f"http://localhost:{port}"
    scope = "%20".join(urllib.parse.quote(s, safe='') for s in OAUTH_SCOPES)

    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    # Save state to temp file
    state = {
        "port": port,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "timestamp": time.time()
    }
    STATE_FILE = GEMINI_DIR / "agy_auth_state.json"
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except Exception as e:
        print(yellow(f"  Warning: could not save auth state: {e}"))

    print()
    print(f"  {bold('Step 1')}: Open this URL in your browser to sign in:")
    print(f"  {cyan(auth_url)}")
    print()
    print(f"  {bold('Step 2')}: After authorizing, your browser will redirect to a localhost URL")
    print(f"  (e.g., http://localhost:{port}/?code=4/0Ad...). It will show 'Unable to connect'—this is normal.")
    print()
    if sys.stdin.isatty():
        print(f"  {bold('Step 3')}: Copy the entire URL or just the code parameter, and paste it below:")
        try:
            code_or_url = input(cyan("  Paste redirect URL or code here: ")).strip()
            if code_or_url:
                cmd_code(code_or_url)
        except (KeyboardInterrupt, EOFError):
            print(yellow("\n  Cancelled."))
    else:
        print(f"  {bold('Step 3')}: Copy the entire URL or just the code parameter, and run:")
        print(f"  {green('/account code <copied_code_or_url>')}  (inside AGY)")
        print(f"  or")
        print(f"  {green('agy-account code <copied_code_or_url>')}  (in your terminal)")
        print()


# ============================================================
# COMMAND: code
# ============================================================
def cmd_code(code_or_url):
    """Exchange code for tokens and save profile."""
    if not code_or_url:
        print(red("  ✗ Please provide the code or redirect URL."))
        return

    # Extract code if full URL was pasted
    code = code_or_url
    if "code=" in code_or_url:
        try:
            parsed = urllib.parse.urlparse(code_or_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code = params["code"][0]
        except Exception as e:
            print(yellow(f"  Failed to parse URL, using raw input as code: {e}"))

    # Load state
    STATE_FILE = GEMINI_DIR / "agy_auth_state.json"
    redirect_uri = None
    client_id, client_secret = get_oauth_client()

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            redirect_uri = state.get("redirect_uri")
            client_id = state.get("client_id", client_id)
            client_secret = state.get("client_secret", client_secret)
        except:
            pass

    if not redirect_uri:
        # Fallback to standard loopback port if state is missing
        port = 8080
        if "localhost:" in code_or_url:
            match = re.search(r"localhost:(\d+)", code_or_url)
            if match:
                port = int(match.group(1))
        redirect_uri = f"http://localhost:{port}"
        print(yellow(f"  ⚠ Auth state file not found. Guessing redirect_uri: {redirect_uri}"))

    print(dim("  Exchanging authorization code..."))
    try:
        import requests
        resp = requests.post(TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=10)
        resp.raise_for_status()
        tokens = resp.json()
    except Exception as e:
        print(red(f"  ✗ Token exchange failed: {e}"))
        if hasattr(e, 'response') and e.response is not None:
            print(red(f"  Response: {e.response.text}"))
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        print(red("  ✗ No access token received."))
        return

    # Get email
    email = get_email_from_token(access_token)
    if not email:
        print(yellow("  ⚠ Could not determine email. Using 'unknown'."))
        email = f"account-{int(time.time())}"

    # Calculate expiry
    expiry = datetime.utcnow().isoformat() + "Z"

    # Save profile
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

    # Update accounts registry
    data = load_accounts()
    accounts = data.get("accounts", [])

    # Check if already exists
    existing = [a for a in accounts if a.get("email") == email]
    if existing:
        print(yellow(f"  ⚠ Account {email} already exists. Credentials updated."))
    else:
        accounts.append({
            "email": email,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        print(green(f"  ✓ Account added: {email}"))

    # Set as active if first account
    if not data.get("active"):
        data["active"] = email
        write_agy_token(token_data)
        print(green(f"  ✓ Set as active account"))

    data["accounts"] = accounts
    save_accounts(data)

    # Clean up state file
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
    """Switch to a specific or next account."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    active = data.get("active")

    if len(accounts) < 2:
        print(yellow("  ⚠ Need at least 2 accounts to switch. Run: /account add"))
        return

    if target is None:
        # Switch to next account
        emails = [a["email"] for a in accounts]
        if active in emails:
            idx = (emails.index(active) + 1) % len(emails)
        else:
            idx = 0
        target = emails[idx]
    else:
        # Find by email or index
        found = None
        # Try as index
        try:
            idx = int(target) - 1
            if 0 <= idx < len(accounts):
                found = accounts[idx]["email"]
        except ValueError:
            pass
        # Try as email (partial match)
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

    # Load target profile
    profile_dir = PROFILES_DIR / target
    token_file = profile_dir / "token.json"

    if not token_file.exists():
        print(red(f"  ✗ No credentials found for: {target}"))
        return

    # Backup current AGY token to current active profile
    if active:
        current_profile = PROFILES_DIR / active
        current_profile.mkdir(parents=True, exist_ok=True)
        if AGY_TOKEN_FILE.exists():
            shutil.copy2(AGY_TOKEN_FILE, current_profile / "token.json")

    # Load target token
    with open(token_file, 'r', encoding='utf-8') as f:
        target_token = json.load(f)

    # Try to refresh the token to ensure it's valid
    refresh_tok = target_token.get("token", {}).get("refresh_token")
    if refresh_tok:
        refreshed = refresh_access_token(refresh_tok)
        if refreshed:
            target_token["token"]["access_token"] = refreshed.get("access_token", target_token["token"]["access_token"])
            if "refresh_token" in refreshed:
                target_token["token"]["refresh_token"] = refreshed["refresh_token"]
            target_token["token"]["expiry"] = datetime.utcnow().isoformat() + "Z"
            # Save refreshed token back to profile
            with open(token_file, 'w', encoding='utf-8') as f:
                json.dump(target_token, f, indent=2)

    # Write to AGY token file
    write_agy_token(target_token)

    # Update active
    data["active"] = target
    save_accounts(data)

    # Clear caches
    for cache in [QUOTA_CACHE_FILE, GEMINI_DIR / "mcp-oauth-tokens-v2.json"]:
        if cache.exists():
            try:
                cache.unlink()
            except:
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

    data = load_accounts()
    accounts = data.get("accounts", [])

    # Find account
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

    # Remove profile
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
    print(f"  Threshold:       {config.get('threshold', 10)}%")
    print(f"  Max Retries:     {config.get('max_retries', 3)}")
    print()

    # Check AGY token
    token = get_current_agy_token()
    if token:
        expiry = token.get("token", {}).get("expiry", "unknown")
        print(f"  Token Expiry:    {dim(expiry)}")
        auth_method = token.get("auth_method", "unknown")
        print(f"  Auth Method:     {dim(auth_method)}")
    else:
        print(yellow("  No AGY token found"))

    print()


# ============================================================
# COMMAND: config
# ============================================================
def cmd_config(args=None):
    """View or set auto-switch configuration."""
    config = load_config()

    if not args:
        # Show config
        print()
        print(bold("  ⚙ Auto-Switch Configuration"))
        print("  " + "─" * 40)
        for key, val in config.items():
            print(f"  {key}: {bold(str(val))}")
        print()
        print(dim("  To change: /account config <key> <value>"))
        print()
        return

    # Set config
    if len(args) >= 2:
        key = args[0]
        value = args[1]
        # Type coercion
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
    else:
        print(red(f"  Unknown command: {command}"))
        print("  Available: list, add, code, switch, remove, status, config")


if __name__ == "__main__":
    main()
