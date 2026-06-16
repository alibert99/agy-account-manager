#!/bin/bash

# Exit on error
set -e

echo "Installing AGY Account Manager Plugin..."

PLUGIN_DIR="$HOME/.gemini/config/plugins/agy-account-manager"
echo "Copying plugin files to $PLUGIN_DIR..."
mkdir -p "$PLUGIN_DIR"

# Copy files, excluding git metadata
cp -rf plugin.json hooks.json "$PLUGIN_DIR/"
[ -d commands ] && cp -rf commands/ "$PLUGIN_DIR/"
[ -d skills ] && cp -rf skills/ "$PLUGIN_DIR/"
[ -d scripts ] && cp -rf scripts/ "$PLUGIN_DIR/"

# Validate plugin
if command -v agy &> /dev/null; then
    echo "Validating plugin installation..."
    agy plugin validate "$PLUGIN_DIR" || echo "Warning: plugin validation returned some warnings."
else
    echo "Warning: agy CLI not found in PATH."
fi

# Create the shell wrapper
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
WRAPPER_PATH="$BIN_DIR/agy-account"

echo "Creating command wrapper at $WRAPPER_PATH..."
cat << 'EOF' > "$WRAPPER_PATH"
#!/bin/bash
# Wrapper for AGY Google Account Manager plugin
python3 "$HOME/.gemini/config/plugins/agy-account-manager/scripts/account_manager.py" "$@"
EOF

chmod +x "$WRAPPER_PATH"

echo ""
echo "Success! The 'agy-account' command has been installed."
echo "If '$BIN_DIR' is not in your PATH, please add it to your shell configuration (e.g. .bashrc or .zshrc):"
echo "  export PATH=\"\$HOME/.external_bin:\$PATH\""
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
