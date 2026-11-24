---
name: quota
description: Check live 5-hour and weekly Gemini API quota usage and model tiers across all registered AGY accounts.
---

# Quota Command

This skill displays the live 5-hour and weekly quota usage along with model tier availability across all registered AGY accounts.

When the user runs `/quota` or asks to check account quotas:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py usage
```

If the user explicitly asks to refresh or bypass the cache, append `--refresh`:
```bash
python3 ~/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py usage --refresh
```

Always run the command and show the output directly to the user.
