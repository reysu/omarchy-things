"""Quick-add popup: a tiny single-input window bound to a global hotkey.

Syntax in the input line:
    buy milk @today #groceries !2026-09-01 -- some notes here
    grammar lives in entry.py (shared with the in-app `n` entry line)
"""

from __future__ import annotations

import platform
import shlex
import subprocess

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import backend, config, entry


def parse_quick(text: str) -> dict:
    """Same grammar as the in-app entry line (see entry.py)."""
    return entry.parse_entry(text)


class QuickAddApp(App):
    TITLE = "things: quick add"
    CSS = """
    Screen {
        background: $q-bg;
    }
    #box {
        height: 100%;
        padding: 1 2;
        background: $q-bg;
    }
    #row {
        height: 1;
    }
    #plus {
        width: 2;
        color: $q-accent;
        text-style: bold;
    }
    #hint {
        color: $q-hint;
        padding-top: 1;
    }
    Input {
        background: transparent;
        border: none;
        height: 1;
        padding: 0;
    }
    """
    VARS = {
        "night": {"q-bg": "#101418", "q-accent": "#4fa8ff", "q-hint": "#3d4654"},
        "terminal": {"q-bg": "ansi_default", "q-accent": "ansi_blue", "q-hint": "ansi_bright_black"},
    }

    BINDINGS = [Binding("escape", "quit", "Cancel")]

    def __init__(self, theme_name: str | None = None) -> None:
        self.theme_name = theme_name or config.theme()
        super().__init__(ansi_color=(self.theme_name == "terminal"))
        self._dests: list[tuple[str, str]] = []

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), **self.VARS[self.theme_name]}

    def on_load(self) -> None:
        self.theme = "ansi-dark" if self.theme_name == "terminal" else "tokyo-night"

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            with Horizontal(id="row"):
                yield Static("＋", id="plus")
                yield Input(placeholder="New To-Do…", id="quick-input")
            yield Static(
                "@today · @tonight · @sat · @next sat · @aug 24 · #project · "
                "--due <date> · +tag · \"notes\"",
                id="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#quick-input", Input).focus()
        self._load_dests()

    @work(thread=True)
    def _load_dests(self) -> None:
        try:
            projects = backend.projects()
            areas = backend.areas()
        except backend.CliError:
            return
        dests = [(p.get("title") or "", p["id"]) for p in projects]
        dests += [(a.get("title") or "", a["id"]) for a in areas if a.get("title")]
        self._dests = dests

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        parsed = parse_quick(event.value)
        if not parsed["title"]:
            return
        dest_id = None
        if parsed["dest"]:
            low = parsed["dest"].lower()
            match = [d for label, d in self._dests if low in label.lower()]
            dest_id = match[0] if match else None
            if dest_id is None:
                self.query_one("#hint", Static).update(
                    f"[#f7ce45]no list matches #{parsed['dest']} — sending to Inbox[/]"
                )
        # fire-and-forget: close instantly, create in a detached process,
        # and only surface a desktop notification if it fails
        cmd = [backend.THINGS_BIN, "--log-level", "off", "new", parsed["title"]]
        if dest_id:
            cmd += ["--in", dest_id]
        if parsed["when"]:
            cmd += ["--when", parsed["when"]]
        if parsed["deadline"]:
            cmd += ["--deadline", parsed["deadline"]]
        if parsed["notes"]:
            cmd += ["--notes", parsed["notes"]]
        if parsed.get("tags"):
            cmd += ["--tags", parsed["tags"]]
        settle = f"{shlex.quote(backend.THINGS_BIN)} --log-level off today"
        msg = "failed to add: " + parsed["title"]
        if platform.system() == "Darwin":
            notify = (
                "osascript -e "
                + shlex.quote(f'display notification {msg!r} with title "things"')
            )
        else:
            notify = f"notify-send 'things' {shlex.quote(msg)}"
        shell = (
            f"if {shlex.join(cmd)} >/dev/null 2>&1; then {settle} >/dev/null 2>&1; "
            f"else {notify}; fi"
        )
        subprocess.Popen(
            ["bash", "-c", shell],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.exit()


def run(theme_name: str | None = None) -> None:
    QuickAddApp(theme_name).run()
