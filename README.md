# Things — Omarchy bar widget

[Things 3](https://culturedcode.com/things/) in your Omarchy bar: task counts
at a glance, a full panel to work the list, and a "now" task pinned right in
the bar — all synced with Things Cloud, so it's the same data as your Mac and
iPhone apps.

## Features

- **Five views** — Today ★, Upcoming 🗓, Done today ✓, Inbox, and Projects ○
  with Things-style pie progress per project (click a project to drill into
  its open tasks). Upcoming groups by the day each task actually falls on
  ("Tomorrow", "Mon Aug 24"). The selected tab shows its name, the rest are
  icons, so all five fit.
- **Bar pill** — shows the current view's icon + count. Left-click opens the
  panel, middle-click cycles views, right-click forces a cloud sync.
- **Work the list** — check tasks off (un-check from Done), quick-add (goes
  to Today, the Inbox, or the open project), rename inline, move between
  Today / This Evening / Tomorrow / Anytime / Someday / Inbox / any project,
  delete with confirmation.
- **Type a date** — the When… picker takes a typed date as well as the five
  presets: `saturday`, `this fri`, `next sat`, `next week`, `in 3 days`,
  `aug 24`, `12/25`, `2026-09-01`. A live preview spells out the result
  ("Enter → Sat Aug 29") before you commit it.
- **Quick-add grammar** — one line carries the lot: `buy milk @next saturday
  #groceries +errand !aug 24 -- some notes`. `@` sets when, `!` or `--due`
  the deadline, `#` the project or area, `+` a tag. A preview under the field
  shows what it parsed.
- **Notes at a glance** — a task with notes shows one faint line of them
  under the title. A pasted link renders as its host and path and opens in
  your browser when clicked.
- **Pin a "now" task** — right-click → Pin puts the task's title in the bar
  (`▶ …`, ellipsized). One pin at a time; finishing or deleting the task
  clears it. The pinned row wears a dotted outline in the list.
- **Instant + smooth** — every action is optimistic: new tasks slide in at
  the top (and are reordered to the top server-side), completed/moved/deleted
  rows collapse in place, and background syncs reconcile without ever
  touching your scroll position.
- **Full keyboard control** — see the `?` help card in the panel. Highlights:
  arrows/`j·k` move, `Space` checks off, `Tab` hops between quick-add and the
  list, `Shift+Tab` cycles views, `t`/`u`/`d`/`i`/`p` jump straight to
  Today / Upcoming / Done / Inbox / Projects, `s`/`m` schedule and move,
  `e`/`x` rename and delete, `Shift+P` pins, `o` opens the full app for
  anything the panel deliberately leaves out (notes body, checklists).

## Requirements

- [things3-cloud](https://github.com/evanpurkhiser/things3-cloud) CLI
  (`yay -S things3-cloud`), authenticated with `things3 set-auth`.

  > **Heads up:** as of 0.8.3 the CLI only understands the `Task6` wire
  > entity. Newer Things clients also emit `Task7` — an identical property
  > set, just a version bump — and the CLI silently discards those records.
  > New to-dos and completions made on those devices never appear. Everything
  > reading through the CLI is affected, this plugin included.
- `jq` (for the per-project progress fetch).
- A Nerd Font in the bar (Omarchy's default setup qualifies).

## Install

```sh
git clone <this repo> ~/.config/omarchy/plugins/reysu.things
omarchy restart shell
```

Then add the **Things** widget to your bar (right-click the bar → add
widget), or add `{ "id": "reysu.things" }` to `bar.layout` in
`~/.config/omarchy/shell.json`.

Optional Hyprland keybind to toggle the panel from anywhere:

```lua
o.bind("SUPER + SHIFT + T", "Things tasks", "qs -p /usr/share/omarchy/shell ipc call reysu.things toggle")
```

## How it syncs

Fetches run `things3` against Things Cloud: a full refresh (panel open +
every 10 minutes) syncs and rebuilds everything including project progress;
action reconciles are lighter syncs. All mutations go through the CLI
(`new`, `mark`, `edit`, `schedule`, `delete`, `reorder`), so changes show up
on your other devices immediately.

State (active view, pinned task) lives in
`~/.local/state/omarchy/reysu.things/settings.json`.

## License

MIT
