# AGY Account Manager

A native plugin for [Antigravity CLI (AGY)](https://github.com) to manage multiple Google accounts and automatically switch credentials when your API quota is exhausted.

## Features

- 🔑 **Multi-Account Support**: Authenticate and save multiple Google accounts via OAuth.
- 🔄 **Auto-Switching**: Automatically intercept `429`, `Resource exhausted`, and other rate-limit error responses, then switch to the next configured account with valid credentials.
- ⚙️ **Custom Configuration**: Toggle auto-switching, set retry thresholds, and customize model matching rules.
- 🎛️ **Slash Commands**: Manage accounts directly inside AGY via the `/account` command.

---

## Installation

### 1. Clone the repository
Clone this repository to your local machine:
```bash
git clone https://github.com/alibert99/agy-account-manager.git ~/agy-account-manager
```

### 2. Install into AGY
Install the plugin using the AGY plugin command:
```bash
agy plugin install ~/agy-account-manager
```

This will automatically copy the manifest, hooks, and skills into your AGY configuration path (`~/.gemini/config/plugins/agy-account-manager/`).

---

## Usage

Once installed, you can use the following commands inside your AGY terminal or run the python script directly.

### Commands

* **List all accounts**
  ```bash
  /account list
  ```
  *(or just `/account`)*

* **Add a new Google account**
  ```bash
  /account add
  ```
  This will launch a local server and open a Google Login page in your browser. After logging in, the token will be fetched, parsed, and registered.

* **Switch active account**
  ```bash
  /account switch
  ```
  *(switches to the next account in the pool)* or:
  ```bash
  /account switch <email_or_index>
  ```
  *(switches to a specific account by matching email or list index)*

* **Remove an account**
  ```bash
  /account remove <email>
  ```

* **Check current status**
  ```bash
  /account status
  ```

* **Configure settings**
  ```bash
  /account config <key> <value>
  ```

---

## Configuration Keys

You can customize the behavior of the auto-switching mechanism with `/account config`:

| Key | Default | Description |
|---|---|---|
| `auto_switch` | `true` | Enable or disable automatic account rotation on quota error |
| `max_retries` | `3` | Maximum number of switch attempts per conversation turn |
| `threshold` | `10` | Quota threshold (percentage) |
| `strategy` | `"gemini3.1-series-only"` | Strategy filter for switching accounts |

Example:
```bash
/account config max_retries 5
/account config auto_switch false
```

---

## File Structure

```
agy-account-manager/
├── plugin.json          # Plugin manifest
├── hooks.json           # Hook registrations for PreInvocation and PostInvocation
├── README.md            # Documentation
├── skills/
│   └── account/
│       └── SKILL.md     # Slash command definition for AGY
└── scripts/
    ├── account_manager.py # Main python script for oauth and switching logic
    └── agy_quota_hook.py  # Hook script executing during Pre/Post invocation
```

## License

MIT
