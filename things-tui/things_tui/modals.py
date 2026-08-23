"""Modal screens: new/edit task, when picker, deadline, move, detail, help."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from . import backend, entry, rows

Task = dict[str, Any]


class FormScreen(ModalScreen):
    """Base: esc cancels, ctrl+s submits."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Save"),
    ]

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        pass


class TaskFormScreen(FormScreen):
    """Shared form for new + edit task."""

    def __init__(
        self,
        *,
        heading: str,
        task: Task | None = None,
        dests: list[tuple[str, str]] | None = None,
        default_dest: str = "",
        default_when: str = "",
    ) -> None:
        super().__init__()
        self._heading = heading
        self._task_data = task
        self._dests = dests or []
        self._default_dest = default_dest
        self._default_when = default_when

    def compose(self) -> ComposeResult:
        t = self._task_data or {}
        dates = t.get("dates") or {}
        start = t.get("start") or {}
        when0 = self._default_when
        if t:
            bucket = start.get("bucket") or ""
            sched = backend.parse_dt(start.get("scheduled_at"))
            if sched:
                when0 = sched.date().isoformat()
            elif start.get("evening"):
                when0 = "evening"
            elif bucket:
                when0 = bucket
        dl = backend.parse_dt(dates.get("deadline_at"))
        tags0 = ", ".join(
            tg.get("title", "") for tg in (t.get("tags") or []) if isinstance(tg, dict)
        )
        with Vertical(id="form"):
            yield Label(self._heading, id="form-title")
            yield Label("Title")
            yield Input(value=t.get("title") or "", placeholder="New To-Do", id="f-title")
            yield Label("Notes")
            yield TextArea(t.get("notes") or "", id="f-notes", tab_behavior="focus")
            with Horizontal(id="form-row"):
                with Vertical():
                    yield Label("When  (today / evening / someday / anytime / tomorrow / YYYY-MM-DD)")
                    yield Input(value=when0, placeholder="anytime", id="f-when")
                with Vertical():
                    yield Label("Deadline  (YYYY-MM-DD, empty = none)")
                    yield Input(
                        value=dl.date().isoformat() if dl else "",
                        placeholder="", id="f-deadline",
                    )
            with Horizontal(id="form-row2"):
                with Vertical():
                    yield Label("List  (inbox, or project/area name)")
                    yield Input(value=self._default_dest, placeholder="inbox", id="f-dest")
                with Vertical():
                    yield Label("Tags  (comma separated)")
                    yield Input(value=tags0, placeholder="", id="f-tags")
            yield Static("[dim]tab next field · ctrl+s save · esc cancel[/]", id="form-hint")

    def on_mount(self) -> None:
        self.query_one("#f-title", Input).focus()

    @on(Input.Submitted)
    def _submit_on_enter(self, event: Input.Submitted) -> None:
        self.action_submit()

    def action_submit(self) -> None:
        title = self.query_one("#f-title", Input).value.strip()
        if not title:
            self.app.notify("Title is required", severity="warning")
            return
        when_raw = self.query_one("#f-when", Input).value.strip()
        when = backend.parse_when(when_raw) if when_raw else None
        if when_raw and when is None:
            self.app.notify(f"Can't parse when: {when_raw!r}", severity="warning")
            return
        deadline = self.query_one("#f-deadline", Input).value.strip()
        dest_raw = self.query_one("#f-dest", Input).value.strip()
        dest = resolve_dest(dest_raw, self._dests)
        if dest_raw and dest is None:
            self.app.notify(f"No list matches {dest_raw!r}", severity="warning")
            return
        self.dismiss(
            {
                "title": title,
                "notes": self.query_one("#f-notes", TextArea).text,
                "when": when,
                "deadline": deadline or None,
                "dest": dest,
                "dest_label": dest_raw,
                "tags": self.query_one("#f-tags", Input).value.strip(),
            }
        )


def resolve_dest(text: str, dests: list[tuple[str, str]]) -> str | None:
    """Map a typed list name to a destination id. dests = [(label, id)]."""
    text = text.strip()
    if not text or text.lower() == "inbox":
        return "inbox" if text else None
    low = text.lower()
    exact = [d for label, d in dests if label.lower() == low]
    if exact:
        return exact[0]
    sub = [d for label, d in dests if low in label.lower()]
    return sub[0] if len(sub) >= 1 else None


class WhenScreen(ModalScreen):
    """Quick schedule picker."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    OPTIONS = [
        ("  Today", "today"),
        ("  This Evening", "evening"),
        ("  Tomorrow", "tomorrow"),
        ("  Anytime", "anytime"),
        ("  Someday", "someday"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="when-box"):
            yield Label("When?", id="form-title")
            yield OptionList(
                *[Option(label, id=val) for label, val in self.OPTIONS],
                id="when-list",
            )
            yield Input(placeholder="or type a date: saturday / next fri / aug 24", id="when-date")
            yield Static("", id="when-preview")

    def on_mount(self) -> None:
        self.query_one("#when-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        val = event.option.id
        self.dismiss(backend.parse_when(val) if val else None)

    @on(Input.Changed)
    def _preview(self, event: Input.Changed) -> None:
        """Echo what the typed text resolves to, so an ambiguous phrase
        never silently schedules the wrong day."""
        text = event.value.strip()
        t = Text(no_wrap=True, overflow="ellipsis")
        if text:
            when = backend.parse_when(text)
            if when:
                t.append("→ ", style=rows.DIM)
                t.append(entry._pretty_when(when), style=f"bold {rows.ACCENT}")
            else:
                t.append("? ", style=rows.DIM)
                t.append("no date there", style=rows.RED)
        self.query_one("#when-preview", Static).update(t)

    @on(Input.Submitted)
    def _typed(self, event: Input.Submitted) -> None:
        when = backend.parse_when(event.value)
        if when is None:
            self.app.notify(f"Can't parse {event.value!r}", severity="warning")
            return
        self.dismiss(when)


class DeadlineScreen(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="when-box"):
            yield Label("Deadline  (empty clears)", id="form-title")
            yield Input(value=self._current, placeholder="YYYY-MM-DD / tomorrow", id="dl-input")

    def on_mount(self) -> None:
        self.query_one("#dl-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _typed(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.dismiss({"clear": True})
            return
        when = backend.parse_when(text)
        if when is None or when in ("anytime", "someday", "evening"):
            self.app.notify(f"Can't parse date {text!r}", severity="warning")
            return
        if when == "today":
            from datetime import date
            when = date.today().isoformat()
        self.dismiss({"deadline": when})


class PickerScreen(ModalScreen):
    """Filterable picker over (label, id) pairs. Returns the chosen id."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "focus_list", "List", show=False),
    ]

    def __init__(self, heading: str, choices: list[tuple[Text | str, str]]) -> None:
        super().__init__()
        self._heading = heading
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label(self._heading, id="form-title")
            yield Input(placeholder="type to filter…", id="picker-filter")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        self._refill("")
        self.query_one("#picker-filter", Input).focus()

    def _refill(self, needle: str) -> None:
        ol = self.query_one("#picker-list", OptionList)
        ol.clear_options()
        low = needle.lower()
        opts = []
        for label, val in self._choices:
            plain = label.plain if isinstance(label, Text) else str(label)
            if low in plain.lower():
                opts.append(Option(label, id=val))
        ol.add_options(opts)
        if opts:
            ol.highlighted = 0

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        self._refill(event.value)

    @on(Input.Submitted)
    def _pick_first(self, event: Input.Submitted) -> None:
        ol = self.query_one("#picker-list", OptionList)
        if ol.option_count:
            idx = ol.highlighted if ol.highlighted is not None else 0
            self.dismiss(ol.get_option_at_index(idx).id)

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_focus_list(self) -> None:
        self.query_one("#picker-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class EntryScreen(ModalScreen):
    """One-line new to-do with live preview of the parsed fields.
    Returns the dict expected by App._create_task, or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, dests: list[tuple[str, str]], default_dest: str = "",
                 default_when: str = "", heading: str = "New To-Do") -> None:
        super().__init__()
        self._dests = dests
        self._default_dest = default_dest      # label of the current project/area
        self._default_when = default_when
        self._heading = heading

    def compose(self) -> ComposeResult:
        with Vertical(id="entry-box"):
            yield Label(self._heading, id="form-title")
            with Horizontal(id="entry-row"):
                yield Static("＋", id="entry-plus")
                yield Input(placeholder="New To-Do…", id="entry-input")
            yield Static("", id="entry-preview")
            yield Static(
                "@today · @tonight · @tomorrow · @fri · @next fri · @aug 24 · @08-24-26 · @someday   "
                "--due <date>   #project   +tag   \"notes\"",
                id="entry-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#entry-input", Input).focus()
        self._preview("")

    def _resolve(self, name: str | None) -> tuple[str | None, str | None]:
        """(dest id, dest label) for a typed #list, or (None, None)."""
        if not name:
            return None, None
        if name.lower() == "inbox":
            return "inbox", "Inbox"
        low = name.lower()
        exact = [(l, d) for l, d in self._dests if l.lower() == low]
        hits = exact or [(l, d) for l, d in self._dests if low in l.lower()]
        if hits:
            label, ident = hits[0]
            return ident, label.split("  (")[0]
        return None, None

    def _preview(self, text: str) -> None:
        parsed = entry.parse_entry(text)
        _, label = self._resolve(parsed["dest"])
        pairs = entry.describe(
            parsed, dest_label=label,
            default_when=self._default_when or None,
            default_dest=self._default_dest or ("Inbox" if not self._default_when else None),
        )
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(parsed["title"] or " ", style=f"bold {rows.FG}")
        for k, v in pairs:
            t.append("   ")
            t.append(f"{k} ", style=rows.DIM)
            bad = k == "in" and v.endswith("?")
            t.append(v, style=rows.RED if bad else rows.ACCENT)
        self.query_one("#entry-preview", Static).update(t)

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        self._preview(event.value)

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        parsed = entry.parse_entry(event.value)
        if not parsed["title"]:
            self.app.notify("Give it a title", severity="warning")
            return
        dest_id, dest_label = self._resolve(parsed["dest"])
        if parsed["dest"] and dest_id is None:
            self.app.notify(f"No list matches #{parsed['dest']}", severity="warning")
            return
        if dest_id is None and self._default_dest:
            dest_id, dest_label = self._resolve(self._default_dest)
        when = parsed["when"] or (self._default_when or None)
        self.dismiss({
            "title": parsed["title"],
            "notes": parsed["notes"] or "",
            "when": when,
            "deadline": parsed["deadline"] or None,
            "dest": dest_id,
            "dest_label": dest_label or "",
            "tags": parsed["tags"] or "",
        })

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchScreen(ModalScreen):
    """`/`: jump to a list (views, areas, projects filtered live) or search
    to-dos. Returns "builtin:x" | "area:id" | "project:id" | "search:<query>"."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "focus_list", show=False),
    ]

    def __init__(self, lists: list[tuple[Text | str, str]]) -> None:
        super().__init__()
        self._lists = lists

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label("Search", id="form-title")
            yield Input(placeholder="a to-do, or a list: today, inbox, a project…", id="picker-filter")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        self._refill("")
        self.query_one("#picker-filter", Input).focus()

    def _refill(self, needle: str) -> None:
        ol = self.query_one("#picker-list", OptionList)
        ol.clear_options()
        low = needle.strip().lower()
        opts = []
        for label, val in self._lists:
            plain = label.plain if isinstance(label, Text) else str(label)
            if low in plain.lower():
                opts.append(Option(label, id=val))
        if low:
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("  ", style=rows.DIM)
            t.append("search to-dos for ", style=rows.DIM)
            t.append(f"“{needle.strip()}”", style=rows.FG)
            opts.append(Option(t, id=f"search:{needle.strip()}"))
        ol.add_options(opts)
        if opts:
            ol.highlighted = 0

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        self._refill(event.value)

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        ol = self.query_one("#picker-list", OptionList)
        if ol.option_count:
            idx = ol.highlighted if ol.highlighted is not None else 0
            self.dismiss(ol.get_option_at_index(idx).id)
        elif event.value.strip():
            self.dismiss(f"search:{event.value.strip()}")

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_focus_list(self) -> None:
        self.query_one("#picker-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "no", "No"),
        Binding("n", "no", "No"),
        Binding("y", "yes", "Yes"),
        Binding("enter", "yes", "Yes", priority=True),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="when-box"):
            yield Label(self._message, id="form-title")
            yield Static("[dim]y / enter confirm · n / esc cancel[/]")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class TaskDetailScreen(ModalScreen):
    """Full task view: notes + checklist with toggling."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("space", "toggle_item", "Toggle item"),
        Binding("a", "add_item", "Add item"),
        Binding("e", "edit_task", "Edit"),
        Binding("m", "hand_off('move')", "Move"),
        Binding("s", "hand_off('schedule')", "When"),
        Binding("d", "hand_off('deadline')", "Deadline"),
        Binding("backspace", "hand_off('delete')", show=False),
        Binding("ctrl+k", "complete_task", "Complete"),
        Binding("ctrl+t", "quick_when('today')", show=False),
        Binding("ctrl+e", "quick_when('evening')", show=False),
        Binding("ctrl+s", "quick_when('someday')", show=False),
        Binding("ctrl+r", "quick_when('anytime')", show=False),
    ]

    def __init__(self, task: Task) -> None:
        super().__init__()
        self.task_data = task
        self.changed = False

    def compose(self) -> ComposeResult:
        t = self.task_data
        with Vertical(id="detail-box"):
            head = Text()
            head.append_text(rows.checkbox(t))
            head.append(t.get("title") or "(untitled)", style="bold")
            yield Static(head, id="detail-title")
            meta = Text()
            crumb = rows.container_label(t)
            if crumb:
                meta.append(f"{crumb}", style=rows.DIM)
            start = t.get("start") or {}
            bucket = start.get("bucket") or ""
            sched = backend.parse_dt(start.get("scheduled_at"))
            when_label = ""
            if sched:
                when_label = rows.date_label(start.get("scheduled_at"))
                if start.get("evening"):
                    when_label += " "
            elif bucket:
                when_label = bucket
            if when_label:
                meta.append(("   " if meta.plain else "") + f"when: {when_label}", style=rows.MOON)
            frag = rows.deadline_frag(t)
            if frag:
                meta.append("   ")
                meta.append_text(frag)
            for tag in t.get("tags") or []:
                title_ = tag.get("title") if isinstance(tag, dict) else ""
                if title_:
                    meta.append(f"   #{title_}", style=rows.PURPLE)
            rec = t.get("recurrence") or {}
            if rec.get("rule") or rec.get("is_instance") or rec.get("is_template"):
                meta.append("    repeats", style=rows.PURPLE)
            if meta.plain:
                yield Static(meta, id="detail-meta")
            with VerticalScroll(id="detail-notes-scroll"):
                notes = t.get("notes") or ""
                if notes:
                    yield Static(Text(notes, style=rows.FG), id="detail-notes")
                else:
                    yield Static(Text("no notes", style=rows.DIM), id="detail-notes")
            yield OptionList(id="detail-checklist")
            yield Static(
                "[dim]space toggle item · a add item · e edit · m move · "
                "s when · d deadline · ctrl+k done · "
                "ctrl+t/e/s/r today/evening/someday/anytime · esc close[/]",
                id="form-hint",
            )

    def on_mount(self) -> None:
        self._fill_checklist()
        ol = self.query_one("#detail-checklist", OptionList)
        if self.task_data.get("checklist"):
            ol.focus()

    def _fill_checklist(self) -> None:
        ol = self.query_one("#detail-checklist", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        items = self.task_data.get("checklist") or []
        opts = []
        for item in items:
            txt = Text(no_wrap=True, overflow="ellipsis")
            done = item.get("status") == "completed"
            txt.append(" " if done else " ", style=rows.ACCENT if done else rows.DIM)
            txt.append(
                item.get("title") or "",
                style=f"strike {rows.DIM}" if done else rows.FG,
            )
            opts.append(Option(txt, id=item["short_id"]))
        ol.add_options(opts)
        ol.display = bool(opts)
        if opts:
            idx = keep if keep is not None else 0
            ol.highlighted = min(idx, len(opts) - 1)

    def action_close(self) -> None:
        self.dismiss({"changed": self.changed})

    def action_edit_task(self) -> None:
        self.dismiss({"changed": self.changed, "edit": True})

    def action_hand_off(self, what: str) -> None:
        # close and let the app open the matching picker for this task
        self.dismiss({"changed": self.changed, what: True})

    def action_quick_when(self, when: str) -> None:
        # Mac Things shortcuts: ⌘T today, ⌘E evening, ⌘S someday, ⌘R anytime
        self.app._do_schedule(
            self.task_data["id"], self.task_data.get("title") or "", when=when
        )
        self.dismiss({"changed": True})

    def action_complete_task(self) -> None:
        self.task_data["status"] = "completed"
        # run on the app so the worker survives this screen being dismissed
        self.app._do_mark(
            [self.task_data["id"]], "done", self.task_data.get("title") or ""
        )
        self.dismiss({"changed": True})

    @on(OptionList.OptionSelected, "#detail-checklist")
    def _item_selected(self, event: OptionList.OptionSelected) -> None:
        # mouse click (or enter) on a checklist item toggles it
        self.action_toggle_item()

    def action_toggle_item(self) -> None:
        ol = self.query_one("#detail-checklist", OptionList)
        if ol.highlighted is None or not ol.option_count:
            return
        short_id = ol.get_option_at_index(ol.highlighted).id
        items = self.task_data.get("checklist") or []
        item = next((i for i in items if i["short_id"] == short_id), None)
        if not item:
            return
        newly_checked = item.get("status") != "completed"
        item["status"] = "completed" if newly_checked else "incomplete"
        self._fill_checklist()
        self.changed = True
        self.app.run_backend(
            backend.check_item, (self.task_data["id"], short_id, newly_checked),
            err="checklist",
        )

    def action_add_item(self) -> None:
        def _added(title: str | None) -> None:
            if not title:
                return
            self.changed = True
            self.task_data.setdefault("checklist", []).append(
                {"short_id": "…", "title": title, "status": "incomplete"}
            )
            self._fill_checklist()
            self.app.run_backend(
                lambda tid, t: backend.edit_task(tid, add_checklist=[t]),
                (self.task_data["id"], title), err="checklist",
            )

        self.app.push_screen(TextPromptScreen("Add checklist item"), _added)


class TextPromptScreen(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, heading: str, value: str = "") -> None:
        super().__init__()
        self._heading = heading
        self._value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="when-box"):
            yield Label(self._heading, id="form-title")
            yield Input(value=self._value, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _done(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class ChordScreen(ModalScreen):
    """Invisible key-capture for the `g` prefix chord (g i → Inbox, …)."""

    def compose(self) -> ComposeResult:
        yield Static(
            " g ›  g top · i inbox · t today · u upcoming · a anytime · "
            "s someday · l logbook · x trash   [dim]esc cancel[/]",
            id="chord-hint",
        )

    def on_mount(self) -> None:
        self._done = False
        self.set_timer(3.0, self._expire)

    def _expire(self) -> None:
        # dismiss() pops whatever screen is on top, so never fire it twice
        if not self._done and self.app.screen is self:
            self._done = True
            self.dismiss(None)

    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        if self._done:
            return
        self._done = True
        self.dismiss(None if event.key == "escape" else event.key)


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
    ]

    TEXT = """\
[bold #4fa8ff]things · keyboard shortcuts[/]

[bold #f7ce45]navigate[/]
  j / k  ↑ / ↓      move                 h / l · tab      sidebar ⇄ list
  g g / G           top / bottom         enter            open selected
  esc               back / dismiss
  1 – 7             inbox · today · upcoming · anytime · someday · logbook · trash
  / · ctrl+f        search: lists (views, areas, projects) filter as you type — enter
                    jumps to one; the last row searches your to-dos for the text
  mouse             click select · double-click open · click the circle to complete

[bold #f7ce45]g — prefix[/]  (press g, then:)
  g g   top         g i   inbox          g t   today      g u   upcoming
  g a   anytime     g s   someday        g l   logbook    g x   trash

[bold #f7ce45]tasks[/]
  space · ctrl+k    complete             c · ctrl+alt+k   cancel
  n · ctrl+n        new to-do (one line) N                new straight to inbox
                    tokens: @today @tonight @tomorrow @fri @aug 24 @08-24-26 @someday
                            --due <date>   #project   +tag   "notes"
  e                 edit                 m                move to list
  s                 schedule (picker)    d                deadline
  backspace         delete (asks first)  ctrl+z           undo a delete
  backspace [dim]in sidebar[/]   delete the selected project or area

[bold #f7ce45]schedule directly[/]  (no picker)
  ctrl+t            → Today              ctrl+e           → This Evening
  ctrl+r            → Anytime            ctrl+s           → Someday

[bold #f7ce45]open task view[/]
  space             toggle checklist item     a       add checklist item
  ctrl+k            complete task & close     e       edit
  click item        toggle it                 esc     close

[bold #f7ce45]forms[/]
  ctrl+s            save                 esc              cancel

[bold #f7ce45]app[/]
  S · ctrl+y        sync now (auto every 30s)             r       refresh view
  T                 toggle theme: night ⇄ terminal (transparent, uses your terminal colors)
  ctrl+b · D        hide/show the sidebar · the detail pane
  a                 logbook: load full history            q       quit
  ? · f1            this page\
"""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(self.TEXT)

    def action_close(self) -> None:
        self.dismiss(None)
