# Changelog

All notable changes to this project will be documented in this file.

## 2026-08-27

### Added

- `things3-cloud/src/commands/headings.rs` - Added native Things Cloud heading listing, creation, positioning, and renaming commands.
- `things3-cloud/trycmd/headings/workflow.in/journal.json` - Added a deterministic project-heading fixture.
- `things3-cloud/trycmd/headings/workflow.trycmd` - Added CLI snapshots for heading reads, writes, renames, and task assignment.

### Modified

- `things-tui/README.md` - Documented heading creation, rendering, destinations, and cross-device sync.
- `things-tui/things_tui/app.py` - Added project heading creation, empty-heading rendering, and heading-aware task destinations.
- `things-tui/things_tui/backend.py` - Added heading CLI wrappers and merged real heading records into project views.
- `things-tui/things_tui/modals.py` - Added the project heading shortcut to in-app help.
- `things3-cloud/README.md` - Documented heading commands and task placement.
- `things3-cloud/src/commands/edit.rs` - Allowed moving existing to-dos into headings.
- `things3-cloud/src/commands/mod.rs` - Registered the headings command group.
- `things3-cloud/src/commands/new.rs` - Allowed creating to-dos directly inside headings.
- `things3-cloud/tests/cli_trycmd.rs` - Registered heading workflow snapshots.

## 2026-08-26

### Added

- `CHANGELOG.md` - Added project change tracking.

### Modified

- `README.md` - Updated the bundled-fork rationale for upstream 0.9.0 and legacy account compatibility.
- `things3-cloud/Cargo.toml` - Updated the vendored CLI to upstream version 0.9.0.
- `things3-cloud/Cargo.lock` - Refreshed the dependency lockfile for version 0.9.0.
- `things3-cloud/src/client.rs` - Integrated upstream updates while preserving bounded cloud responses and sync budgets.
- `things3-cloud/src/commands/delete.rs` - Integrated upstream Task7-compatible deletion behavior.
- `things3-cloud/src/commands/edit.rs` - Integrated upstream Task7-compatible editing behavior.
- `things3-cloud/src/commands/find.rs` - Integrated upstream task-family filtering updates.
- `things3-cloud/src/commands/mark.rs` - Integrated upstream Task7 status changes and repeater safeguards.
- `things3-cloud/src/commands/new.rs` - Integrated upstream Task7 task creation behavior.
- `things3-cloud/src/commands/projects.rs` - Integrated upstream Task7 project writes.
- `things3-cloud/src/commands/reorder.rs` - Integrated upstream Task7 reorder behavior.
- `things3-cloud/src/commands/schedule.rs` - Integrated upstream Task7 scheduling and repeater safeguards.
- `things3-cloud/src/commands/today.rs` - Integrated upstream task-family handling.
- `things3-cloud/src/ids/things_id.rs` - Added support for historical `ACTIONGROUP-<UUID>` identifiers and regression tests.
- `things3-cloud/src/log_cache.rs` - Preserved bounded append-log syncing, added actionable parse diagnostics, and tested legacy action-group replay.
- `things3-cloud/src/store/entities.rs` - Integrated upstream entity and recurrence state updates.
- `things3-cloud/src/store/mod.rs` - Integrated upstream Task7 and future task-family materialization.
- `things3-cloud/src/store/state.rs` - Integrated upstream patch folding for Task7, recurrence, checklist, note, and nullable-field updates.
- `things3-cloud/src/ui/components/progress_badge.rs` - Integrated upstream project progress rendering fix.
- `things3-cloud/src/wire/checklist.rs` - Integrated upstream checklist stop-date compatibility.
- `things3-cloud/src/wire/notes.rs` - Integrated upstream structured-note and UTF-8 delta handling.
- `things3-cloud/src/wire/recurrence.rs` - Integrated upstream recovered recurrence wire fields.
- `things3-cloud/src/wire/task.rs` - Integrated upstream Task7, recurrence, and nullable task fields.
- `things3-cloud/src/wire/wire_object.rs` - Integrated upstream Task7 and future task-family recognition.
- `things3-cloud/src/wire_tests.rs` - Integrated upstream wire compatibility coverage.
- `things3-cloud/tests/cli_trycmd.rs` - Updated CLI test binary discovery.
- `things3-cloud/trycmd/delete/multiple.trycmd` - Updated expected Task7 delete payloads.
- `things3-cloud/trycmd/delete/single.trycmd` - Updated expected Task7 delete payloads.
- `things3-cloud/trycmd/mark/done_single.trycmd` - Updated expected Task7 status payloads.
- `things3-cloud/trycmd/new/after_gap.trycmd` - Updated expected Task7 creation payloads.
- `things3-cloud/trycmd/new/bare_create.trycmd` - Updated expected Task7 creation payloads.
- `things3-cloud/trycmd/new/when_today.trycmd` - Updated expected Task7 creation payloads.
- `things3-cloud/trycmd/project/grouped_with_progress_counts.trycmd` - Updated expected project progress counts.
- `things3-cloud/trycmd/projects/new_compatible_payload.trycmd` - Updated expected Task7 project payloads.
- `things3-cloud/trycmd/reorder/before.trycmd` - Updated expected Task7 reorder payloads.
- `things3-cloud/trycmd/reorder/rebalance_payload_and_ancestors.trycmd` - Updated expected Task7 rebalance payloads.
- `things3-cloud/trycmd/schedule/deadline.trycmd` - Updated expected Task7 scheduling payloads.
- `things3-cloud/trycmd/schedule/when_today.trycmd` - Updated expected Task7 scheduling payloads.
