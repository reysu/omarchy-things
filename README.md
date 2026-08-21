# Things — Omarchy bar widget

[Things 3](https://culturedcode.com/things/) in your Omarchy bar: task counts
at a glance, a full panel to work the list, and a "now" task pinned right in
the bar — all synced with Things Cloud, so it's the same data as your Mac and
iPhone apps.

## Features

- **Four views** — Today ★, Done today ✓, Inbox, and Projects ○ with
  Things-style pie progress per project (click a project to drill into its
  open tasks).
- **Bar pill** — shows the current view's icon + count. Left-click opens the
  panel, middle-click cycles views, right-click forces a cloud sync.
- **Work the list** — check tasks off (un-check from Done), quick-add (goes
  to Today, the Inbox, or the open project), rename inline, move between
  Today / This Evening / Tomorrow / Anytime / Someday / Inbox / any project,
  delete with confirmation.
- **Pin a "now" task** — right-click → Pin puts the task's title in the bar
  (`▶ …`, ellipsized). One pin at a time; finishing or deleting the task
  clears it. The pinned row wears a dotted outline in the list.
- **Instant + smooth** — every action is optimistic: new tasks slide in at
  the top (and are reordered to the top server-side), completed/moved/deleted
  rows collapse in place, and background syncs reconcile without ever
  touching your scroll position.
- **Full keyboard control** — see the `?` help card in the panel. Highlights:
  arrows/`j·k` move, `Space` checks off, `Tab` hops between quick-add and the
  list, `Shift+Tab` cycles views, `e`/`m`/`x` rename/move/delete, `Shift+P`
  pins.

## Requirements

- [things3-cloud](https://github.com/evanpurkhiser/things3-cloud) CLI
  (`yay -S things3-cloud`), authenticated with `things3 set-auth`.
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
