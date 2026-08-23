"""One-line task entry grammar, shared by `n`/`N` in the app and the
ALT+SPACE quick-add popup.

    buy milk @today #groceries --due aug 24 +errand "get the oat one"

  @when      today · tonight/evening · tomorrow/tmr · someday · anytime ·
             a date (see below) · a weekday (mon…sun, "this fri",
             "next fri" = the week after) · "next week" · "in 3 days"
  --due D    deadline (also  !D  or  due:D);  --due none  clears
  #list      project or area (substring; #"two words" if it has spaces)
  +tag       tag (repeatable)
  "…" / '…'  notes (or everything after a bare  --  )

Dates: 2026-08-24 · 08-24 · 08-24-26 · 08/24/2026 · aug 24 · 24 aug ·
august 24 · aug24. Month-day without a year means the next occurrence.
"""

from __future__ import annotations

import re
import shlex
from datetime import date, timedelta

WHEN_WORDS = {
    "today": "today", "tod": "today",
    "tonight": "evening", "evening": "evening", "eve": "evening",
    "someday": "someday", "anytime": "anytime", "any": "anytime",
}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTHS.update({"sept": 9})
WEEKDAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}
WEEKDAYS.update({"tues": 1, "wednes": 2, "thur": 3, "thurs": 3})


def _month(word: str) -> int | None:
    w = word.lower().rstrip(".")
    for name, n in MONTHS.items():
        if w == name or (len(w) >= 3 and name.startswith(w[:3]) and w.startswith(name[:3])
                         and w in ("january", "february", "march", "april", "may", "june", "july",
                                   "august", "september", "october", "november", "december", name)):
            return n
    return None


def _weekday(word: str) -> int | None:
    w = word.lower().rstrip(".")
    for name, n in WEEKDAYS.items():
        if w == name or (len(w) >= 3 and w.startswith(name) and name.startswith(w[:3])):
            return n
        if w in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday") \
                and w.startswith(name):
            return n
    return None


def _next_occurrence(month: int, day: int, today: date) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def parse_date(words: list[str], today: date | None = None) -> tuple[date | None, int]:
    """Try to read a date from the start of `words`. Returns (date, words consumed)."""
    today = today or date.today()
    if not words:
        return None, 0
    w0 = words[0].lower()

    if w0 in ("today",):
        return today, 1
    if w0 in ("tomorrow", "tmr", "tom", "tmrw"):
        return today + timedelta(days=1), 1
    if w0 in ("next", "this") and len(words) > 1:
        wd = _weekday(words[1])
        if wd is not None:
            delta = (wd - today.weekday()) % 7 or 7
            # "next sat" is the one after the coming Saturday — unless the
            # coming one is already a full week out (i.e. today is Saturday).
            if w0 == "next" and delta < 7:
                delta += 7
            return today + timedelta(days=delta), 2
        if w0 == "next" and words[1].lower().startswith("week"):
            return today + timedelta(weeks=1), 2
    wd = _weekday(w0)
    if wd is not None:
        delta = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=delta), 1
    if w0 == "in" and len(words) > 2 and words[1].isdigit():
        n = int(words[1]); unit = words[2].lower()
        if unit.startswith("day"):
            return today + timedelta(days=n), 3
        if unit.startswith("week"):
            return today + timedelta(weeks=n), 3

    # numeric forms
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", w0)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])), 1
        except ValueError:
            return None, 0
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2}|\d{4}))?", w0)
    if m:
        mo, da = int(m[1]), int(m[2])
        if m[3]:
            yr = int(m[3]); yr = yr + 2000 if yr < 100 else yr
            try:
                return date(yr, mo, da), 1
            except ValueError:
                return None, 0
        d = _next_occurrence(mo, da, today)
        return (d, 1) if d else (None, 0)

    # "aug 24" / "aug24" / "24 aug" / "august 24 2026"
    m = re.fullmatch(r"([a-z]+)(\d{1,2})", w0)
    if m and _month(m[1]):
        d = _next_occurrence(_month(m[1]), int(m[2]), today)
        return (d, 1) if d else (None, 0)
    mo = _month(w0)
    if mo and len(words) > 1 and re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)?,?", words[1].lower()):
        da = int(re.match(r"\d+", words[1])[0])
        if len(words) > 2 and re.fullmatch(r"\d{4}", words[2]):
            try:
                return date(int(words[2]), mo, da), 3
            except ValueError:
                return None, 0
        d = _next_occurrence(mo, da, today)
        return (d, 2) if d else (None, 0)
    if re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)?", w0) and len(words) > 1 and _month(words[1]):
        da = int(re.match(r"\d+", w0)[0]); mo = _month(words[1])
        if len(words) > 2 and re.fullmatch(r"\d{4}", words[2]):
            try:
                return date(int(words[2]), mo, da), 3
            except ValueError:
                return None, 0
        d = _next_occurrence(mo, da, today)
        return (d, 2) if d else (None, 0)
    return None, 0


def parse_entry(text: str, today: date | None = None) -> dict:
    """Parse one entry line into fields for backend.new_task."""
    today = today or date.today()
    notes: str | None = None
    if " -- " in f" {text} ":
        # bare `--` separator (not --due): everything after is notes
        parts = re.split(r"(?:^| )--(?: |$)", text, maxsplit=1)
        if len(parts) == 2 and not parts[1].lower().startswith(("due", "deadline")):
            text, notes = parts[0], parts[1].strip() or None
    try:
        words = shlex.split(text, posix=True)
        raw = text
    except ValueError:
        words = text.split(); raw = text
    # shlex drops the quotes; re-detect quoted spans for notes
    quoted = re.findall(r'"([^"]*)"|\'([^\']*)\'', raw)
    for a, b in quoted:
        q = (a or b).strip()
        if q and not raw.startswith(f'#"{q}"') and f'#"{q}"' not in raw and f"#'{q}'" not in raw:
            notes = q if notes is None else notes + "\n" + q
    quoted_texts = {(a or b).strip() for a, b in quoted}

    when = None; deadline: str | None = None; dest = None; tags: list[str] = []
    title_words: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower()
        if w in quoted_texts and not (i > 0 and words[i - 1] == "#"):
            i += 1; continue  # notes, already captured
        if low in ("--due", "--deadline", "-d") or low.startswith(("due:", "deadline:")):
            inline = w.split(":", 1)[1] if ":" in w and not low.startswith("--") else ""
            rest = ([inline] if inline else []) + words[i + 1:]
            if rest and rest[0].lower() in ("none", "clear", "-"):
                deadline = ""; i += 1 + (0 if inline else 1); continue
            d, n = parse_date(rest, today)
            if d:
                deadline = d.isoformat()
                i += 1 + (n - (1 if inline else 0)); continue
            i += 1; continue
        if low.startswith("!") and len(w) > 1:
            d, n = parse_date([w[1:]] + words[i + 1:], today)
            if d:
                deadline = d.isoformat(); i += n; continue
        if low.startswith("@") and len(w) > 1:
            key = low[1:]
            if key in WHEN_WORDS:
                when = WHEN_WORDS[key]; i += 1; continue
            d, n = parse_date([w[1:]] + words[i + 1:], today)
            if d:
                when = "today" if d == today else d.isoformat(); i += n; continue
        if w.startswith("#") and len(w) > 1:
            dest = w[1:]; i += 1; continue
        if w.startswith("+") and len(w) > 1:
            tags.append(w[1:]); i += 1; continue
        title_words.append(w); i += 1
    return {
        "title": " ".join(title_words).strip(),
        "when": when,
        "deadline": deadline,       # ISO date, "" = clear, None = unchanged
        "dest": dest,               # list name as typed (resolve against projects/areas)
        "notes": notes,
        "tags": ",".join(tags) or None,
    }


def describe(parsed: dict, *, dest_label: str | None = None,
             default_when: str | None = None, default_dest: str | None = None) -> list[tuple[str, str]]:
    """(label, value) pairs for a live preview line."""
    out: list[tuple[str, str]] = []
    when = parsed["when"] or default_when
    if when:
        out.append(("when", _pretty_when(when)))
    if parsed["deadline"]:
        out.append(("due", _pretty_date(parsed["deadline"])))
    elif parsed["deadline"] == "":
        out.append(("due", "cleared"))
    lst = dest_label if parsed["dest"] else default_dest
    if parsed["dest"] and dest_label is None:
        lst = f"{parsed['dest']}?"
    if lst:
        out.append(("in", lst))
    if parsed["tags"]:
        out.append(("tags", parsed["tags"].replace(",", ", ")))
    if parsed["notes"]:
        n = parsed["notes"].replace("\n", " ")
        out.append(("notes", n if len(n) <= 40 else n[:39] + "…"))
    return out


def _pretty_when(when: str) -> str:
    if when in ("today", "evening", "someday", "anytime"):
        return {"evening": "this evening"}.get(when, when)
    return _pretty_date(when)


def _pretty_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    today = date.today()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return d.strftime("%a %b %-d" if d.year == today.year else "%a %b %-d %Y")
