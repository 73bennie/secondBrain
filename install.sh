#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.secondbrain/bin"
BASHRC="$HOME/.bashrc"

echo "Installing SecondBrain..."

# 1. Create target directory
mkdir -p "$TARGET_DIR"

# 2. Symlink all sb* scripts
for file in "$REPO_DIR"/sb*; do
    name="$(basename "$file")"
    ln -sf "$file" "$TARGET_DIR/$name"
    echo "Linked: $name"
done

# 3. Ensure PATH contains ~/.secondbrain/bin
if ! grep -q 'export PATH="$HOME/.secondbrain/bin:$PATH"' "$BASHRC"; then
    echo 'export PATH="$HOME/.secondbrain/bin:$PATH"' >> "$BASHRC"
    echo "Added ~/.secondbrain/bin to PATH in .bashrc"
else
    echo "PATH already configured"
fi

echo
echo "Installation complete."
echo "Run: source ~/.bashrc"
