#!/usr/bin/env bash
# Linux (Omarchy/Hyprland) install: desktop entry, icon, ALT+SPACE quick-add.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
command -v things3 >/dev/null || {
  echo "things3 CLI not found. Run the bundled setup first — it builds the vendored"
  echo "things3-cloud fork and logs you in:"
  echo "  $(cd "$here/../.." && pwd)/setup"
  echo
  echo "Do not install the released things3-cloud instead: 0.8.3 only understands the"
  echo "Task6 wire entity and silently drops Task7 records from newer Things clients."
  exit 1
}
command -v pipx >/dev/null && pipx install --force "$here/.." || {
  python3 -m venv "$here/../.venv" && "$here/../.venv/bin/pip" install -q "$here/.."
  mkdir -p ~/.local/bin && ln -sf "$here/../bin/things-tui" ~/.local/bin/things-tui
}
mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
cp "$here/things-tui.svg" ~/.local/share/icons/hicolor/scalable/apps/
sed "s|__THINGS_TUI_BIN__|$(command -v things-tui || echo "$HOME/.local/bin/things-tui")|" \
  "$here/things.desktop" > ~/.local/share/applications/things.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
cat <<MSG

Installed. Add the ALT+SPACE quick-add to Hyprland (~/.config/hypr/bindings.lua):
  o.bind("ALT + SPACE", "Things quick add", "foot --app-id=things.quick --window-size-chars=72x5 $(command -v things-tui || echo "$HOME/.local/bin/things-tui") quick")
and in ~/.config/hypr/hyprland.lua:
  o.window("things.quick", { float = true })
  o.window("things.quick", { center = true })
MSG
