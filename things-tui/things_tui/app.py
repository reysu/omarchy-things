"""things — a Things 3 client for the terminal."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import backend, config, modals, rows

Task = dict[str, Any]

SYNC_INTERVAL = 30  # seconds between background cloud syncs (a sync is ~0.5s)

BUILTIN_VIEWS = [
    ("inbox", "  Inbox", rows.ACCENT),
    ("today", "  Today", rows.YELLOW),
    ("upcoming", "  Upcoming", rows.RED),
    ("anytime", "  Anytime", rows.GREEN),
    ("someday", "  Someday", rows.MOON),
    ("logbook", "  Logbook", rows.GREEN),
    ("trash", "  Trash", rows.DIM),
]


@dataclass
class ViewSpec:
    kind: str  # builtin name | "project" | "area" | "search"
    id: str | None = None
    title: str = ""
    query: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id or self.query or ''}"


@dataclass
class ListRow:
    kind: str  # "task" | "project" | "trash" | "header"
    task: Task | None = None
    label: str = ""


def cluster(items, key_of):
    """Group `items`, keeping each key at its first appearance and Things'
    own order within it. Returns (loose, [(label, items), …]).

    The CLI hands back a list view in Things' order, which interleaves
    containers and headings. Printing a header whenever the value differed
    from the *previous row* therefore repeated the same few titles down the
    whole list — 261 headers for 19 distinct projects in Anytime, and "Bugs"
    six times inside one project — instead of grouping under them.
    """
    loose: list = []
    order: list = []
    groups: dict = {}
    for it in items:
        key, label = key_of(it)
        if not key:
            loose.append(it)
            continue
        if key not in groups:
            groups[key] = (label, [])
            order.append(key)
        groups[key][1].append(it)
    return loose, [groups[k] for k in order]


class MarkedList(OptionList):
    """OptionList that paints the ▌ cursor mark in the highlighted row's
    gutter during its own render pass, so it moves in the same frame as
    the highlight (swapping prompts afterwards lagged a row behind)."""

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        if self.highlighted is None:
            return strip
        try:
            option_index, line_offset = self._lines[self.scroll_offset.y + y]
        except IndexError:
            return strip
        if option_index != self.highlighted or line_offset != 0 or not strip.cell_length:
            return strip
        segs = list(strip)
        base = segs[0].style or Style()
        mark = Segment(rows.MARK, base + Style.parse(rows.MARK_STYLE))
        rest = strip.crop(1)
        return Strip([mark], 1) + rest


class Sidebar(MarkedList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("l", "app.focus_list", show=False),
        Binding("tab", "app.focus_list", show=False),
        Binding("backspace", "app.delete_sidebar_item", show=False),
    ]


class TaskList(MarkedList):
    class CheckboxClicked(Message):
        """Mouse click landed on a row's checkbox."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    class RowOpened(Message):
        """Double-click on a row: open it."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    async def _on_click(self, event: events.Click) -> None:
        # Textual also runs OptionList._on_click (MRO dispatch) unless the
        # default is prevented — without this a click would select AND open.
        event.prevent_default()
        clicked: int | None = event.style.meta.get("option")
        if clicked is None or self._options[clicked].disabled:
            return
        event.stop()
        self.highlighted = clicked
        # checkbox hitbox: the "○ " glyph at the start of the row (after the
        # 1-cell padding; indented rows shift it right)
        if event.x <= 3 + self._row_indent(clicked):
            self.post_message(self.CheckboxClicked(clicked))
        elif event.chain >= 2:
            self.post_message(self.RowOpened(clicked))

    def _row_indent(self, index: int) -> int:
        app = self.app
        rows_ = getattr(app, "list_rows", [])
        if 0 <= index < len(rows_):
            row = rows_[index]
            if row and row.task and row.task.get("heading"):
                return 2
        return 0

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("h", "app.focus_sidebar", show=False),
        Binding("tab", "app.focus_sidebar", show=False),
        Binding("space", "app.toggle_done", "done", show=False),
        Binding("c", "app.cancel_task", show=False),
        Binding("e", "app.edit_task", show=False),
        Binding("s", "app.schedule_task", show=False),
        Binding("d", "app.deadline_task", show=False),
        Binding("m", "app.move_task", show=False),
        Binding("backspace", "app.trash_task", show=False),
    ]


THEME_VARS = {
    "night": {
        "t-bg": "#101418", "t-panel": "#0c1014", "t-modal": "#131920",
        "t-border": "#232a33", "t-border2": "#2c3644", "t-hint": "#3d4654",
        "t-hl": "#1b2733", "t-accent": "#4fa8ff", "t-fg": "#d8dee8",
        "t-dim": "#6b7482", "t-overlay": "#040608",
    },
    # no backgrounds at all → the terminal's own bg/transparency shows; all
    # colors are the 16 ANSI slots so the terminal theme drives the look
    "terminal": {
        "t-bg": "ansi_default", "t-panel": "ansi_default", "t-modal": "ansi_default",
        "t-border": "ansi_bright_black", "t-border2": "ansi_bright_black",
        "t-hint": "ansi_bright_black", "t-hl": "ansi_default",
        "t-accent": "ansi_blue", "t-fg": "ansi_default", "t-dim": "ansi_bright_black",
        "t-overlay": "ansi_default",
    },
}


class ThingsApp(App):
    TITLE = "things"
    CSS_PATH = "styles.tcss"

    def __init__(self, theme_name: str | None = None) -> None:
        self.theme_name = theme_name or config.theme()
        rows.apply_palette(self.theme_name)
        super().__init__(ansi_color=(self.theme_name == "terminal"))
        self._init_state()

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), **THEME_VARS[self.theme_name]}

    BINDINGS = [
        Binding("q", "quit", "quit", show=False),
        Binding("question_mark", "help", "help"),
        Binding("f1", "help", show=False),
        Binding("n", "new_task", "new"),
        Binding("ctrl+n", "new_task", show=False),
        Binding("N", "new_inbox_task", show=False),
        Binding("slash", "search", "search"),
        Binding("ctrl+f", "search", show=False),
        Binding("g", "chord", show=False),
        Binding("G", "list_bottom", show=False),
        Binding("S", "sync", "sync"),
        Binding("ctrl+y", "sync", show=False),
        Binding("r", "refresh", "refresh"),
        Binding("T", "toggle_theme", show=False),
        Binding("ctrl+b", "toggle_sidebar", show=False),
        Binding("D", "toggle_detail", show=False),
        Binding("a", "logbook_all", show=False),
        Binding("escape", "back", show=False),
        # Things-style task shortcuts (work from the list)
        Binding("ctrl+z", "undo", show=False),
        Binding("ctrl+k", "toggle_done", show=False),
        Binding("ctrl+alt+k", "cancel_task", show=False),
        Binding("ctrl+t", "when_shortcut('today')", show=False),
        Binding("ctrl+e", "when_shortcut('evening')", show=False),
        Binding("ctrl+s", "when_shortcut('someday')", show=False),
        Binding("ctrl+r", "when_shortcut('anytime')", show=False),
        Binding("1", "view('inbox')", show=False),
        Binding("2", "view('today')", show=False),
        Binding("3", "view('upcoming')", show=False),
        Binding("4", "view('anytime')", show=False),
        Binding("5", "view('someday')", show=False),
        Binding("6", "view('logbook')", show=False),
        Binding("7", "view('trash')", show=False),
    ]

    # `g` chord table: press g, then one of these
    CHORDS = {
        "i": "builtin:inbox",
        "t": "builtin:today",
        "u": "builtin:upcoming",
        "a": "builtin:anytime",
        "s": "builtin:someday",
        "l": "builtin:logbook",
        "x": "builtin:trash",
    }

    sync_status: reactive[str] = reactive("–")

    def _init_state(self) -> None:
        self.view_spec = ViewSpec("today", title="Today")
        self.nav_stack: list[ViewSpec] = []
        self.list_rows: list[ListRow | None] = []  # index-aligned with OptionList
        self._rendered_sig: tuple | None = None    # what the list currently shows

        self.areas: list[dict] = []
        self.projects: list[Task] = []
        self.counts: dict[str, int] = {}
        self.logbook_days: int | None = 60
        self.undo_stack: list[dict] = []
        self._last_sync: float | None = None
        self._syncing = False

    # ---------------------------------------------------------------- UI

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Static(" things", id="brand")
                yield Sidebar(id="sidebar")
            with Vertical(id="main"):
                yield Static("", id="view-title")
                yield TaskList(id="tasklist")
            with VerticalScroll(id="detail"):
                yield Static("", id="detail-body")
        yield Static("", id="statusbar")

    def on_mount(self) -> None:
        self.theme = "ansi-dark" if self.theme_name == "terminal" else "tokyo-night"
        # direct references: safe to use even while a modal screen is on top
        self._tl = self.query_one("#tasklist", TaskList)
        self._sb = self.query_one("#sidebar", Sidebar)
        self._detail_body = self.query_one("#detail-body", Static)
        self._view_title = self.query_one("#view-title", Static)
        self._statusbar = self.query_one("#statusbar", Static)
        if not backend.STATE_CACHE.exists():
            # fresh install: the CLI has no local cache yet, so the first
            # sync (full history) has to complete before anything can show
            self._view_title.update(
                Text(" first sync — pulling your Things Cloud data…",
                     style=f"bold {rows.YELLOW}")
            )
            self.notify("first sync with Things Cloud — this can take a minute")
        else:
            self.refresh_sidebar_data()
            self.load_view()
        self.action_sync()
        self.set_interval(SYNC_INTERVAL, self.action_sync)
        self.set_interval(5, self._update_statusbar)
        self._tl.focus()

    def on_resize(self, event: events.Resize) -> None:
        # counts are right-aligned to the sidebar width, so re-lay them out
        if self.areas or self.projects or self.counts:
            self._fill_sidebar()

    def check_action(self, action: str, parameters: tuple) -> bool:
        # keep app-level shortcuts (n, 1-7, space via list, …) inert while a
        # modal screen is open
        if len(self.screen_stack) > 1 and action not in ("quit", "focus_next", "focus_previous"):
            return False
        return True

    # ------------------------------------------------------------ sidebar

    @work(thread=True, exclusive=True, group="sidebar")
    def refresh_sidebar_data(self) -> None:
        try:
            areas = backend.areas()
            projects = backend.projects()
            counts = {
                "inbox": len(backend.view("inbox")),
                "today": len(backend.view("today")),
            }
        except backend.CliError as e:
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        def apply() -> None:
            self.areas = [a for a in areas if a.get("title")]
            self.projects = projects
            self.counts = counts
            self._fill_sidebar()
        self.call_from_thread(apply)

    def _fill_sidebar(self) -> None:
        sb = self._sb
        keep = sb.highlighted
        sb.clear_options()
        opts: list[Option] = []
        self._sidebar_keys: list[str] = []
        # strict grid: every icon in a fixed column, counts flush right
        width = sb.scrollable_content_region.width
        if width < 12:
            width = 26

        def add(prompt: Text, key: str) -> None:
            opts.append(Option(prompt, id=key))
            self._sidebar_keys.append(key)

        def line(
            indent: int, icon: str, icon_style: str,
            title: str, title_style: str, count: int | None = None,
        ) -> Text:
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append(" " * indent)
            t.append(icon, style=icon_style)
            t.append(" ")
            if count:
                c = str(count)
                room = width - indent - 2 - len(c) - 2
                if cell_len(title) > room > 4:
                    title = title[: room - 1] + "…"
                t.append(title, style=title_style)
                pad = width - cell_len(t.plain) - len(c) - 1
                t.append(" " * max(pad, 2))
                t.append(c, style=rows.DIM)
            else:
                t.append(title, style=title_style)
            return t

        for name, label, color in BUILTIN_VIEWS:
            icon, _, text = label.partition("  ")
            add(
                line(1, icon, color, text, rows.FG, self.counts.get(name)),
                f"builtin:{name}",
            )

        by_area: dict[str | None, list[Task]] = {}
        for p in self.projects:
            aid = (p.get("area") or {}).get("id") if p.get("area") else None
            by_area.setdefault(aid, []).append(p)

        for area in self.areas:
            add(
                line(1, "", rows.GREEN, area["title"], f"bold {rows.FG}"),
                f"area:{area['id']}",
            )
            for p in by_area.get(area["id"], []):
                add(
                    line(3, "", rows.ACCENT, p.get("title") or "(untitled)", rows.FG),
                    f"project:{p['id']}",
                )

        loose = by_area.get(None, [])
        if loose:
            add_header = Text(" projects", style=f"bold {rows.DIM}")
            opts.append(Option(add_header, disabled=True))
            self._sidebar_keys.append("")
            for p in loose:
                add(
                    line(3, "", rows.ACCENT, p.get("title") or "(untitled)", rows.FG),
                    f"project:{p['id']}",
                )

        sb.add_options(opts)
        if keep is not None and sb.option_count:
            sb.highlighted = min(keep, sb.option_count - 1)

    @on(OptionList.OptionSelected, "#sidebar")
    def _sidebar_pick(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id or ""
        self._open_key(key)

    def _open_key(self, key: str, *, push: bool = True) -> None:
        kind, _, ident = key.partition(":")
        if kind == "builtin":
            self._switch(ViewSpec(ident, title=ident.capitalize()), push=push)
        elif kind == "project":
            p = next((p for p in self.projects if p["id"] == ident), None)
            self._switch(
                ViewSpec("project", id=ident, title=(p or {}).get("title") or "Project"),
                push=push,
            )
        elif kind == "area":
            a = next((a for a in self.areas if a["id"] == ident), None)
            self._switch(
                ViewSpec("area", id=ident, title=(a or {}).get("title") or "Area"),
                push=push,
            )

    def _switch(self, spec: ViewSpec, *, push: bool = True) -> None:
        if spec.key == self.view_spec.key:
            self.load_view()
            return
        if push:
            self.nav_stack.append(self.view_spec)
            if len(self.nav_stack) > 20:
                self.nav_stack.pop(0)
        self.view_spec = spec
        self.load_view()
        self._tl.focus()

    # ------------------------------------------------------------- loading

    @work(thread=True, exclusive=True, group="load")
    def load_view(self) -> None:
        spec = self.view_spec
        try:
            if spec.kind in ("inbox", "today", "upcoming", "anytime", "someday"):
                tasks = backend.view(spec.kind)
            elif spec.kind == "logbook":
                tasks = backend.logbook(self.logbook_days)
            elif spec.kind == "trash":
                tasks = backend.trash()
            elif spec.kind == "project":
                tasks = backend.project_tasks(spec.id or "")
            elif spec.kind == "area":
                tasks = backend.area_items(spec.id or "")
            elif spec.kind == "search":
                tasks = backend.find(spec.query, notes=True)
            else:
                tasks = []
        except backend.CliError as e:
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        self.call_from_thread(self._render_view, spec, tasks)

    def _view_signature(self, spec: ViewSpec, tasks: list[Task]) -> tuple:
        """Everything the rendered list depends on. Identical signature means
        a rebuild would draw exactly what is already on screen."""
        lists = tuple((p.get("id"), p.get("title")) for p in self.projects) \
            + tuple((a.get("id"), a.get("title")) for a in self.areas)
        return (spec.key, self.logbook_days, lists, tuple(
            (t.get("id"), t.get("title"), t.get("status"), t.get("heading"),
             (t.get("dates") or {}).get("modified_at"),
             (t.get("dates") or {}).get("deadline_at"),
             (t.get("start") or {}).get("scheduled_at"),
             (t.get("start") or {}).get("evening"))
            for t in tasks))

    def _render_view(self, spec: ViewSpec, tasks: list[Task]) -> None:
        if spec.key != self.view_spec.key:
            return
        tl = self._tl
        # A background refresh that fetched identical data must not touch the
        # OptionList: clearing and refilling it resets the scroll offset and
        # flickers the whole view for no reason.
        sig = self._view_signature(spec, tasks)
        if sig == self._rendered_sig and tl.option_count:
            self._update_detail()
            self._update_statusbar()
            return
        self._rendered_sig = sig
        # Remember WHICH to-do was selected, not where it sat. Deleting a row
        # above the cursor — or an insert at the top — shifts every index, so
        # restoring by position silently lands you on a different to-do.
        keep = tl.highlighted
        keep_id = None
        cur = self._current_row()
        if cur is not None and cur.task is not None:
            keep_id = cur.task.get("id")
        tl.clear_options()
        self.list_rows = []
        opts: list[Option] = []

        def header(label: str, style: str = f"bold {rows.ACCENT}") -> None:
            t = Text(f"\n{label}", style=style)
            opts.append(Option(t, disabled=True))
            self.list_rows.append(ListRow("header", label=label))

        def add_task(task: Task, **kw: Any) -> None:
            opts.append(Option(rows.task_row(task, **kw)))
            self.list_rows.append(ListRow("task", task=task))

        n_shown = 0
        if spec.kind == "today":
            day = [t for t in tasks if not (t.get("start") or {}).get("evening")]
            eve = [t for t in tasks if (t.get("start") or {}).get("evening")]
            for t in day:
                add_task(t)
            if eve:
                header("  This Evening", style=f"bold {rows.MOON}")
                for t in eve:
                    add_task(t)
            n_shown = len(tasks)
        elif spec.kind == "upcoming":
            last = None
            for t in sorted(
                tasks,
                key=lambda t: (t.get("start") or {}).get("scheduled_at") or "9999",
            ):
                label = rows.date_label((t.get("start") or {}).get("scheduled_at"))
                if label != last:
                    header(label or "Later", style=f"bold {rows.RED}")
                    last = label
                add_task(t)
            n_shown = len(tasks)
        elif spec.kind == "logbook":
            last = None
            for t in tasks:
                label = rows.date_label(
                    t["dates"].get("completed_at"), with_year=True
                )
                if label != last:
                    header(label or "Unknown", style=f"bold {rows.GREEN}")
                    last = label
                add_task(t)
            n_shown = len(tasks)
        elif spec.kind == "trash":
            for item in tasks:
                opts.append(Option(rows.trash_row(item)))
                self.list_rows.append(ListRow("trash", task=item))
            n_shown = len(tasks)
        elif spec.kind in ("anytime", "someday"):
            _, byproject = cluster(
                tasks,
                lambda t: ((rows.container_label(t) or "No project"),) * 2,
            )
            for label, group in byproject:
                header(label, style=f"bold {rows.DIM}")
                for t in group:
                    add_task(t, show_container=False)
            n_shown = len(tasks)
        elif spec.kind == "project":
            tasks = [t for t in tasks if t.get("id") != spec.id]
            # Un-headed to-dos first, which is where Things puts them, then
            # one block per heading. Keyed by heading id so two headings that
            # happen to share a title stay separate.
            def heading_key(t):
                h = t.get("heading") or {}
                return (h.get("id") or h.get("title"),
                        h.get("title") or "(untitled heading)")

            loose, byheading = cluster(tasks, heading_key)
            for t in loose:
                add_task(t, show_container=False)
            for label, group in byheading:
                header(f"¶ {label}", style=f"bold {rows.PURPLE}")
                for t in group:
                    add_task(t, show_container=False, indent=2)
            n_shown = len(tasks)
        elif spec.kind == "area":
            projs = [t for t in tasks if t.get("type") == "project"]
            todos = [t for t in tasks if t.get("type") != "project"]
            if projs:
                header("Projects", style=f"bold {rows.ACCENT}")
                for p in projs:
                    add_task(p, show_container=False)
            if todos:
                if projs:
                    header("To-Dos", style=f"bold {rows.DIM}")
                for t in todos:
                    add_task(t, show_container=False)
            n_shown = len(tasks)
        elif spec.kind == "search":
            low = spec.query.lower()
            lists = self._matching_lists(low)
            if lists:
                header("Lists", style=f"bold {rows.ACCENT}")
                for kind, ident, title in lists:
                    if kind == "project":
                        p = next((p for p in self.projects if p["id"] == ident), None)
                        if p:
                            add_task(p, show_container=False)
                    else:
                        add_task(self._area_as_row(ident, title), show_container=False)
                if tasks:
                    header("To-Dos", style=f"bold {rows.DIM}")
            for t in tasks:
                add_task(t)
            n_shown = len(tasks) + len(lists)
        else:  # inbox, fallback
            for t in tasks:
                add_task(t)
            n_shown = len(tasks)

        tl.add_options(opts)
        if tl.option_count:
            idx = None
            if keep_id is not None and keep_id != "pending":
                for i, r in enumerate(self.list_rows):
                    if r.task is not None and r.task.get("id") == keep_id:
                        idx = i
                        break
            if idx is None:
                # The selected to-do is gone (deleted, or completed out of the
                # view). Holding the offset lands on whatever slid up into it,
                # which is where the eye already is.
                idx = keep if keep is not None else 0
            idx = min(idx, tl.option_count - 1)
            tl.highlighted = idx
            # skip leading header
            if self.list_rows[idx].kind == "header" and idx + 1 < tl.option_count:
                tl.highlighted = idx + 1

        title = self._view_title
        icon_color = {
            "today": rows.YELLOW, "upcoming": rows.RED, "someday": rows.MOON,
            "logbook": rows.GREEN, "trash": rows.DIM,
        }.get(spec.kind, rows.ACCENT)
        head = Text()
        head.append(f" {spec.title} ", style=f"bold {icon_color}")
        head.append(f" {n_shown}", style=rows.DIM)
        if spec.kind == "logbook" and self.logbook_days is not None:
            head.append(f"  · last {self.logbook_days}d — press a for all", style=rows.DIM)
        if spec.kind == "trash":
            head.append(
                "  · items trashed from your other Things apps (read-only here)",
                style=rows.DIM,
            )
        title.update(head)
        self._update_detail()
        self._update_statusbar()

    # ------------------------------------------------------------- detail

    @staticmethod
    def _area_as_row(area_id: str, title: str) -> Task:
        return {
            "id": area_id, "type": "area", "title": title, "status": "incomplete",
            "start": {}, "dates": {}, "tags": [], "checklist": [], "recurrence": {},
            "project": None, "area": None, "heading": None, "notes": None,
        }

    def _current_row(self) -> ListRow | None:
        tl = self._tl
        if tl.highlighted is None or not self.list_rows:
            return None
        if 0 <= tl.highlighted < len(self.list_rows):
            return self.list_rows[tl.highlighted]
        return None

    @on(OptionList.OptionHighlighted, "#tasklist")
    def _task_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._update_detail()

    def _update_detail(self) -> None:
        body = self._detail_body
        row = self._current_row()
        if not row or row.kind == "header" or not row.task:
            body.update(Text("", style=rows.DIM))
            return
        if row.kind == "trash":
            item = row.task
            t = Text()
            t.append(item.get("title") or "", style=f"bold {rows.FG}")
            if item.get("notes"):
                t.append("\n\n")
                t.append(item["notes"], style=rows.DIM)
            body.update(t)
            return
        task = row.task
        t = Text()
        t.append(task.get("title") or "(untitled)", style=f"bold {rows.FG}")
        crumb = rows.container_label(task)
        if crumb:
            t.append(f"\n{crumb}", style=rows.DIM)
        start = task.get("start") or {}
        when_bits = []
        sched = start.get("scheduled_at")
        if sched:
            when_bits.append(rows.date_label(sched))
        elif start.get("bucket"):
            when_bits.append(start["bucket"])
        if start.get("evening"):
            when_bits.append(" evening")
        if when_bits:
            t.append(f"\nwhen: {' · '.join(when_bits)}", style=rows.MOON)
        frag = rows.deadline_frag(task)
        if frag:
            t.append("\n")
            t.append_text(frag)
        rec = task.get("recurrence") or {}
        if rec.get("rule") or rec.get("is_instance"):
            t.append("\n repeating", style=rows.PURPLE)
        tags = [
            tg.get("title") for tg in (task.get("tags") or [])
            if isinstance(tg, dict) and tg.get("title")
        ]
        if tags:
            t.append("\n" + "  ".join(f"#{x}" for x in tags), style=rows.PURPLE)
        notes = task.get("notes")
        if notes:
            t.append("\n\n")
            t.append(notes, style=rows.FG)
        checklist = task.get("checklist") or []
        if checklist:
            t.append("\n")
            for item in checklist:
                done = item.get("status") == "completed"
                t.append("\n")
                t.append(" " if done else " ", style=rows.ACCENT if done else rows.DIM)
                t.append(
                    item.get("title") or "",
                    style=f"strike {rows.DIM}" if done else rows.FG,
                )
        dates = task.get("dates") or {}
        created = rows.date_label(dates.get("created_at"), with_year=True)
        if created:
            t.append(f"\n\ncreated {created}", style=rows.DIM)
        completed = rows.date_label(dates.get("completed_at"), with_year=True)
        if completed:
            t.append(f" · completed {completed}", style=rows.DIM)
        body.update(t)

    @on(TaskList.CheckboxClicked)
    def _checkbox_clicked(self, event: TaskList.CheckboxClicked) -> None:
        self.action_toggle_done()

    @on(TaskList.RowOpened)
    def _row_opened(self, event: TaskList.RowOpened) -> None:
        self._open_current_row()

    @on(OptionList.OptionSelected, "#tasklist")
    def _task_selected(self, event: OptionList.OptionSelected) -> None:
        self._open_current_row()

    def _open_current_row(self) -> None:
        row = self._current_row()
        if not row or not row.task or row.task.get("_pending"):
            return
        if row.kind == "task" and row.task.get("type") == "project":
            self._switch(
                ViewSpec("project", id=row.task["id"], title=row.task.get("title") or "Project")
            )
            return
        if row.kind == "task" and row.task.get("type") == "area":
            self._switch(
                ViewSpec("area", id=row.task["id"], title=row.task.get("title") or "Area")
            )
            return
        if row.kind == "task":
            task = row.task

            def _closed(result: dict | None) -> None:
                if not result:
                    return
                if result.get("edit"):
                    self.action_edit_task()
                elif result.get("move"):
                    self.action_move_task()
                elif result.get("schedule"):
                    self.action_schedule_task()
                elif result.get("deadline"):
                    self.action_deadline_task()
                elif result.get("delete"):
                    self.action_trash_task()
                elif result.get("changed"):
                    self.load_view()

            self.push_screen(modals.TaskDetailScreen(task), _closed)

    # ------------------------------------------------------------- actions

    def action_swap_focus(self) -> None:
        tl = self._tl
        sb = self._sb
        (sb if tl.has_focus else tl).focus()

    def action_focus_list(self) -> None:
        self._tl.focus()

    def action_focus_sidebar(self) -> None:
        self._sb.focus()

    def action_view(self, name: str) -> None:
        self._open_key(f"builtin:{name}")

    def action_back(self) -> None:
        if self.nav_stack:
            self._switch(self.nav_stack.pop(), push=False)

    def action_toggle_sidebar(self) -> None:
        left = self.query_one("#left")
        left.display = not left.display
        if not left.display and self._sb.has_focus:
            self._tl.focus()

    def action_toggle_detail(self) -> None:
        pane = self.query_one("#detail")
        pane.display = not pane.display

    def action_toggle_theme(self) -> None:
        """Flip night ⇄ terminal (transparent) and remember it."""
        self.theme_name = "terminal" if self.theme_name == "night" else "night"
        rows.apply_palette(self.theme_name)
        self.ansi_color = self.theme_name == "terminal"
        self.theme = "ansi-dark" if self.theme_name == "terminal" else "tokyo-night"
        self.refresh_css()
        self._fill_sidebar()
        self.load_view()
        self._update_statusbar()
        config.save_theme(self.theme_name)
        self.notify(f"theme: {self.theme_name}  (saved)")

    def action_help(self) -> None:
        self.push_screen(modals.HelpScreen())

    def action_refresh(self) -> None:
        self.load_view()
        self.refresh_sidebar_data()

    def action_logbook_all(self) -> None:
        if self.view_spec.kind == "logbook":
            self.logbook_days = None
            self.load_view()

    # ---- new / edit

    def _dest_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for p in self.projects:
            area = (p.get("area") or {}).get("title")
            label = f"{p.get('title') or ''}" + (f"  ({area})" if area else "")
            choices.append((label, p["id"]))
        for a in self.areas:
            choices.append((a["title"], a["id"]))
        return choices

    def _default_dest_label(self) -> str:
        spec = self.view_spec
        if spec.kind in ("project", "area"):
            return spec.title
        return ""

    def _default_when(self) -> str:
        return {
            "today": "today", "someday": "someday", "anytime": "anytime",
        }.get(self.view_spec.kind, "")

    def action_new_task(self) -> None:
        self.push_screen(
            modals.EntryScreen(
                dests=self._dest_choices(),
                default_dest=self._default_dest_label(),
                default_when=self._default_when(),
            ),
            self._create_task,
        )

    def action_new_inbox_task(self) -> None:
        self.push_screen(
            modals.EntryScreen(dests=self._dest_choices(), heading="New To-Do → Inbox"),
            self._create_task,
        )

    def _create_task(self, result: dict | None) -> None:
        if not result:
            return
        self.notify(f"added “{result['title']}”")
        self._optimistic_insert(result)
        self._do_create(result)

    def _optimistic_insert(self, r: dict) -> None:
        """Show the new to-do in the current view right away if it belongs."""
        k, when, dest = self.view_spec.kind, r["when"], r["dest"]
        belongs = (
            (k == "inbox" and dest in (None, "inbox") and not when)
            or (k == "today" and when in ("today", "evening"))
            or (k == "someday" and when == "someday")
            or (k in ("project", "area") and dest == self.view_spec.id)
        )
        if not belongs:
            return
        fake: Task = {
            "id": "pending", "_pending": True, "type": "todo",
            "title": r["title"], "status": "incomplete",
            "start": {"bucket": "anytime", "evening": when == "evening",
                      "scheduled_at": None},
            "project": None, "area": None, "heading": None,
            "tags": [{"title": t.strip()} for t in r["tags"].split(",") if t.strip()],
            "dates": {"deadline_at": (r["deadline"] + "T00:00:00+00:00")
                      if r["deadline"] else None},
            "notes": r["notes"] or None, "checklist": [],
            "recurrence": {}, "flags": {}, "indexes": {},
        }
        self._tl.add_option(Option(rows.task_row(fake, show_container=False)))
        self.list_rows.append(ListRow("task", task=fake))

    @work(thread=True)
    def _do_create(self, r: dict) -> None:
        try:
            backend.new_task(
                r["title"],
                dest=r["dest"],
                when=r["when"],
                deadline=r["deadline"],
                notes=r["notes"] or None,
                tags=r["tags"] or None,
            )
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"create failed: {e}", severity="error")
            self.call_from_thread(self.load_view)
            return
        self.call_from_thread(self.load_view)
        self.call_from_thread(self.refresh_sidebar_data)

    def action_edit_task(self) -> None:
        row = self._current_row()
        if not row or row.kind != "task" or not row.task:
            return
        task = row.task
        screen = modals.TaskFormScreen(
            heading="Edit To-Do",
            task=task,
            dests=self._dest_choices(),
            default_dest=rows.container_label(task).split(" › ")[-1]
            if rows.container_label(task) else "",
        )

        idx = self._tl.highlighted

        def _apply(result: dict | None) -> None:
            if not result:
                return
            # instant local feedback; the worker trues everything up after
            task["title"] = result["title"]
            task["notes"] = result["notes"] or None
            task["tags"] = [
                {"title": t.strip()} for t in result["tags"].split(",") if t.strip()
            ]
            if result["deadline"]:
                task.setdefault("dates", {})["deadline_at"] = (
                    result["deadline"] + "T00:00:00+00:00"
                )
            else:
                task.setdefault("dates", {})["deadline_at"] = None
            if idx is not None:
                self._restyle_row(idx, task)
            self.notify("saved")
            self._do_edit(task, result)

        self.push_screen(screen, _apply)

    @work(thread=True)
    def _do_edit(self, task: Task, r: dict) -> None:
        tid = task["id"]
        old_tags = {
            tg.get("title") for tg in (task.get("tags") or []) if isinstance(tg, dict)
        }
        new_tags = {x.strip() for x in r["tags"].split(",") if x.strip()}
        add = ",".join(sorted(new_tags - old_tags)) or None
        remove = ",".join(sorted(old_tags - new_tags)) or None
        try:
            backend.edit_task(
                tid,
                title=r["title"] if r["title"] != task.get("title") else None,
                notes=r["notes"] if r["notes"] != (task.get("notes") or "") else None,
                add_tags=add,
                remove_tags=remove,
            )
            # container change
            cur_crumb = rows.container_label(task)
            if r["dest_label"] and r["dest"] and (
                not cur_crumb or r["dest_label"].lower() not in cur_crumb.lower()
            ):
                backend.edit_task(tid, move=r["dest"])
            elif not r["dest_label"] and task.get("project"):
                pass  # leaving dest blank keeps current container
            # schedule
            start = task.get("start") or {}
            cur_when = None
            if start.get("scheduled_at"):
                cur_when = (backend.parse_dt(start["scheduled_at"]) or None)
                cur_when = cur_when.date().isoformat() if cur_when else None
            elif start.get("evening"):
                cur_when = "evening"
            else:
                cur_when = start.get("bucket")
            dl = backend.parse_dt((task.get("dates") or {}).get("deadline_at"))
            cur_dl = dl.date().isoformat() if dl else None
            kw: dict[str, Any] = {}
            if r["when"] and r["when"] != cur_when:
                kw["when"] = r["when"]
            if r["deadline"] != cur_dl:
                if r["deadline"]:
                    kw["deadline"] = r["deadline"]
                elif cur_dl:
                    kw["clear_deadline"] = True
            if kw:
                backend.schedule(tid, **kw)
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"edit failed: {e}", severity="error")
            self.call_from_thread(self.load_view)
            return
        self.call_from_thread(self.load_view)

    # ---- quick mutations

    def _selected_task(self) -> Task | None:
        row = self._current_row()
        if (
            row and row.kind == "task" and row.task
            and not row.task.get("_pending") and row.task.get("type") != "area"
        ):
            return row.task
        return None

    def action_toggle_done(self) -> None:
        task = self._selected_task()
        if not task:
            return
        prev = task.get("status") or "incomplete"
        new = "incomplete" if task.get("status") == "completed" else "done"
        task["status"] = "completed" if new == "done" else "incomplete"
        self._restyle_current(task)
        verb = "completed" if new == "done" else "reopened"
        self._push_undo_mark(task, prev)
        self.notify(f"{verb} “{task.get('title') or ''}” — ctrl+z to undo")
        self._do_mark([task["id"]], new, task.get("title") or "")

    def action_cancel_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        prev = task.get("status") or "incomplete"
        new = "incomplete" if task.get("status") == "canceled" else "canceled"
        task["status"] = "canceled" if new == "canceled" else "incomplete"
        self._restyle_current(task)
        verb = "canceled" if new == "canceled" else "reopened"
        self._push_undo_mark(task, prev)
        self.notify(f"{verb} “{task.get('title') or ''}” — ctrl+z to undo")
        self._do_mark([task["id"]], new, task.get("title") or "")

    def _push_undo_mark(self, task: Task, prev_status: str) -> None:
        """Remember a status flip so ctrl+z can flip it back.

        Unlike a delete, nothing has to be recreated here — the to-do still
        exists, so undo is just marking it with whatever it was before.
        """
        self.undo_stack.append({
            "type": "mark",
            "id": task["id"],
            "status": prev_status,
            "title": task.get("title") or "",
        })
        del self.undo_stack[:-10]

    def _restyle_current(self, task: Task) -> None:
        if self._tl.highlighted is None:
            return
        self._restyle_row(self._tl.highlighted, task)

    def _remove_row(self, idx: int) -> None:
        """Optimistically drop a row from the visible list."""
        tl = self._tl
        try:
            tl.remove_option_at_index(idx)
        except Exception:
            self.load_view()
            return
        if 0 <= idx < len(self.list_rows):
            del self.list_rows[idx]
        self._update_detail()

    def _restyle_row(self, idx: int, task: Task) -> None:
        show_container = self.view_spec.kind in (
            "inbox", "today", "upcoming", "search", "logbook"
        )
        try:
            self._tl.replace_option_prompt_at_index(
                idx, rows.task_row(task, show_container=show_container)
            )
        except Exception:
            self.load_view()
        self._update_detail()

    def _when_leaves_view(self, when: str) -> bool:
        k = self.view_spec.kind
        if k == "inbox":
            return True
        if k == "today":
            return when not in ("today", "evening")
        if k == "someday":
            return when != "someday"
        if k == "anytime":
            return when == "someday"
        if k == "upcoming":
            return when in ("today", "evening", "anytime", "someday")
        return False

    @staticmethod
    def _apply_when_locally(task: Task, when: str) -> None:
        start = task.setdefault("start", {})
        today_iso = date.today().isoformat() + "T00:00:00+00:00"
        if when in ("today", "evening"):
            start["evening"] = when == "evening"
            start["scheduled_at"] = today_iso
        elif when in ("someday", "anytime"):
            start["evening"] = False
            start["scheduled_at"] = None
            start["bucket"] = when
        else:  # a date
            start["evening"] = False
            start["scheduled_at"] = when + "T00:00:00+00:00"

    def _optimistic_when(self, task: Task, when: str) -> None:
        idx = self._tl.highlighted
        if idx is None:
            return
        if self._when_leaves_view(when):
            self._remove_row(idx)
        else:
            self._apply_when_locally(task, when)
            self._restyle_row(idx, task)

    @work(thread=True)
    def run_backend(self, fn, args: tuple, err: str = "operation") -> None:
        """Generic backend mutation worker; survives modal screens closing."""
        try:
            fn(*args)
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"{err} failed: {e}", severity="error")

    @work(thread=True)
    def _do_mark(self, ids: list[str], status: str, title: str) -> None:
        try:
            backend.mark(ids, status)
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"mark failed: {e}", severity="error")
            self.call_from_thread(self.load_view)
            return

    def action_trash_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        title = task.get("title") or ""
        is_project = task.get("type") == "project"
        self._confirm_delete(task["id"], title, is_project, snapshot=task)

    def _confirm_delete(
        self, tid: str, title: str, is_project: bool, snapshot: Task | None = None
    ) -> None:
        # the CLI has no trash operation — deletion is permanent (skips the
        # Things trash), so always ask; ctrl+z recreates from a snapshot
        what = f"project “{title}” and its to-dos" if is_project else f"“{title}”"

        def _go(confirmed: bool | None) -> None:
            if not confirmed:
                return
            entry: dict | None = None
            if snapshot is not None:
                entry = {
                    "type": "project" if is_project else "task",
                    "item": snapshot,
                    "tasks": None,
                }
                self.undo_stack.append(entry)
                del self.undo_stack[:-10]
            # instant feedback: drop the row/sidebar entry before the CLI runs
            row = self._current_row()
            if row and row.task and row.task.get("id") == tid and                     self._tl.highlighted is not None:
                self._remove_row(self._tl.highlighted)
            suffix = " — ctrl+z to restore" if entry else ""
            self.notify(f"deleted “{title}”{suffix}")
            self._do_delete(tid, title, is_project, entry)

        note = "skips Trash — ctrl+z to undo" if snapshot is not None else "no undo"
        self.push_screen(
            modals.ConfirmScreen(f"Delete {what}?  ({note})"), _go
        )

    @work(thread=True)
    def _do_delete(
        self, tid: str, title: str, is_project: bool, entry: dict | None
    ) -> None:
        try:
            if is_project and entry is not None:
                entry["tasks"] = [
                    t for t in backend.project_tasks(tid) if t.get("id") != tid
                ]
            backend.delete([tid])
        except backend.CliError as e:
            if entry is not None and entry in self.undo_stack:
                self.undo_stack.remove(entry)
            self.call_from_thread(self.notify, f"delete failed: {e}", severity="error")
            self.call_from_thread(self.load_view)
            self.call_from_thread(self.refresh_sidebar_data)
            return
        self.call_from_thread(self.load_view)
        if is_project:
            self.call_from_thread(self.refresh_sidebar_data)

    def action_undo(self) -> None:
        if not self.undo_stack:
            self.notify("nothing to undo")
            return
        self._do_undo(self.undo_stack.pop())

    @staticmethod
    def _restore_kwargs(task: Task) -> dict:
        start = task.get("start") or {}
        when = None
        sched = backend.parse_dt(start.get("scheduled_at"))
        if sched:
            when = "evening" if (
                start.get("evening") and sched.date() == date.today()
            ) else sched.date().isoformat()
        elif start.get("bucket") in ("someday", "anytime"):
            when = start["bucket"]
        dl = backend.parse_dt((task.get("dates") or {}).get("deadline_at"))
        tags = ",".join(
            tg["title"] for tg in (task.get("tags") or [])
            if isinstance(tg, dict) and tg.get("title")
        )
        return {
            "when": when,
            "deadline": dl.date().isoformat() if dl else None,
            "notes": task.get("notes") or None,
            "tags": tags or None,
        }

    def _undo_mark(self, entry: dict) -> None:
        """Put a completed/canceled to-do back the way it was. Runs on the
        _do_undo worker thread, so it talks to the backend directly."""
        try:
            backend.mark([entry["id"]], entry["status"])
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"undo failed: {e}", severity="error")
            return
        verb = "reopened" if entry["status"] == "incomplete" else entry["status"]
        self.call_from_thread(self.notify, f"{verb} “{entry['title']}”")
        self.call_from_thread(self.load_view)
        self.call_from_thread(self.refresh_sidebar_data)

    @work(thread=True)
    def _do_undo(self, entry: dict) -> None:
        if entry["type"] == "mark":
            self._undo_mark(entry)
            return
        item = entry["item"]
        title = item.get("title") or "(untitled)"
        try:
            if entry["type"] == "task":
                dest = None
                if item.get("project"):
                    dest = item["project"]["id"]
                elif item.get("area"):
                    dest = item["area"]["id"]
                elif (item.get("start") or {}).get("bucket") == "inbox":
                    dest = "inbox"
                kw = self._restore_kwargs(item)
                checklist = [
                    c.get("title") for c in (item.get("checklist") or [])
                    if c.get("title")
                ]
                backend.new_task(title, dest=dest, checklist=checklist or None, **kw)
            else:  # project
                kw = self._restore_kwargs(item)
                area = (item.get("area") or {}).get("id")
                pid = backend.new_project(title, area=area, **kw)
                restored = 0
                if pid:
                    for t in entry.get("tasks") or []:
                        if t.get("status") in ("completed", "canceled"):
                            continue  # keep the restore to live to-dos
                        tkw = self._restore_kwargs(t)
                        checklist = [
                            c.get("title") for c in (t.get("checklist") or [])
                            if c.get("title")
                        ]
                        backend.new_task(
                            t.get("title") or "(untitled)",
                            dest=pid,
                            checklist=checklist or None,
                            **tkw,
                        )
                        restored += 1
                self.call_from_thread(
                    self.notify,
                    f"restored “{title}” with {restored} to-dos"
                    " (headings/logbook entries aren't recoverable)",
                )
                self.call_from_thread(self.load_view)
                self.call_from_thread(self.refresh_sidebar_data)
                return
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"undo failed: {e}", severity="error")
            return
        self.call_from_thread(self.notify, f"restored “{title}”")
        self.call_from_thread(self.load_view)
        self.call_from_thread(self.refresh_sidebar_data)

    def action_delete_sidebar_item(self) -> None:
        sb = self._sb
        if sb.highlighted is None or not sb.option_count:
            return
        key = sb.get_option_at_index(sb.highlighted).id or ""
        kind, _, ident = key.partition(":")
        if kind == "project":
            p = next((p for p in self.projects if p["id"] == ident), None)
            if p:
                self._confirm_delete(ident, p.get("title") or "project", True, snapshot=p)
        elif kind == "area":
            a = next((a for a in self.areas if a["id"] == ident), None)
            if a:
                self._confirm_delete(ident, a.get("title") or "area", True)

    def action_schedule_task(self) -> None:
        task = self._selected_task()
        if not task:
            return

        def _apply(when: str | None) -> None:
            if when:
                self._optimistic_when(task, when)
                self.notify(f"“{task.get('title') or ''}” → {when}")
                self._do_schedule(task["id"], task.get("title") or "", when=when)

        self.push_screen(modals.WhenScreen(), _apply)

    def action_deadline_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        dl = backend.parse_dt((task.get("dates") or {}).get("deadline_at"))

        def _apply(result: dict | None) -> None:
            if not result:
                return
            idx = self._tl.highlighted
            if result.get("clear"):
                task.setdefault("dates", {})["deadline_at"] = None
                if idx is not None:
                    self._restyle_row(idx, task)
                self.notify("deadline cleared")
                self._do_schedule(task["id"], task.get("title") or "", clear_deadline=True)
            else:
                task.setdefault("dates", {})["deadline_at"] = (
                    result["deadline"] + "T00:00:00+00:00"
                )
                if idx is not None:
                    self._restyle_row(idx, task)
                self.notify(f"deadline {result['deadline']}")
                self._do_schedule(
                    task["id"], task.get("title") or "", deadline=result["deadline"]
                )

        self.push_screen(
            modals.DeadlineScreen(dl.date().isoformat() if dl else ""), _apply
        )

    @work(thread=True)
    def _do_schedule(self, tid: str, title: str, **kw: Any) -> None:
        try:
            backend.schedule(tid, **kw)
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"schedule failed: {e}", severity="error")
            self.call_from_thread(self.load_view)
            return
        self.call_from_thread(self.load_view)
        self.call_from_thread(self.refresh_sidebar_data)

    def action_move_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        choices: list[tuple[Text | str, str]] = [("  Inbox", "inbox")]
        for label, val in self._dest_choices():
            choices.append((f"  {label}", val))

        def _apply(dest: str | None) -> None:
            if not dest:
                return
            idx = self._tl.highlighted
            k = self.view_spec.kind
            leaves = (
                (k in ("project", "area") and dest != self.view_spec.id)
                or (k == "inbox" and dest != "inbox")
            )
            if idx is not None:
                if leaves:
                    self._remove_row(idx)
                else:
                    if dest == "inbox":
                        task["project"] = None
                        task["area"] = None
                    else:
                        proj = next((p for p in self.projects if p["id"] == dest), None)
                        area = next((a for a in self.areas if a["id"] == dest), None)
                        if proj:
                            task["project"] = {"id": dest, "title": proj.get("title")}
                        elif area:
                            task["project"] = None
                            task["area"] = {"id": dest, "title": area.get("title")}
                    self._restyle_row(idx, task)
            self.notify(f"moved “{task.get('title') or ''}”")
            self._do_move(task["id"], task.get("title") or "", dest)

        self.push_screen(modals.PickerScreen("Move to…", choices), _apply)

    @work(thread=True)
    def _do_move(self, tid: str, title: str, dest: str) -> None:
        try:
            backend.edit_task(tid, move=dest)
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"move failed: {e}", severity="error")
            self.call_from_thread(self.load_view)
            return
        self.call_from_thread(self.load_view)

    # ---- search / goto

    def _matching_lists(self, low: str) -> list[tuple[str, str, str]]:
        """(kind, id, title) for projects/areas whose title contains `low`."""
        hits: list[tuple[str, str, str]] = []
        for a in self.areas:
            if low in (a.get("title") or "").lower():
                hits.append(("area", a["id"], a.get("title") or ""))
        for p in self.projects:
            if low in (p.get("title") or "").lower():
                hits.append(("project", p["id"], p.get("title") or ""))
        return hits

    def _list_choices(self) -> list[tuple[Text | str, str]]:
        """Every jumpable list: views, areas, projects (used by search)."""
        choices: list[tuple[Text | str, str]] = []
        for name, label, color in BUILTIN_VIEWS:
            icon, _, text = label.partition("  ")
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append(f"{icon}  ", style=color)
            t.append(text, style=rows.FG)
            choices.append((t, f"builtin:{name}"))
        for a in self.areas:
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("\uf1b2  ", style=rows.GREEN)
            t.append(a["title"], style=rows.FG)
            choices.append((t, f"area:{a['id']}"))
        for p in self.projects:
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("\uf200  ", style=rows.ACCENT)
            t.append(p.get("title") or "(untitled)", style=rows.FG)
            area = (p.get("area") or {}).get("title")
            if area:
                t.append(f"  {area}", style=rows.DIM)
            choices.append((t, f"project:{p['id']}"))
        return choices

    def action_search(self) -> None:
        def _go(key: str | None) -> None:
            if not key:
                return
            if key.startswith("search:"):
                q = key[len("search:"):]
                self._switch(ViewSpec("search", title=f"“{q}”", query=q))
            else:
                self._open_key(key)

        self.push_screen(modals.SearchScreen(self._list_choices()), _go)

    def _focused_list(self) -> OptionList:
        return self.focused if isinstance(self.focused, OptionList) else self._tl

    def action_list_bottom(self) -> None:
        self._focused_list().action_last()

    def action_chord(self) -> None:
        def _fire(key: str | None) -> None:
            if key is None:
                return
            if key == "g":
                ol = self._focused_list()
                ol.action_first()
                # skip a leading disabled header row
                if ol is self._tl and self.list_rows and self.list_rows[0].kind == "header" \
                        and ol.option_count > 1:
                    ol.highlighted = 1
            elif key in self.CHORDS:
                self._open_key(self.CHORDS[key])

        self.push_screen(modals.ChordScreen(), _fire)

    def action_when_shortcut(self, when: str) -> None:
        task = self._selected_task()
        if not task:
            return
        self._optimistic_when(task, when)
        self.notify(f"“{task.get('title') or ''}” → {when}")
        self._do_schedule(task["id"], task.get("title") or "", when=when)


    # ---- sync

    def action_sync(self) -> None:
        if self._syncing:
            return
        self._sync_worker()

    @work(thread=True, exclusive=True, group="sync")
    def _sync_worker(self) -> None:
        self._syncing = True
        self.call_from_thread(self._update_statusbar)
        log = backend.STATE_CACHE.parent / "things.log"

        def size() -> int:
            try:
                return log.stat().st_size
            except OSError:
                return -1

        try:
            before = size()
            backend.sync_now()
            after = size()
        except backend.CliError as e:
            self.call_from_thread(self.notify, f"sync failed: {e}", severity="error")
            self.call_from_thread(self._update_statusbar)
            return
        finally:
            self._syncing = False
        first = self._last_sync is None
        self._last_sync = time.monotonic()
        # The append-log only grows when the cloud actually handed us new
        # data, so its size is a reliable "was there anything to fetch?".
        pulled = after != before
        if first or pulled:
            self.call_from_thread(self.load_view)
            self.call_from_thread(self.refresh_sidebar_data)
        self.call_from_thread(self._update_statusbar)
        # A sync usually finishes in well under a second, so the "⟳ syncing…"
        # indicator flashes past and it looks like nothing happened. Say what
        # it found — "nothing new" is an answer, silence isn't.
        self.call_from_thread(
            self.notify,
            "synced — new changes pulled in" if pulled else "synced — nothing new",
        )

    def _update_statusbar(self) -> None:
        bar = self._statusbar
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append("  space", style=rows.ACCENT)
        t.append(" done  ", style=rows.DIM)
        t.append("n", style=rows.ACCENT)
        t.append(" new  ", style=rows.DIM)
        t.append("e", style=rows.ACCENT)
        t.append(" edit  ", style=rows.DIM)
        t.append("s", style=rows.ACCENT)
        t.append(" when  ", style=rows.DIM)
        t.append("m", style=rows.ACCENT)
        t.append(" move  ", style=rows.DIM)
        t.append("/", style=rows.ACCENT)
        t.append(" search  ", style=rows.DIM)
        t.append("?", style=rows.ACCENT)
        t.append(" help", style=rows.DIM)
        t.append("   ·   ", style=rows.DIM)
        if self._syncing:
            t.append("⟳ syncing…", style=rows.YELLOW)
        elif self._last_sync is None:
            t.append("synced: not yet (S)", style=rows.DIM)
        else:
            ago = int(time.monotonic() - self._last_sync)
            mins, secs = divmod(ago, 60)
            label = f"{mins}m ago" if mins else f"{secs}s ago"
            t.append(f"✓ synced {label}", style=rows.GREEN)
        bar.update(t)


def run(theme_name: str | None = None) -> None:
    ThingsApp(theme_name).run()
