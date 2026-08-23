use std::{
    collections::BTreeMap,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, anyhow};
use reqwest::blocking::Client;
use serde_json::{Value, json};
use urlencoding::encode;

use crate::{
    store::{RawState, fold_item},
    wire::{
        task::{TaskPatch, TaskStatus},
        wire_object::{EntityType, WireItem, WireObject},
    },
};

const BASE_URL: &str = "https://cloud.culturedcode.com/version/1";
const USER_AGENT: &str = "ThingsMac/32209501";
const CLIENT_INFO: &str = "eyJkbSI6Ik1hYzE0LDIiLCJsciI6IlVTIiwibmYiOnRydWUsIm5rIjp0cnVlLCJubiI6IlRoaW5nc01hYyIsIm52IjoiMzIyMDk1MDEiLCJvbiI6Im1hY09TIiwib3YiOiIyNi4zLjAiLCJwbCI6ImVuLVVTIiwidWwiOiJlbi1MYXRuLVVTIn0=";
const APP_ID: &str = "com.culturedcode.ThingsMac";
const SCHEMA: &str = "301";
const WRITE_PUSH_PRIORITY: &str = "10";

/// Ceiling on a single response body. Things serves history in pages of a few
/// hundred KB, so this is headroom rather than a target.
const MAX_BODY_BYTES: u64 = 8 * 1024 * 1024;

// Aggregate ceilings for one pagination run. Bounding each response is not
// enough on its own: a peer can keep answering with non-empty pages while
// still reporting `end < latest`, which walks the offset forward forever and
// grows RawState without limit. Every axis of the run is therefore capped, and
// the server additionally has to move its own progress counter.
const MAX_SYNC_PAGES: u32 = 5_000;
const MAX_SYNC_ITEMS: u64 = 2_000_000;
const MAX_SYNC_BYTES: u64 = 512 * 1024 * 1024;
const MAX_SYNC_SECS: u64 = 600;

/// Spend limit for a single paginated sync, shared by the in-memory and
/// append-log paths so both are bounded identically.
#[derive(Debug)]
pub struct SyncBudget {
    pages: u32,
    items: u64,
    bytes: u64,
    started: std::time::Instant,
    last_end: Option<i64>,
}

impl Default for SyncBudget {
    fn default() -> Self {
        Self::new()
    }
}

impl SyncBudget {
    pub fn new() -> Self {
        Self {
            pages: 0,
            items: 0,
            bytes: 0,
            started: std::time::Instant::now(),
            last_end: None,
        }
    }

    /// Charge one page against the run's ceilings.
    ///
    /// `end` is the server's own `end-total-content-size`. When a page carries
    /// items that counter has to advance: if it stalls or moves backwards the
    /// peer is handing us content while claiming it delivered none, which is
    /// exactly the shape that would otherwise loop forever.
    pub fn charge(&mut self, items: usize, bytes: u64, end: i64) -> Result<()> {
        self.pages += 1;
        self.items += items as u64;
        self.bytes += bytes;

        if self.pages > MAX_SYNC_PAGES {
            return Err(anyhow!("sync exceeded {MAX_SYNC_PAGES} pages"));
        }
        if self.items > MAX_SYNC_ITEMS {
            return Err(anyhow!("sync exceeded {MAX_SYNC_ITEMS} items"));
        }
        if self.bytes > MAX_SYNC_BYTES {
            return Err(anyhow!("sync exceeded {MAX_SYNC_BYTES} bytes"));
        }
        if self.started.elapsed().as_secs() > MAX_SYNC_SECS {
            return Err(anyhow!("sync exceeded {MAX_SYNC_SECS}s"));
        }

        if items > 0
            && let Some(prev) = self.last_end
            && end <= prev
        {
            return Err(anyhow!(
                "server reported no progress: end-total-content-size stuck at {prev}"
            ));
        }
        self.last_end = Some(end);
        Ok(())
    }
}

fn app_instance_id() -> String {
    std::env::var("THINGS_APP_INSTANCE_ID").unwrap_or_else(|_| "things3-cloud".to_string())
}

fn now_ts() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub(crate) fn now_timestamp() -> f64 {
    now_ts()
}

#[derive(Debug, Clone)]
pub struct ThingsCloudClient {
    pub email: String,
    pub password: String,
    pub history_key: Option<String>,
    pub head_index: i64,
    http: Client,
    /// Size of the most recent response body, so paginating callers can charge
    /// real transferred bytes against their budget without threading it
    /// through every return type.
    last_body_bytes: std::cell::Cell<u64>,
}

impl ThingsCloudClient {
    pub fn new(email: String, password: String) -> Result<Self> {
        let http = Client::builder().build()?;
        Ok(Self {
            email,
            password,
            history_key: None,
            head_index: 0,
            http,
            last_body_bytes: std::cell::Cell::new(0),
        })
    }

    /// Bytes read for the most recent response body.
    pub fn last_body_bytes(&self) -> u64 {
        self.last_body_bytes.get()
    }

    fn request(
        &self,
        method: reqwest::Method,
        url: &str,
        body: Option<Value>,
        extra_headers: &[(&str, String)],
    ) -> Result<Value> {
        let mut req = self
            .http
            .request(method, url)
            .header("Accept", "application/json")
            .header("Accept-Charset", "UTF-8")
            .header("User-Agent", USER_AGENT)
            .header("things-client-info", CLIENT_INFO)
            .header("App-Id", APP_ID)
            .header("Schema", SCHEMA)
            .header("App-Instance-Id", app_instance_id());

        for (k, v) in extra_headers {
            req = req.header(*k, v);
        }

        if let Some(payload) = body {
            req = req
                .header("Content-Type", "application/json; charset=UTF-8")
                .header("Content-Encoding", "UTF-8")
                .json(&payload);
        }

        let resp = req
            .send()
            .with_context(|| format!("request failed: {url}"))?;
        let status = resp.status();
        // Read the body through a cap rather than resp.text(): that buffers
        // whatever the peer sends, so a hostile or malfunctioning endpoint could
        // grow this allocation until the process dies. Taking one byte past the
        // limit lets us tell "exactly at the cap" from "overran it".
        let text = {
            use std::io::Read;
            let mut buf = String::new();
            match resp.take(MAX_BODY_BYTES + 1).read_to_string(&mut buf) {
                Ok(_) => {
                    if buf.len() as u64 > MAX_BODY_BYTES {
                        return Err(anyhow!(
                            "response body from {url} exceeded {MAX_BODY_BYTES} bytes"
                        ));
                    }
                    buf
                }
                Err(_) => String::new(),
            }
        };
        self.last_body_bytes.set(text.len() as u64);
        if !status.is_success() {
            return Err(anyhow!("HTTP {} for {}: {}", status.as_u16(), url, text));
        }
        if text.trim().is_empty() {
            return Ok(json!({}));
        }
        serde_json::from_str(&text).with_context(|| format!("invalid json from {url}"))
    }

    pub fn authenticate(&mut self) -> Result<String> {
        let url = format!("{BASE_URL}/account/{}", encode(&self.email));
        let result = self.request(
            reqwest::Method::GET,
            &url,
            None,
            &[(
                "Authorization",
                format!("Password {}", encode(&self.password)),
            )],
        )?;
        let key = result
            .get("history-key")
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow!("missing history-key in auth response"))?
            .to_string();
        self.history_key = Some(key.clone());
        Ok(key)
    }

    pub fn get_items_page(&self, start_index: i64) -> Result<Value> {
        let history_key = self
            .history_key
            .as_ref()
            .ok_or_else(|| anyhow!("Must authenticate first"))?;
        let url = format!("{BASE_URL}/history/{history_key}/items?start-index={start_index}");
        self.request(reqwest::Method::GET, &url, None, &[])
    }

    pub fn get_all_items(&mut self) -> Result<RawState> {
        if self.history_key.is_none() {
            let _ = self.authenticate()?;
        }

        let mut state = RawState::new();
        let mut start_index = 0i64;
        let mut budget = SyncBudget::new();

        loop {
            let page = self.get_items_page(start_index)?;
            let body_bytes = self.last_body_bytes();
            let items = page
                .get("items")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let item_count = items.len();
            self.head_index = page
                .get("current-item-index")
                .and_then(Value::as_i64)
                .unwrap_or(self.head_index);

            let end = page
                .get("end-total-content-size")
                .and_then(Value::as_i64)
                .unwrap_or(0);
            let latest = page
                .get("latest-total-content-size")
                .and_then(Value::as_i64)
                .unwrap_or(0);

            // Charge before folding: the point is to refuse a runaway peer
            // *before* its items are absorbed into RawState.
            budget.charge(item_count, body_bytes, end)?;

            for item in items {
                let wire: WireItem = serde_json::from_value(item)?;
                fold_item(wire, &mut state);
            }

            if end >= latest {
                break;
            }
            // A page with no items advances start_index by zero, so continuing
            // would re-request the same offset forever. The server still claims
            // more content here, but it is giving us nothing to make progress
            // with — stop rather than spin. (log_cache's paginator already does.)
            if item_count == 0 {
                break;
            }
            start_index += item_count as i64;
        }

        Ok(state)
    }

    pub fn commit(
        &mut self,
        changes: BTreeMap<String, WireObject>,
        ancestor_index: Option<i64>,
    ) -> Result<i64> {
        let history_key = self
            .history_key
            .as_ref()
            .ok_or_else(|| anyhow!("Must authenticate first"))?;
        let idx = ancestor_index.unwrap_or(self.head_index);
        let url = format!("{BASE_URL}/history/{history_key}/commit?ancestor-index={idx}&_cnt=1");

        let mut payload = BTreeMap::new();
        for (uuid, obj) in changes {
            payload.insert(uuid, obj);
        }

        let result = self.request(
            reqwest::Method::POST,
            &url,
            Some(serde_json::to_value(payload)?),
            &[("Push-Priority", WRITE_PUSH_PRIORITY.to_string())],
        )?;

        let new_index = result
            .get("server-head-index")
            .and_then(Value::as_i64)
            .unwrap_or(idx);
        self.head_index = new_index;
        Ok(new_index)
    }

    pub fn set_task_status(
        &mut self,
        task_uuid: &str,
        status: i32,
        entity: Option<String>,
        stop_date: Option<f64>,
    ) -> Result<i64> {
        let entity_type = entity.map(EntityType::from).unwrap_or(EntityType::Task6);
        let mut changes = BTreeMap::new();
        changes.insert(
            task_uuid.to_string(),
            WireObject::update(
                entity_type,
                TaskPatch {
                    status: Some(TaskStatus::from(status)),
                    stop_date: Some(stop_date),
                    modification_date: Some(now_ts()),
                    ..Default::default()
                },
            ),
        );
        self.commit(changes, None)
    }
}
