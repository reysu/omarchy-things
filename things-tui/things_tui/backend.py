"""Data layer: wraps the `things3` cloud CLI and its local state cache."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import entry

THINGS_BIN = os.environ.get("THINGS3_BIN", "things3")


def _state_cache() -> Path:
    """Locate the CLI's state cache across platforms (XDG or macOS dirs)."""
    candidates = []
    if os.environ.get("XDG_STATE_HOME"):
        candidates.append(Path(os.environ["XDG_STATE_HOME"]) / "things3")
    candidates.append(Path.home() / ".local/state/things3")
    candidates.append(Path.home() / "Library/Application Support/things3")
    for base in candidates:
        p = base / "append-log/state_cache.json"
        if p.exists():
            return p
    return candidates[0] / "append-log/state_cache.json"


STATE_CACHE = _state_cache()

Task = dict[str, Any]


class CliError(RuntimeError):
    pass


# Syncing invocations must not overlap: they race on the CLI's append-log
# state files ("No such file or directory" failures). --no-sync reads are
# safe alongside a running sync (verified: 120 concurrent reads / 6 syncs,
# 0 failures) and must NOT wait on this lock, or a slow network sync would
# freeze every view switch behind it.
_SYNC_LOCK = threading.Lock()


def _run(args: list[str], *, sync: bool, timeout: int = 60) -> str:
    cmd = [THINGS_BIN, "--log-level", "off"]
    if not sync:
        cmd.append("--no-sync")
    cmd += args
    try:
        if sync:
            with _SYNC_LOCK:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout
                )
        else:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
    except FileNotFoundError:
        raise CliError(f"`{THINGS_BIN}` not found on PATH")
    except subprocess.TimeoutExpired:
        raise CliError(f"things3 {' '.join(args)} timed out")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise CliError(msg[-1] if msg else f"things3 exited {proc.returncode}")
    return proc.stdout


def _run_json(args: list[str], *, sync: bool = False) -> Any:
    out = _run(args + ["--json"], sync=sync)
    return json.loads(out) if out.strip() else []


# ---------------------------------------------------------------- views

def view(name: str) -> list[Task]:
    """One of: inbox, today, upcoming, anytime, someday.

    `today` and `upcoming` get corrected on the way out. The CLI splits them by
    comparing the serialized UTC instant against now, so a task stored as a
    floating date (…T00:00:00+00:00) stays in Upcoming until UTC midnight —
    up to a full day after Things itself moved it into Today. We re-split on
    the real local date so both views agree with the Things apps.
    """
    tasks = _run_json([name])
    if name == "today":
        have = {t.get("id") for t in tasks}
        try:
            pending = _run_json(["upcoming"])
        except CliError:
            return tasks
        tasks += [t for t in pending if t.get("id") not in have and _has_arrived(t)]
    elif name == "upcoming":
        tasks = [t for t in tasks if not _has_arrived(t)]
    return tasks


def _has_arrived(task: Task) -> bool:
    """Is this task's scheduled day today or already past, locally?"""
    d = local_date((task.get("start") or {}).get("scheduled_at"))
    return d is not None and d <= date.today()


def logbook(days: int | None = 60) -> list[Task]:
    args = ["logbook"]
    if days is not None:
        since = (date.today() - timedelta(days=days)).isoformat()
        args += ["--from", since]
    return _run_json(args)


def project_tasks(project_id: str) -> list[Task]:
    return _run_json(["project", project_id])


def area_items(area_id: str) -> list[Task]:
    return _run_json(["area", area_id])


def areas() -> list[dict]:
    return _run_json(["areas"])


def projects() -> list[Task]:
    return _run_json(["projects"])


def find(query: str, **flags: bool) -> list[Task]:
    args = ["find"]
    if flags.get("any_status"):
        args.append("--any-status")
    if flags.get("notes"):
        args.append("--notes")
    if query:
        args.append(query)
    return _run_json(args)


def trash() -> list[dict]:
    """Read trashed items straight from the CLI's local state cache."""
    if not STATE_CACHE.exists():
        return []
    try:
        state = json.loads(STATE_CACHE.read_text())["state"]
    except (json.JSONDecodeError, KeyError, OSError):
        return []
    items = []
    for uuid, ent in state.items():
        if not isinstance(ent, dict) or not (ent.get("entity_type") or "").startswith("Task"):
            continue
        props = ent.get("properties", {})
        t = props.get("Task") if isinstance(props, dict) else None
        if not isinstance(t, dict) or not t.get("trashed"):
            continue
        items.append(
            {
                "id": uuid,
                "title": t.get("title") or "(untitled)",
                "item_type": t.get("item_type", 0),  # 0 todo, 1 project, 2 heading
                "status": t.get("status", 0),
                "modified": t.get("modification_date"),
                "notes": t.get("notes"),
            }
        )
    items.sort(key=lambda i: i.get("modified") or 0, reverse=True)
    return items


# ------------------------------------------------------------- mutations
# Mutations run WITH sync so changes push to Things Cloud immediately.

def _settle() -> None:
    """Extra sync after a mutation: the local cache only reflects a change
    after the following sync, so views read with --no-sync would lag."""
    try:
        _run(["today"], sync=True, timeout=45)
    except CliError:
        pass


def mark(ids: list[str], status: str) -> None:
    flag = {"done": "--done", "incomplete": "--incomplete", "canceled": "--canceled"}[status]
    _run(["mark", flag, *ids], sync=True)
    _settle()


def check_item(task_id: str, item_short_id: str, checked: bool) -> None:
    flag = "--check" if checked else "--uncheck"
    _run(["mark", flag, item_short_id, task_id], sync=True)
    _settle()


def delete(ids: list[str]) -> None:
    """PERMANENT: the CLI has no trash — deleted items do not go to Trash."""
    _run(["delete", *ids], sync=True)
    _settle()


_ANSI = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _created_id(output: str) -> str | None:
    """`new` prints '✓ Created <title>  <id>' — grab the id."""
    text = _ANSI.sub("", output).strip()
    if not text:
        return None
    tail = text.split()[-1]
    return tail if len(tail) >= 17 and " " not in tail else None


def new_task(
    title: str,
    *,
    dest: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    notes: str | None = None,
    tags: str | None = None,
    checklist: list[str] | None = None,
) -> str | None:
    args = ["new", title]
    if dest:
        args += ["--in", dest]
    if when:
        args += ["--when", when]
    if deadline:
        args += ["--deadline", deadline]
    if notes:
        args += ["--notes", notes]
    if tags:
        args += ["--tags", tags]
    out = _run(args, sync=True)
    new_id = _created_id(out)
    if checklist and new_id:
        args = ["edit", new_id]
        for item in checklist:
            args += ["--add-checklist", item]
        _run(args, sync=True)
    _settle()
    return new_id


def new_project(
    title: str,
    *,
    area: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    notes: str | None = None,
    tags: str | None = None,
) -> str | None:
    args = ["projects", "new", title]
    if area:
        args += ["--area", area]
    if when:
        args += ["--when", when]
    if deadline:
        args += ["--deadline", deadline]
    if notes:
        args += ["--notes", notes]
    if tags:
        args += ["--tags", tags]
    out = _run(args, sync=True)
    _settle()
    return _created_id(out)


def edit_task(
    task_id: str,
    *,
    title: str | None = None,
    notes: str | None = None,
    move: str | None = None,
    add_tags: str | None = None,
    remove_tags: str | None = None,
    add_checklist: list[str] | None = None,
    remove_checklist: str | None = None,
) -> None:
    args = ["edit", task_id]
    if title is not None:
        args += ["--title", title]
    if notes is not None:
        args += ["--notes", notes]
    if move is not None:
        args += ["--move", move]
    if add_tags:
        args += ["--add-tags", add_tags]
    if remove_tags:
        args += ["--remove-tags", remove_tags]
    for item in add_checklist or []:
        args += ["--add-checklist", item]
    if remove_checklist:
        args += ["--remove-checklist", remove_checklist]
    if len(args) > 2:
        _run(args, sync=True)
        _settle()


def schedule(
    task_id: str,
    *,
    when: str | None = None,
    deadline: str | None = None,
    clear_deadline: bool = False,
) -> None:
    args = ["schedule", task_id]
    if when:
        args += ["--when", when]
    if deadline:
        args += ["--deadline", deadline]
    if clear_deadline:
        args.append("--clear-deadline")
    if len(args) > 2:
        _run(args, sync=True)
        _settle()


def sync_now() -> None:
    """Pull/push with Things Cloud (any command without --no-sync syncs).
    A first-ever sync pulls full history and can take minutes."""
    timeout = 600 if not STATE_CACHE.exists() else 45
    _run(["today"], sync=True, timeout=timeout)


# ------------------------------------------------------------- helpers

def parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def local_date(iso: str | None) -> date | None:
    """The calendar day a Things timestamp means, read the way Things reads it.

    Things stores a plain date; the CLI serializes it two different ways. A
    bare UTC midnight is a floating date — Aug 22 means Aug 22 anywhere, so
    take it as written. Anything else is a real instant (the Things apps store
    local midnight converted to UTC) and has to come back to local time before
    the day is read off it. Confusing the two labels a task a day early or
    late, depending on which side of UTC you live on.
    """
    dt = parse_dt(iso)
    if dt is None:
        return None
    if (dt.tzinfo is not None and dt.utcoffset() == timedelta(0)
            and (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0)):
        return dt.date()
    return dt.astimezone().date()


def parse_when(text: str) -> str | None:
    """Normalize a user-entered `when` into CLI syntax.

    Keywords (today/tonight/someday/anytime) stay keywords; everything else
    goes through the shared entry-line grammar, so the `s` picker understands
    the same dates the `n` line does — "saturday", "next sat", "aug 24", ISO.
    """
    text = text.strip().lower()
    if not text:
        return None
    if text in entry.WHEN_WORDS:
        return entry.WHEN_WORDS[text]
    words = text.split()
    d, used = entry.parse_date(words)
    if d and used == len(words):        # reject trailing junk
        return "today" if d == date.today() else d.isoformat()
    return None
