---
name: account
description: Manage AGY Google accounts. Add, list, switch, or remove accounts for quota rotation.
---

# Account Manager

This skill manages multiple Google accounts for AGY with automatic quota rotation.

When the user invokes `/account` or asks about account management, determine the subcommand from their input and run the appropriate command using the terminal.

## Available Commands

### List accounts
When the user says `/account list` or `/account` with no arguments:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py list
```

### Add a new account
When the user says `/account add`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py add
```
This will display a Google OAuth URL and instructions. Tell the user to open the link, login, and copy the code or the redirect URL.

### Exchange code for tokens
When the user says `/account code <copied_code_or_url>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py code <copied_code_or_url>
```
Replace `<copied_code_or_url>` with the code or URL provided by the user.

### Switch to next account
When the user says `/account switch` with no specific account:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py switch
```

### Switch to a specific account
When the user says `/account switch <email>` with a specific email:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py switch <email>
```
Replace `<email>` with the actual email provided by the user.

### Remove an account
When the user says `/account remove <email>`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py remove <email>
```

### Show current status
When the user says `/account status`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py status
```

### Configure auto-switch settings
When the user says `/account config`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py config
```

### Show quota limits
When the user says `/account limits`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py limits
```

## Important Notes
- After switching accounts, inform the user they may need to restart AGY for the new credentials to take effect.
- Always run the command and show the output to the user.
- Do not modify the commands - run them exactly as shown.
