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
function shapeToday(list) {
  var out = (list || []).filter(keep).filter(function(t) { return t.status === "incomplete" })
  out.sort(function(a, b) {
    var aEve = isEvening(a) ? 1 : 0
    var bEve = isEvening(b) ? 1 : 0
    if (aEve !== bEve) return aEve - bEve
    var aIdx = a.indexes ? a.indexes.today_sort_index : 0
    var bIdx = b.indexes ? b.indexes.today_sort_index : 0
    return aIdx - bIdx
  })
  return out
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
