#!/usr/bin/env bash
# macOS install: things3-cloud via Homebrew, the app via pipx.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
command -v brew >/dev/null || { echo "install Homebrew first: https://brew.sh"; exit 1; }
if ! brew list things3-cloud >/dev/null 2>&1; then
  brew install evanpurkhiser/personal/things3-cloud
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
