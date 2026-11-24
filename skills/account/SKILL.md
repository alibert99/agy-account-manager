---
name: account
description: Manage multiple AGY Google accounts. Add, list, switch, remove accounts, inspect quotas/models, or configure automatic quota rotation.
---

# AGY Account Manager

This skill manages multiple Google accounts for Antigravity CLI (`agy`) with isolated quota checking and automatic quota rotation.

When the user invokes `/account` or asks about managing AGY accounts, determine the requested action and run the matching command below using the terminal.

## Available Commands

### List accounts
When the user runs `/account` or `/account list`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py list --no-interactive
```

### Add a new account
When the user runs `/account add`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py add
```
This outputs a Google OAuth URL (PKCE). Instruct the user to open the link, sign in, and copy the authorization code or callback URL.

### Complete sign-in with authorization code
When the user runs `/account code <copied_code_or_url>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py code "<copied_code_or_url>"
```

### Switch to the best available account
When the user runs `/account switch` with no arguments:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py switch
```

### Switch to a specific account
When the user runs `/account switch <email_or_index>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py switch "<email_or_index>"
```

### Remove an account
When the user runs `/account remove <email>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py remove "<email>"
```

### Show account and configuration status
When the user runs `/account status`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py status
```

### View or update auto-switch configuration
When the user runs `/account config` or `/account config <key> <value>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py config
```
Or with arguments:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py config <key> <value>
```

### Check quota usage across all accounts
When the user runs `/account usage` (append `--refresh` if the user asks for fresh/live data):
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py usage
```

### Check available models across accounts
When the user runs `/account models` or `/account models <filter>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py models
```

### Check Gemini 4 Hydrogen availability
When the user runs `/account hydrogen` or asks which accounts have Gemini 4 Hydrogen access:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py hydrogen
```

## Important Notes
- Always execute the command and present its output clearly to the user.
- After switching accounts, remind the user to restart AGY if the running session continues using cached in-memory credentials.
