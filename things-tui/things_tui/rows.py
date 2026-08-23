"""Row building: turn CLI task dicts into Rich renderables for the list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from rich.text import Text

from . import backend

Task = dict[str, Any]

# palettes: "night" is the built-in dark look; "terminal" uses the 16 ANSI
# colors so everything follows the terminal's own theme (and transparency).
PALETTES = {
    "night": dict(
        ACCENT="#4fa8ff", YELLOW="#f7ce45", RED="#ff6b6b", GREEN="#69d26e",
        PURPLE="#b98bf2", DIM="#6b7482", FG="#d8dee8", MOON="#8fb8e8",
        MARK_STYLE="bold #4fa8ff",
    ),
    # cursor mark in the terminal's own foreground colour: the palette's blue
    # can be too dark against a black background
    "terminal": dict(
        ACCENT="blue", YELLOW="yellow", RED="red", GREEN="green",
        PURPLE="magenta", DIM="bright_black", FG="default", MOON="cyan",
        MARK_STYLE="bold bright_white",
    ),
}
ACCENT = YELLOW = RED = GREEN = PURPLE = DIM = FG = MOON = MARK_STYLE = ""


def apply_palette(name: str) -> None:
    globals().update(PALETTES.get(name, PALETTES["night"]))


apply_palette("night")


@dataclass
class Row:
    kind: str  # "task" | "project" | "trash"
    task: Task


def _fmt_day(d: date, *, with_year: bool = False) -> str:
    today = date.today()
    if d == today:
        return "Today"
    delta = (d - today).days
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    fmt = "%a %b %-d" if not with_year and d.year == today.year else "%a %b %-d %Y"
    return d.strftime(fmt)


def date_label(iso: str | None, *, with_year: bool = False) -> str:
    d = backend.local_date(iso)
    if not d:
        return ""
    return _fmt_day(d, with_year=with_year)


def deadline_frag(task: Task) -> Text | None:
    d = backend.local_date(task["dates"].get("deadline_at"))
    if not d:
        return None
    days = (d - date.today()).days
    if days < 0:
        return Text(f" {abs(days)}d overdue", style=f"bold {RED}")
    if days == 0:
        return Text(" today", style=f"bold {RED}")
    if days == 1:
        return Text(" tomorrow", style=YELLOW)
    return Text(f" {_fmt_day(d)}", style=DIM)


def checkbox(task: Task) -> Text:
    status = task.get("status")
    if status == "completed":
        return Text(" ", style=ACCENT)
    if status == "canceled":
        return Text(" ", style=DIM)
    return Text(" ", style=DIM)


MARK = "▌"


def task_row(task: Task, *, show_container: bool = True, indent: int = 0) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(" ")  # cursor gutter: swapped for MARK on the highlighted row
    t.append(" " * indent)

    if task.get("type") == "project":
        t.append(" ", style=ACCENT)
    elif task.get("type") == "area":
        t.append(" ", style=GREEN)
    else:
        t.append_text(checkbox(task))

    title = task.get("title") or "(untitled)"
    done = task.get("status") in ("completed", "canceled")
    t.append(title, style=f"strike {DIM}" if done else FG)

    start = task.get("start") or {}
    if start.get("evening"):
        t.append("  ", style=MOON)

    rec = task.get("recurrence") or {}
    if rec.get("is_instance") or rec.get("is_template") or rec.get("rule"):
        t.append("  ", style=PURPLE)

    checklist = task.get("checklist") or []
    if checklist:
        ndone = sum(1 for c in checklist if c.get("status") == "completed")
        t.append(f"   {ndone}/{len(checklist)}", style=DIM)

    if task.get("notes"):
        t.append("  ", style=DIM)

    frag = deadline_frag(task)
    if frag:
        t.append("  ")
        t.append_text(frag)

    for tag in task.get("tags") or []:
        title_ = tag.get("title") if isinstance(tag, dict) else str(tag)
        if title_:
            t.append(f"  #{title_}", style=PURPLE)

    if show_container:
        crumb = container_label(task)
        if crumb:
            t.append(f"  ·  {crumb}", style=DIM)
    return t


def container_label(task: Task) -> str:
    proj = task.get("project")
    area = task.get("area")
    heading = task.get("heading")
    parts = []
    if area and area.get("title"):
        parts.append(area["title"])
    if proj and proj.get("title"):
        parts.append(proj["title"])
    if heading and heading.get("title"):
        parts.append(heading["title"])
    return " › ".join(parts)


def header_text(label: str, *, style: str = f"bold {ACCENT}") -> Text:
    return Text(label, style=style)


def logbook_day_key(task: Task) -> str:
    dt = backend.parse_dt(task["dates"].get("completed_at"))
    return dt.date().isoformat() if dt else "unknown"


def trash_row(item: dict) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(" ")
    icon = {0: " ", 1: " ", 2: "¶ "}.get(item.get("item_type", 0), " ")
    t.append(icon, style=DIM)
    t.append(item.get("title") or "(untitled)", style=DIM)
    when = item.get("modified")
    if when:
        d = datetime.fromtimestamp(when).date()
        t.append(f"  ·  {_fmt_day(d, with_year=True)}", style=DIM)
    return t
