// Pure data helpers for the Things panel: response shaping, sorting, and
// labels. No QML/Qt types here — the panel owns everything stateful (the
// things3 processes, settings file, UI).

function safeTrim(value) {
  return String(value === undefined || value === null ? "" : value).trim()
}

function pad2(n) {
  return n < 10 ? "0" + n : String(n)
}

function todayIsoDate() {
  var now = new Date()
  return now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate())
}

function tomorrowIsoDate() {
  var d = new Date()
  d.setDate(d.getDate() + 1)
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
}

// Cheap identity for "did this list actually change?" — id + title +
// modified_at covers reorders, renames, edits, and adds/removes, so a
// background refresh that returns identical data can skip replacing the
// ListView model (replacing it resets scroll position).
function tasksSignature(list) {
  return JSON.stringify((list || []).map(function(t) {
    return [t.id, t.title, t.dates ? t.dates.modified_at : ""]
  }))
}

function isoDatePrefix(dateStr) {
  var s = String(dateStr || "")
  return s.length >= 10 ? s.substring(0, 10) : s
}

// Local calendar date of an ISO timestamp (completed_at is stored in UTC —
// going through Date() converts to local time before taking the day).
function localDateOf(isoStr) {
  if (!isoStr) return ""
  var d = new Date(isoStr)
  if (isNaN(d.getTime())) return ""
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
}

function localTimeOf(isoStr) {
  if (!isoStr) return ""
  var d = new Date(isoStr)
  if (isNaN(d.getTime())) return ""
  return pad2(d.getHours()) + ":" + pad2(d.getMinutes())
}

var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

function shortDate(isoStr) {
  var s = isoDatePrefix(isoStr)
  if (s.length < 10) return s
  var m = parseInt(s.substring(5, 7), 10)
  var day = parseInt(s.substring(8, 10), 10)
  return MONTHS[m - 1] + " " + day
}

function isEvening(task) {
  return !!(task && task.start && task.start.evening === true)
}

function keep(task) {
  return task && !(task.flags && task.flags.trashed) && task.type !== "heading"
}

// ---- Per-view shaping. The combined fetch returns raw CLI arrays; each view
//      gets its own sort mirroring the Things apps: Today by the app's own
//      today order (day tasks before This Evening), Inbox by manual order,
//      Done today newest-first.
function shapeToday(list, upcoming) {
  var out = (list || []).filter(keep).filter(function(t) { return t.status === "incomplete" })
  // Fold back anything the CLI left in Upcoming whose day has actually
  // arrived locally (see hasArrived) — Things already counts these as Today.
  var have = {}
  out.forEach(function(t) { have[t.id] = true })
  ;(upcoming || []).filter(keep).forEach(function(t) {
    if (!have[t.id] && t.status === "incomplete" && hasArrived(t)) out.push(t)
  })
  out.sort(function(a, b) {
    var aEve = isEvening(a) ? 1 : 0
    var bEve = isEvening(b) ? 1 : 0
    if (aEve !== bEve) return aEve - bEve
    var aIdx = a.indexes ? a.indexes.today_sort_index : 0
    var bIdx = b.indexes ? b.indexes.today_sort_index : 0
    return aIdx - bIdx
  })
  var day = out.filter(function(t) { return !isEvening(t) })
  var eve = out.filter(isEvening)
  return clusterByContainer(day).concat(clusterByContainer(eve))
}

function shapeInbox(list) {
  var out = (list || []).filter(keep).filter(function(t) { return t.status === "incomplete" })
  out.sort(function(a, b) {
    var aIdx = a.indexes ? a.indexes.sort_index : 0
    var bIdx = b.indexes ? b.indexes.sort_index : 0
    return aIdx - bIdx
  })
  return out
}

// logbook --from already limits to "on/after" that date, but the CLI compares
// in its own way and the file may lag — re-check against the local calendar
// day so "done today" never shows yesterday's late-night items.
function shapeDone(list) {
  var today = todayIsoDate()
  var out = (list || []).filter(keep).filter(function(t) {
    return t.status === "completed"
      && t.dates && localDateOf(t.dates.completed_at) === today
  })
  out.sort(function(a, b) {
    var aAt = (a.dates && a.dates.completed_at) || ""
    var bAt = (b.dates && b.dates.completed_at) || ""
    return aAt < bAt ? 1 : (aAt > bAt ? -1 : 0)
  })
  return out
}

// A project's open tasks, in the project's own manual order.
function shapeProjectTasks(list) {
  // _removing rows linger so the drill-in list can animate their collapse.
  var out = (list || []).filter(keep).filter(function(t) { return t.status === "incomplete" || t._removing === true })
  out.sort(function(a, b) {
    var aIdx = a.indexes ? a.indexes.sort_index : 0
    var bIdx = b.indexes ? b.indexes.sort_index : 0
    return aIdx - bIdx
  })
  return out
}

// Things-style pie progress: done counts completed AND canceled (both fill
// the pie in the apps), total is every non-trashed to-do in the project.
function projectStats(list) {
  var total = 0, done = 0
  for (var i = 0; i < (list || []).length; i++) {
    var t = list[i]
    if (!keep(t)) continue
    total++
    if (t.status !== "incomplete") done++
  }
  return { done: done, total: total }
}

function deadlineOverdue(task) {
  if (!task || !task.dates || !task.dates.deadline_at) return false
  return isoDatePrefix(task.dates.deadline_at) < todayIsoDate()
}

// Caption line under a task title: project (or area), plus deadline flag or
// completion time depending on the view.
function taskCaption(task, view) {
  if (!task) return ""
  var parts = []
  if (view === "done" && task.dates && task.dates.completed_at)
    parts.push(" " + localTimeOf(task.dates.completed_at))
  // Inside a project drill-in every row shares the same project — skip it.
  if (view !== "project") {
    if (task.project && task.project.title) parts.push(task.project.title)
    else if (task.area && task.area.title) parts.push(task.area.title)
  }
  if (view !== "done" && task.dates && task.dates.deadline_at)
    parts.push(" " + shortDate(task.dates.deadline_at))
  return parts.join("  ·  ")
}

function errorMessageForExit(exitCode, stderrText) {
  var text = String(stderrText || "").trim()
  if (exitCode === 127) return "things3 CLI not found — install things3-cloud."
  if (text.indexOf("auth") !== -1 || text.indexOf("credential") !== -1 || text.indexOf("login") !== -1)
    return "Things Cloud auth failed — run `things3 set-auth`."
  if (exitCode === 124) return "things3 took too long — network down?"
  var firstLine = text.split("\n")[0]
  return "things3 failed" + (firstLine !== "" ? (": " + firstLine) : ".")
}

// ---- Entry-line date grammar ----------------------------------------
//      A port of things-tui's entry.py (parse_date / parse_entry). The two
//      implementations are independent, so a change to the grammar in one
//      has to be mirrored in the other. Weekday numbering here is
//      Monday-based to match Python's date.weekday(), NOT JS getDay().

var WHEN_WORDS = {
  "today": "today", "tod": "today",
  "tonight": "evening", "evening": "evening", "eve": "evening",
  "someday": "someday", "anytime": "anytime", "any": "anytime"
}
var MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun",
                  "jul", "aug", "sep", "oct", "nov", "dec"]
var MONTH_FULL = ["january", "february", "march", "april", "may", "june",
                  "july", "august", "september", "october", "november", "december"]
var WEEKDAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
var WEEKDAY_FULL = ["monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday"]
var WEEKDAY_ALIAS = { "tues": 1, "wednes": 2, "thur": 3, "thurs": 3 }

function isoOf(d) {
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
}

function addDays(d, n) {
  var x = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  x.setDate(x.getDate() + n)
  return x
}

// Monday=0 … Sunday=6, matching Python's weekday().
function isoWeekday(d) {
  return (d.getDay() + 6) % 7
}

function monthOf(word) {
  var w = String(word || "").toLowerCase().replace(/\.$/, "")
  if (w.length < 3) return 0
  for (var i = 0; i < 12; i++)
    if (w === MONTH_ABBR[i] || w === MONTH_FULL[i] || (w === "sept" && i === 8)) return i + 1
  return 0
}

// Any unambiguous prefix of a weekday name: "s" is ambiguous, "sat" is not.
function weekdayOf(word) {
  var w = String(word || "").toLowerCase().replace(/\.$/, "")
  if (w.length < 3) return -1
  if (WEEKDAY_ALIAS[w] !== undefined) return WEEKDAY_ALIAS[w]
  for (var i = 0; i < 7; i++)
    if (w === WEEKDAY_ABBR[i] || w === WEEKDAY_FULL[i]
        || (WEEKDAY_FULL[i].indexOf(w) === 0 && w.indexOf(WEEKDAY_ABBR[i]) === 0)) return i
  return -1
}

// Month/day with no year means the next time it comes around.
function nextOccurrence(month, day, today) {
  for (var i = 0; i < 2; i++) {
    var year = today.getFullYear() + i
    var d = new Date(year, month - 1, day)
    // Reject rollover (Feb 30 becomes Mar 2 in JS).
    if (d.getMonth() !== month - 1 || d.getDate() !== day) continue
    if (d >= today) return d
  }
  return null
}

// Read a date off the FRONT of `words`. Returns {date, used}: `used` is how
// many words it swallowed, so the caller knows what is left over.
function parseDate(words, today) {
  today = today || new Date()
  today = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  var none = { date: null, used: 0 }
  if (!words || words.length === 0) return none
  var w0 = String(words[0] || "").toLowerCase()
  var m, delta, wd, mo, da, yr

  if (w0 === "today") return { date: today, used: 1 }
  if (w0 === "tomorrow" || w0 === "tmr" || w0 === "tom" || w0 === "tmrw")
    return { date: addDays(today, 1), used: 1 }

  if ((w0 === "next" || w0 === "this") && words.length > 1) {
    wd = weekdayOf(words[1])
    if (wd !== -1) {
      delta = (wd - isoWeekday(today) + 7) % 7
      if (delta === 0) delta = 7
      // "next sat" is the one AFTER the coming Saturday — unless the coming
      // one is already a full week out (today is itself a Saturday).
      if (w0 === "next" && delta < 7) delta += 7
      return { date: addDays(today, delta), used: 2 }
    }
    if (w0 === "next" && String(words[1]).toLowerCase().indexOf("week") === 0)
      return { date: addDays(today, 7), used: 2 }
  }

  wd = weekdayOf(w0)
  if (wd !== -1) {
    delta = (wd - isoWeekday(today) + 7) % 7
    if (delta === 0) delta = 7
    return { date: addDays(today, delta), used: 1 }
  }

  if (w0 === "in" && words.length > 2 && /^\d+$/.test(words[1])) {
    var n = parseInt(words[1], 10)
    var unit = String(words[2]).toLowerCase()
    if (unit.indexOf("day") === 0) return { date: addDays(today, n), used: 3 }
    if (unit.indexOf("week") === 0) return { date: addDays(today, n * 7), used: 3 }
  }

  // ---- numeric forms
  m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(w0)
  if (m) {
    var iso = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10))
    if (iso.getMonth() !== parseInt(m[2], 10) - 1) return none
    return { date: iso, used: 1 }
  }
  m = /^(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2}|\d{4}))?$/.exec(w0)
  if (m) {
    mo = parseInt(m[1], 10); da = parseInt(m[2], 10)
    if (m[3]) {
      yr = parseInt(m[3], 10)
      if (yr < 100) yr += 2000
      var exact = new Date(yr, mo - 1, da)
      if (exact.getMonth() !== mo - 1) return none
      return { date: exact, used: 1 }
    }
    var near = nextOccurrence(mo, da, today)
    return near ? { date: near, used: 1 } : none
  }

  // ---- "aug24" / "aug 24" / "24 aug" / "august 24 2026"
  m = /^([a-z]+)(\d{1,2})$/.exec(w0)
  if (m && monthOf(m[1])) {
    var glued = nextOccurrence(monthOf(m[1]), parseInt(m[2], 10), today)
    return glued ? { date: glued, used: 1 } : none
  }
  mo = monthOf(w0)
  if (mo && words.length > 1 && /^\d{1,2}(?:st|nd|rd|th)?,?$/.test(String(words[1]).toLowerCase())) {
    da = parseInt(/^\d+/.exec(words[1])[0], 10)
    if (words.length > 2 && /^\d{4}$/.test(words[2])) {
      yr = parseInt(words[2], 10)
      var md = new Date(yr, mo - 1, da)
      return md.getMonth() === mo - 1 ? { date: md, used: 3 } : none
    }
    var monthFirst = nextOccurrence(mo, da, today)
    return monthFirst ? { date: monthFirst, used: 2 } : none
  }
  if (/^\d{1,2}(?:st|nd|rd|th)?$/.test(w0) && words.length > 1 && monthOf(words[1])) {
    da = parseInt(/^\d+/.exec(w0)[0], 10); mo = monthOf(words[1])
    if (words.length > 2 && /^\d{4}$/.test(words[2])) {
      yr = parseInt(words[2], 10)
      var dm = new Date(yr, mo - 1, da)
      return dm.getMonth() === mo - 1 ? { date: dm, used: 3 } : none
    }
    var dayFirst = nextOccurrence(mo, da, today)
    return dayFirst ? { date: dayFirst, used: 2 } : none
  }
  return none
}

// A whole string as a `when` value: a keyword stays a keyword, anything else
// has to consume the entire input (trailing junk means the user is mid-typo,
// and silently scheduling off a partial parse is worse than saying nothing).
function parseWhen(text) {
  var s = safeTrim(text).toLowerCase()
  if (s === "") return null
  if (WHEN_WORDS[s] !== undefined) return WHEN_WORDS[s]
  var words = s.split(/\s+/)
  var hit = parseDate(words)
  if (hit.date && hit.used === words.length)
    return isoOf(hit.date) === todayIsoDate() ? "today" : isoOf(hit.date)
  return null
}

// Split a line into words, keeping "quoted spans" and #"two word lists"
// together as single tokens (a poor man's shlex — good enough for one line).
function tokenize(text) {
  var out = []
  var re = /(#|\+|@|!)?"([^"]*)"|(#|\+|@|!)?'([^']*)'|(\S+)/g
  var m
  while ((m = re.exec(text)) !== null) {
    if (m[5] !== undefined) out.push(m[5])
    else if (m[2] !== undefined) out.push({ prefix: m[1] || "", quoted: m[2] })
    else out.push({ prefix: m[3] || "", quoted: m[4] })
  }
  return out
}

// Parse one entry line into the fields `things3 new` wants. Mirrors
// entry.py parse_entry: @when  !due / --due  #list  +tag  "notes"  -- notes
function parseEntry(text, today) {
  var raw = safeTrim(text)
  var notes = ""
  var sep = raw.split(/(?:^|\s)--(?:\s|$)/)
  if (sep.length > 1 && !/^(due|deadline)/i.test(sep[1])) {
    raw = sep[0]
    notes = safeTrim(sep.slice(1).join(" -- "))
  }

  var tokens = tokenize(raw)
  var when = null, deadline = null, dest = "", tags = [], titleWords = []
  var i = 0
  while (i < tokens.length) {
    var tok = tokens[i]
    if (typeof tok !== "string") {          // a quoted span
      if (tok.prefix === "#") dest = tok.quoted
      else if (tok.prefix === "+") tags.push(tok.quoted)
      else notes = notes ? notes + "\n" + tok.quoted : tok.quoted
      i += 1
      continue
    }
    var low = tok.toLowerCase()
    // words the date parser may still need, as plain strings
    var rest = []
    for (var j = i + 1; j < tokens.length; j++)
      rest.push(typeof tokens[j] === "string" ? tokens[j] : tokens[j].quoted)

    if (low === "--due" || low === "--deadline" || low === "-d"
        || low.indexOf("due:") === 0 || low.indexOf("deadline:") === 0) {
      var inline = low.indexOf(":") !== -1 && low.indexOf("--") !== 0
        ? tok.substring(tok.indexOf(":") + 1) : ""
      var dueWords = (inline ? [inline] : []).concat(rest)
      if (dueWords.length > 0 && ["none", "clear", "-"].indexOf(dueWords[0].toLowerCase()) !== -1) {
        deadline = ""
        i += inline ? 1 : 2
        continue
      }
      var due = parseDate(dueWords, today)
      if (due.date) {
        deadline = isoOf(due.date)
        i += 1 + due.used - (inline ? 1 : 0)
        continue
      }
      i += 1
      continue
    }
    if (low.charAt(0) === "!" && tok.length > 1) {
      var bang = parseDate([tok.substring(1)].concat(rest), today)
      if (bang.date) { deadline = isoOf(bang.date); i += bang.used; continue }
    }
    if (low.charAt(0) === "@" && tok.length > 1) {
      var key = low.substring(1)
      if (WHEN_WORDS[key] !== undefined) { when = WHEN_WORDS[key]; i += 1; continue }
      var at = parseDate([tok.substring(1)].concat(rest), today)
      if (at.date) {
        when = isoOf(at.date) === todayIsoDate() ? "today" : isoOf(at.date)
        i += at.used
        continue
      }
    }
    if (tok.charAt(0) === "#" && tok.length > 1) { dest = tok.substring(1); i += 1; continue }
    if (tok.charAt(0) === "+" && tok.length > 1) { tags.push(tok.substring(1)); i += 1; continue }
    titleWords.push(tok)
    i += 1
  }
  return {
    title: titleWords.join(" ").trim(),
    when: when,                 // keyword, ISO date, or null
    deadline: deadline,         // ISO date, "" to clear, or null
    dest: dest,
    tags: tags.join(","),
    notes: notes
  }
}

// Human label for a `when` value, for the live preview line.
function prettyWhen(value) {
  var v = safeTrim(value)
  if (v === "") return ""
  if (v === "today") return "today"
  if (v === "evening") return "this evening"
  if (v === "anytime" || v === "someday") return v
  if (v === todayIsoDate()) return "today"
  if (v === tomorrowIsoDate()) return "tomorrow"
  var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v)
  if (!parts) return v
  var d = new Date(parseInt(parts[1], 10), parseInt(parts[2], 10) - 1, parseInt(parts[3], 10))
  var label = WEEKDAY_FULL[isoWeekday(d)].substring(0, 3)
  label = label.charAt(0).toUpperCase() + label.substring(1)
  var mon = MONTH_ABBR[d.getMonth()]
  mon = mon.charAt(0).toUpperCase() + mon.substring(1)
  var out = label + " " + mon + " " + d.getDate()
  if (d.getFullYear() !== new Date().getFullYear()) out += " " + d.getFullYear()
  return out
}

// ---- Notes -----------------------------------------------------------

// One-line gist of a task's notes for the row beneath the title: the first
// non-empty line with whitespace collapsed. A bare URL is shortened to
// host + a hint of the path, so a pasted link reads as a source rather than
// a wall of tracking parameters.
function notesSnippet(task) {
  if (!task) return ""
  var raw = safeTrim(task.notes)
  if (raw === "") return ""
  var lines = raw.split(/[\n\u2028\u2029]/)
  var first = ""
  for (var i = 0; i < lines.length; i++) {
    first = safeTrim(lines[i])
    if (first !== "") break
  }
  if (first === "") return ""
  first = first.replace(/\s+/g, " ")
  var url = /^https?:\/\/([^/\s]+)(\/[^\s]*)?$/.exec(first)
  if (url) {
    var host = url[1].replace(/^www\./, "")
    var path = url[2] ? url[2].split("?")[0].replace(/\/$/, "") : ""
    if (path.length > 24) path = path.substring(0, 23) + "…"
    return host + path
  }
  return first
}

// The first link anywhere in the notes, for click-to-open. "" when there
// isn't one — the row then falls back to its normal click behavior.
function firstUrl(task) {
  if (!task) return ""
  var m = /https?:\/\/[^\s<>"'\u2028\u2029]+/.exec(String(task.notes || ""))
  return m ? m[0].replace(/[.,;:)\]]+$/, "") : ""
}

// ---- Which day a scheduled task really falls on ----------------------

// Things stores a plain date; the CLI serializes it two ways. A bare UTC
// midnight is a floating date — Aug 22 means Aug 22 anywhere, so take it as
// written. Anything else is a real instant (the Things apps store local
// midnight converted to UTC) and has to come back to local time first.
// Mirrors backend.local_date in things-tui; keep the two in step.
function localDateIso(iso) {
  var s = safeTrim(iso)
  if (s === "") return ""
  if (/T00:00:00(\.0+)?(Z|\+00:00)$/.test(s)) return s.substring(0, 10)
  var d = new Date(s)
  if (isNaN(d.getTime())) return ""
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
}

// The CLI splits today/upcoming by comparing the serialized UTC instant
// against now, so a floating-date task sits in Upcoming until UTC midnight —
// up to a day after Things itself moved it into Today. Re-split on the real
// local date so the widget agrees with the Things apps.
function hasArrived(task) {
  var d = localDateIso(task && task.start ? task.start.scheduled_at : "")
  return d !== "" && d <= todayIsoDate()      // ISO dates compare as strings
}

// Upcoming: everything still ahead, earliest first. The CLI's own upcoming
// list also carries tasks whose day has already arrived (see hasArrived) —
// those belong in Today, and shapeToday has already claimed them.
function shapeUpcoming(list) {
  var out = (list || []).filter(keep).filter(function(t) {
    return t.status === "incomplete" && !hasArrived(t)
  })
  out.sort(function(a, b) {
    var aAt = (a.start && a.start.scheduled_at) || "9999"
    var bAt = (b.start && b.start.scheduled_at) || "9999"
    if (aAt !== bAt) return aAt < bAt ? -1 : 1
    var aIdx = a.indexes ? a.indexes.sort_index : 0
    var bIdx = b.indexes ? b.indexes.sort_index : 0
    return aIdx - bIdx
  })
  return out
}

// "Tomorrow" / "Sat Aug 29" / "Tue Jan 5 2027" for a task's scheduled day.
function dayLabel(task) {
  var d = localDateIso(task && task.start ? task.start.scheduled_at : "")
  return d === "" ? "Later" : prettyWhen(d)
}

// Cap a title before it goes into a confirmation message. ConfirmDialog
// sizes its card from the wrapped message height with no line limit, so a
// long to-do grows the dialog until its buttons are pushed off the panel.
function ellipsize(text, max) {
  var s = safeTrim(text).replace(/\s+/g, " ")
  if (s.length <= max) return s
  // Prefer breaking at a space so the tail is not a severed word.
  var cut = s.substring(0, max)
  var space = cut.lastIndexOf(" ")
  if (space > max * 0.6) cut = cut.substring(0, space)
  return cut + "…"
}

// Title of the project or area a task lives in ("" when it's loose).
function containerTitle(task) {
  if (!task) return ""
  if (task.project && task.project.title) return String(task.project.title)
  if (task.area && task.area.title) return String(task.area.title)
  return ""
}

// Stable-cluster a section the way the Things apps draw Today: loose tasks
// first, then each project/area's tasks together, containers in order of
// first appearance — the app's own ordering survives inside every group.
function clusterByContainer(list) {
  var loose = [], order = [], groups = {}
  ;(list || []).forEach(function(t) {
    var k = containerTitle(t)
    if (k === "") { loose.push(t); return }
    if (!groups[k]) { groups[k] = []; order.push(k) }
    groups[k].push(t)
  })
  var out = loose
  order.forEach(function(k) { out = out.concat(groups[k]) })
  return out
}
