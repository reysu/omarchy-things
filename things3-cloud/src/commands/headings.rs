use std::collections::BTreeMap;

use anyhow::Result;
use clap::{Args, Subcommand};
use serde::Serialize;
use serde_json::json;

use crate::{
    app::Cli,
    commands::{Command, write_json},
    common::{DIM, GREEN, ICONS, colored, task6_note},
    ids::ThingsId,
    wire::{
        task::{TaskPatch, TaskProps, TaskStart, TaskStatus, TaskType},
        wire_object::{EntityType, WireObject},
    },
};

#[derive(Debug, Subcommand)]
pub enum HeadingsSubcommand {
    #[command(about = "Show headings in a project")]
    List(HeadingsListArgs),
    #[command(about = "Create a heading in a project")]
    New(HeadingsNewArgs),
    #[command(about = "Rename a heading")]
    Edit(HeadingsEditArgs),
}

#[derive(Debug, Args)]
#[command(about = "Show, create, or edit project headings")]
pub struct HeadingsArgs {
    #[command(subcommand)]
    pub command: HeadingsSubcommand,
}

#[derive(Debug, Args)]
pub struct HeadingsListArgs {
    /// Project UUID (or unique UUID prefix)
    pub project_id: String,
}

#[derive(Debug, Args)]
pub struct HeadingsNewArgs {
    /// Heading title
    pub title: String,
    #[arg(
        long = "project",
        short = 'p',
        help = "Project UUID/prefix that will contain the heading"
    )]
    pub project: String,
    #[arg(
        long = "before",
        short = 'b',
        conflicts_with = "after_id",
        help = "Insert before this heading UUID/prefix"
    )]
    pub before_id: Option<String>,
    #[arg(
        long = "after",
        short = 'a',
        conflicts_with = "before_id",
        help = "Insert after this heading UUID/prefix"
    )]
    pub after_id: Option<String>,
}

#[derive(Debug, Args)]
pub struct HeadingsEditArgs {
    /// Heading UUID (or unique UUID prefix)
    pub heading_id: String,
    #[arg(long, short = 't', help = "Replace title")]
    pub title: String,
}

#[derive(Debug, Serialize)]
struct HeadingProjectJson {
    id: String,
    short_id: String,
    title: String,
}

#[derive(Debug, Serialize)]
struct HeadingJson {
    id: String,
    short_id: String,
    title: String,
    r#type: &'static str,
    index: i32,
    project: HeadingProjectJson,
}

#[derive(Debug, Clone)]
struct HeadingsNewPlan {
    new_uuid: String,
    title: String,
    changes: BTreeMap<String, WireObject>,
}

fn resolve_project(
    store: &crate::store::ThingsStore,
    identifier: &str,
) -> std::result::Result<crate::store::Task, String> {
    let (project, err, _) = store.resolve_mark_identifier(identifier);
    let Some(project) = project else {
        return Err(err);
    };
    if !project.is_project() {
        return Err("The specified ID is not a project.".to_string());
    }
    if project.trashed {
        return Err("Cannot use a trashed project.".to_string());
    }
    Ok(project)
}

fn resolve_heading(
    store: &crate::store::ThingsStore,
    identifier: &str,
) -> std::result::Result<crate::store::Task, String> {
    let (heading, err, _) = store.resolve_task_identifier(identifier);
    let Some(heading) = heading else {
        return Err(err);
    };
    if !heading.is_heading() {
        return Err("The specified ID is not a heading.".to_string());
    }
    if heading.trashed {
        return Err("Cannot use a trashed heading.".to_string());
    }
    if !heading.entity.can_upgrade_to_task7() {
        return Err(format!(
            "Unsupported heading entity for cloud writes: {}",
            heading.entity
        ));
    }
    Ok(heading)
}

fn project_headings(
    store: &crate::store::ThingsStore,
    project_id: &ThingsId,
) -> Vec<crate::store::Task> {
    let mut headings = store
        .tasks_by_uuid
        .values()
        .filter(|task| {
            task.is_heading()
                && !task.trashed
                && task.status == TaskStatus::Incomplete
                && task.project.as_ref() == Some(project_id)
        })
        .cloned()
        .collect::<Vec<_>>();
    headings.sort_by_key(|heading| (heading.index, heading.uuid.clone()));
    headings
}

fn plan_ix_insert(ordered: &[crate::store::Task], insert_at: usize) -> (i32, Vec<(String, i32)>) {
    let prev_ix = insert_at
        .checked_sub(1)
        .and_then(|index| ordered.get(index))
        .map(|task| task.index);
    let next_ix = ordered.get(insert_at).map(|task| task.index);

    match (prev_ix, next_ix) {
        (None, None) => return (0, Vec::new()),
        (None, Some(next)) => return (next - 1, Vec::new()),
        (Some(prev), None) => return (prev + 1, Vec::new()),
        (Some(prev), Some(next)) if prev + 1 < next => {
            return ((prev + next) / 2, Vec::new());
        }
        _ => {}
    }

    let stride = 1024;
    let mut new_index = stride;
    let mut target_slot = 1;
    let mut updates = Vec::new();
    for position in 0..=ordered.len() {
        let target_index = target_slot * stride;
        if position == insert_at {
            new_index = target_index;
            target_slot += 1;
            continue;
        }
        let source_position = if position < insert_at {
            position
        } else {
            position - 1
        };
        if let Some(heading) = ordered.get(source_position) {
            if heading.index != target_index {
                updates.push((heading.uuid.to_string(), target_index));
            }
            target_slot += 1;
        }
    }
    (new_index, updates)
}

fn build_new_plan(
    args: &HeadingsNewArgs,
    store: &crate::store::ThingsStore,
    now: f64,
    next_id: &mut dyn FnMut() -> String,
) -> std::result::Result<HeadingsNewPlan, String> {
    let title = args.title.trim();
    if title.is_empty() {
        return Err("Heading title cannot be empty.".to_string());
    }

    let project = resolve_project(store, args.project.trim())?;
    let headings = project_headings(store, &project.uuid);
    let anchor_id = args.before_id.as_ref().or(args.after_id.as_ref());
    let insert_at = if let Some(anchor_id) = anchor_id {
        let anchor = resolve_heading(store, anchor_id)?;
        if anchor.project.as_ref() != Some(&project.uuid) {
            return Err("The anchor heading belongs to a different project.".to_string());
        }
        let Some(anchor_position) = headings
            .iter()
            .position(|heading| heading.uuid == anchor.uuid)
        else {
            return Err("Anchor heading not found in the project.".to_string());
        };
        if args.before_id.is_some() {
            anchor_position
        } else {
            anchor_position + 1
        }
    } else {
        headings.len()
    };

    let (sort_index, index_updates) = plan_ix_insert(&headings, insert_at);
    let new_uuid = next_id();
    let props = TaskProps {
        title: title.to_string(),
        notes: Some(task6_note("")),
        item_type: TaskType::Heading,
        status: TaskStatus::Incomplete,
        start_location: TaskStart::Anytime,
        parent_project_ids: vec![project.uuid],
        sort_index,
        conflict_overrides: Some(json!({"_t": "oo", "sn": {}})),
        creation_date: Some(now),
        modification_date: Some(now),
        ..Default::default()
    };

    let mut changes = BTreeMap::new();
    changes.insert(
        new_uuid.clone(),
        WireObject::create(EntityType::Task7, props),
    );
    for (heading_uuid, index) in index_updates {
        changes.insert(
            heading_uuid,
            WireObject::update(
                EntityType::Task7,
                TaskPatch {
                    sort_index: Some(index),
                    modification_date: Some(Some(now)),
                    ..Default::default()
                },
            ),
        );
    }

    Ok(HeadingsNewPlan {
        new_uuid,
        title: title.to_string(),
        changes,
    })
}

fn build_edit_plan(
    args: &HeadingsEditArgs,
    store: &crate::store::ThingsStore,
    now: f64,
) -> std::result::Result<(crate::store::Task, TaskPatch), String> {
    let heading = resolve_heading(store, args.heading_id.trim())?;
    let title = args.title.trim();
    if title.is_empty() {
        return Err("Heading title cannot be empty.".to_string());
    }
    Ok((
        heading,
        TaskPatch {
            title: Some(title.to_string()),
            modification_date: Some(Some(now)),
            ..Default::default()
        },
    ))
}

impl Command for HeadingsArgs {
    fn run_with_ctx(
        &self,
        cli: &Cli,
        out: &mut dyn std::io::Write,
        ctx: &mut dyn crate::cmd_ctx::CmdCtx,
    ) -> Result<()> {
        match &self.command {
            HeadingsSubcommand::List(args) => {
                let store = cli.load_store()?;
                let project = match resolve_project(&store, args.project_id.trim()) {
                    Ok(project) => project,
                    Err(err) => {
                        eprintln!("{err}");
                        return Ok(());
                    }
                };
                let headings = project_headings(&store, &project.uuid);
                if cli.json {
                    let json = headings
                        .iter()
                        .map(|heading| HeadingJson {
                            id: heading.uuid.to_string(),
                            short_id: store.short_id(&heading.uuid),
                            title: heading.title.clone(),
                            r#type: "heading",
                            index: heading.index,
                            project: HeadingProjectJson {
                                id: project.uuid.to_string(),
                                short_id: store.short_id(&project.uuid),
                                title: project.title.clone(),
                            },
                        })
                        .collect::<Vec<_>>();
                    write_json(out, &json)?;
                    return Ok(());
                }

                for heading in headings {
                    writeln!(
                        out,
                        "{}  {}",
                        heading.title,
                        colored(store.short_id(&heading.uuid), &[DIM], cli.no_color)
                    )?;
                }
            }
            HeadingsSubcommand::New(args) => {
                let store = cli.load_store()?;
                let now = ctx.now_timestamp();
                let mut id_gen = || ctx.next_id();
                let plan = match build_new_plan(args, &store, now, &mut id_gen) {
                    Ok(plan) => plan,
                    Err(err) => {
                        eprintln!("{err}");
                        return Ok(());
                    }
                };
                if let Err(err) = ctx.commit_changes(plan.changes, None) {
                    eprintln!("Failed to create heading: {err}");
                    return Ok(());
                }
                writeln!(
                    out,
                    "{} {}  {}",
                    colored(format!("{} Created", ICONS.done), &[GREEN], cli.no_color),
                    plan.title,
                    colored(&plan.new_uuid, &[DIM], cli.no_color)
                )?;
            }
            HeadingsSubcommand::Edit(args) => {
                let store = cli.load_store()?;
                let (heading, update) = match build_edit_plan(args, &store, ctx.now_timestamp()) {
                    Ok(plan) => plan,
                    Err(err) => {
                        eprintln!("{err}");
                        return Ok(());
                    }
                };
                let mut changes = BTreeMap::new();
                changes.insert(
                    heading.uuid.to_string(),
                    WireObject::update(EntityType::Task7, update),
                );
                if let Err(err) = ctx.commit_changes(changes, None) {
                    eprintln!("Failed to edit heading: {err}");
                    return Ok(());
                }
                writeln!(
                    out,
                    "{} {}  {}",
                    colored(format!("{} Edited", ICONS.done), &[GREEN], cli.no_color),
                    args.title.trim(),
                    colored(&heading.uuid, &[DIM], cli.no_color)
                )?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        store::{ThingsStore, fold_items},
        wire::wire_object::{OperationType, WireItem},
    };

    const NOW: f64 = 1_700_000_222.0;
    const PROJECT_UUID: &str = "KGvAPpMrzHAKMdgMiERP1V";
    const HEADING_A_UUID: &str = "A7h5eCi24RvAWKC3Hv3muf";
    const HEADING_B_UUID: &str = "3H9jsMx3kYMrQ4M7DReSRn";
    const NEW_UUID: &str = "MpkEei6ybkFS2n6SXvwfLf";

    fn item(uuid: &str, title: &str, item_type: TaskType, index: i32) -> (String, WireObject) {
        (
            uuid.to_string(),
            WireObject::create(
                EntityType::Task7,
                TaskProps {
                    title: title.to_string(),
                    item_type,
                    status: TaskStatus::Incomplete,
                    start_location: TaskStart::Anytime,
                    parent_project_ids: if item_type == TaskType::Heading {
                        vec![PROJECT_UUID.parse().expect("project id")]
                    } else {
                        vec![]
                    },
                    sort_index: index,
                    creation_date: Some(1.0),
                    modification_date: Some(1.0),
                    ..Default::default()
                },
            ),
        )
    }

    fn store() -> ThingsStore {
        let mut entries: WireItem = BTreeMap::new();
        for (uuid, object) in [
            item(PROJECT_UUID, "Roadmap", TaskType::Project, 0),
            item(HEADING_A_UUID, "Design", TaskType::Heading, 10),
            item(HEADING_B_UUID, "Build", TaskType::Heading, 20),
        ] {
            entries.insert(uuid, object);
        }
        ThingsStore::from_raw_state(&fold_items([entries]))
    }

    #[test]
    fn new_heading_matches_things_task7_shape() {
        let mut id_gen = || NEW_UUID.to_string();
        let plan = build_new_plan(
            &HeadingsNewArgs {
                title: "Launch".to_string(),
                project: PROJECT_UUID.to_string(),
                before_id: None,
                after_id: None,
            },
            &store(),
            NOW,
            &mut id_gen,
        )
        .expect("new heading plan");
        let create = plan.changes.get(NEW_UUID).expect("heading create");
        assert_eq!(create.operation_type, OperationType::Create);
        assert_eq!(create.entity_type, Some(EntityType::Task7));
        let props = create.properties_map();
        assert_eq!(props.get("tp"), Some(&json!(2)));
        assert_eq!(props.get("tt"), Some(&json!("Launch")));
        assert_eq!(props.get("pr"), Some(&json!([PROJECT_UUID])));
        assert_eq!(props.get("agr"), Some(&json!([])));
        assert_eq!(props.get("st"), Some(&json!(1)));
        assert_eq!(props.get("ix"), Some(&json!(21)));
        assert_eq!(props.get("md"), Some(&json!(NOW)));
    }

    #[test]
    fn new_heading_can_insert_before_an_existing_heading() {
        let mut id_gen = || NEW_UUID.to_string();
        let plan = build_new_plan(
            &HeadingsNewArgs {
                title: "Research".to_string(),
                project: PROJECT_UUID.to_string(),
                before_id: Some(HEADING_B_UUID.to_string()),
                after_id: None,
            },
            &store(),
            NOW,
            &mut id_gen,
        )
        .expect("insert heading plan");
        let props = plan
            .changes
            .get(NEW_UUID)
            .expect("heading")
            .properties_map();
        assert_eq!(props.get("ix"), Some(&json!(15)));
    }

    #[test]
    fn heading_edit_only_patches_title_and_modified_date() {
        let (heading, patch) = build_edit_plan(
            &HeadingsEditArgs {
                heading_id: HEADING_A_UUID.to_string(),
                title: "Discovery".to_string(),
            },
            &store(),
            NOW,
        )
        .expect("edit heading plan");
        assert_eq!(heading.uuid.to_string(), HEADING_A_UUID);
        assert_eq!(
            patch.into_properties(),
            BTreeMap::from([
                ("md".to_string(), json!(NOW)),
                ("tt".to_string(), json!("Discovery")),
            ])
        );
    }
}
