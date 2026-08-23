"""User preferences: ~/.config/things-tui/config.toml (tiny; written by hand)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

THEMES = ("night", "terminal")

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "things-tui"
CONFIG_FILE = CONFIG_DIR / "config.toml"


def load() -> dict:
    try:
        return tomllib.loads(CONFIG_FILE.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def theme(default: str = "night") -> str:
    """Resolution order: env THINGS_TUI_THEME → config file → default."""
    t = os.environ.get("THINGS_TUI_THEME") or load().get("theme") or default
    return t if t in THEMES else default


def save_theme(name: str) -> None:
    cfg = load()
    cfg["theme"] = name
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {str(v).lower()}"
             for k, v in cfg.items()]
    CONFIG_FILE.write_text(
        "# things-tui preferences\n# theme = \"night\" (built-in dark palette) | "
        "\"terminal\" (transparent — inherits your terminal's colors)\n"
        + "\n".join(lines) + "\n"
    )
