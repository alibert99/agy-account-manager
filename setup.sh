#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="${HOME}/.gemini/config/plugins/agy-account-manager"
BIN_DIR="${HOME}/.local/bin"
WRAPPER_PATH="${BIN_DIR}/agy-account"

echo "Installing AGY Account Manager v2.0.0..."

if [[ "${SCRIPT_DIR}" != "${PLUGIN_DIR}" ]]; then
  if command -v agy >/dev/null 2>&1; then
    echo "Installing plugin via 'agy plugin install'..."
    agy plugin install "${SCRIPT_DIR}"
  else
    echo "Copying plugin files to ${PLUGIN_DIR}..."
    mkdir -p "${PLUGIN_DIR}"
    cp -f "${SCRIPT_DIR}/plugin.json" "${SCRIPT_DIR}/hooks.json" "${PLUGIN_DIR}/"
    cp -rf "${SCRIPT_DIR}/assets" "${SCRIPT_DIR}/skills" "${SCRIPT_DIR}/scripts" "${PLUGIN_DIR}/"
  fi
fi

chmod +x "${PLUGIN_DIR}/scripts/account_manager.py" "${PLUGIN_DIR}/scripts/agy_quota_hook.py"

if command -v agy >/dev/null 2>&1; then
  echo "Validating plugin..."
  agy plugin validate --strict "${PLUGIN_DIR}"
else
  echo "Warning: 'agy' CLI not found in PATH."
fi

mkdir -p "${BIN_DIR}"
cat > "${WRAPPER_PATH}" <<'EOF'
#!/usr/bin/env bash
exec python3 "${HOME}/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py" "$@"
EOF
chmod +x "${WRAPPER_PATH}"

echo ""
echo "Installed successfully!"
echo "  Plugin:      ${PLUGIN_DIR}"
echo "  CLI wrapper: ${WRAPPER_PATH}"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo ""
  echo "Add ${BIN_DIR} to your PATH to use 'agy-account' from anywhere:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
