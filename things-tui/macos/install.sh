#!/usr/bin/env bash
# macOS install: the bundled things3-cloud fork, the app via pipx.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
command -v brew >/dev/null || { echo "install Homebrew first: https://brew.sh"; exit 1; }
# Build the vendored fork rather than the released things3-cloud: 0.8.3 only
# understands the Task6 wire entity and silently drops the Task7 records newer
# Things clients emit, so to-dos from those devices never appear.
if ! command -v things3 >/dev/null 2>&1; then
  command -v cargo >/dev/null 2>&1 || brew install rust
  echo "building the bundled things3 CLI (first run only)…"
  (cd "$here/../../things3-cloud" && cargo build --release)
  sudo mkdir -p /usr/local/bin
  sudo install -m755 "$here/../../things3-cloud/target/release/things3" /usr/local/bin/things3
fi
if ! command -v pipx >/dev/null 2>&1; then
  brew install pipx
fi
pipx ensurepath >/dev/null 2>&1 || true
echo "installing things-tui…"
pipx install --force "$here/.."
cat <<MSG

Installed `things-tui`. Next (open a NEW terminal so PATH picks up ~/.local/bin):
  1. things3 set-auth          # Things Cloud email + password (stored locally)
  2. things-tui                # first launch does a full sync — give it a minute
     things-tui quick          # quick add
Terminal font: use a Nerd Font (brew install --cask font-jetbrains-mono-nerd-font)
so the icons render as single-width glyphs.
Global quick-add hotkey: bind a Raycast/Hammerspoon/skhd shortcut to e.g.
  open -na Ghostty --args -e things-tui quick      (or your terminal's equivalent)
MSG
