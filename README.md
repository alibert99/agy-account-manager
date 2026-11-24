# AGY Account Manager

A **native plugin** for **Antigravity CLI (`agy`)** to manage multiple Google accounts, monitor live quotas and model access in parallel, and automatically rotate accounts on quota exhaustion.

Built with **zero external dependencies** (Python 3.8+ standard library only) — ideal for headless Linux servers, VPS/SSH sessions, and terminal-first workflows without needing a desktop GUI or background daemon.

---

## Features

- **Native AGY Plugin** — Installs directly with `agy plugin install` and exposes `/account` and `/quota` skills inside your AGY session.
- **Parallel Isolated Quota Scanning** — Checks 5-hour and weekly quotas plus available model tiers (including Argon & Hydrogen) across all accounts in parallel inside isolated temporary `HOME` directories, so your active OAuth token is never overwritten or corrupted.
- **Smart Auto-Switching Hooks** — Lifecycle hooks (`PreInvocation`, `PostInvocation`, and `Stop`) detect `429` / `RESOURCE_EXHAUSTED` / rate-limit errors and automatically rotate to the account with the highest remaining quota.
- **Interactive Terminal Switcher** — Running `agy-account` (or `agy-account list`) in an interactive terminal opens a fast arrow-key / vim (`j`/`k`) / number (`1-9`) account picker.
- **OAuth 2.0 + PKCE** — Add accounts cleanly from headless/SSH environments by copying the sign-in URL and pasting the authorization code.
- **Hardened & Zero-Dependency** — Uses reentrant `flock` locking, atomic writes, `0600` credential permissions, and Python's standard library only (no `pip install` needed).

---

## Requirements

- [Antigravity CLI (`agy`)](https://antigravity.google) installed and available in `PATH`
- Python 3.8+ (Linux / macOS)

---

## Installation

### 1. Quick Setup (Plugin + CLI Wrapper)

```bash
git clone https://github.com/alibert99/agy-account-manager.git ~/agy-account-manager
cd ~/agy-account-manager
./setup.sh
```

This installs the native plugin into AGY, validates it with `agy plugin validate --strict`, and creates the optional `agy-account` terminal command in `~/.local/bin/agy-account`.

### 2. Manual Plugin-Only Installation

```bash
git clone https://github.com/alibert99/agy-account-manager.git ~/agy-account-manager
agy plugin install ~/agy-account-manager
agy plugin list
```

---

## Usage

You can run commands **inside AGY** via `/account` and `/quota`, or **from your terminal** via `agy-account`.

| Inside AGY | Terminal (`agy-account`) | Description |
|---|---|---|
| `/account` or `/account list` | `agy-account` / `agy-account list` | List accounts (opens interactive picker in a TTY; pass `--no-interactive` for plain list) |
| `/account add` | `agy-account add` | Generate a Google OAuth (PKCE) sign-in URL to add an account |
| `/account code <code>` | `agy-account code <code>` | Exchange the copied authorization code or callback URL for tokens |
| `/account switch` | `agy-account switch` | Auto-select and switch to the account with the highest remaining quota |
| `/account switch <email\|#>` | `agy-account switch <email\|#>` | Switch to a specific account by email substring or list index |
| `/account remove <email>` | `agy-account remove <email>` | Remove an account and automatically re-sync active credentials if needed |
| `/account status` | `agy-account status` | Display active account, auto-switch settings, and local credential health |
| `/account config [k v]` | `agy-account config [k v]` | View or update auto-switch settings |
| `/quota` or `/account usage` | `agy-account usage [-r]` | Show live 5-hour & weekly quotas plus Argon/Hydrogen tiers (`-r` bypasses 90s cache) |
| `/account models [filter]` | `agy-account models [filter] [-r]` | Check available AGY models across all accounts (optional substring filter) |
| `/account hydrogen` | `agy-account hydrogen` | Dedicated check for Gemini 4 Hydrogen access across all accounts |

### Example Output (`/quota` / `agy-account usage`)

```text
  📊 AGY Quota & Model Tier Status
  ──────────────────────────────────────────────────────────────────
  Account                     5-Hour  Weekly   Argon     Hydrogen
  ──────────────────────────────────────────────────────────────────
► alice@gmail.com               100%     92%   100% ⚡   OPEN ⚛
  bob@gmail.com                  85%     64%   —         —
  dev@gmail.com                   0%     40%   —         —
  ──────────────────────────────────────────────────────────────────
```

---

## Configuration

Customize auto-switching behavior with `/account config <key> <value>` (stored in `~/.gemini/agy_auth_config.json`):

| Key | Default | Description |
|---|---|---|
| `auto_switch` | `true` | Enable or disable automatic account rotation on quota errors |
| `threshold` | `10` | Minimum 5-hour remaining quota (%) required to consider an account available |
| `max_retries` | `3` | Maximum automatic switch attempts per turn |
| `strategy` | `"highest-quota-first"` | Selection strategy when switching without a target account |

Examples:

```bash
/account config threshold 15
/account config max_retries 5
/account config auto_switch false
```

### Custom OAuth Client (Optional)

By default, the plugin uses Google's public installed-app OAuth configuration. You can override it via environment variables:

```bash
export AGY_OAUTH_CLIENT_ID="your-client-id"
export AGY_OAUTH_CLIENT_SECRET="your-client-secret"
```

---

## Storage & Security

- Account profiles are saved under `~/.gemini/agy_auth_profiles/<email>/token.json` with `0600` file permissions.
- Registry and config live in `~/.gemini/agy_accounts.json` and `~/.gemini/agy_auth_config.json` (`0600`).
- Quota scans run inside ephemeral temporary directories (`HOME=<tempdir>`) so `~/.gemini/antigravity-cli/antigravity-oauth-token` is never touched during inspection.
- Concurrent writes are guarded by a reentrant process lock (`~/.gemini/.agy_account_manager.lock`).

---

## Project Structure

```text
agy-account-manager/
├── plugin.json                 # Native AGY plugin manifest
├── hooks.json                  # PreInvocation, PostInvocation, and Stop hook definitions
├── assets/
│   └── logo.svg                # Plugin icon
├── skills/
│   ├── account/SKILL.md        # /account skill definition
│   └── quota/SKILL.md          # /quota skill definition
├── scripts/
│   ├── account_manager.py      # Zero-dependency account & quota manager CLI
│   └── agy_quota_hook.py       # Official AGY hook handler for auto-rotation
├── setup.sh                    # One-step plugin & CLI wrapper installer
├── LICENSE                     # MIT License
└── README.md
```

---

## License

[MIT](LICENSE)
