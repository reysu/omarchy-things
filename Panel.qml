import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Things 3 task popup. Owns every bit of state BarWidget.qml reads back
// (view, barCount, viewIcon) plus the things3 CLI processes that talk to
// Things Cloud. No token/settings UI — the CLI is already authenticated via
// `things3 set-auth`; the only persisted setting is which view the pill is
// showing.
Panel {
  id: root
  moduleName: "reysu.things"
  ipcTarget: "reysu.things"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property string homeDir: Quickshell.env("HOME")
  readonly property string stateDir: homeDir + "/.local/state/omarchy/reysu.things"
  readonly property string settingsPath: stateDir + "/settings.json"

  // ---- The active view, shared by the bar pill and the popup tabs:
  //      "today" (due today), "done" (checked off today), "inbox".
  property string view: "today"
  readonly property var viewOrder: ["today", "upcoming", "done", "inbox", "projects"]

  // Tab strip. The selected tab shows its icon AND its name; the rest are
  // icon-only, which is what makes five of them fit in 340px.
  readonly property var viewTabs: [
    { view: "today", icon: "\uf005", label: "Today" },
    { view: "upcoming", icon: "\uf073", label: "Upcoming" },
    { view: "done", icon: "\uf00c", label: "Done" },
    { view: "inbox", icon: "\uf01c", label: "Inbox" },
    { view: "projects", icon: "\uf10c", label: "Projects" }
  ]

  // One combined fetch fills everything at once, so cycling the pill with
  // middle-click flips between already-loaded counts instead of kicking off
  // a fetch per flip.
  property var todayTasks: []
  property var doneTasks: []
  property var inboxTasks: []
  property var upcomingTasks: []
  // Projects tab: a map of project id -> all its tasks (feeds the pie
  // progress), plus the project currently drilled into (null = the list).
  property var projectTasks: ({})
  property var activeProject: null
  readonly property bool projectListMode: root.view === "projects" && !root.activeProject

  readonly property var tasks: root.view === "done" ? root.doneTasks
    : root.view === "upcoming" ? root.upcomingTasks
    : root.view === "inbox" ? root.inboxTasks
    : root.view === "projects" ? (root.activeProject
      ? Model.shapeProjectTasks(root.projectTasks[root.activeProject.id])
      : root.projects)
    : root.todayTasks

  readonly property int barCount: root.view === "projects" ? root.projects.length : root.tasks.length
  readonly property string viewIcon: root.view === "done" ? "\uf00c"
    : root.view === "upcoming" ? "\uf073"
    : root.view === "inbox" ? "\uf01c"
    : root.view === "projects" ? "\uf10c"
    : "\uf005"

  readonly property string viewLabel: root.view === "done" ? "DONE TODAY"
    : root.view === "upcoming" ? "UPCOMING"
    : root.view === "inbox" ? "INBOX"
    : root.view === "projects" ? "PROJECTS"
    : "TODAY"

  readonly property string countsTooltip: "Things — " + root.todayTasks.length + " due today · "
    + root.doneTasks.length + " done · " + root.inboxTasks.length + " inbox"
    + (root.pinnedTitle !== "" ? " · now: " + root.pinnedTitle : "")

  readonly property string emptyStateMessage: root.view === "done" ? "Nothing checked off yet today."
    : root.view === "upcoming" ? "Nothing scheduled ahead."
    : root.view === "inbox" ? "Inbox zero."
    : root.view === "projects" ? (root.activeProject ? "No open tasks — project done!" : "No projects.")
    : "Nothing due. You're clear."

  property bool settingsLoaded: false
  property bool helpOpen: false
  property bool loading: false
  property string errorText: ""
  // Set when refresh() is called while a fetch is already in flight —
  // fetchProc's own exit handler starts one more fetch once it sees this,
  // so a triggering action never has its refresh silently dropped.
  property bool refreshPending: false
  property bool refreshPendingFast: false

  // ---- Input ceilings -------------------------------------------------
  //      This panel runs inside the long-lived shell process, and the
  //      StdioCollectors below retain a whole stream before anything parses
  //      it. So nothing external is allowed in unbounded: every producer is
  //      capped in the fetch script (read cap+1, reject on overrun rather
  //      than truncate into invalid JSON), the per-project fan-out is capped
  //      in count as well as size, and the assembled document is re-checked
  //      here before JSON.parse. A very large legitimate account trips these
  //      the same way a hostile reply would — better a visible error than an
  //      exhausted shell.
  readonly property int maxStreamBytes: 4194304        // 4 MiB per CLI stream
  readonly property int maxProjectStreamBytes: 524288  // 512 KiB per project
  readonly property int maxProjects: 200               // project fan-out
  readonly property int maxPayloadBytes: 29360128      // 28 MiB assembled
  readonly property int maxStderrBytes: 2048           // retained diagnostics

  // Keyboard cursor over the task list. -1 = nothing selected yet;
  // taskCursorActive gates the row highlight so it only shows up once the
  // user has actually pressed an arrow key, not on every open.
  property int selectedTaskIndex: -1
  property bool taskCursorActive: false
  onViewChanged: {
    root.activeProject = null
    root.selectedTaskIndex = -1
    root.taskCursorActive = false
  }
  onTasksChanged: {
    if (root.selectedTaskIndex >= root.tasks.length) root.selectedTaskIndex = root.tasks.length - 1
  }

  // Tasks mid-mark: already sent to the CLI, but kept in the list (struck
  // through, dimmed) for a moment so the click reads as "done", not
  // "vanished". In the Done view the same machinery drives un-checking.
  property var completingTaskIds: []
  property var pendingRemovalIds: []
  property var actionQueue: []

  property string quickAddText: ""
  property bool quickAddSubmitting: false

  // Live read of the quick-add line. The entry grammar (@when, !due, #list,
  // +tag, "notes") is shared with things-tui — see Model.parseEntry — and the
  // preview under the field shows what it resolved to before you commit.
  readonly property var quickAddParsed: Model.parseEntry(root.quickAddText)
  readonly property string quickAddPreview: {
    if (Model.safeTrim(root.quickAddText) === "") return ""
    var p = root.quickAddParsed
    var bits = []
    if (p.when) bits.push("when " + Model.prettyWhen(p.when))
    if (p.deadline) bits.push("due " + Model.prettyWhen(p.deadline))
    else if (p.deadline === "") bits.push("due cleared")
    if (p.dest) bits.push("in " + p.dest + (root.resolveDestId(p.dest) === "" ? "?" : ""))
    if (p.tags) bits.push("tags " + p.tags.replace(/,/g, ", "))
    if (p.notes) {
      var n = p.notes.replace(/\n/g, " ")
      bits.push("notes " + (n.length > 30 ? n.substring(0, 29) + "\u2026" : n))
    }
    return bits.join("   ")
  }

  // Inline title editing. -1 = no row being edited.
  property int editingTaskIndex: -1
  property string editDraft: ""

  // Delete confirmation (unlike completing, this can't be undone from here).
  property string pendingDeleteTaskId: ""
  property string pendingDeleteTaskTitle: ""

  // Picker overlay, in Things' own two flavors: "when" schedules a task,
  // "move" files it under a project (or the Inbox). Same card, same keys.
  property bool moveOverlayOpen: false
  property string pickerMode: "move"
  property string moveTaskId: ""
  property string moveTaskTitle: ""
  property int moveCursor: 0
  property var projects: []

  // Typed date in the When? picker, for everything the five presets don't
  // cover. Same grammar as the quick-add line (Model.parseWhen).
  property string pickerDateText: ""
  readonly property var pickerTypedWhen: Model.safeTrim(root.pickerDateText) === ""
    ? null : Model.parseWhen(root.pickerDateText)
  // While there is text in the date field, that text — not the preset under
  // the cursor — is what Enter commits. The list dims and drops its highlight
  // to say so, and the preview spells out the target.
  readonly property bool pickerTyping: Model.safeTrim(root.pickerDateText) !== ""
  readonly property string pickerDatePreview: {
    if (!root.pickerTyping) return ""
    return root.pickerTypedWhen ? "Enter \u2192 " + Model.prettyWhen(root.pickerTypedWhen)
      : "? no date there \u2014 Enter does nothing"
  }

  readonly property var whenOptions: [
    { label: "Today", icon: "\uf005", kind: "when", value: "today" },
    { label: "This Evening", icon: "\uf186", kind: "when", value: "evening" },
    { label: "Tomorrow", icon: "\uf064", kind: "when", value: "tomorrow" },
    { label: "Anytime", icon: "\uf073", kind: "when", value: "anytime" },
    { label: "Someday", icon: "\uf187", kind: "when", value: "someday" }
  ]

  readonly property var moveOptions: {
    var opts = [{ label: "Inbox", icon: "\uf01c", kind: "move", value: "inbox" }]
    for (var i = 0; i < root.projects.length; i++) {
      var p = root.projects[i]
      if (p && p.id && p.title) opts.push({ label: p.title, icon: "\uf10c", kind: "move", value: p.id })
    }
    return opts
  }

  // Ctrl-shortcuts act on the selected task, so they stay out of the way
  // whenever some other surface owns the keyboard (overlays, rename field,
  // quick-add) or the list isn't showing tasks at all (project list).
  readonly property bool taskShortcutsEnabled: root.opened && !root.helpOpen
    && !root.moveOverlayOpen && !root.contextMenuOpen && !confirmDialog.opened
    && root.editingTaskIndex === -1 && !quickAddField.activeFocus && !root.projectListMode

  readonly property var pickerOptions: root.pickerMode === "when" ? root.whenOptions : root.moveOptions
  readonly property string pickerTitle: (root.pickerMode === "when" ? "When?  " : "Move to\u2026  ") + root.moveTaskTitle

  readonly property int panelWidth: 340

  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property string headerSubtitle: {
    if (root.loading && root.tasks.length === 0) return "LOADING…"
    var n = root.taskCount
    if (root.view === "projects" && root.activeProject)
      return String(root.activeProject.title || "").toUpperCase() + " · " + n + " OPEN"
    if (root.projectListMode)
      return "PROJECTS · " + n
    return root.viewLabel + " · " + n + " " + (n === 1 ? "TASK" : "TASKS")
  }
  readonly property int taskCount: tasks.length

  // ---- Today's day/evening split. todayTasks is already sorted with day
  //      tasks before This Evening ones (Model.shapeToday), so the groups
  //      are contiguous — just a row-anchored header where the group flips.
  //      Headers only appear when there's actually an evening group.
  readonly property int eveningCount: {
    var n = 0
    for (var i = 0; i < root.todayTasks.length; i++) if (Model.isEvening(root.todayTasks[i])) n++
    return n
  }

  function taskSectionTitle(index) {
    if (index < 0 || index >= root.tasks.length) return ""
    // Upcoming groups by the day a task actually falls on, and only prints a
    // header when that day changes.
    if (root.view === "upcoming") {
      var day = Model.dayLabel(root.tasks[index])
      if (index > 0 && Model.dayLabel(root.tasks[index - 1]) === day) return ""
      return day.toUpperCase()
    }
    if (root.view !== "today" || root.eveningCount === 0) return ""
    var isEve = Model.isEvening(root.tasks[index])
    if (index > 0 && Model.isEvening(root.tasks[index - 1]) === isEve) return ""
    return isEve ? ("THIS EVENING · " + root.eveningCount)
      : ("TODAY · " + (root.tasks.length - root.eveningCount))
  }

  // Project/area sub-header inside Today: printed when the container
  // changes between neighbouring rows of the same day/evening section
  // (shapeToday clusters them, so one group prints one header).
  function taskGroupTitle(index) {
    if (root.view !== "today" || root.projectListMode) return ""
    if (index < 0 || index >= root.tasks.length) return ""
    var t = root.tasks[index]
    var cur = Model.containerTitle(t)
    if (cur === "") return ""
    if (index > 0) {
      var prev = root.tasks[index - 1]
      if (prev && Model.isEvening(prev) === Model.isEvening(t)
          && Model.containerTitle(prev) === cur) return ""
    }
    return cur
  }

  // ---- Lifecycle. open() refreshes before showing so the list is never
  //      more than one popup-open stale.
  function open() {
    refresh()
    root.controller.show()
    if (root.pinnedId !== "") {
      for (var i = 0; i < root.tasks.length; i++) {
        if (root.tasks[i] && root.tasks[i].id === root.pinnedId) {
          root.selectedTaskIndex = i
          root.taskCursorActive = true
          Qt.callLater(function() {
            if (taskListView.count > 0) taskListView.positionViewAtIndex(root.selectedTaskIndex, ListView.Contain)
          })
          break
        }
      }
    }
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // ---- View switching (pill middle-click, popup tabs, Tab key).
  function selectView(v) {
    if (v === root.view) return
    root.view = v
    persistSettings()
  }

  function cycleView(direction) {
    var idx = root.viewOrder.indexOf(root.view)
    if (idx === -1) idx = 0
    var next = (idx + direction + root.viewOrder.length) % root.viewOrder.length
    root.selectView(root.viewOrder[next])
  }

  // ---- Settings persistence: just the active view, in a local state file.
  function ensureStateDir() {
    mkdirProc.running = true
  }

  function loadSettingsFromText(text) {
    var parsed = {}
    try { parsed = JSON.parse(text || "{}") } catch (e) { parsed = {} }
    if (typeof parsed.view === "string" && root.viewOrder.indexOf(parsed.view) !== -1)
      root.view = parsed.view
    if (typeof parsed.pinnedId === "string") root.pinnedId = parsed.pinnedId
    if (typeof parsed.pinnedTitle === "string") root.pinnedTitle = parsed.pinnedTitle
    root.settingsLoaded = true
  }

  function persistSettings() {
    settingsFile.setText(JSON.stringify({
      view: root.view,
      pinnedId: root.pinnedId,
      pinnedTitle: root.pinnedTitle
    }, null, 2) + "\n")
  }

  // ---- Fetch. One bash pipeline: the first call does a real cloud sync
  //      (so counts match your other devices), the other two read the
  //      just-synced local cache with --no-sync. If the sync times out
  //      (offline), fall back to cache-only so the widget still shows data.
  // fast=true: post-action reconcile — skips only the 19-call per-project
  // dump (existing projectTasks are kept until the next full refresh). The
  // today fetch ALWAYS does a real sync: delete/reorder push to the cloud
  // WITHOUT updating the local cache, so a cache-only read right after an
  // action resurrects deletes and un-does reorders.
  function refresh(fast) {
    if (fetchProc.running) { root.refreshPending = true; root.refreshPendingFast = !!fast; return }
    root.refreshPending = false
    root.loading = true
    fetchProc.wasFast = !!fast
    fetchProc.startedAt = Date.now()

    var from = Model.todayIsoDate()
    // `cap NAME LIMIT` reads stdin through LIMIT+1 bytes and fails if it
    // overran, so an oversized stream is rejected outright instead of being
    // truncated into JSON that would parse wrong. LC_ALL=C makes ${#v} count
    // bytes; pipefail makes a failing producer fail its whole pipeline so the
    // `||` fallbacks below still fire.
    var script = 'set -o pipefail; export LC_ALL=C\n'
      + 'CAP=' + root.maxStreamBytes + '; PCAP=' + root.maxProjectStreamBytes + '; MAXP=' + root.maxProjects + '\n'
      + 'cap() { v=$(head -c $(($2+1))); if [ ${#v} -gt $2 ]; then printf "%s exceeded %s bytes\\n" "$1" "$2" >&2; return 1; fi; printf %s "$v"; }\n'
      + 'if ! command -v things3 >/dev/null 2>&1; then echo "things3 CLI not found" >&2; exit 127; fi\n'
      + 'ERR=$(mktemp); trap \'rm -f "$ERR"\' EXIT\n'
      + 'T=$(timeout 25 things3 --json today 2>"$ERR" | cap today $CAP) || T=$(things3 --no-sync --json today 2>"$ERR" | cap today $CAP) || { head -c ' + root.maxStderrBytes + ' "$ERR" >&2; exit 1; }\n'
      + 'U=$(things3 --no-sync --json upcoming 2>"$ERR" | cap upcoming $CAP) || U="[]"\n'
      + 'I=$(things3 --no-sync --json inbox 2>"$ERR" | cap inbox $CAP) || I="[]"\n'
      + 'D=$(things3 --no-sync --json logbook --from ' + from + ' 2>"$ERR" | cap logbook $CAP) || D="[]"\n'
      + 'P=$(things3 --no-sync --json projects 2>"$ERR" | cap projects $CAP) || P="[]"\n'
      + (fast ? 'PD="{}"\n'
        : 'PD=$(printf %s "$P" | jq -r \'.[] | select(.status == "incomplete") | .id\' | head -n $MAXP | while read -r pid; do things3 --no-sync --json project "$pid" 2>/dev/null | cap "project" $PCAP | jq --arg id "$pid" \'{key: $id, value: [.[] | {id, title, status, flags, dates, indexes}]}\' || true; done | jq -s \'from_entries\' | cap projectTasks $CAP) || PD="{}"\n')
      + 'printf \'{"today":%s,"upcoming":%s,"inbox":%s,"done":%s,"projects":%s,"projectTasks":%s}\' "$T" "$U" "$I" "$D" "$P" "$PD"\n'
    fetchProc.command = ["bash", "-c", script]
    fetchProc.running = true

    openRefreshTimer.restart()
    backgroundRefreshTimer.restart()
  }

  // ---- Churn-free updates. Replacing a ListView's model array resets its
  //      scroll position, so a refresh right after checking something off
  //      would yank the list back to the top ("I lose my place"). Two
  //      defenses: skip the replacement entirely when the data is identical
  //      (Model.tasksSignature), and when it did change, restore both scroll
  //      positions and re-find the selected task by id afterward.
  function preserveListScroll(fn) {
    var listY = taskListView.contentY
    var outerY = scroll.contentY
    var selId = (root.selectedTaskIndex >= 0 && root.selectedTaskIndex < root.tasks.length && root.tasks[root.selectedTaskIndex])
      ? root.tasks[root.selectedTaskIndex].id : ""
    fn()
    if (selId !== "") {
      var found = -1
      for (var i = 0; i < root.tasks.length; i++) {
        if (root.tasks[i] && root.tasks[i].id === selId) { found = i; break }
      }
      if (found !== -1) root.selectedTaskIndex = found
    }
    Qt.callLater(function() {
      taskListView.contentY = Math.max(0, Math.min(listY, taskListView.contentHeight - taskListView.height))
      scroll.contentY = Math.max(0, Math.min(outerY, scroll.contentHeight - scroll.height))
    })
  }

  function projectsSignature(projects, projectTasks) {
    var flat = []
    for (var i = 0; i < (projects || []).length; i++)
      flat.push(projects[i].id + ":" + projects[i].title)
    for (var k in (projectTasks || {})) {
      var l = projectTasks[k] || []
      for (var j = 0; j < l.length; j++) flat.push(k + "/" + l[j].id + "/" + l[j].status)
    }
    return JSON.stringify(flat)
  }

  function applyFetched(newToday, newUpcoming, newDone, newInbox, newProjects, newProjectTasks) {
    var changed = Model.tasksSignature(newToday) !== Model.tasksSignature(root.todayTasks)
      || Model.tasksSignature(newUpcoming) !== Model.tasksSignature(root.upcomingTasks)
      || Model.tasksSignature(newDone) !== Model.tasksSignature(root.doneTasks)
      || Model.tasksSignature(newInbox) !== Model.tasksSignature(root.inboxTasks)
      || root.projectsSignature(newProjects, newProjectTasks) !== root.projectsSignature(root.projects, root.projectTasks)
    // Rows mid-animation (striking through or collapsing) must survive the
    // swap untouched: replace the fetched copy with the local marked copy,
    // and re-inject at the old spot if the fetch already dropped them —
    // dropTimer does the final removal when the animation has played.
    var animIds = root.completingTaskIds.concat(root.dropQueue.map(function(q) { return q.id }))
      .concat(root.pendingRemovalIds)
    var mergeAnimating = function(oldList, newList) {
      if (animIds.length === 0) return newList
      var result = []
      for (var j = 0; j < newList.length; j++) {
        var t = newList[j]
        if (t && animIds.indexOf(t.id) !== -1) {
          var localCopy = null
          for (var i = 0; i < oldList.length; i++)
            if (oldList[i] && oldList[i].id === t.id) { localCopy = oldList[i]; break }
          result.push(localCopy || t)
        } else result.push(t)
      }
      for (var k = 0; k < oldList.length; k++) {
        var ot = oldList[k]
        if (!ot || animIds.indexOf(ot.id) === -1) continue
        var present = false
        for (var m = 0; m < result.length; m++)
          if (result[m] && result[m].id === ot.id) { present = true; break }
        if (!present) result.splice(Math.min(k, result.length), 0, ot)
      }
      return result
    }
    newToday = mergeAnimating(root.todayTasks, newToday)
    newDone = mergeAnimating(root.doneTasks, newDone)
    newInbox = mergeAnimating(root.inboxTasks, newInbox)
    newUpcoming = mergeAnimating(root.upcomingTasks, newUpcoming)
    changed = Model.tasksSignature(newToday) !== Model.tasksSignature(root.todayTasks)
      || Model.tasksSignature(newUpcoming) !== Model.tasksSignature(root.upcomingTasks)
      || Model.tasksSignature(newDone) !== Model.tasksSignature(root.doneTasks)
      || Model.tasksSignature(newInbox) !== Model.tasksSignature(root.inboxTasks)
      || root.projectsSignature(newProjects, newProjectTasks) !== root.projectsSignature(root.projects, root.projectTasks)
    if (!changed) return
    var oldToday = root.todayTasks, oldDone = root.doneTasks, oldInbox = root.inboxTasks
    root.preserveListScroll(function() {
      root.todayTasks = newToday
      root.upcomingTasks = newUpcoming
      root.doneTasks = newDone
      root.inboxTasks = newInbox
      root.projects = newProjects
      root.projectTasks = newProjectTasks
      root.pendingChecklistIds = []
    })
    // Rows that just appeared slide in. Quick-add reconciles are excluded —
    // their synthetic row already played the entrance (a pending- id in the
    // old list marks that case) — as are initial loads and bulk changes.
    var noteDiff = function(oldList, newList) {
      if (oldList.length === 0) return
      for (var p = 0; p < oldList.length; p++)
        if (oldList[p] && String(oldList[p].id).indexOf("pending-") === 0) return
      var oldIds = {}
      for (var i = 0; i < oldList.length; i++) if (oldList[i]) oldIds[oldList[i].id] = true
      var added = []
      for (var j = 0; j < newList.length; j++) if (newList[j] && !oldIds[newList[j].id]) added.push(newList[j].id)
      if (added.length > 0 && added.length <= 5) root.noteAdded(added)
    }
    noteDiff(oldToday, newToday)
    noteDiff(oldDone, newDone)
    noteDiff(oldInbox, newInbox)
    // Keep the pin honest: follow renames, clear it if the task got
    // completed or canceled elsewhere (e.g. on the phone).
    if (root.pinnedId !== "") {
      var everything = root.todayTasks.concat(root.inboxTasks)
      for (var pk in root.projectTasks) everything = everything.concat(root.projectTasks[pk] || [])
      var hit = null
      for (var h = 0; h < everything.length; h++)
        if (everything[h] && everything[h].id === root.pinnedId) { hit = everything[h]; break }
      if (hit) {
        if (hit.status !== "incomplete") root.unpin()
        else if (hit.title !== root.pinnedTitle) { root.pinnedTitle = hit.title; persistSettings() }
      } else {
        // Not a task — maybe a pinned checklist item.
        var itemHit = null
        for (var e2 = 0; e2 < everything.length && !itemHit; e2++) {
          var cl = (everything[e2] && everything[e2].checklist) || []
          for (var c2 = 0; c2 < cl.length; c2++)
            if (cl[c2] && cl[c2].id === root.pinnedId) { itemHit = cl[c2]; break }
        }
        if (itemHit) {
          if (itemHit.status !== "incomplete") root.unpin()
          else if (itemHit.title !== root.pinnedTitle) { root.pinnedTitle = itemHit.title; persistSettings() }
        } else {
          for (var d = 0; d < root.doneTasks.length; d++)
            if (root.doneTasks[d] && root.doneTasks[d].id === root.pinnedId) { root.unpin(); break }
        }
      }
    }
    // Re-point the drill-in at the refreshed project object (or back out if
    // the project finished/vanished server-side).
    if (root.activeProject) {
      var ap = null
      for (var i = 0; i < root.projects.length; i++)
        if (root.projects[i].id === root.activeProject.id) { ap = root.projects[i]; break }
      root.activeProject = ap
    }
  }

  // ---- Mark done / incomplete. Marked "completing" (struck through,
  //      dimmed) right away for feedback, then actually removed from the
  //      list a moment later — the CLI call itself fires immediately, only
  //      the row's disappearance is delayed. In the Done view the action
  //      flips to --incomplete so clicking a filled check un-does it.
  function requestToggle(taskId) {
    if (taskId === "" || root.completingTaskIds.indexOf(taskId) !== -1) return
    if (String(taskId).indexOf("pending-") === 0) return
    for (var q = 0; q < root.dropQueue.length; q++) if (root.dropQueue[q].id === taskId) return
    if (taskId === root.pinnedId && root.view !== "done") root.unpin()
    root.noteMutation()
    root.completingTaskIds = root.completingTaskIds.concat([taskId])
    root.pendingRemovalIds.push(taskId)
    root.actionQueue.push({ id: taskId, action: root.view === "done" ? "--incomplete" : "--done" })
    processActionQueue()
    completionRemovalTimer.restart()
  }

  function undoCompleting(taskId) {
    root.completingTaskIds = root.completingTaskIds.filter(function(id) { return id !== taskId })
    root.pendingRemovalIds = root.pendingRemovalIds.filter(function(id) { return id !== taskId })
  }

  // ---- Animated removal. Marking _removing collapses the row (TaskRow's
  //      reveal animation); the actual data drop happens once the collapse
  //      has played, so neighbors slide up instead of jumping.
  property var dropQueue: []

  // Timestamp of the last local mutation (mark/add/move/edit/delete). A
  // fetch snapshot taken BEFORE this moment is stale by construction — the
  // classic race: open-triggered full fetch is in flight, user un-checks a
  // task, old snapshot lands after the optimistic removal and resurrects
  // the row. Such results are discarded and refetched.
  property real lastMutationAt: 0

  function noteMutation() {
    root.lastMutationAt = Date.now()
  }

  function animateRemove(ids, mode) {
    var mark = function(t) {
      if (!t || ids.indexOf(t.id) === -1) return t
      var c = {}
      for (var k in t) c[k] = t[k]
      c._removing = true
      return c
    }
    root.preserveListScroll(function() {
      root.todayTasks = root.todayTasks.map(mark)
      root.doneTasks = root.doneTasks.map(mark)
      root.inboxTasks = root.inboxTasks.map(mark)
      var pt = {}
      for (var k in root.projectTasks) pt[k] = (root.projectTasks[k] || []).map(mark)
      root.projectTasks = pt
    })
    for (var i = 0; i < ids.length; i++) root.dropQueue.push({ id: ids[i], mode: mode })
    dropTimer.restart()
  }

  // ---- Entrance animation bookkeeping: ids that should slide in when
  //      their delegate is (re)created. Cleared shortly after so refreshes
  //      months later don't replay the entrance.
  property var recentlyAddedIds: []

  function noteAdded(ids) {
    root.recentlyAddedIds = root.recentlyAddedIds.concat(ids)
    newIdsTimer.restart()
  }

  function removeSynthetic(synthId) {
    if (!synthId) return
    var drop = function(t) { return !t || t.id !== synthId }
    root.preserveListScroll(function() {
      root.todayTasks = root.todayTasks.filter(drop)
      root.inboxTasks = root.inboxTasks.filter(drop)
      var pt = {}
      for (var k in root.projectTasks) pt[k] = (root.projectTasks[k] || []).filter(drop)
      root.projectTasks = pt
    })
  }

  function flushCompletedRemovals() {
    var ids = root.pendingRemovalIds
    root.pendingRemovalIds = []
    if (ids.length === 0) return
    root.animateRemove(ids, root.view === "done" ? "uncomplete" : "complete")
  }

  function processActionQueue() {
    if (actionProc.running || root.actionQueue.length === 0) return
    var item = root.actionQueue.shift()
    actionProc.pendingTaskId = item.id
    actionProc.command = ["timeout", "25", "things3", "mark", item.action, item.id]
    actionProc.running = true
  }

  function activateSelectedTask() {
    if (root.selectedTaskIndex < 0 || root.selectedTaskIndex >= root.tasks.length) return
    var task = root.tasks[root.selectedTaskIndex]
    if (!task) return
    if (root.projectListMode) { root.openProject(task); return }
    root.requestToggle(task.id)
  }

  function openProject(p) {
    if (!p) return
    root.activeProject = p
    root.selectedTaskIndex = -1
    root.taskCursorActive = false
  }

  function closeProject() {
    root.activeProject = null
    root.selectedTaskIndex = -1
    root.taskCursorActive = false
  }

  function moveTaskCursor(delta) {
    if (root.tasks.length === 0) return
    root.taskCursorActive = true
    var next = root.selectedTaskIndex + delta
    if (next < 0) next = 0
    if (next > root.tasks.length - 1) next = root.tasks.length - 1
    root.selectedTaskIndex = next
    Qt.callLater(function() {
      if (taskListView.count > 0) taskListView.positionViewAtIndex(root.selectedTaskIndex, ListView.Contain)
    })
  }

  // Match a typed #list against the loaded projects: exact title first, then
  // the first substring hit. "" means nothing matched — the preview flags it
  // with a "?" and the task still lands in the default container.
  function resolveDestId(name) {
    var n = Model.safeTrim(name).toLowerCase()
    if (n === "") return ""
    if (n === "inbox") return "inbox"
    var i
    for (i = 0; i < root.projects.length; i++)
      if (String(root.projects[i].title || "").toLowerCase() === n) return root.projects[i].id
    for (i = 0; i < root.projects.length; i++)
      if (String(root.projects[i].title || "").toLowerCase().indexOf(n) !== -1) return root.projects[i].id
    return ""
  }

  // ---- Quick add. An explicit @when in the line wins; failing that the
  //      Today view schedules for today and the other views drop it in the
  //      Inbox (the CLI's default container).
  function submitQuickAdd() {
    var parsed = root.quickAddParsed
    var title = Model.safeTrim(parsed.title)
    if (title === "" || root.quickAddSubmitting) return
    root.noteMutation()
    root.quickAddSubmitting = true
    root.errorText = ""

    // The row appears instantly at the top (entrance animation); the CLI
    // create + a reorder-to-top run behind it, and the next fast refresh
    // swaps the synthetic row for the real task in place.
    var synth = {
      id: "pending-" + Date.now(),
      title: title, status: "incomplete", type: "todo",
      start: { evening: false }, project: null, area: null,
      dates: {}, indexes: {}, flags: {}
    }
    createProc.pendingSynthId = synth.id
    // The RAW line, not the parsed title — a failed add restores exactly what
    // was typed, @dates and all, so nothing has to be retyped.
    createProc.pendingTitle = root.quickAddText
    createProc.pendingAnchorId = ""
    if (root.view === "today") {
      if (root.todayTasks.length > 0) createProc.pendingAnchorId = root.todayTasks[0].id
      root.preserveListScroll(function() { root.todayTasks = [synth].concat(root.todayTasks) })
    } else if (root.view === "projects" && root.activeProject) {
      var open = Model.shapeProjectTasks(root.projectTasks[root.activeProject.id])
      if (open.length > 0) createProc.pendingAnchorId = open[0].id
      var pt = {}
      for (var k in root.projectTasks) pt[k] = root.projectTasks[k]
      pt[root.activeProject.id] = [synth].concat(root.projectTasks[root.activeProject.id] || [])
      root.preserveListScroll(function() { root.projectTasks = pt })
    } else {
      if (root.inboxTasks.length > 0) createProc.pendingAnchorId = root.inboxTasks[0].id
      root.preserveListScroll(function() { root.inboxTasks = [synth].concat(root.inboxTasks) })
    }
    root.noteAdded([synth.id])

    var cmd = ["timeout", "25", "things3", "--no-color", "new", title]
    var when = parsed.when
    if (!when && root.view === "today") when = "today"
    if (when) cmd = cmd.concat(["--when", when])
    if (parsed.deadline) cmd = cmd.concat(["--deadline", parsed.deadline])
    if (parsed.tags) cmd = cmd.concat(["--tags", parsed.tags])
    if (parsed.notes) cmd = cmd.concat(["--notes", parsed.notes])
    var destId = root.resolveDestId(parsed.dest)
    if (destId !== "" && destId !== "inbox") cmd = cmd.concat(["-i", destId])
    else if (destId === "" && root.view === "projects" && root.activeProject)
      cmd = cmd.concat(["-i", root.activeProject.id])
    createProc.command = cmd
    createProc.running = true

    // Field clears right away so back-to-back adds feel instant.
    root.quickAddText = ""
    quickAddField.text = ""
  }

  // Hand off to the full app — things-tui in its own window — for everything
  // this panel deliberately doesn't show: the notes body, the checklist,
  // repeat rules. The panel gets out of the way on the way out.
  function openInApp() {
    Quickshell.execDetached(["uwsm-app", "--", "things.desktop"])
    root.close()
  }

  function openUrl(url) {
    if (Model.safeTrim(url) === "") return
    Quickshell.execDetached(["uwsm-app", "--", "xdg-open", url])
    root.close()
  }

  // ---- Inline title editing (e, or the row's pencil button).
  function startEditTask(index) {
    if (index < 0 || index >= root.tasks.length) return
    var task = root.tasks[index]
    if (!task || root.completingTaskIds.indexOf(task.id) !== -1) return
    if (task._removing || String(task.id).indexOf("pending-") === 0) return
    root.editDraft = task.title || ""
    root.editingTaskIndex = index
  }

  function cancelEditTask() {
    root.editingTaskIndex = -1
    root.editDraft = ""
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function commitEditTask() {
    var index = root.editingTaskIndex
    root.editingTaskIndex = -1
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
    if (index < 0 || index >= root.tasks.length) { root.editDraft = ""; return }
    var task = root.tasks[index]
    var newTitle = Model.safeTrim(root.editDraft)
    root.editDraft = ""
    if (!task || newTitle === "" || newTitle === task.title) return
    root.noteMutation()
    editProc.command = ["timeout", "25", "things3", "edit", task.id, "--title", newTitle]
    editProc.running = true
  }

  // ---- Delete (x, or the row's trash button). Confirmed first.
  function requestDeleteTask(index) {
    if (index < 0 || index >= root.tasks.length) return
    var task = root.tasks[index]
    if (!task || task._removing || String(task.id).indexOf("pending-") === 0) return
    root.pendingDeleteTaskId = task.id
    root.pendingDeleteTaskTitle = task.title || ""
    confirmDialog.selectedIndex = 0
    confirmDialog.opened = true
  }

  function cancelDeleteTask() {
    confirmDialog.opened = false
    root.pendingDeleteTaskId = ""
    root.pendingDeleteTaskTitle = ""
    // Hand the keyboard back to the panel — the dialog's focus item held it
    // while open, and nothing restores it automatically.
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function confirmDeleteTask() {
    confirmDialog.opened = false
    var taskId = root.pendingDeleteTaskId
    root.pendingDeleteTaskId = ""
    root.pendingDeleteTaskTitle = ""
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
    if (taskId === "") return
    if (taskId === root.pinnedId) root.unpin()
    root.noteMutation()
    root.animateRemove([taskId], "drop")
    deleteProc.command = ["timeout", "25", "things3", "delete", taskId]
    deleteProc.running = true
  }

  // ---- Picker (s / Ctrl+S for when, m / Ctrl+Shift+M for move).
  function openPicker(index, mode) {
    if (index < 0 || index >= root.tasks.length) return
    if (root.projectListMode) return
    var task = root.tasks[index]
    if (!task || task._removing || String(task.id).indexOf("pending-") === 0) return
    root.pickerMode = mode
    root.moveTaskId = task.id
    root.moveTaskTitle = task.title || ""
    root.moveCursor = 0
    root.pickerDateText = ""
    pickerDateField.text = ""
    root.moveOverlayOpen = true
  }

  function openMoveOverlay(index) {
    root.openPicker(index, "move")
  }

  function openWhenOverlay(index) {
    root.openPicker(index, "when")
  }

  function closeMoveOverlay() {
    root.moveOverlayOpen = false
    root.moveTaskId = ""
    root.moveTaskTitle = ""
    root.pickerDateText = ""
    pickerDateField.text = ""
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  // ---- Pinned "now" task: its full title rides in the bar pill. One pin
  //      at a time — pinning another task replaces it; completing or
  //      deleting the pinned task clears it. Persisted across restarts.
  property string pinnedId: ""
  property string pinnedTitle: ""

  // Checklist toggles in flight: the row draws the flipped state until the
  // reconcile refresh lands, then real data takes over.
  property var pendingChecklistIds: []

  function toggleChecklistItem(task, item) {
    if (!task || !item || String(task.id).indexOf("pending-") === 0) return
    if (checkItemProc.running) return
    if (root.pendingChecklistIds.indexOf(item.id) !== -1) return
    root.pendingChecklistIds = root.pendingChecklistIds.concat([item.id])
    root.noteMutation()
    checkItemProc.command = ["timeout", "25", "things3", "mark",
      item.status === "incomplete" ? "--check" : "--uncheck",
      item.short_id, task.id]
    checkItemProc.running = true
  }

  // A checklist item can ride in the bar exactly like a task — its uuid is
  // globally unique, so the one pinnedId slot serves both kinds.
  function pinChecklistItem(item) {
    if (!item) return
    if (root.pinnedId === item.id) { root.unpin(); return }
    root.pinnedId = item.id
    root.pinnedTitle = item.title || ""
    persistSettings()
  }

  function pinTask(task) {
    if (!task || String(task.id).indexOf("pending-") === 0) return
    root.pinnedId = task.id
    root.pinnedTitle = task.title || ""
    persistSettings()
  }

  function unpin() {
    root.pinnedId = ""
    root.pinnedTitle = ""
    persistSettings()
  }

  function togglePin(index) {
    if (index < 0 || index >= root.tasks.length) return
    var task = root.tasks[index]
    if (!task) return
    if (task.id === root.pinnedId) root.unpin()
    else root.pinTask(task)
  }

  // ---- Right-click context menu on a task row.
  property bool contextMenuOpen: false
  property int contextTaskIndex: -1
  property point contextMenuPos: Qt.point(0, 0)

  function openContextMenu(index, item, x, y) {
    if (index < 0 || index >= root.tasks.length) return
    root.contextTaskIndex = index
    root.contextMenuPos = item.mapToItem(keyCatcher, x, y)
    root.contextMenuOpen = true
  }

  function closeContextMenu() {
    root.contextMenuOpen = false
    root.contextTaskIndex = -1
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  readonly property var contextOptions: {
    var t = (root.contextTaskIndex >= 0 && root.contextTaskIndex < root.tasks.length)
      ? root.tasks[root.contextTaskIndex] : null
    var isPinned = t && t.id === root.pinnedId
    return [
      { icon: "\uf08d", label: isPinned ? "Unpin" : "Pin", action: "pin" },
      { icon: "\uf073", label: "When\u2026", action: "when" },
      { icon: "\uf064", label: "Move to\u2026", action: "move" },
      { icon: "\uf1f8", label: "Delete", action: "delete" }
    ]
  }

  function moveMoveCursor(delta) {
    if (root.pickerOptions.length === 0) return
    var next = root.moveCursor + delta
    if (next < 0) next = 0
    if (next > root.pickerOptions.length - 1) next = root.pickerOptions.length - 1
    root.moveCursor = next
    Qt.callLater(function() {
      if (moveListView.count > 0) moveListView.positionViewAtIndex(root.moveCursor, ListView.Contain)
    })
  }

  // Schedule the selected task straight from a key (Ctrl+T / Ctrl+E /
  // Ctrl+O), no picker in between — Things' own direct shortcuts.
  function scheduleSelected(value) {
    if (root.projectListMode) return
    if (root.selectedTaskIndex < 0 || root.selectedTaskIndex >= root.tasks.length) return
    var task = root.tasks[root.selectedTaskIndex]
    if (!task || task._removing || String(task.id).indexOf("pending-") === 0) return
    for (var i = 0; i < root.whenOptions.length; i++)
      if (root.whenOptions[i].value === value) { root.applyPickerOption(root.whenOptions[i], task.id); return }
  }

  // Enter in the picker's date field schedules straight to what was typed,
  // bypassing the five presets entirely.
  function applyTypedWhen() {
    var when = root.pickerTypedWhen
    if (!when) return
    root.applyPickerOption({ kind: "when", value: when, label: Model.prettyWhen(when) })
  }

  function applyPickerOption(option, taskIdOverride) {
    var taskId = taskIdOverride || root.moveTaskId
    if (!taskIdOverride) root.closeMoveOverlay()
    if (!option || taskId === "") return
    // Collapse the row right away when the move takes it out of this view —
    // the CLI catches up behind the animation.
    var leaves = false
    if (root.view === "today") leaves = !(option.kind === "when" && (option.value === "today" || option.value === "evening"))
    else if (root.view === "inbox") leaves = true
    else if (root.view === "projects" && root.activeProject) leaves = option.kind === "move" && option.value !== root.activeProject.id
    root.noteMutation()
    if (leaves) root.animateRemove([taskId], "drop")
    if (option.kind === "when") {
      var when = option.value === "tomorrow" ? Model.tomorrowIsoDate() : option.value
      moveProc.command = ["timeout", "25", "things3", "schedule", taskId, "-w", when]
    } else {
      moveProc.command = ["timeout", "25", "things3", "edit", taskId, "--move", option.value]
    }
    moveProc.running = true
  }

  // One body-text line's height — used to center each row's leading icon
  // (check circle / project pie) on the title's FIRST line, Things-style,
  // instead of top-aligning two boxes of different heights.
  FontMetrics {
    id: bodyMetrics
    font.family: root.contentFontFamily
    font.pixelSize: Style.font.body
  }

  Component.onCompleted: {
    ensureStateDir()
    Qt.callLater(function() { settingsFile.reload() })
  }

  // ---- Processes -----------------------------------------------------

  Process {
    id: mkdirProc
    command: ["mkdir", "-p", root.stateDir]
  }

  Process {
    id: fetchProc
    property bool wasFast: false
    property real startedAt: 0
    stdout: StdioCollector {
      id: fetchOut
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        var raw = String(text || "").trim()
        if (raw === "") return
        // Belt to the script's braces: if anything got past the per-stream
        // caps, refuse it here rather than hand it to JSON.parse.
        if (raw.length > root.maxPayloadBytes) {
          root.errorText = "things3 returned more data than the panel will read."
          return
        }
        // Stale snapshot: the user changed something after this fetch
        // began. Throw it away and let onExited refire a fresh fast one.
        if (fetchProc.startedAt < root.lastMutationAt) {
          root.refreshPending = true
          root.refreshPendingFast = true
          return
        }
        try {
          var parsed = JSON.parse(raw)
          root.applyFetched(
            Model.shapeToday(parsed.today, parsed.upcoming),
            Model.shapeUpcoming(parsed.upcoming),
            Model.shapeDone(parsed.done),
            Model.shapeInbox(parsed.inbox),
            (parsed.projects || []).filter(function(p) {
              return p && p.status === "incomplete" && !(p.flags && p.flags.trashed)
            }),
            fetchProc.wasFast ? root.projectTasks : (parsed.projectTasks || {}))
          root.errorText = ""
        } catch (e) {
          root.errorText = "Couldn't read the things3 response."
        }
      }
    }
    stderr: StdioCollector {
      id: fetchErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.loading = false
      if (exitCode !== 0) root.errorText = Model.errorMessageForExit(exitCode, fetchErr.text)
      if (root.refreshPending) {
        var f = root.refreshPendingFast
        Qt.callLater(function() { root.refresh(f) })
      }
    }
  }

  Process {
    id: actionProc
    // Which task this run is marking — lets the failure path undo the
    // optimistic strike-through for exactly that task.
    property string pendingTaskId: ""
    stderr: StdioCollector {
      id: actionErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.errorText = Model.errorMessageForExit(exitCode, actionErr.text)
        root.undoCompleting(actionProc.pendingTaskId)
        root.refresh(true)
      } else if (root.actionQueue.length === 0) {
        // Server now matches the optimistic state; the mark already synced
        // the cloud, so a cache-only reconcile is enough.
        root.refresh(true)
      }
      Qt.callLater(root.processActionQueue)
    }
  }

  Process {
    id: createProc
    property string pendingSynthId: ""
    property string pendingAnchorId: ""
    property string pendingTitle: ""
    stdout: StdioCollector {
      id: createOut
      waitForEnd: true
    }
    stderr: StdioCollector {
      id: createErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.quickAddSubmitting = false
      if (exitCode === 0) {
        // "Created <title>  <id>" — the id is the last token. Reorder the
        // real task to the top server-side so it stays where the optimistic
        // row already is (Things appends new tasks at the bottom).
        var match = String(createOut.text || "").trim().match(/([A-Za-z0-9]{16,})\s*$/)
        if (match && createProc.pendingAnchorId !== "") {
          reorderProc.command = ["timeout", "25", "things3", "reorder", match[1], "--before-id", createProc.pendingAnchorId]
          reorderProc.running = true
        } else {
          root.refresh(true)
        }
      } else {
        root.removeSynthetic(createProc.pendingSynthId)
        quickAddField.text = createProc.pendingTitle
        root.errorText = Model.errorMessageForExit(exitCode, createErr.text)
      }
    }
  }

  Process {
    id: reorderProc
    onExited: function(exitCode) {
      root.refresh(true)
    }
  }

  Process {
    id: editProc
    stderr: StdioCollector {
      id: editErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.errorText = Model.errorMessageForExit(exitCode, editErr.text)
      root.refresh(true)
    }
  }

  Process {
    id: deleteProc
    stderr: StdioCollector {
      id: deleteErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.errorText = Model.errorMessageForExit(exitCode, deleteErr.text)
      // Reconciles a failed delete's optimistic removal on failure, confirms
      // server-truth on success.
      root.refresh(true)
    }
  }

  Process {
    id: checkItemProc
    stderr: StdioCollector {
      id: checkItemErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.errorText = Model.errorMessageForExit(exitCode, checkItemErr.text)
      root.refresh(true)
    }
  }

  Process {
    id: moveProc
    stderr: StdioCollector {
      id: moveErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.errorText = Model.errorMessageForExit(exitCode, moveErr.text)
      root.refresh(true)
    }
  }

  Timer {
    id: completionRemovalTimer
    interval: 700
    repeat: false
    onTriggered: root.flushCompletedRemovals()
  }

  Timer {
    id: newIdsTimer
    interval: 900
    repeat: false
    onTriggered: root.recentlyAddedIds = []
  }

  Timer {
    id: dropTimer
    interval: 230
    repeat: false
    onTriggered: {
      var queue = root.dropQueue
      root.dropQueue = []
      var ids = queue.map(function(q) { return q.id })
      var modeOf = {}
      for (var i = 0; i < queue.length; i++) modeOf[queue[i].id] = queue[i].mode
      var drop = function(t) { return !t || ids.indexOf(t.id) === -1 }
      root.preserveListScroll(function() {
        root.todayTasks = root.todayTasks.filter(drop)
        root.doneTasks = root.doneTasks.filter(drop)
        root.inboxTasks = root.inboxTasks.filter(drop)
        // Completions flip status inside projectTasks (pie total stays
        // honest); moves/deletes drop the task from its project outright.
        var pt = {}
        for (var k in root.projectTasks) {
          pt[k] = (root.projectTasks[k] || []).filter(function(t) {
            return !t || ids.indexOf(t.id) === -1 || modeOf[t.id] !== "drop"
          }).map(function(t) {
            if (!t || ids.indexOf(t.id) === -1) return t
            var c = {}
            for (var kk in t) c[kk] = t[kk]
            delete c._removing
            if (modeOf[t.id] === "complete") c.status = "completed"
            else if (modeOf[t.id] === "uncomplete") c.status = "incomplete"
            return c
          })
        }
        root.projectTasks = pt
      })
      root.completingTaskIds = root.completingTaskIds.filter(function(id) { return ids.indexOf(id) === -1 })
    }
  }

  FileView {
    id: settingsFile
    path: root.settingsPath
    watchChanges: false
    atomicWrites: true
    printErrors: false
    onLoaded: root.loadSettingsFromText(text())
    onLoadFailed: root.loadSettingsFromText("")
  }

  // Two polling timers, mutually exclusive by root.opened. Background poll
  // keeps the bar count fresh while nobody's looking; the open poll is the
  // only one live while the panel's up. Every user action already refreshes
  // immediately — these only cover the gaps.
  Timer {
    id: backgroundRefreshTimer
    interval: 10 * 60 * 1000
    running: !root.opened
    repeat: true
    onTriggered: root.refresh()
  }

  Timer {
    id: openRefreshTimer
    interval: 2 * 60 * 1000
    running: root.opened
    repeat: true
    onTriggered: root.refresh()
  }

  // ---- One task row: check button + title + caption. ------------------
  component TaskRow: Item {
    id: row
    required property var task
    // Not named "index" — the delegate instantiating this also needs its own
    // ListView-injected "required property int index" (same DeviceRow/rowIndex
    // convention as the built-in bluetooth panel).
    required property int rowIndex
    property bool hasCursor: false

    readonly property bool completing: task ? root.completingTaskIds.indexOf(task.id) !== -1 : false
    readonly property bool doneRow: root.view === "done"
    readonly property string caption: Model.taskCaption(
      task, (root.activeProject || root.view === "today") ? "project" : root.view)
    // A task whose notes are just a pasted link is really a bookmark — the
    // snippet shows it as one, and the URL makes the line clickable.
    readonly property string notesLine: Model.notesSnippet(task)
    readonly property string notesUrl: Model.firstUrl(task)
    readonly property bool overdue: Model.deadlineOverdue(task)
    readonly property bool editing: root.editingTaskIndex === rowIndex
    // "Struck" = the state the row is animating toward: marking done strikes
    // it, un-marking (Done view) un-strikes it.
    readonly property bool struck: doneRow ? !completing : completing

    // Entrance/exit: reveal drives height+opacity, so a new row grows in
    // (pushing neighbors down) and a removed row collapses (neighbors slide
    // up). Delegates are recreated whenever the model array is replaced, so
    // Component.onCompleted has to start whichever animation applies.
    readonly property bool isNew: task ? root.recentlyAddedIds.indexOf(task.id) !== -1 : false
    readonly property bool removing: task ? task._removing === true : false
    property real reveal: 1
    onRemovingChanged: if (removing) hideAnim.restart()
    Component.onCompleted: {
      if (removing) hideAnim.restart()
      else if (isNew) { reveal = 0; showAnim.restart() }
    }

    NumberAnimation {
      id: showAnim
      target: row
      property: "reveal"
      from: 0
      to: 1
      duration: 220
      easing.type: Easing.OutCubic
    }

    NumberAnimation {
      id: hideAnim
      target: row
      property: "reveal"
      from: 1
      to: 0
      duration: 200
      easing.type: Easing.InCubic
    }

    // editField.text isn't kept bound to root.editDraft once the user has
    // typed in it (assigning to a QML property severs a declarative binding)
    // — re-sync explicitly whenever this row starts editing.
    onEditingChanged: {
      if (editing) {
        editField.text = root.editDraft
        Qt.callLater(function() {
          if (row.editing) { editField.forceActiveFocus(); editField.selectAll() }
        })
      }
    }

    readonly property real textTop: Style.spacing.sm + Math.max(0, (checkBtn.height - textColumn.implicitHeight) / 2)

    height: (Math.max(checkBtn.height, textColumn.implicitHeight) + Style.spacing.sm * 2) * reveal
    opacity: reveal
    clip: reveal < 1

    Rectangle {
      anchors.fill: parent
      anchors.topMargin: -Style.spacing.xs
      anchors.bottomMargin: -Style.spacing.xs
      anchors.leftMargin: 1
      anchors.rightMargin: 1
      radius: Style.cornerRadius
      visible: (row.hasCursor || rowHover.hovered) && reveal === 1
      color: Style.hoverFillFor(root.contentForeground, Color.accent)
    }

    // Dotted accent outline marks the pinned "now" task. Edges stay 1px
    // INSIDE the row so the ListView clip can't square the corners off.
    Canvas {
      id: pinOutline
      anchors.fill: parent
      anchors.topMargin: -Style.spacing.xs
      anchors.bottomMargin: -Style.spacing.xs
      anchors.leftMargin: 1
      anchors.rightMargin: 1
      visible: row.task && row.task.id === root.pinnedId && reveal === 1
      opacity: 0.9
      property color ringColor: Color.accent
      onRingColorChanged: requestPaint()
      onWidthChanged: requestPaint()
      onHeightChanged: requestPaint()
      onVisibleChanged: if (visible) requestPaint()

      function dashedLine(ctx, x1, y1, x2, y2) {
        var dx = x2 - x1, dy = y2 - y1
        var len = Math.sqrt(dx * dx + dy * dy)
        if (len <= 0) return
        var ux = dx / len, uy = dy / len
        var pos = 0
        while (pos < len) {
          var seg = Math.min(3, len - pos)
          ctx.moveTo(x1 + ux * pos, y1 + uy * pos)
          ctx.lineTo(x1 + ux * (pos + seg), y1 + uy * (pos + seg))
          pos += 6
        }
      }

      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        ctx.strokeStyle = ringColor
        ctx.lineWidth = 1
        var r = Style.cornerRadius
        var x = 0.5, y = 0.5, w = width - 1, h = height - 1
        ctx.beginPath()
        dashedLine(ctx, x + r, y, x + w - r, y)
        dashedLine(ctx, x + w, y + r, x + w, y + h - r)
        dashedLine(ctx, x + w - r, y + h, x + r, y + h)
        dashedLine(ctx, x, y + h - r, x, y + r)
        ctx.moveTo(x, y + r)
        ctx.arc(x + r, y + r, r, Math.PI, 1.5 * Math.PI)
        ctx.moveTo(x + w - r, y)
        ctx.arc(x + w - r, y + r, r, 1.5 * Math.PI, 2 * Math.PI)
        ctx.moveTo(x + w, y + h - r)
        ctx.arc(x + w - r, y + h - r, r, 0, 0.5 * Math.PI)
        ctx.moveTo(x + r, y + h)
        ctx.arc(x + r, y + h - r, r, 0.5 * Math.PI, Math.PI)
        ctx.stroke()
      }
    }

    HoverHandler {
      id: rowHover
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onClicked: function(mouse) {
        root.selectedTaskIndex = row.rowIndex
        root.taskCursorActive = true
        if (mouse.button === Qt.RightButton) root.openContextMenu(row.rowIndex, row, mouse.x, mouse.y)
      }
      onDoubleClicked: function(mouse) {
        if (mouse.button === Qt.LeftButton) root.startEditTask(row.rowIndex)
      }
    }

    PanelActionButton {
      id: checkBtn
      anchors.left: parent.left
      anchors.leftMargin: Style.spacing.sm + Style.spacing.xs + 1
      anchors.top: parent.top
      anchors.topMargin: row.textTop + (bodyMetrics.height - checkBtn.height) / 2 + 1
      iconText: row.struck ? "●" : "○"
      tooltipText: row.doneRow ? "Mark incomplete (Space)" : "Mark complete (Space)"
      foreground: root.contentForeground
      enabled: !row.completing
      onClicked: root.requestToggle(row.task ? row.task.id : "")
    }

    Column {
      id: textColumn
      anchors.left: checkBtn.right
      anchors.leftMargin: Style.spacing.sm
      anchors.right: parent.right
      anchors.rightMargin: Style.spacing.sm + Style.spacing.xs + 1
      anchors.top: parent.top
      anchors.topMargin: row.textTop
      spacing: 2

      Text {
        visible: !row.editing
        height: visible ? implicitHeight : 0
        width: parent.width
        text: row.task ? row.task.title : ""
        opacity: row.struck ? 0.5 : 1.0
        font.strikeout: row.struck
        color: root.contentForeground
        wrapMode: Text.WordWrap
        // Titles are not length-limited in Things, and a pasted paragraph
        // turns one row into a wall that buries every other to-do and pushes
        // the row actions out of reach. Three lines, then elide — "e" still
        // shows the whole thing, and "o" opens it in the full app.
        maximumLineCount: 3
        elide: Text.ElideRight
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.body
      }

      TextField {
        id: editField
        visible: row.editing
        height: visible ? implicitHeight : 0
        width: parent.width
        onTextChanged: if (row.editing) root.editDraft = text
        onAccepted: root.commitEditTask()
        Keys.onEscapePressed: root.cancelEditTask()
      }

      // One faint line of the notes, so a task that is really a link or a
      // stashed thought says so without being opened. Kept to a single
      // elided line — "O" is the way to the full text.
      Text {
        visible: row.notesLine !== "" && !row.editing
        width: parent.width
        text: row.notesLine
        elide: Text.ElideRight
        maximumLineCount: 1
        opacity: row.struck ? 0.4 : 0.85
        color: row.notesUrl !== "" ? Color.accent : Qt.darker(root.contentForeground, 1.6)
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.caption
        font.underline: row.notesUrl !== "" && notesHover.hovered

        HoverHandler {
          id: notesHover
          enabled: row.notesUrl !== ""
          cursorShape: Qt.PointingHandCursor
        }

        // Opening the link is the whole point of surfacing it — this sits
        // above the row's click-to-edit handler so it wins the click.
        MouseArea {
          anchors.fill: parent
          enabled: row.notesUrl !== ""
          onClicked: root.openUrl(row.notesUrl)
        }
      }

      Text {
        visible: row.caption !== "" && !row.editing
        height: visible ? implicitHeight : 0
        width: parent.width
        text: row.caption
        color: row.overdue ? Color.urgent : Qt.darker(root.contentForeground, 1.5)
        wrapMode: Text.WordWrap
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.caption
      }

      // ---- Checklist items, indented under the title like Things' own
      //      subtasks. The circle checks one off; the pin appears on hover
      //      and rides the item's title in the bar, exactly like a task.
      Repeater {
        model: (row.task && !row.editing) ? (row.task.checklist || []) : []

        Item {
          id: clRow
          required property var modelData
          readonly property bool clStruck:
            (modelData.status !== "incomplete") !== (root.pendingChecklistIds.indexOf(modelData.id) !== -1)
          readonly property bool clPinned: modelData.id === root.pinnedId
          width: parent.width
          height: clText.implicitHeight + Style.spacing.xs

          HoverHandler { id: clHover }

          Text {
            id: clCircle
            anchors.left: parent.left
            anchors.leftMargin: Style.spacing.sm
            anchors.verticalCenter: parent.verticalCenter
            text: clRow.clStruck ? "●" : "○"
            color: Qt.darker(root.contentForeground, 1.4)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption

            MouseArea {
              anchors.fill: parent
              anchors.margins: -Style.spacing.xs
              onClicked: root.toggleChecklistItem(row.task, clRow.modelData)
            }
          }

          Text {
            id: clText
            anchors.left: clCircle.right
            anchors.leftMargin: Style.spacing.sm
            anchors.right: clPin.visible ? clPin.left : parent.right
            anchors.rightMargin: Style.spacing.sm
            anchors.verticalCenter: parent.verticalCenter
            text: clRow.modelData.title || ""
            elide: Text.ElideRight
            maximumLineCount: 1
            opacity: clRow.clStruck ? 0.45 : 0.8
            font.strikeout: clRow.clStruck
            color: Qt.darker(root.contentForeground, 1.25)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Text {
            id: clPin
            visible: clHover.hovered || clRow.clPinned
            anchors.right: parent.right
            anchors.rightMargin: Style.spacing.sm
            anchors.verticalCenter: parent.verticalCenter
            text: "\uf08d"
            color: clRow.clPinned ? Color.accent : Qt.darker(root.contentForeground, 1.4)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption

            MouseArea {
              anchors.fill: parent
              anchors.margins: -Style.spacing.xs
              onClicked: root.pinChecklistItem(clRow.modelData)
            }
          }
        }
      }
    }

  }

  // ---- One project row: pie progress + title + done/total caption. ----
  component ProjectRow: Item {
    id: prow
    required property var project
    required property int rowIndex
    property bool hasCursor: false

    readonly property var stats: Model.projectStats(root.projectTasks[project.id])
    readonly property string caption: {
      var parts = [stats.done + "/" + stats.total]
      if (project && project.area && project.area.title) parts.push(project.area.title)
      return parts.join("  \u00b7  ")
    }

    height: Math.max(pie.height, ptextColumn.implicitHeight) + Style.spacing.sm * 2

    Rectangle {
      anchors.fill: parent
      anchors.topMargin: -Style.spacing.xs
      anchors.bottomMargin: -Style.spacing.xs
      anchors.leftMargin: 1
      anchors.rightMargin: 1
      radius: Style.cornerRadius
      visible: prow.hasCursor || prowHover.hovered
      color: Style.hoverFillFor(root.contentForeground, Color.accent)
    }

    HoverHandler { id: prowHover }

    MouseArea {
      anchors.fill: parent
      onClicked: root.openProject(prow.project)
    }

    // Things-style circular progress: outline ring, pie wedge fills as the
    // project's done/total fraction grows.
    Canvas {
      id: pie
      width: 20
      height: 20
      anchors.left: parent.left
      anchors.leftMargin: Style.spacing.sm + Style.spacing.xs + 6
      anchors.top: parent.top
      anchors.topMargin: Style.spacing.sm + (bodyMetrics.height - pie.height) / 2 + 1
      property real fraction: prow.stats.total > 0 ? prow.stats.done / prow.stats.total : 0
      property color ringColor: Color.accent
      onFractionChanged: requestPaint()
      onRingColorChanged: requestPaint()
      onVisibleChanged: if (visible) requestPaint()
      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        var c = width / 2
        var r = c - 1.5
        ctx.strokeStyle = ringColor
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.arc(c, c, r, 0, 2 * Math.PI)
        ctx.stroke()
        if (fraction > 0) {
          ctx.fillStyle = ringColor
          ctx.beginPath()
          ctx.moveTo(c, c)
          ctx.arc(c, c, r - 2.5, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * Math.min(1, fraction), false)
          ctx.closePath()
          ctx.fill()
        }
      }
    }

    Column {
      id: ptextColumn
      anchors.left: pie.right
      anchors.leftMargin: Style.spacing.sm + 2
      anchors.right: parent.right
      anchors.rightMargin: Style.spacing.sm + Style.spacing.xs + 1
      anchors.top: parent.top
      anchors.topMargin: Style.spacing.sm
      spacing: 2

      Text {
        width: parent.width
        text: prow.project ? prow.project.title : ""
        color: root.contentForeground
        wrapMode: Text.WordWrap
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        text: prow.caption
        color: Qt.darker(root.contentForeground, 1.5)
        wrapMode: Text.WordWrap
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  component FastScroll: Item {
    id: fs
    required property var flickable
    property real speedMultiplier: 3.5
    property real mouseWheelStep: Math.max(1,
      Number(Application.styleHints.wheelScrollLines) || 3) * 24

    x: 0
    y: 0
    width: flickable.width
    height: flickable.height
    z: 1000

    function boundedContentY(value) {
      var minimum = Number(flickable.originY) || 0
      var maximum = Math.max(minimum,
        minimum + Math.max(0, Number(flickable.contentHeight) || 0)
          - Math.max(0, Number(flickable.height) || 0))
      return Math.max(minimum, Math.min(maximum, value))
    }

    WheelHandler {
      target: null
      acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
      blocking: true

      onWheel: function(event) {
        var distance = Number(event.pixelDelta.y) || 0
        if (distance === 0)
          distance = (Number(event.angleDelta.y) || 0) / 120 * fs.mouseWheelStep
        distance *= fs.speedMultiplier
        // Decline when there's nothing meaningful to scroll — this overlay
        // can sit ABOVE a nested list's own handler (the outer panel
        // flickable stacks over the task list), and accepting here for a
        // 1-2px travel range would eat the wheel the list needs.
        var range = (Number(fs.flickable.contentHeight) || 0) - (Number(fs.flickable.height) || 0)
        if (distance === 0 || !fs.flickable.interactive || range <= 4) {
          event.accepted = false
          return
        }
        var previous = fs.flickable.contentY
        fs.flickable.cancelFlick()
        fs.flickable.contentY = fs.boundedContentY(fs.flickable.contentY - distance)
        event.accepted = Math.abs(fs.flickable.contentY - previous) > 0.01
      }
    }
  }

  // ---- Chrome ----------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    // Drops right below the bar icon, not centered on the whole bar.
    centerOnBar: false
    focusTarget: keyCatcher
    // Fixed width, but height hugs the content (same pattern as the
    // built-in weather/power panels) so a short list doesn't leave a block
    // of dead padding at the bottom. The cap keeps long lists scrolling
    // inside the ListView instead of growing the card off-screen.
    contentWidth: panel.fittedContentWidth(root.panelWidth)
    contentHeight: panel.fittedContentHeight(mainColumn.implicitHeight, Style.space(520))
    padding: Style.space(20)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      clip: true
      blocked: quickAddField.activeFocus || root.editingTaskIndex !== -1 || confirmDialog.opened || root.moveOverlayOpen || root.contextMenuOpen
      onCloseRequested: {
        if (root.helpOpen) root.helpOpen = false
        else if (root.view === "projects" && root.activeProject) root.closeProject()
        else root.close()
      }
      // Tab cycles the views, same order as the pill's middle-click.
      onTabRequested: function(direction) {
        if (root.helpOpen) return
        if (direction === 1) {
          // Plain Tab hops into the typing box; Shift+Tab cycles views.
          quickAddField.forceActiveFocus()
          return
        }
        root.cycleView(1)
      }
      onMoveRequested: function(dx, dy) {
        if (root.helpOpen) return
        if (dy !== 0) root.moveTaskCursor(dy)
      }
      // Enter fires returnRequested then activateRequested back-to-back for
      // the same keypress — acting only on activateRequested makes Enter and
      // Space both mean "toggle the selected task" without double-firing
      // (requestToggle's completingTaskIds guard covers the race anyway).
      onActivateRequested: {
        if (root.helpOpen) { root.helpOpen = false; return }
        root.activateSelectedTask()
      }
      // "x" is this shell's established delete shortcut (see
      // Ui/PanelKeyCatcher.qml).
      onDeleteRequested: {
        if (root.helpOpen) return
        root.requestDeleteTask(root.selectedTaskIndex)
      }
      onTextKey: function(t) {
        if (root.helpOpen) { root.helpOpen = false; return }
        if (t === "?") { root.helpOpen = true; return }
        if (t === "P") { root.togglePin(root.selectedTaskIndex); return }
        if (t === "r" || t === "R") { root.refresh(); return }
        if (t === "q" || t === "Q") { quickAddField.forceActiveFocus(); return }
        if (t === "e" || t === "E") { root.startEditTask(root.selectedTaskIndex); return }
        if (t === "m" || t === "M") { root.openMoveOverlay(root.selectedTaskIndex); return }
        if (t === "s" || t === "S") { root.openWhenOverlay(root.selectedTaskIndex); return }
        if (t === "t" || t === "T") { root.selectView("today"); return }
        if (t === "d" || t === "D") { root.selectView("done"); return }
        if (t === "o" || t === "O") { root.openInApp(); return }
        if (t === "u" || t === "U") { root.selectView("upcoming"); return }
        if (t === "p") { root.activeProject = null; root.selectView("projects"); return }
        if (t === "i" || t === "I") root.selectView("inbox")
      }

      // ---- Things' own shortcuts, Cmd → Ctrl. Kept identical to the Mac
      //      app so muscle memory carries over: ⌘S When, ⌘⇧M Move,
      //      ⌘T Today, ⌘E This Evening, ⌘O Someday, ⌘K complete,
      //      ⌘N new to-do.
      Shortcut {
        sequence: "Ctrl+S"
        enabled: root.taskShortcutsEnabled
        onActivated: root.openWhenOverlay(root.selectedTaskIndex)
      }

      Shortcut {
        sequence: "Ctrl+Shift+M"
        enabled: root.taskShortcutsEnabled
        onActivated: root.openMoveOverlay(root.selectedTaskIndex)
      }

      Shortcut {
        sequence: "Ctrl+T"
        enabled: root.taskShortcutsEnabled
        onActivated: root.scheduleSelected("today")
      }

      Shortcut {
        sequence: "Ctrl+E"
        enabled: root.taskShortcutsEnabled
        onActivated: root.scheduleSelected("evening")
      }

      Shortcut {
        sequence: "Ctrl+O"
        enabled: root.taskShortcutsEnabled
        onActivated: root.scheduleSelected("someday")
      }

      Shortcut {
        sequence: "Ctrl+K"
        enabled: root.taskShortcutsEnabled
        onActivated: root.activateSelectedTask()
      }

      // The one that stays live while typing — it only moves focus.
      Shortcut {
        sequence: "Ctrl+N"
        enabled: root.opened && !root.helpOpen && !root.moveOverlayOpen
          && !root.contextMenuOpen && !confirmDialog.opened && root.editingTaskIndex === -1
        onActivated: quickAddField.forceActiveFocus()
      }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: mainColumn
          width: parent.width
          spacing: Style.spacing.lg

          // ---- Header -------------------------------------------------
          Item {
            width: parent.width
            height: titleColumn.implicitHeight

            Column {
              id: titleColumn
              anchors.left: parent.left
              anchors.top: parent.top
              anchors.right: actionsRow.left
              anchors.rightMargin: Style.spacing.sm
              spacing: 2

              Row {
                spacing: Style.spacing.xs

                Text {
                  text: root.viewIcon
                  color: Color.accent
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.title
                }

                Text {
                  text: "Things"
                  font.bold: true
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.title
                  color: root.contentForeground
                }
              }

              Text {
                width: parent.width
                text: root.headerSubtitle
                elide: Text.ElideRight
                color: Qt.darker(root.contentForeground, 1.4)
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Row {
              id: actionsRow
              anchors.right: parent.right
              anchors.verticalCenter: titleColumn.verticalCenter
              spacing: Style.spacing.sm

              PanelActionButton {
                iconText: "\uf060"
                tooltipText: "Back to projects (Esc)"
                visible: root.view === "projects" && !!root.activeProject
                foreground: root.contentForeground
                onClicked: root.closeProject()
              }

              PanelActionButton {
                iconText: "\uf11c"
                tooltipText: "Keyboard shortcuts (?)"
                foreground: root.contentForeground
                onClicked: root.helpOpen = !root.helpOpen
              }

              PanelActionButton {
                iconText: ""
                tooltipText: root.loading ? "Refreshing…" : "Refresh (r)"
                foreground: root.contentForeground
                enabled: !root.loading
                onClicked: root.refresh()
              }
            }
          }

          PanelSeparator {
            foreground: root.contentForeground
          }

          // ---- Task list ----------------------------------------------
          Column {
            id: taskColumn
            width: parent.width
            spacing: Style.spacing.md

            Row {
              width: parent.width
              clip: true
              spacing: Style.spacing.sm

              TextField {
                id: quickAddField
                width: parent.width - addButton.width - Style.spacing.sm
                placeholderText: root.view === "today" ? "Add to Today…"
                  : (root.view === "projects" && root.activeProject) ? ("Add to " + root.activeProject.title + "…")
                  : "Add to Inbox…"
                text: root.quickAddText
                onTextChanged: root.quickAddText = text
                onAccepted: root.submitQuickAdd()
                // Escape just leaves the field (back to normal keyboard nav)
                // rather than falling through to the panel's own Escape,
                // which would otherwise do nothing while blocked.
                Keys.onEscapePressed: keyCatcher.forceActiveFocus()
                // Plain Tab hops from the typing box to the list cursor;
                // Shift+Tab cycles the views (works while typing). Needs the
                // raw Keys.onPressed form: TextInput consumes Tab natively
                // before the convenience handlers ever fire.
                Keys.onPressed: function(event) {
                  if (event.key === Qt.Key_Tab) {
                    if (root.tasks.length > 0) {
                      if (root.selectedTaskIndex < 0) root.selectedTaskIndex = 0
                      root.taskCursorActive = true
                    }
                    keyCatcher.forceActiveFocus()
                    event.accepted = true
                  } else if (event.key === Qt.Key_Backtab) {
                    root.cycleView(1)
                    event.accepted = true
                  }
                }
              }

              Button {
                id: addButton
                text: root.quickAddSubmitting ? "Adding…" : "Add"
                enabled: !root.quickAddSubmitting && Model.safeTrim(root.quickAddParsed.title) !== ""
                onClicked: root.submitQuickAdd()
              }
            }

            // What the entry grammar made of the line. Hidden until it finds
            // something, so a plain title reads exactly as it did before.
            Text {
              visible: root.quickAddPreview !== ""
              width: parent.width
              text: root.quickAddPreview
              elide: Text.ElideRight
              color: Qt.darker(root.contentForeground, 1.3)
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
            }

            // Segmented-control look: zero spacing between the Buttons,
            // differentiated only by the selected-state fill. Five tabs only
            // fit in 340px because just the selected one spells its name out;
            // the rest are their icon alone, and the header right above
            // already says which view you are in.
            Row {
              width: parent.width
              clip: true
              spacing: 0

              Repeater {
                model: root.viewTabs

                Button {
                  required property var modelData
                  readonly property bool isCurrent: root.view === modelData.view
                  text: isCurrent ? (modelData.icon + "  " + modelData.label) : modelData.icon
                  selected: isCurrent
                  tooltipText: modelData.label
                  onClicked: {
                    if (modelData.view === "projects") root.activeProject = null
                    root.selectView(modelData.view)
                  }
                }
              }
            }

            PanelSeparator {
              foreground: root.contentForeground
            }

            ListView {
              id: taskListView
              width: parent.width
              height: Math.min(contentHeight, Style.space(300))
              // Row highlights bleed Style.spacing.xs past the row on both
              // edges (see the TaskRow backdrop). The slack has to be real
              // content, not Flickable margins: it must land inside this
              // list's clip AND inside contentHeight, so the outer scroll
              // reserves room for it too. Otherwise the first and last rows
              // get their rounded highlight sliced flat.
              header: Item { width: 1; height: Style.spacing.xs }
              footer: Item { width: 1; height: Style.spacing.xs }
              spacing: Style.spacing.sm
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              interactive: contentHeight > height
              model: root.tasks
              currentIndex: root.selectedTaskIndex

              FastScroll { parent: taskListView; flickable: taskListView }

              delegate: Item {
                id: delegateItem
                required property var modelData
                required property int index
                readonly property string sectionTitle: root.taskSectionTitle(index)
                readonly property string groupTitle: root.taskGroupTitle(index)
                width: taskListView.width
                height: delegateColumn.implicitHeight

                Column {
                  id: delegateColumn
                  width: parent.width
                  spacing: Style.spacing.sm

                  PanelSeparator {
                    visible: delegateItem.index > 0 && delegateItem.sectionTitle !== ""
                    height: visible ? implicitHeight : 0
                    foreground: root.contentForeground
                  }

                  PanelSectionHeader {
                    visible: delegateItem.sectionTitle !== ""
                    height: visible ? implicitHeight : 0
                    text: delegateItem.sectionTitle
                    foreground: root.contentForeground
                    fontFamily: root.contentFontFamily
                  }

                  PanelSectionHeader {
                    visible: delegateItem.groupTitle !== ""
                    height: visible ? implicitHeight : 0
                    text: delegateItem.groupTitle
                    foreground: Qt.darker(root.contentForeground, 1.35)
                    fontFamily: root.contentFontFamily
                  }

                  TaskRow {
                    visible: !root.projectListMode
                    width: parent.width
                    task: delegateItem.modelData
                    rowIndex: delegateItem.index
                    hasCursor: root.taskCursorActive && delegateItem.index === root.selectedTaskIndex
                  }

                  ProjectRow {
                    visible: root.projectListMode
                    width: parent.width
                    project: delegateItem.modelData
                    rowIndex: delegateItem.index
                    hasCursor: root.taskCursorActive && delegateItem.index === root.selectedTaskIndex
                  }
                }
              }
            }

            Text {
              // Only shown for the initial fetch on an empty list — a
              // background refresh of an already-populated list stays quiet.
              visible: root.loading && root.tasks.length === 0
              height: visible ? implicitHeight : 0
              width: parent.width
              text: "Loading…"
              color: Qt.darker(root.contentForeground, 1.3)
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              visible: !root.loading && root.tasks.length === 0 && root.errorText === ""
              height: visible ? implicitHeight : 0
              width: parent.width
              text: root.emptyStateMessage
              color: Qt.darker(root.contentForeground, 1.3)
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              visible: root.errorText !== ""
              height: visible ? implicitHeight : 0
              width: parent.width
              text: root.errorText
              // errorText can carry a line of CLI stderr. AutoText would let
              // that be sniffed as rich text and rendered as markup, so pin it
              // to PlainText.
              textFormat: Text.PlainText
              color: Color.urgent
              wrapMode: Text.WordWrap
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }
        }
      }

      // Overlays, declared after the Flickable so they paint on top of it.
      ConfirmDialog {
        id: confirmDialog
        anchors.fill: parent
        message: "Delete \"" + Model.ellipsize(root.pendingDeleteTaskTitle, 72) + "\"?"
        confirmText: "Delete"
        background: Color.popups.background
        foreground: root.contentForeground
        onCanceled: root.cancelDeleteTask()
        onConfirmed: root.confirmDeleteTask()

        Item {
          anchors.fill: parent
          focus: confirmDialog.opened
          Keys.onPressed: function(event) {
            confirmDialog.handleKey(event)
            event.accepted = true
          }
        }
      }

      // Right-click context menu: a small card at the pointer.
      Item {
        id: contextOverlay
        anchors.fill: parent
        visible: root.contextMenuOpen

        // Click-away closes (either button).
        MouseArea {
          anchors.fill: parent
          acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
          onClicked: root.closeContextMenu()
        }

        BorderSurface {
          id: contextCard
          x: Math.max(0, Math.min(root.contextMenuPos.x, parent.width - width))
          y: Math.max(0, Math.min(root.contextMenuPos.y, parent.height - height))
          width: Style.space(150)
          height: contextCard.contentTopInset + contextCard.contentBottomInset + contextColumn.implicitHeight
          color: Color.popups.background
          borderSpec: Border.flat(Color.accent, Style.normalBorderWidth)
          radius: Style.cornerRadius
          padding: Style.space(6)

          MouseArea {
            anchors.fill: parent
            onClicked: {}
          }

          Item {
            anchors.fill: parent
            anchors.topMargin: contextCard.contentTopInset
            anchors.rightMargin: contextCard.contentRightInset
            anchors.bottomMargin: contextCard.contentBottomInset
            anchors.leftMargin: contextCard.contentLeftInset

            Column {
              id: contextColumn
              width: parent.width

              Repeater {
                model: root.contextOptions

                delegate: Item {
                  id: ctxRow
                  required property var modelData
                  width: contextColumn.width
                  height: ctxText.implicitHeight + Style.spacing.sm * 2

                  Rectangle {
                    anchors.fill: parent
                    radius: Style.cornerRadius
                    visible: ctxHover.hovered
                    color: Style.hoverFillFor(root.contentForeground, Color.accent)
                  }

                  HoverHandler { id: ctxHover }

                  MouseArea {
                    anchors.fill: parent
                    onClicked: {
                      var index = root.contextTaskIndex
                      root.closeContextMenu()
                      if (ctxRow.modelData.action === "pin") root.togglePin(index)
                      else if (ctxRow.modelData.action === "when") root.openWhenOverlay(index)
                      else if (ctxRow.modelData.action === "move") root.openMoveOverlay(index)
                      else root.requestDeleteTask(index)
                    }
                  }

                  Text {
                    id: ctxText
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: ctxRow.modelData.icon + "  " + ctxRow.modelData.label
                    elide: Text.ElideRight
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.body
                  }
                }
              }
            }
          }
        }

        Item {
          anchors.fill: parent
          focus: root.contextMenuOpen
          Keys.onEscapePressed: root.closeContextMenu()
        }
      }

      // "Move to\u2026" picker: schedule targets first, then every project.
      Item {
        id: moveOverlay
        anchors.fill: parent
        visible: root.moveOverlayOpen

        Rectangle {
          anchors.fill: parent
          color: Util.alpha(Color.background, 0.75)

          MouseArea {
            anchors.fill: parent
            onClicked: root.closeMoveOverlay()
          }

          BorderSurface {
            id: moveCard
            anchors.centerIn: parent
            width: Math.min(parent.width - Style.space(24), Style.space(280))
            height: Math.min(parent.height - Style.space(24),
              moveCard.contentTopInset + moveCard.contentBottomInset + moveColumn.implicitHeight + Style.space(8))
            color: Color.popups.background
            borderSpec: Border.flat(Color.accent, Style.normalBorderWidth)
            radius: Style.cornerRadius
            padding: Style.space(12)

            MouseArea {
              anchors.fill: parent
              onClicked: {}
            }

            Item {
              anchors.fill: parent
              anchors.topMargin: moveCard.contentTopInset
              anchors.rightMargin: moveCard.contentRightInset
              anchors.bottomMargin: moveCard.contentBottomInset
              anchors.leftMargin: moveCard.contentLeftInset

              Column {
                id: moveColumn
                width: parent.width
                spacing: Style.spacing.sm

                Text {
                  width: parent.width
                  text: root.pickerTitle
                  elide: Text.ElideRight
                  font.bold: true
                  color: root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.body
                }

                ListView {
                  id: moveListView
                  width: parent.width
                  height: Math.min(contentHeight, Style.space(260))
                  // Dimmed while a typed date owns Enter.
                  opacity: root.pickerTyping ? 0.45 : 1.0
                  clip: true
                  boundsBehavior: Flickable.StopAtBounds
                  interactive: contentHeight > height
                  model: root.pickerOptions
                  currentIndex: root.moveCursor

                  FastScroll { parent: moveListView; flickable: moveListView }

                  delegate: Item {
                    id: moveDelegate
                    required property var modelData
                    required property int index
                    width: moveListView.width
                    height: moveText.implicitHeight + Style.spacing.sm * 2

                    Rectangle {
                      anchors.fill: parent
                      radius: Style.cornerRadius
                      visible: (moveDelegate.index === root.moveCursor && !root.pickerTyping)
                        || moveHover.hovered
                      color: Style.hoverFillFor(root.contentForeground, Color.accent)
                    }

                    HoverHandler { id: moveHover }

                    MouseArea {
                      anchors.fill: parent
                      onClicked: root.applyPickerOption(moveDelegate.modelData)
                    }

                    Text {
                      id: moveText
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: moveDelegate.modelData.icon + "  " + moveDelegate.modelData.label
                      elide: Text.ElideRight
                      color: root.contentForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.body
                    }
                  }
                }

                // Anything the five presets don't cover: "next sat",
                // "aug 24", "12/25", "2026-09-01". When? flavor only — the
                // Move picker has no use for a date.
                TextField {
                  id: pickerDateField
                  visible: root.pickerMode === "when"
                  width: parent.width
                  placeholderText: "or type a date…"
                  onTextChanged: root.pickerDateText = text
                  onAccepted: root.applyTypedWhen()
                  // First Escape abandons the typed date and hands the
                  // presets back; a second one closes the picker.
                  Keys.onEscapePressed: {
                    if (root.pickerTyping) {
                      text = ""
                      pickerKeys.forceActiveFocus()
                    } else {
                      root.closeMoveOverlay()
                    }
                  }
                }

                Text {
                  visible: root.pickerMode === "when" && root.pickerDatePreview !== ""
                  width: parent.width
                  text: root.pickerDatePreview
                  elide: Text.ElideRight
                  color: root.pickerTypedWhen ? Color.accent
                    : Qt.darker(root.contentForeground, 1.3)
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }
            }
          }
        }

        Item {
          id: pickerKeys
          anchors.fill: parent
          focus: root.moveOverlayOpen
          Keys.onEscapePressed: root.closeMoveOverlay()
          Keys.onPressed: function(event) {
            if (event.key === Qt.Key_Down || (event.text === "j")) { root.moveMoveCursor(1); event.accepted = true }
            else if (event.key === Qt.Key_Up || (event.text === "k")) { root.moveMoveCursor(-1); event.accepted = true }
            else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
              if (root.pickerTypedWhen) root.applyTypedWhen()
              else root.applyPickerOption(root.pickerOptions[root.moveCursor])
              event.accepted = true
            }
            // Anything else printable starts typing a date. j/k stay list
            // navigation, so a month beginning with one wants either the day
            // first ("24 jan") or a Tab into the field.
            else if (root.pickerMode === "when"
                     && (event.key === Qt.Key_Tab
                         || (event.text.length === 1 && event.text >= " " && event.text <= "~"))) {
              if (event.key !== Qt.Key_Tab) pickerDateField.text += event.text
              pickerDateField.cursorPosition = pickerDateField.text.length
              pickerDateField.forceActiveFocus()
              event.accepted = true
            }
          }
        }
      }

      // Shortcuts help, toggled by "?" or the header's keyboard button.
      // Declared last so it paints above everything.
      Item {
        id: helpOverlay
        anchors.fill: parent
        visible: root.helpOpen

        Rectangle {
          anchors.fill: parent
          color: Util.alpha(Color.background, 0.75)

          MouseArea {
            anchors.fill: parent
            onClicked: root.helpOpen = false
          }

          BorderSurface {
            id: helpCard
            anchors.centerIn: parent
            width: Math.min(parent.width - Style.space(24), Style.space(320))
            height: Math.min(parent.height - Style.space(24),
              helpCard.contentTopInset + helpCard.contentBottomInset + helpColumn.implicitHeight + Style.space(8))
            color: Color.popups.background
            borderSpec: Border.flat(Color.accent, Style.normalBorderWidth)
            radius: Style.cornerRadius
            padding: Style.space(16)

            MouseArea {
              anchors.fill: parent
              onClicked: {}
            }

            Item {
              anchors.fill: parent
              anchors.topMargin: helpCard.contentTopInset
              anchors.rightMargin: helpCard.contentRightInset
              anchors.bottomMargin: helpCard.contentBottomInset
              anchors.leftMargin: helpCard.contentLeftInset

              Flickable {
                anchors.fill: parent
                contentWidth: width
                contentHeight: helpColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                interactive: contentHeight > height

                Column {
                  id: helpColumn
                  width: parent.width
                  spacing: Style.spacing.sm

                  Text {
                    text: "Keyboard shortcuts"
                    font.bold: true
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.title
                  }

                  Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.bodySmall
                    text: "Tab — switch between typing box and list\n"
                      + "Shift+Tab — cycle views (works while typing)\n"
                      + "t / u / d / i / p — Today / Upcoming / Done / Inbox / Projects\n"
                      + "\u2191/\u2193 or k/j — move selection\n"
                      + "Space or Enter — check off (Done view: un-check)\n"
                      + "Enter on a project — open it (Esc backs out)\n"
                      + "e — rename (or double-click)\n"
                      + "s — when\u2026 (schedule)    m — move to\u2026\n"
                      + "     in either picker: \u2191/\u2193 or k/j, Enter picks, Esc closes\n"
                      + "     in when\u2026: type a date — \"next sat\", \"aug 24\", \"12/25\"\n"
                      + "o — open the full app (notes, checklist, repeats)\n"
                      + "x — delete (or right-click)\n"
                      + "Shift+P — pin/unpin as \"now\" (or right-click)\n"
                      + "q — quick add\n"
                      + "r — refresh\n"
                      + "Escape — back / close\n"
                      + "? — toggle this help\n\n"
                      + "Things-style (⌘ → Ctrl):\n"
                      + "Ctrl+S — When\u2026    Ctrl+Shift+M — Move to\u2026\n"
                      + "Ctrl+T — Today    Ctrl+E — This Evening\n"
                      + "Ctrl+O — Someday    Ctrl+K — complete\n"
                      + "Ctrl+N — new to-do\n\n"
                      + "Bar pill: middle-click cycles views, right-click syncs.\n"
                      + "Super+Shift+T toggles this panel from anywhere."
                  }
                }
              }
            }
          }

        }
      }
    }
  }
}
