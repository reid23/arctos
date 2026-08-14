use super::TeamSelectionField;
use crate::Route;
use crate::api;
use crate::components::AssEntry;
use crate::display::short_or_truncate;
use crate::types::*;
use dioxus::html::ModifiersInteraction;
use dioxus::prelude::*;
#[cfg(target_arch = "wasm32")]
use gloo_timers::callback::Interval;
use serde::Serialize;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast as _;

/// CSS for schedule page: timeline layout and team-token inputs (used by modals in both table and timeline view).
const SCHEDULE_PAGE_CSS: &str = include_str!("schedule_timeline.css");
const SCHEDULE_REFRESH_INTERVAL_MS: u32 = 60_000;

/// Browser timezone offset in minutes (local = utc + offset). Used for table and timeline.
fn schedule_tz_offset_minutes() -> i64 {
    #[cfg(target_arch = "wasm32")]
    {
        let date = js_sys::Date::new_0();
        let offset = date.get_timezone_offset();
        -offset as i64
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        0_i64
    }
}

/// Convert a datetime-local value (local time, no timezone) to UTC ISO string for the API.
fn local_datetime_to_utc_iso(local_str: &str) -> Option<String> {
    use chrono::{FixedOffset, TimeZone, Utc};
    let s = local_str.trim();
    if s.is_empty() {
        return None;
    }
    let ndt = chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M").ok()?;
    let offset_secs = schedule_tz_offset_minutes() * 60;
    let offset = FixedOffset::east_opt(offset_secs as i32)?;
    let local = offset.from_local_datetime(&ndt).single()?;
    Some(
        local
            .with_timezone(&Utc)
            .format("%Y-%m-%dT%H:%M:%SZ")
            .to_string(),
    )
}

/// Convert a UTC-ish ISO datetime string from the API into a `datetime-local` value (local time, no timezone).
fn utc_iso_to_local_datetime_input(iso: &str) -> Option<String> {
    use chrono::NaiveDateTime;
    let s = iso.trim();
    if s.is_empty() {
        return None;
    }

    let utc_dt = if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(s) {
        dt.naive_utc()
    } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f") {
        dt
    } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S") {
        dt
    } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M") {
        dt
    } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        dt
    } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M") {
        dt
    } else {
        return None;
    };

    let local = utc_dt + chrono::Duration::minutes(schedule_tz_offset_minutes());
    Some(local.format("%Y-%m-%dT%H:%M").to_string())
}

/// Plan-mode start: stable published time. Falls back to nominal if scheduled is missing.
fn plan_start_str(m: &MatchSetupData) -> Option<&str> {
    m.scheduled_start_time
        .as_deref()
        .or(m.nominal_start_time.as_deref())
}

/// Real/estimated start: confirmed if started, else the solver's live estimate.
fn actual_start_str(m: &MatchSetupData) -> Option<&str> {
    m.confirmed_start_time
        .as_deref()
        .or(m.nominal_start_time.as_deref())
        .or(m.scheduled_start_time.as_deref())
}

/// Parse an ISO-ish schedule timestamp from the API into naive UTC.
fn parse_schedule_time_utc(s: &str) -> Option<chrono::NaiveDateTime> {
    use chrono::NaiveDateTime;
    if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(s) {
        return Some(dt.naive_utc());
    }
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(dt) = NaiveDateTime::parse_from_str(s, fmt) {
            return Some(dt);
        }
    }
    None
}

/// Displayed interval (naive UTC) for a match block. Single source of truth for
/// table, timeline, and team views.
///
/// Viewer rule ("planned or earlier"): element-wise minimum of
/// - the planned interval: plan start (`scheduled_start_time`, fallback nominal)
///   .. plan start + `nominal_length`, and
/// - the real/estimated interval: `confirmed_start_time` if started else
///   `nominal_start_time` .. `completed_time` if completed else real start +
///   `nominal_length`.
///
/// When the day runs ahead, blocks pull earlier and completed matches show their
/// real (earlier) end; a late-running day never shifts blocks later — lateness is
/// visible via the now line.
///
/// `show_as_happened` (edit-mode-only toggle) instead places blocks at the exact
/// real/estimated times with no min-capping.
fn display_interval_utc(
    m: &MatchSetupData,
    show_as_happened: bool,
) -> Option<(chrono::NaiveDateTime, chrono::NaiveDateTime)> {
    let len = chrono::Duration::minutes(m.nominal_length.unwrap_or(30) as i64);
    let min_len = chrono::Duration::minutes(1);
    let real_start = actual_start_str(m).and_then(parse_schedule_time_utc);
    let real_end = m
        .completed_time
        .as_deref()
        .and_then(parse_schedule_time_utc)
        .or_else(|| real_start.map(|s| s + len));
    if show_as_happened {
        let start = real_start?;
        let end = real_end.unwrap_or(start + len).max(start + min_len);
        return Some((start, end));
    }
    let plan_start = plan_start_str(m).and_then(parse_schedule_time_utc);
    let plan_end = plan_start.map(|s| s + len);
    let start = match (plan_start, real_start) {
        (Some(p), Some(r)) => p.min(r),
        (p, r) => p.or(r)?,
    };
    let end = match (plan_end, real_end) {
        (Some(p), Some(r)) => p.min(r),
        (p, r) => p.or(r).unwrap_or(start + len),
    }
    .max(start + min_len);
    Some((start, end))
}

/// Format a naive-UTC timestamp as local "HH:MM".
fn format_naive_utc_time_local(dt: chrono::NaiveDateTime, tz_offset_minutes: i64) -> String {
    (dt + chrono::Duration::minutes(tz_offset_minutes))
        .format("%H:%M")
        .to_string()
}

/// Per-slot ref tokens: prefer resolved team id, else initial expression.
/// Important: `refs` CSV can be non-empty while only containing blank slots (`",,"`);
/// those must still fall through to `refs_initial` per slot so reffing teams show up.
fn refs_tokens(m: &MatchSetupData) -> Vec<String> {
    let resolved: Vec<&str> = m
        .refs
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(|s| s.trim())
        .collect();
    let initial: Vec<&str> = m
        .refs_initial
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(|s| s.trim())
        .collect();
    let n = resolved.len().max(initial.len());
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let r = resolved.get(i).copied().unwrap_or("");
        let init = initial.get(i).copied().unwrap_or("");
        let tok = if !r.is_empty() { r } else { init };
        if !tok.is_empty() {
            out.push(tok.to_string());
        }
    }
    out
}

/// True for BREAK / STATBREAK / JOIN — structural schedule items, not games with lifecycle chrome.
fn is_structural_match(m: &MatchSetupData) -> bool {
    is_structural_type(m.schedule_type.as_deref())
}

fn is_structural_type(schedule_type: Option<&str>) -> bool {
    matches!(
        schedule_type,
        Some("BREAK") | Some("STATBREAK") | Some("JOIN")
    )
}

/// True for BREAK / STATBREAK — break-like blocks that are edited as a
/// same-name group across fields.
fn is_break_like_type(schedule_type: Option<&str>) -> bool {
    matches!(schedule_type, Some("BREAK") | Some("STATBREAK"))
}

/// True if a ref/team token refers to the given focus team id.
fn token_matches_team(token: &str, team_id: &str, team_options: &[TeamOption]) -> bool {
    let token = token.trim();
    let team_id = team_id.trim();
    if token.is_empty() || team_id.is_empty() {
        return false;
    }
    if token == team_id {
        return true;
    }
    let Some(opt) = team_options.iter().find(|o| o.id == team_id) else {
        return token.eq_ignore_ascii_case(team_id);
    };
    if opt.id.eq_ignore_ascii_case(token) {
        return true;
    }
    if opt
        .pseudonym
        .as_deref()
        .map(|p| p.eq_ignore_ascii_case(token))
        .unwrap_or(false)
    {
        return true;
    }
    if opt
        .shortname
        .as_deref()
        .map(|s| s.eq_ignore_ascii_case(token))
        .unwrap_or(false)
    {
        return true;
    }
    false
}

/// Full display name for a team option (no shortname truncation).
fn team_full_label(opt: &TeamOption) -> String {
    opt.pseudonym
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or(opt.id.as_str())
        .to_string()
}

/// Resolve a team id/token to (full label, photo). Uses full pseudonym, never shortname.
fn resolve_team_display(
    token: &str,
    team_options: &[TeamOption],
) -> (String, Option<String>, u8) {
    if let Some(opt) = team_options.iter().find(|o| o.id == token) {
        return (team_full_label(opt), opt.profile_photo.clone(), 0);
    }
    // Unresolved tag/reference expression
    let (kind, label) = team_ref_display(token);
    (label, None, kind)
}

/// Whether a match involves a focus team (playing or reffing).
fn match_involves_team(m: &MatchSetupData, team_id: &str, team_options: &[TeamOption]) -> bool {
    if team_id.is_empty() {
        return false;
    }
    team_is_playing(m, team_id) || team_is_reffing(m, team_id, team_options)
}

fn team_is_playing(m: &MatchSetupData, team_id: &str) -> bool {
    !team_id.is_empty()
        && (m.team1.as_deref() == Some(team_id) || m.team2.as_deref() == Some(team_id))
}

fn team_is_reffing(m: &MatchSetupData, team_id: &str, team_options: &[TeamOption]) -> bool {
    if team_id.is_empty() || team_is_playing(m, team_id) {
        return false;
    }
    refs_tokens(m)
        .iter()
        .any(|t| token_matches_team(t, team_id, team_options))
}

/// Opponent full label + photo relative to focus team. None if not playing.
fn opponent_for_focus(
    m: &MatchSetupData,
    team_id: &str,
    team_options: &[TeamOption],
) -> Option<(String, Option<String>, u8)> {
    if m.team1.as_deref() == Some(team_id) {
        let token = m
            .team2
            .as_deref()
            .or(m.team2_initial.as_deref())
            .unwrap_or("TBA");
        return Some(resolve_team_display(token, team_options));
    }
    if m.team2.as_deref() == Some(team_id) {
        let token = m
            .team1
            .as_deref()
            .or(m.team1_initial.as_deref())
            .unwrap_or("TBA");
        return Some(resolve_team_display(token, team_options));
    }
    None
}

/// localStorage helpers (wasm only).
fn ls_get(key: &str) -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        web_sys::window()?
            .local_storage()
            .ok()??
            .get_item(key)
            .ok()?
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = key;
        None
    }
}

fn ls_set(key: &str, val: &str) {
    #[cfg(target_arch = "wasm32")]
    {
        if let Some(window) = web_sys::window() {
            if let Ok(Some(storage)) = window.local_storage() {
                let _ = storage.set_item(key, val);
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (key, val);
    }
}

/// Compute the scrollTop that keeps the current viewport center fixed after a
/// uniform content-height scale of `ratio` (new/old). Returns None if the
/// scroll element is missing.
fn scroll_top_after_centered_zoom(scroll_el_id: &str, ratio: f64) -> Option<i32> {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        let window = web_sys::window()?;
        let doc = window.document()?;
        let el = doc.get_element_by_id(scroll_el_id)?;
        let html_el = el.dyn_ref::<web_sys::HtmlElement>()?;
        let client_h = html_el.client_height() as f64;
        let scroll_top = html_el.scroll_top() as f64;
        let center = scroll_top + client_h / 2.0;
        Some((center * ratio - client_h / 2.0).max(0.0) as i32)
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (scroll_el_id, ratio);
        None
    }
}

fn apply_scroll_top(scroll_el_id: &str, scroll_top: i32) {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        if let Some(window) = web_sys::window() {
            if let Some(doc) = window.document() {
                if let Some(el) = doc.get_element_by_id(scroll_el_id) {
                    if let Some(html_el) = el.dyn_ref::<web_sys::HtmlElement>() {
                        html_el.set_scroll_top(scroll_top);
                    }
                }
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (scroll_el_id, scroll_top);
    }
}

fn focus_team_storage_key(tournament_url: &str) -> String {
    format!("schedule_focus_team:{tournament_url}")
}

/// Remembered nav location (view + team + field) per tournament, used when the
/// URL carries no query params.
fn nav_storage_key(tournament_url: &str) -> String {
    format!("schedule_last_nav:{tournament_url}")
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, serde::Deserialize)]
struct ScheduleNavState {
    #[serde(default)]
    view: String,
    #[serde(default)]
    team: String,
    #[serde(default)]
    field: String,
}

/// Public view modes: "team" / "field". The all-fields "timeline" and "table"
/// views live exclusively on the edit page (`/:url/schedule/edit`).
fn is_valid_view(view: &str) -> bool {
    matches!(view, "team" | "field")
}

const VERTICAL_SCALE_KEY: &str = "schedule_vertical_scale";
/// Edit-mode-only "Show times as they happened" toggle. No effect outside edit mode.
const EDIT_SHOW_AS_HAPPENED_KEY: &str = "schedule_edit_show_as_happened";
/// Base slot height in rem at scale 1.0
const BASE_SLOT_HEIGHT_REM: f64 = 7.0;
const MIN_VERTICAL_SCALE: f64 = 0.55;
const MAX_VERTICAL_SCALE: f64 = 2.5;

/// Full-timestamp local formatter so debug-mode tables show the full timestamp.
fn format_datetime_local(iso: &str, tz_offset_minutes: i64) -> String {
    let utc_dt = if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(iso) {
        dt.naive_utc()
    } else if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(iso, "%Y-%m-%dT%H:%M:%S%.f") {
        dt
    } else if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(iso, "%Y-%m-%dT%H:%M") {
        dt
    } else {
        return iso.to_string();
    };
    let local = utc_dt + chrono::Duration::minutes(tz_offset_minutes);
    local.format("%Y-%m-%d %H:%M").to_string()
}

/// Read the `debug` flag from `localStorage` (truthy when value is `"1"`).
/// On non-wasm builds always returns `false`.
fn read_debug_mode() -> bool {
    #[cfg(target_arch = "wasm32")]
    {
        if let Some(window) = web_sys::window() {
            if let Ok(Some(storage)) = window.local_storage() {
                if let Ok(Some(val)) = storage.get_item("debug") {
                    return val == "1";
                }
            }
        }
        false
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        false
    }
}

/// Payload emitted by the edit-page timeline when a drag-to-create gesture
/// completes (or a plain click on empty grid space).
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct DragCreatePayload {
    /// Field column the drag happened in.
    field_name: String,
    /// Snapped drag start, local time.
    start_local: chrono::NaiveDateTime,
    /// Drag extent in minutes (already min-clamped); None = plain click → default length.
    length_min: Option<u32>,
    /// Suggested previous match on that field (latest displayed start at-or-before the drag start).
    prev_match_id: Option<String>,
}

/// Placeholder block shown on the editor timeline while the create card is
/// open: mirrors the card's field(s) / start-time / length values so the user
/// can see where the new match will land. Break/join group forms list every
/// checked field (one placeholder per field). Cleared on save or cancel.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PendingCreateGhost {
    field_names: Vec<String>,
    start_local: chrono::NaiveDateTime,
    length_min: i64,
    /// JOIN groups render as a thin line-like placeholder, not a block.
    is_join: bool,
}

/// Payload emitted by the edit-page timeline when a drag-to-move gesture commits.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MoveCommitPayload {
    match_id: String,
    /// Schedule type of the dragged match (decides which API call to make).
    schedule_type: String,
    /// Match name (break groups are addressed by name).
    group_name: String,
    /// Target field name.
    new_field: Option<String>,
    /// New start time as UTC ISO (STATIC/STATBREAK).
    new_start_utc: Option<String>,
    /// New previous match id (dynamic types).
    new_prev_id: Option<String>,
}

/// Public read-only schedule page. Edit affordances live on [`ScheduleEdit`].
#[component]
pub fn Schedule(url: String, view: String, team: String, field: String) -> Element {
    rsx! {
        SchedulePage {
            url,
            view,
            team,
            field,
            editor: false,
        }
    }
}

/// Dedicated schedule-editing page (`/:url/schedule/edit`). TO-only; renders the
/// all-fields timeline + table with edit capabilities permanently on. Non-TOs
/// are redirected to the public schedule page.
#[component]
pub fn ScheduleEdit(url: String) -> Element {
    rsx! {
        SchedulePage {
            url,
            view: String::new(),
            team: String::new(),
            field: String::new(),
            editor: true,
        }
    }
}

#[component]
fn SchedulePage(url: String, view: String, team: String, field: String, editor: bool) -> Element {
    let url_data = url.clone();
    let mut setup_data = use_resource(move || {
        let u = url_data.clone();
        async move { api::schedule_setup(&u).await }
    });

    // Initial nav state: URL query params win; otherwise fall back to the
    // remembered location. Computed once so navigator().replace below (which
    // changes props) can't feed back into state.
    let initial_nav = use_hook(|| {
        if editor {
            // The edit page is always the all-fields grid (or table); no URL/query state.
            return ScheduleNavState {
                view: "timeline".to_string(),
                team: String::new(),
                field: "all".to_string(),
            };
        }
        let from_url = ScheduleNavState {
            view: view.clone(),
            team: team.clone(),
            field: field.clone(),
        };
        let mut nav = if !view.is_empty() || !team.is_empty() || !field.is_empty() {
            from_url
        } else {
            ls_get(&nav_storage_key(&url))
                .and_then(|s| serde_json::from_str::<ScheduleNavState>(&s).ok())
                .unwrap_or_default()
        };
        if !is_valid_view(&nav.view) {
            // Default view is the personal single-column timeline.
            nav.view = "team".to_string();
        }
        if nav.field.is_empty() {
            nav.field = "all".to_string();
        }
        nav
    });

    let mut view_mode = use_signal({
        let v = initial_nav.view.clone();
        move || v
    });
    // Editing is a property of the page: permanently on for the edit page,
    // permanently off on the public schedule page.
    let edit_mode = editor;
    let mut selected_field = use_signal({
        let f = initial_nav.field.clone();
        move || f
    });
    let mut highlight_team = use_signal(|| "".to_string());
    /// Edit-mode-only "Show times as they happened" toggle: place blocks at exact
    /// real times (confirmed/completed, falling back to nominal estimates) with no
    /// min-capping. Viewers always get the "planned or earlier" rule.
    let mut show_as_happened = use_signal(|| {
        ls_get(EDIT_SHOW_AS_HAPPENED_KEY)
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false)
    });
    /// Vertical scale for timeline slot height (#222). 1.0 = default.
    let mut vertical_scale = use_signal(|| {
        ls_get(VERTICAL_SCALE_KEY)
            .and_then(|s| s.parse::<f64>().ok())
            .map(|s| s.clamp(MIN_VERTICAL_SCALE, MAX_VERTICAL_SCALE))
            .unwrap_or(1.0)
    });
    /// Focus team id for Team view (#195). URL/remembered nav wins; otherwise
    /// prefer localStorage so the dropdown shows a selection immediately, then
    /// upgrade from registration if needed (effect below).
    let mut focus_team_id = use_signal({
        let t = initial_nav.team.clone();
        move || t
    });
    // When a team was specified via URL or remembered nav, skip the
    // localStorage/registration resolution entirely.
    let mut focus_team_ready = use_signal({
        let specified = !initial_nav.team.is_empty();
        move || specified
    });

    let mut is_to = use_signal(|| false);

    let mut active_modal = use_signal(|| "none".to_string());
    let mut selected_match_id = use_signal(|| "".to_string());
    // Name of the break group being edited (BREAK/STATBREAK blocks open the
    // group modal instead of the single-match edit modal).
    let mut selected_break_group = use_signal(|| "".to_string());
    let mut key_nav = use_signal(|| None::<String>);
    let refresh_trigger = use_signal(|| 0u32);
    // Debug mode is opt-in via `localStorage.setItem("debug", "1")`. Read once at mount —
    // toggling requires a refresh, which is fine for a developer-only switch.
    let debug_mode = use_signal(read_debug_mode);
    let navigator = use_navigator();

    // Edit-page-only state.
    // Bulk match-length tool: click blocks to multi-select, then apply one length to all.
    let mut bulk_mode = use_signal(|| false);
    let mut bulk_selected = use_signal(Vec::<String>::new);
    let mut bulk_length_input = use_signal(|| 30u32);
    // Inline error affordance for drag-move / bulk failures (dismissible alert near the toolbar).
    let mut edit_error = use_signal(|| None::<String>);
    // Prefill for the create-match card when opened from a drag-to-create gesture.
    let mut create_prefill = use_signal(|| None::<DragCreatePayload>);
    // Bumped per drag-create so the card remounts with fresh prefill state.
    let mut create_prefill_nonce = use_signal(|| 0u32);
    // Default schedule type for newly created matches (toolbar dropdown; not persisted).
    let mut default_match_type = use_signal(|| "STATIC".to_string());
    // Placeholder on the timeline while the create card is open, kept in sync with
    // the card's field/start/length values (the card writes it via this signal).
    let mut create_ghost = use_signal(|| None::<PendingCreateGhost>);
    // Last-focused team-ish input in the open editor card ("team1" | "team2" | "refs").
    let mut team_field_focus = use_signal(|| None::<String>);
    // Winner/Loser chip clicks queue a `<Match>::winner|loser` token for the open card.
    let mut insert_team_ref = use_signal(|| None::<String>);

    // Pull schedule warnings in parallel so we can surface a cycle banner at the top
    // of the page. Re-runs whenever refresh_trigger bumps so the banner stays in sync
    // with the schedule view. Errors (e.g. non-TO 403s) silently leave warnings empty.
    let url_for_warnings = url.clone();
    let warnings_resource = use_resource(move || {
        let u = url_for_warnings.clone();
        let _tick = refresh_trigger();
        async move { api::fetch_schedule_warnings(&u).await }
    });
    #[cfg(target_arch = "wasm32")]
    let schedule_refresh_interval = use_signal(|| None as Option<Interval>);

    let url_for_redirect = url.clone();
    use_effect(move || {
        if let Some(Ok(data)) = setup_data.value().read().as_ref() {
            is_to.set(data.is_to);
            // The edit page is TO-only: bounce non-TOs to the public schedule.
            if editor && !data.is_to {
                navigator.replace(Route::Schedule {
                    url: url_for_redirect.clone(),
                    view: String::new(),
                    team: String::new(),
                    field: String::new(),
                });
                return;
            }
            let v = view_mode();
            // The edit page only has the all-fields grid and the table.
            if editor && !matches!(v.as_str(), "timeline" | "table") {
                view_mode.set("timeline".to_string());
                return;
            }
            // The public page only has "team" and "field"; coerce stray values (e.g. from URL).
            if !editor && !matches!(v.as_str(), "team" | "field") {
                view_mode.set("team".to_string());
                return;
            }
            // "By field" needs a concrete field: keep a remembered valid one, else
            // default to the first field alphabetically.
            if v == "field" {
                let sf = selected_field.peek().clone();
                let valid = data.fields.iter().any(|f| f.id.to_string() == sf);
                if !valid {
                    let mut fields: Vec<&FieldSetupData> = data.fields.iter().collect();
                    fields.sort_by(|a, b| a.name.cmp(&b.name));
                    if let Some(f) = fields.first() {
                        selected_field.set(f.id.to_string());
                    }
                }
            }
        }
    });

    // Keep localStorage and the URL in sync with the nav state (view/team/field)
    // so copying the address bar deep-links correctly. `replace` (not `push`) to
    // avoid history spam; the guard prevents loops with the route props.
    {
        let url_for_nav = url.clone();
        let nav_handle = use_navigator();
        let mut last_nav_synced = use_signal(|| None::<ScheduleNavState>);
        use_effect(move || {
            // The edit page has its own route with no query state; never rewrite its URL.
            if editor {
                return;
            }
            let state = ScheduleNavState {
                view: view_mode(),
                team: focus_team_id(),
                field: {
                    let f = selected_field();
                    if f == "all" { String::new() } else { f }
                },
            };
            if last_nav_synced.peek().as_ref() == Some(&state) {
                return;
            }
            if let Ok(encoded) = serde_json::to_string(&state) {
                ls_set(&nav_storage_key(&url_for_nav), &encoded);
            }
            nav_handle.replace(Route::Schedule {
                url: url_for_nav.clone(),
                view: state.view.clone(),
                team: state.team.clone(),
                field: state.field.clone(),
            });
            last_nav_synced.set(Some(state));
        });
    }

    // Resolve default focus team for Team view: localStorage first (instant select),
    // then registered team / player's team (overrides empty only, or confirms).
    {
        let url_for_focus = url.clone();
        use_effect(move || {
            if focus_team_ready() {
                return;
            }
            let u = url_for_focus.clone();
            // Immediate: restore persisted selection so the dropdown isn't blank on first paint.
            if let Some(stored) = ls_get(&focus_team_storage_key(&u)) {
                if !stored.is_empty() {
                    focus_team_id.set(stored);
                }
            }
            spawn(async move {
                let mut resolved = String::new();
                // 1) Logged-in team with a registration for this tournament
                if let Ok(me) = api::me().await {
                    if me.user_type == "team" {
                        if api::get_my_team_registration(&u).await.is_ok() {
                            resolved = me.id.clone();
                        }
                    } else if me.user_type == "player" {
                        if let Ok(preg) = api::get_my_player_registration(&u).await {
                            if let Some(team) = preg.current_team {
                                resolved = team.id;
                            } else if let Some(tid) = preg.registration.team {
                                resolved = tid;
                            }
                        }
                    }
                }
                // Prefer registration identity when available; else keep localStorage.
                if resolved.is_empty() {
                    if let Some(stored) = ls_get(&focus_team_storage_key(&u)) {
                        if !stored.is_empty() {
                            resolved = stored;
                        }
                    }
                }
                if !resolved.is_empty() {
                    focus_team_id.set(resolved.clone());
                    ls_set(&focus_team_storage_key(&u), &resolved);
                }
                focus_team_ready.set(true);
            });
        });
    }

    use_effect(move || {
        if refresh_trigger() > 0 {
            setup_data.restart();
        }
    });

    #[cfg(target_arch = "wasm32")]
    {
        let mut refresh_trigger = refresh_trigger;
        let mut schedule_refresh_interval = schedule_refresh_interval;
        use_effect(move || {
            if schedule_refresh_interval.read().is_some() {
                return;
            }

            let handle = Interval::new(SCHEDULE_REFRESH_INTERVAL_MS, move || {
                refresh_trigger.set(refresh_trigger().wrapping_add(1));
            });
            schedule_refresh_interval.set(Some(handle));
        });
    }

    // Refocus the schedule container when a modal closes so keyboard shortcuts work without a click
    use_effect(move || {
        let _ = active_modal();
        #[cfg(target_arch = "wasm32")]
        if active_modal() == "none" {
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let Ok(Some(el)) = doc.query_selector(".schedule-keyboard-focus") {
                            if let Some(html_el) = el.dyn_ref::<web_sys::HtmlElement>() {
                                let _ = html_el.focus();
                            }
                        }
                    }
                }
            });
        }
    });

    let refresh = move || {
        // Bumping refresh_trigger restarts setup_data via the existing effect and
        // causes the warnings resource to re-fetch (its closure reads the trigger).
        let mut t = refresh_trigger;
        t.set(t().wrapping_add(1));
    };

    let val = setup_data.value();
    let data_opt = val.read().as_ref().and_then(|r| r.as_ref().ok().cloned());

    match data_opt {
        Some(data) => {
            let is_to = data.is_to;
            let url_for_export = url.clone();
            let url_for_recompute = url.clone();
            let url_for_export_key = url_for_export.clone();
            let url_for_recompute_key = url_for_recompute.clone();
            let handle_keydown = move |ev: Event<KeyboardData>| {
                let key_str = ev.key().to_string();
                let modal_open = active_modal() != "none";
                if modal_open {
                    // When a modal is open, only handle Escape to close it; let all other keys go to modal inputs
                    if key_str == "Escape" {
                        ev.prevent_default();
                        active_modal.set("none".to_string());
                    }
                    return;
                }
                if key_str == "Escape" {
                    ev.prevent_default();
                    // On the edit page Esc first exits the bulk-length tool.
                    if bulk_mode() {
                        bulk_mode.set(false);
                        bulk_selected.set(Vec::new());
                    } else {
                        active_modal.set("none".to_string());
                    }
                } else {
                    match key_str.as_str() {
                        "n" | "N" => {
                            ev.prevent_default();
                            if matches!(view_mode().as_str(), "team" | "field" | "timeline") {
                                key_nav.set(Some("next".to_string()));
                            }
                        }
                        "p" | "P" => {
                            ev.prevent_default();
                            if matches!(view_mode().as_str(), "team" | "field" | "timeline") {
                                key_nav.set(Some("prev".to_string()));
                            }
                        }
                        "t" | "T" => {
                            ev.prevent_default();
                            if editor {
                                active_modal.set("tags".to_string());
                            } else if matches!(view_mode().as_str(), "team" | "field" | "timeline")
                            {
                                key_nav.set(Some("today".to_string()));
                            }
                        }
                        "a" | "A" => {
                            ev.prevent_default();
                            if editor {
                                view_mode.set("table".to_string());
                            }
                        }
                        "l" | "L" => {
                            ev.prevent_default();
                            if editor {
                                view_mode.set("timeline".to_string());
                            }
                        }
                        "y" | "Y" => {
                            ev.prevent_default();
                            if !edit_mode {
                                view_mode.set("team".to_string());
                            }
                        }
                        "f" | "F" => {
                            if editor {
                                ev.prevent_default();
                                active_modal.set("fields".to_string());
                            } else if !edit_mode {
                                ev.prevent_default();
                                view_mode.set("field".to_string());
                            }
                        }
                        "x" | "X" => {
                            if editor {
                                ev.prevent_default();
                                let u = url_for_export_key.clone();
                                spawn(async move {
                                    if let Ok(res) = api::export_schedule(&u).await {
                                        #[cfg(target_arch = "wasm32")]
                                        if let Some(window) = web_sys::window() {
                                            let doc = window.document().expect("document");
                                            let bytes = res.toml.as_bytes();
                                            let arr = js_sys::Uint8Array::new_from_slice(bytes);
                                            let parts = js_sys::Array::new();
                                            parts.push(&arr);
                                            let blob_opts = web_sys::BlobPropertyBag::new();
                                            blob_opts.set_type("application/toml");
                                            let blob = web_sys::Blob::new_with_u8_array_sequence_and_options(
                                                &parts.into(),
                                                &blob_opts,
                                            ).expect("Blob");
                                            let url =
                                                web_sys::Url::create_object_url_with_blob(&blob)
                                                    .expect("object URL");
                                            let filename = format!(
                                                "{}_schedule_{}.toml",
                                                u,
                                                chrono::Utc::now().format("%Y%m%d_%H%M%S")
                                            );
                                            if let Ok(a) = doc.create_element("a") {
                                                let _ = a.set_attribute("href", &url);
                                                let _ = a.set_attribute("download", &filename);
                                                if let Some(anchor) =
                                                    a.dyn_ref::<web_sys::HtmlAnchorElement>()
                                                {
                                                    anchor.click();
                                                }
                                            }
                                            web_sys::Url::revoke_object_url(&url).ok();
                                        }
                                    }
                                });
                            }
                        }
                        "i" | "I" => {
                            if editor {
                                ev.prevent_default();
                                active_modal.set("toml_import".to_string());
                            }
                        }
                        "r" | "R" => {
                            if editor {
                                ev.prevent_default();
                                let u = url_for_recompute_key.clone();
                                let mut trigger = refresh_trigger;
                                spawn(async move {
                                    if let Ok(_) = api::recompute_schedule(&u).await {
                                        trigger.set(trigger() + 1);
                                    }
                                });
                            }
                        }
                        _ => {}
                    }
                }
            };

            rsx! {
                style { {SCHEDULE_PAGE_CSS} }
                div {
                    // The editor gets the full viewport width (escapes the layout's
                    // centered max-width container via the full-bleed class).
                    class: if editor { "container-fluid mt-3 position-relative schedule-keyboard-focus schedule-editor-fullbleed" } else { "container-fluid mt-3 position-relative schedule-keyboard-focus" },
                    tabindex: 0,
                    onkeydown: handle_keydown,
                    onmounted: move |ev| {
                        spawn(async move {
                            let _ = ev.data().set_focus(true).await;
                        });
                    },
                    role: "application",
                    aria_label: "Schedule",
                    div { class: "row mb-3",
                        div { class: "col",
                            h1 { "{data.tournament.name}" }
                            nav { "aria-label": "breadcrumb",
                                ol { class: "breadcrumb",
                                    li { class: "breadcrumb-item",
                                        Link { to: Route::TournamentHome { url: url.clone() }, "{data.tournament.name}" }
                                    }
                                    if editor {
                                        li { class: "breadcrumb-item",
                                            Link { to: Route::Schedule { url: url.clone(), view: String::new(), team: String::new(), field: String::new() }, "Schedule" }
                                        }
                                        li { class: "breadcrumb-item active", "Edit" }
                                    } else {
                                        li { class: "breadcrumb-item active", "Schedule" }
                                    }
                                }
                            }
                        }
                    }

                    {
                        let has_cycle = warnings_resource
                            .value()
                            .read()
                            .as_ref()
                            .and_then(|r| r.as_ref().ok())
                            .map(|ws| ws.iter().any(|w| w.kind == "cycle"))
                            .unwrap_or(false);
                        if has_cycle {
                            rsx! {
                                div {
                                    class: "alert alert-danger d-flex align-items-center mb-3",
                                    role: "alert",
                                    span { class: "me-2", "⚠" }
                                    span { class: "flex-grow-1",
                                        strong { "Schedule failed to solve: " }
                                        "circular dependency detected."
                                        if editor {
                                            " See "
                                            button {
                                                r#type: "button",
                                                class: "btn btn-link p-0 align-baseline",
                                                onclick: move |_| active_modal.set("schedule_warnings".to_string()),
                                                "Warnings"
                                            }
                                            " for more info."
                                        } else if is_to {
                                            " See the "
                                            Link {
                                                to: Route::ScheduleEdit { url: url.clone() },
                                                "schedule editor"
                                            }
                                            " for more info."
                                        }
                                    }
                                }
                            }
                        } else {
                            rsx! {}
                        }
                    }

                    div { class: "card mb-3 bg-light",
                        div { class: "card-body p-2",
                            div { class: "d-flex flex-wrap justify-content-between align-items-center gap-2",
                                div { class: "d-flex flex-wrap align-items-center gap-2",
                                    if view_mode() != "team" {
                                        select {
                                            class: "form-select form-select-sm d-inline-block w-auto",
                                            value: "{selected_field}",
                                            onchange: move |e| selected_field.set(e.value()),
                                            // "By field" requires a concrete field; the grid/table allow "all".
                                            if view_mode() != "field" {
                                                option { value: "all", "All Fields" }
                                            }
                                            for f in &data.fields {
                                                option {
                                                    value: "{f.id}",
                                                    selected: f.id.to_string() == selected_field(),
                                                    "{f.name}"
                                                }
                                            }
                                        }
                                        input {
                                            class: "form-control form-control-sm d-inline-block",
                                            style: "width: 10rem;",
                                            placeholder: "Highlight Team...",
                                            value: "{highlight_team}",
                                            oninput: move |e| highlight_team.set(e.value()),
                                            onkeydown: move |ev: Event<KeyboardData>| ev.stop_propagation(),
                                        }
                                    } else {
                                        {
                                            let selected = focus_team_id();
                                            let selected_in_options = data.team_options.iter().any(|t| t.id == selected);
                                            rsx! {
                                                select {
                                                    class: "form-select form-select-sm d-inline-block w-auto",
                                                    // Controlled value + per-option selected so default team shows correctly.
                                                    value: "{selected}",
                                                    onchange: {
                                                        let u = url.clone();
                                                        move |e| {
                                                            let v = e.value();
                                                            focus_team_id.set(v.clone());
                                                            ls_set(&focus_team_storage_key(&u), &v);
                                                        }
                                                    },
                                                    option {
                                                        value: "",
                                                        selected: selected.is_empty(),
                                                        "Choose your team…"
                                                    }
                                                    // If the resolved team isn't in team_options yet, still show it selected.
                                                    if !selected.is_empty() && !selected_in_options {
                                                        option {
                                                            value: "{selected}",
                                                            selected: true,
                                                            "{selected}"
                                                        }
                                                    }
                                                    for t in &data.team_options {
                                                        option {
                                                            value: "{t.id}",
                                                            selected: t.id == selected,
                                                            {
                                                                t.pseudonym
                                                                    .as_deref()
                                                                    .unwrap_or(t.id.as_str())
                                                                    .to_string()
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    div { class: "btn-group btn-group-sm",
                                        if !editor {
                                            button {
                                                class: if view_mode() == "team" { "btn btn-primary" } else { "btn btn-outline-primary" },
                                                onclick: move |_| view_mode.set("team".to_string()),
                                                "By team"
                                            }
                                            button {
                                                class: if view_mode() == "field" { "btn btn-primary" } else { "btn btn-outline-primary" },
                                                onclick: move |_| view_mode.set("field".to_string()),
                                                "By field"
                                            }
                                        }
                                        if editor {
                                            button {
                                                class: if view_mode() == "timeline" { "btn btn-primary" } else { "btn btn-outline-primary" },
                                                onclick: move |_| view_mode.set("timeline".to_string()),
                                                "All fields"
                                            }
                                            button {
                                                class: if view_mode() == "table" { "btn btn-primary" } else { "btn btn-outline-primary" },
                                                onclick: move |_| view_mode.set("table".to_string()),
                                                "Table"
                                            }
                                        }
                                    }
                                }
                                if editor {
                                    div { class: "d-flex flex-wrap align-items-center gap-1",
                                            button { class: "btn btn-sm btn-outline-secondary", onclick: move |_| active_modal.set("tags".to_string()), "Tags" }
                                            button { class: "btn btn-sm btn-outline-secondary", onclick: move |_| active_modal.set("fields".to_string()), "Fields" }
                                            div {
                                                class: "d-flex align-items-center gap-1 ms-1",
                                                title: "Schedule type new matches start with (click or drag on the schedule to create one)",
                                                label { class: "small text-muted mb-0", r#for: "defaultMatchTypeSelect", "Default new match type" }
                                                select {
                                                    id: "defaultMatchTypeSelect",
                                                    class: "form-select form-select-sm w-auto",
                                                    value: "{default_match_type}",
                                                    onchange: move |e| default_match_type.set(e.value()),
                                                    option { value: "STATIC", "Static" }
                                                    option { value: "SAFE", "Safe" }
                                                    option { value: "FAST", "Fast" }
                                                    option { value: "BREAK", "Break" }
                                                    option { value: "STATBREAK", "Static Break" }
                                                    option { value: "JOIN", "Join" }
                                                }
                                            }
                                            button { class: "btn btn-sm btn-outline-secondary", onclick: move |_| {
                                                let u = url_for_export.clone();
                                                spawn(async move {
                                                    if let Ok(res) = api::export_schedule(&u).await {
                                                        #[cfg(target_arch = "wasm32")]
                                                        if let Some(window) = web_sys::window() {
                                                            let doc = window.document().expect("document");
                                                            let bytes = res.toml.as_bytes();
                                                            let arr = js_sys::Uint8Array::new_from_slice(bytes);
                                                            let parts = js_sys::Array::new();
                                                            parts.push(&arr);
                                                            let blob_opts = web_sys::BlobPropertyBag::new();
                                                            blob_opts.set_type("application/toml");
                                                            let blob = web_sys::Blob::new_with_u8_array_sequence_and_options(
                                                                &parts.into(),
                                                                &blob_opts,
                                                            ).expect("Blob");
                                                            let url = web_sys::Url::create_object_url_with_blob(&blob).expect("object URL");
                                                            let filename = format!(
                                                                "{}_schedule_{}.toml",
                                                                u,
                                                                chrono::Utc::now().format("%Y%m%d_%H%M%S")
                                                            );
                                                            if let Ok(a) = doc.create_element("a") {
                                                                let _ = a.set_attribute("href", &url);
                                                                let _ = a.set_attribute("download", &filename);
                                                                if let Some(anchor) = a.dyn_ref::<web_sys::HtmlAnchorElement>() {
                                                                    anchor.click();
                                                                }
                                                            }
                                                            web_sys::Url::revoke_object_url(&url).ok();
                                                        }
                                                    }
                                                });
                                            }, "Export TOML" }
                                            button { class: "btn btn-sm btn-outline-secondary", onclick: move |_| active_modal.set("toml_import".to_string()), "Import TOML" }
                                            button {
                                                class: "btn btn-sm btn-outline-primary",
                                                onclick: move |_| {
                                                    let u = url_for_recompute.clone();
                                                    let mut trigger = refresh_trigger;
                                                    spawn(async move {
                                                        if let Ok(_) = api::recompute_schedule(&u).await {
                                                            trigger.set(trigger() + 1);
                                                        }
                                                    });
                                                },
                                                "Recompute Times"
                                            }
                                            button {
                                                class: "btn btn-sm btn-outline-warning",
                                                title: "Show schedule warnings (unknown teams, cycles, missing match refs, double-bookings)",
                                                onclick: move |_| active_modal.set("schedule_warnings".to_string()),
                                                "⚠ Warnings"
                                            }
                                            button {
                                                class: if bulk_mode() { "btn btn-sm btn-secondary" } else { "btn btn-sm btn-outline-secondary" },
                                                title: "Click blocks to multi-select, then apply one length to all (Esc to exit)",
                                                onclick: move |_| {
                                                    let on = !bulk_mode();
                                                    bulk_mode.set(on);
                                                    if !on {
                                                        bulk_selected.set(Vec::new());
                                                    }
                                                },
                                                "Bulk change length"
                                            }
                                            div {
                                                class: "form-check form-switch mb-0 ms-1",
                                                title: "Place blocks at exact real times (confirmed/completed, falling back to estimates) instead of the planned-or-earlier rule.",
                                                input {
                                                    class: "form-check-input",
                                                    r#type: "checkbox",
                                                    role: "switch",
                                                    id: "showAsHappenedSwitch",
                                                    checked: "{show_as_happened}",
                                                    onchange: move |e| {
                                                        let on = e.value() == "true";
                                                        show_as_happened.set(on);
                                                        ls_set(EDIT_SHOW_AS_HAPPENED_KEY, if on { "1" } else { "0" });
                                                    }
                                                }
                                                label {
                                                    class: "form-check-label small",
                                                    r#for: "showAsHappenedSwitch",
                                                    "Show times as they happened"
                                                }
                                            }
                                    }
                                }
                            }
                        }
                    }

                    if editor {
                        if let Some(err) = edit_error() {
                            div { class: "alert alert-danger alert-dismissible d-flex align-items-center py-2 mb-2",
                                span { class: "flex-grow-1", "{err}" }
                                button {
                                    r#type: "button",
                                    class: "btn-close",
                                    "aria-label": "Dismiss",
                                    onclick: move |_| edit_error.set(None),
                                }
                            }
                        }
                        if bulk_mode() {
                            div { class: "card mb-2 border-secondary",
                                div { class: "card-body py-2 d-flex flex-wrap align-items-center gap-2",
                                    strong { class: "small", "Bulk length:" }
                                    span { class: "small text-muted",
                                        "click blocks to select — {bulk_selected().len()} selected"
                                    }
                                    label { class: "small mb-0 ms-2", "Length (min)" }
                                    input {
                                        class: "form-control form-control-sm d-inline-block",
                                        style: "width: 6rem;",
                                        r#type: "number",
                                        min: "1",
                                        value: "{bulk_length_input}",
                                        onkeydown: move |ev: Event<KeyboardData>| ev.stop_propagation(),
                                        oninput: move |e| {
                                            bulk_length_input.set(e.value().parse().unwrap_or(30));
                                        },
                                    }
                                    button {
                                        class: "btn btn-sm btn-success",
                                        disabled: bulk_selected().is_empty() || bulk_length_input() == 0,
                                        onclick: {
                                            let u = url.clone();
                                            move |_| {
                                                let u = u.clone();
                                                let ids = bulk_selected();
                                                let len = bulk_length_input();
                                                spawn(async move {
                                                    let req = BulkMatchLengthRequest {
                                                        match_ids: ids,
                                                        length: len,
                                                    };
                                                    match api::bulk_match_length(&u, &req).await {
                                                        Ok(res) => {
                                                            let skipped = res
                                                                .results
                                                                .iter()
                                                                .filter(|r| r.status != "updated")
                                                                .count();
                                                            if skipped > 0 {
                                                                edit_error.set(Some(format!(
                                                                    "Updated {} matches; {} skipped (locked / join / missing).",
                                                                    res.updated, skipped
                                                                )));
                                                            } else {
                                                                edit_error.set(None);
                                                            }
                                                            bulk_mode.set(false);
                                                            bulk_selected.set(Vec::new());
                                                            refresh();
                                                        }
                                                        Err(e) => edit_error.set(Some(e)),
                                                    }
                                                });
                                            }
                                        },
                                        "Apply"
                                    }
                                    button {
                                        class: "btn btn-sm btn-outline-secondary",
                                        onclick: move |_| {
                                            bulk_mode.set(false);
                                            bulk_selected.set(Vec::new());
                                        },
                                        "Cancel (Esc)"
                                    }
                                }
                            }
                        }
                    }

                    // Editor: schedule + docked editor card side by side on wide screens
                    // (card above the schedule on narrow ones). Public: plain block.
                    div { class: if editor { "schedule-editor-split" } else { "" },
                    div { class: if editor { "schedule-editor-main" } else { "" },
                    if view_mode() == "team" && focus_team_id().is_empty() {
                        div { class: "alert alert-info",
                            "Choose your team above to see only the matches you play or ref."
                        }
                    } else if view_mode() != "table" {
                        ScheduleTimeline {
                            data: data.clone(),
                            // Team view spans all fields; "By field" reuses the grid
                            // with a single concrete field (breaks/joins included).
                            selected_field: if view_mode() == "team" {
                                "all".to_string()
                            } else {
                                selected_field()
                            },
                            highlight_team: if view_mode() == "team" {
                                String::new()
                            } else {
                                highlight_team()
                            },
                            edit_mode: edit_mode && view_mode() == "timeline",
                            // "As happened" placement is an edit-mode-only concept.
                            show_as_happened: edit_mode && show_as_happened(),
                            vertical_scale: vertical_scale,
                            // Empty = multi-field grid; non-empty = single-column team view.
                            focus_team_id: if view_mode() == "team" {
                                focus_team_id()
                            } else {
                                String::new()
                            },
                            tournament_url: url.clone(),
                            editor: editor && view_mode() == "timeline",
                            bulk_select_active: bulk_mode(),
                            selected_ids: bulk_selected(),
                            // Pending-create placeholder: stays visible while the create card
                            // is open, mirroring the card's field/start/length.
                            pending_create: if editor && active_modal() == "match_create" {
                                create_ghost()
                            } else {
                                None
                            },
                            // Winner/Loser chips: only while a create/edit card is open and a
                            // team-ish input was the last-focused field (and not bulk-selecting).
                            result_pick_active: editor
                                && !bulk_mode()
                                && matches!(active_modal().as_str(), "match_create" | "match_edit")
                                && team_field_focus().is_some(),
                            on_pick_result: move |tok: String| insert_team_ref.set(Some(tok)),
                            on_edit_match: {
                                let matches_for_edit = data.matches.clone();
                                move |id: String| {
                                    // Bulk-length tool: clicks toggle selection instead of editing.
                                    if bulk_mode() {
                                        let mut sel = bulk_selected();
                                        if let Some(pos) = sel.iter().position(|s| s == &id) {
                                            sel.remove(pos);
                                        } else {
                                            sel.push(id);
                                        }
                                        bulk_selected.set(sel);
                                        return;
                                    }
                                    // Structural blocks (breaks/joins) are edited as a same-name group.
                                    team_field_focus.set(None);
                                    if let Some(m) = matches_for_edit.iter().find(|m| m.uuid == id) {
                                        if is_structural_match(m) {
                                            selected_break_group.set(m.name.clone());
                                            active_modal.set("break_group".to_string());
                                            return;
                                        }
                                    }
                                    selected_match_id.set(id);
                                    active_modal.set("match_edit".to_string());
                                }
                            },
                            on_drag_create: move |p: DragCreatePayload| {
                                create_prefill.set(Some(p));
                                create_prefill_nonce.set(create_prefill_nonce().wrapping_add(1));
                                team_field_focus.set(None);
                                active_modal.set("match_create".to_string());
                            },
                            on_move_match: {
                                let u = url.clone();
                                move |mc: MoveCommitPayload| {
                                    let u = u.clone();
                                    if !matches!(mc.schedule_type.as_str(), "STATIC" | "STATBREAK")
                                        && mc.new_prev_id.is_none()
                                    {
                                        edit_error.set(Some(format!(
                                            "{} matches need a previous match — drop the block after another match on the field.",
                                            mc.schedule_type
                                        )));
                                        return;
                                    }
                                    spawn(async move {
                                        let none_update = UpdateMatchRequest {
                                            field: None,
                                            schedule_type: None,
                                            length: None,
                                            start_time: None,
                                            previous_match_id: None,
                                            refs: None,
                                            team1: None,
                                            team2: None,
                                            set_type: None,
                                            nsets: None,
                                            stones_per_set: None,
                                            ribbon: None,
                                            skip_condition: None,
                                        };
                                        let res = match mc.schedule_type.as_str() {
                                            "STATIC" => {
                                                let req = UpdateMatchRequest {
                                                    field: mc.new_field.clone(),
                                                    start_time: mc.new_start_utc.clone(),
                                                    ..none_update
                                                };
                                                api::update_match(&u, &mc.match_id, &req).await
                                            }
                                            "STATBREAK" => {
                                                // Static breaks move as a group: shared start time.
                                                let req = UpdateBreakGroupRequest {
                                                    schedule_type: None,
                                                    length: None,
                                                    start_time: mc.new_start_utc.clone(),
                                                    fields: None,
                                                };
                                                api::update_break_group(&u, &mc.group_name, &req)
                                                    .await
                                            }
                                            _ => {
                                                let req = UpdateMatchRequest {
                                                    field: mc.new_field.clone(),
                                                    previous_match_id: mc.new_prev_id.clone(),
                                                    ..none_update
                                                };
                                                api::update_match(&u, &mc.match_id, &req).await
                                            }
                                        };
                                        match res {
                                            Ok(_) => {
                                                edit_error.set(None);
                                                refresh();
                                            }
                                            Err(e) => {
                                                edit_error.set(Some(e));
                                                // Refetch so the block snaps back to server truth.
                                                refresh();
                                            }
                                        }
                                    });
                                }
                            },
                            key_nav: key_nav,
                            on_key_nav_consumed: move |_| key_nav.set(None),
                        }
                    } else {
                        TableView {
                            data: data.clone(),
                            selected_field: selected_field(),
                            highlight_team: highlight_team(),
                            edit_mode: edit_mode,
                            debug_mode: debug_mode(),
                            show_as_happened: edit_mode && show_as_happened(),
                            tournament_url: url.clone(),
                            on_edit_match: {
                                let matches_for_edit = data.matches.clone();
                                move |id: String| {
                                    team_field_focus.set(None);
                                    if let Some(m) = matches_for_edit.iter().find(|m| m.uuid == id) {
                                        if is_structural_match(m) {
                                            selected_break_group.set(m.name.clone());
                                            active_modal.set("break_group".to_string());
                                            return;
                                        }
                                    }
                                    selected_match_id.set(id);
                                    active_modal.set("match_edit".to_string());
                                }
                            }
                        }
                    }

                    } // schedule-editor-main

                    // Docked editor card (editor only): create / edit / break-group forms.
                    // The schedule stays visible and interactive next to (or below) it.
                    if editor && active_modal() == "match_edit" {
                        div { class: "schedule-editor-panel", key: "edit-{selected_match_id()}",
                            EditMatchModal {
                                tournament_url: url.clone(),
                                match_id: selected_match_id(),
                                data: data.clone(),
                                team_field_focus: team_field_focus,
                                insert_team_ref: insert_team_ref,
                                on_close: move |_| {
                                    team_field_focus.set(None);
                                    active_modal.set("none".to_string());
                                },
                                on_save: move |_| {
                                    team_field_focus.set(None);
                                    active_modal.set("none".to_string());
                                    refresh();
                                }
                            }
                        }
                    }
                    if editor && active_modal() == "match_create" {
                        div { class: "schedule-editor-panel", key: "create-{create_prefill_nonce()}",
                            CreateMatchModal {
                                tournament_url: url.clone(),
                                data: data.clone(),
                                prefill_field: create_prefill().map(|p| p.field_name.clone()),
                                prefill_start_time: create_prefill()
                                    .map(|p| p.start_local.format("%Y-%m-%dT%H:%M").to_string()),
                                prefill_length: create_prefill().and_then(|p| p.length_min),
                                prefill_prev_match_id: create_prefill()
                                    .and_then(|p| p.prev_match_id.clone()),
                                default_schedule_type: default_match_type(),
                                show_as_happened: show_as_happened(),
                                create_ghost: create_ghost,
                                team_field_focus: team_field_focus,
                                insert_team_ref: insert_team_ref,
                                on_close: move |_| {
                                    create_prefill.set(None);
                                    create_ghost.set(None);
                                    team_field_focus.set(None);
                                    active_modal.set("none".to_string());
                                },
                                on_save: move |_| {
                                    create_prefill.set(None);
                                    create_ghost.set(None);
                                    team_field_focus.set(None);
                                    active_modal.set("none".to_string());
                                    refresh();
                                }
                            }
                        }
                    }
                    if editor && active_modal() == "break_group" {
                        div { class: "schedule-editor-panel", key: "group-{selected_break_group()}",
                            BreakGroupModal {
                                tournament_url: url.clone(),
                                group_name: selected_break_group(),
                                data: data.clone(),
                                on_close: move |_| active_modal.set("none".to_string()),
                                on_save: move |_| {
                                    active_modal.set("none".to_string());
                                    refresh();
                                }
                            }
                        }
                    }
                    } // schedule-editor-split

                    // Modals that don't need the schedule visible stay as real modals.
                    if active_modal() == "tags" {
                        TagsModal {
                            tournament_url: url.clone(),
                            data: data.clone(),
                            on_close: move |_| active_modal.set("none".to_string()),
                            on_change: move |_| refresh()
                        }
                    }
                    if active_modal() == "fields" {
                        FieldsModal {
                            tournament_url: url.clone(),
                            data: data.clone(),
                            on_close: move |_| active_modal.set("none".to_string()),
                            on_change: move |_| refresh()
                        }
                    }
                    if active_modal() == "toml_import" {
                        TOMLImportModal {
                            tournament_url: url.clone(),
                            on_close: move |_| active_modal.set("none".to_string()),
                            on_import: move |_| {
                                active_modal.set("none".to_string());
                                refresh();
                            },
                        }
                    }
                    if active_modal() == "schedule_warnings" {
                        ScheduleWarningsModal {
                            tournament_url: url.clone(),
                            on_close: move |_| active_modal.set("none".to_string()),
                        }
                    }
                }
            }
        }
        None => {
            // Check if it was an error or loading
            match val.read().as_ref() {
                Some(Err(e)) => rsx! { div { class: "alert alert-danger", "Error: {e}" } },
                _ => rsx! { div { class: "text-center mt-5", "Loading..." } },
            }
        }
    }
}

// ... Toolbar ...
// ... TableView ...
// ... TimelineView ...
// ... EditMatchModal ...

/// Matches on the given field, sorted by nominal_start_time descending (most recent first).
fn matches_on_field_sorted<'a>(
    matches: &'a [MatchSetupData],
    field_name: &str,
    exclude_uuid: Option<&str>,
) -> Vec<&'a MatchSetupData> {
    let mut v: Vec<_> = matches
        .iter()
        .filter(|m| m.field.as_deref() == Some(field_name))
        .filter(|m| exclude_uuid.map_or(true, |id| m.uuid != id))
        .collect();
    v.sort_by(|a, b| {
        b.nominal_start_time
            .as_deref()
            .cmp(&a.nominal_start_time.as_deref())
    });
    v
}

/// Skip condition help modal
#[component]
fn SkipConditionHelpModal(on_close: EventHandler<()>) -> Element {
    rsx! {
        div {
            class: "modal d-block",
            style: "background: rgba(0,0,0,0.5); z-index: 1060;",
            tabindex: -1,
            div {
                class: "modal-dialog modal-lg",
                style: "z-index: 1061;",
                div { class: "modal-content",
                    div { class: "modal-header",
                        h5 { class: "modal-title", "Skip Condition Help" }
                        button { class: "btn-close", "type": "button", onclick: move |_| on_close.call(()) }
                    }
                    div { class: "modal-body",
                          p {
                  "The skip condition uses "
                  Link {
                  to: Route::ArctosScheduleScript {},
                  "Arctos Schedule Script"
                  }
                  " to express boolean conditions. If it evaluates to true, the match will be skipped. This evaluation happens when this match's last dependency becomes finished or skipped. If this match is not skipped, the skip condition will be re-evaluated every time a match starts or finishes until it is started or the skip condition evaluates to true and it gets skipped."
              }

                        h6 { class: "mt-3", "Examples" }
                        ul {
                            li { code { "(== 0 (losses [TeamName]))" } " - Skip if team has no losses" }
                            li { code { "(> (wins [TeamA]) (wins [TeamB]))" } " - Skip if TeamA has more wins than TeamB" }
                            li { code { "(== (winner {{Match1}}) [TeamName])" } " - Skip if TeamName won Match1" }
                        }

                        p { class: "text-muted small mt-3",
                            strong { "Note:" } " The expression must eventually evaluate to a boolean (true/false), but it doesn't need to simplify to a boolean immediately. "
                            "There is very minimal error checking, so be careful. "
                            strong { "You can deadlock your tournament if you do this wrong!" }
                        }
                    }
                    div { class: "modal-footer",
                        button { class: "btn btn-secondary", "type": "button", onclick: move |_| on_close.call(()), "Close" }
                    }
                }
            }
        }
    }
}

#[component]
fn CreateMatchModal(
    tournament_url: String,
    data: ScheduleSetupResponse,
    on_close: EventHandler<()>,
    on_save: EventHandler<()>,
    /// Drag-to-create prefill: field column the drag happened in.
    #[props(default)]
    prefill_field: Option<String>,
    /// Drag-to-create prefill: datetime-local start (STATIC semantics).
    #[props(default)]
    prefill_start_time: Option<String>,
    /// Drag-to-create prefill: drag extent in minutes.
    #[props(default)]
    prefill_length: Option<u32>,
    /// Drag-to-create prefill: chain-position default for dynamic types.
    #[props(default)]
    prefill_prev_match_id: Option<String>,
    /// Toolbar "Default new match type" (any schedule type; structural types
    /// open the card in the corresponding group form).
    #[props(default)]
    default_schedule_type: Option<String>,
    /// Page-level "show times as they happened" toggle (for previous-match lookup).
    #[props(default = false)]
    show_as_happened: bool,
    /// Pending-create placeholder on the timeline; the card keeps it in sync
    /// with its field/start/length values.
    create_ghost: Signal<Option<PendingCreateGhost>>,
    /// Last-focused team-ish input ("team1" | "team2" | "refs").
    team_field_focus: Signal<Option<String>>,
    /// Winner/Loser token queued by the timeline chips for insertion.
    insert_team_ref: Signal<Option<String>>,
) -> Element {
    let name = use_signal(|| "".to_string());
    let mut field = use_signal({
        let f = prefill_field.clone();
        move || f.unwrap_or_default()
    });
    let initial_type = default_schedule_type
        .clone()
        .filter(|t| {
            matches!(
                t.as_str(),
                "STATIC" | "SAFE" | "FAST" | "BREAK" | "STATBREAK" | "JOIN"
            )
        })
        .unwrap_or_else(|| "STATIC".to_string());
    let schedule_type = use_signal({
        let t = initial_type.clone();
        move || t
    });
    let length = use_signal(move || prefill_length.unwrap_or(60));
    let start_time = use_signal({
        let s = prefill_start_time.clone();
        move || s.unwrap_or_default()
    });
    // Dynamic default types start with the drag-derived previous match preselected.
    let mut previous_match_id = use_signal({
        let init = if matches!(initial_type.as_str(), "SAFE" | "FAST") {
            prefill_prev_match_id.clone().unwrap_or_default()
        } else {
            String::new()
        };
        move || init
    });
    let mut refs = use_signal(|| "".to_string());
    let mut team1 = use_signal(|| "".to_string());
    let mut team2 = use_signal(|| "".to_string());
    let set_type = use_signal(|| "SETS".to_string());
    let nsets = use_signal(|| 3u32);
    let stones_per_set = use_signal(|| 100u32);
    let ribbon = use_signal(|| false);
    let mut skip_condition = use_signal(|| "".to_string());
    let mut skip_condition_help_open = use_signal(|| false);
    let mut skip_condition_validity = use_signal(|| None::<Result<(), String>>);
    // Break-group mode (type BREAK/STATBREAK): fields the break spans.
    // Drag-to-create seeds the dragged column.
    let mut break_fields = use_signal({
        let f = prefill_field.clone();
        move || f.map(|f| vec![f]).unwrap_or_default()
    });

    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);

    // Keep the timeline's pending-create placeholder in sync with the card's
    // field(s) / start-time / length. Group forms (break/join) place one
    // placeholder on every checked field. Cleared by the page on save/cancel.
    {
        let mut create_ghost = create_ghost;
        use_effect(move || {
            let st = schedule_type();
            let is_group = matches!(st.as_str(), "BREAK" | "STATBREAK" | "JOIN");
            let field_names: Vec<String> = if is_group {
                break_fields()
            } else {
                let f = field();
                if f.is_empty() { Vec::new() } else { vec![f] }
            };
            let parsed = chrono::NaiveDateTime::parse_from_str(
                start_time().trim(),
                "%Y-%m-%dT%H:%M",
            )
            .ok();
            let next = match (field_names.is_empty(), parsed) {
                (false, Some(start_local)) => Some(PendingCreateGhost {
                    field_names,
                    start_local,
                    length_min: (length() as i64).max(10),
                    is_join: st == "JOIN",
                }),
                _ => None,
            };
            if *create_ghost.peek() != next {
                create_ghost.set(next);
            }
        });
    }

    // Consume Winner/Loser tokens queued by the timeline chips into whichever
    // team-ish input was focused last (refs appends; team1/team2 replace).
    {
        let mut insert_team_ref = insert_team_ref;
        use_effect(move || {
            if let Some(tok) = insert_team_ref() {
                match team_field_focus.peek().as_deref() {
                    Some("team1") => team1.set(tok.clone()),
                    Some("team2") => team2.set(tok.clone()),
                    Some("refs") => {
                        let cur = refs
                            .peek()
                            .trim()
                            .trim_end_matches(',')
                            .trim()
                            .to_string();
                        refs.set(if cur.is_empty() {
                            tok.clone()
                        } else {
                            format!("{cur}, {tok}")
                        });
                    }
                    _ => {}
                }
                insert_team_ref.set(None);
            }
        });
    }

    #[cfg(target_arch = "wasm32")]
    use_effect(move || {
        spawn(async move {
            gloo_timers::future::TimeoutFuture::new(50).await;
            if let Some(window) = web_sys::window() {
                if let Some(doc) = window.document() {
                    if let Ok(Some(el)) = doc.query_selector("#new-match-name-input") {
                        if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                            let _ = input.focus();
                        }
                    }
                }
            }
        });
    });

    let matches_on_field = matches_on_field_sorted(&data.matches, &field(), None);

    // Previous match derived from the card's start time: the match on `field_name`
    // whose displayed interval most closely precedes it (same rule as drag-create).
    let compute_prev_from_start = {
        let data_prev = data.clone();
        move |field_name: &str, start_local_str: &str| -> Option<String> {
            let start_local =
                chrono::NaiveDateTime::parse_from_str(start_local_str.trim(), "%Y-%m-%dT%H:%M")
                    .ok()?;
            latest_match_before(
                &data_prev.matches,
                field_name,
                start_local,
                "",
                show_as_happened,
                schedule_tz_offset_minutes(),
            )
            .map(|(u, _, _)| u)
        }
    };

    // Note: never auto-assign length / format from the previous match — the
    // drag-derived (or default) length stays unless the user types a new one.
    let compute_prev_for_field = compute_prev_from_start.clone();
    let mut on_field_change = move |new_field: String| {
        field.set(new_field.clone());
        previous_match_id.set("".to_string());
        // Dynamic types: recompute the previous match on the new field from the
        // card's start time.
        if !new_field.is_empty() && matches!(schedule_type().as_str(), "SAFE" | "FAST" | "JOIN") {
            if let Some(prev) = compute_prev_for_field(&new_field, &start_time()) {
                previous_match_id.set(prev);
            }
        }
    };

    let data_create_validate = data.clone();
    let validate_create_rc: Rc<RefCell<Box<dyn FnMut() -> bool>>> =
        Rc::new(RefCell::new(Box::new(move || {
            let st = schedule_type();
            if st == "BREAK" || st == "STATBREAK" || st == "JOIN" {
                // Structural groups: one row per selected field; no previous
                // match needed (each row appends at its field's chain tail).
                if break_fields().is_empty() {
                    error.set(Some("Select at least one field.".to_string()));
                    return false;
                }
                if st == "STATBREAK" && start_time().trim().is_empty() {
                    error.set(Some(
                        "Start time is required for a static break.".to_string(),
                    ));
                    return false;
                }
                return true;
            }
            if st == "FAST" || st == "SAFE" {
                let prev_id = previous_match_id().trim().to_string();
                if prev_id.is_empty() {
                    error.set(Some(
                        "Previous match is required for Fast and Safe matches."
                            .to_string(),
                    ));
                    return false;
                }
                let current_field = field();
                if current_field.is_empty() {
                    error.set(Some(
                        "Field is required when using a previous match.".to_string(),
                    ));
                    return false;
                }
                if let Some(prev_m) = data_create_validate
                    .matches
                    .iter()
                    .find(|x| x.uuid == prev_id)
                {
                    if prev_m.field.as_deref() != Some(current_field.as_str()) {
                        error.set(Some(
                            "Previous match must be on the same field.".to_string(),
                        ));
                        return false;
                    }
                }
            }
            true
        })));
    let validate_create_rc2 = validate_create_rc.clone();

    let tournament_url_submit = tournament_url.clone();
    let onsubmit = move |ev: Event<FormData>| {
        ev.prevent_default();
        if !validate_create_rc.borrow_mut()() {
            return;
        }
        let tournament_url = tournament_url_submit.clone();
        let on_save = on_save.clone();
        spawn(async move {
            saving.set(true);
            error.set(None);
            if matches!(schedule_type().as_str(), "BREAK" | "STATBREAK" | "JOIN") {
                let is_join = schedule_type() == "JOIN";
                let req = CreateBreakGroupRequest {
                    name: name(),
                    schedule_type: schedule_type(),
                    length: if is_join { 0 } else { length() },
                    fields: break_fields(),
                    start_time: if schedule_type() == "STATBREAK" {
                        local_datetime_to_utc_iso(&start_time()).or_else(|| Some(start_time()))
                    } else {
                        None
                    },
                };
                match api::create_break_group(&tournament_url, &req).await {
                    Ok(_) => {
                        saving.set(false);
                        on_save.call(());
                    }
                    Err(e) => {
                        error.set(Some(e));
                        saving.set(false);
                    }
                }
                return;
            }
            if (schedule_type() == "SAFE" || schedule_type() == "FAST")
                && !skip_condition().trim().is_empty()
            {
                if let Some(Err(msg)) = skip_condition_validity() {
                    error.set(Some(format!("Skip condition: {msg}")));
                    saving.set(false);
                    return;
                }
                match api::validate_dsl(&tournament_url, &skip_condition()).await {
                    Ok(res) => {
                        if !res.valid {
                            error.set(Some(format!(
                                "Skip condition: {}",
                                res.error.unwrap_or_else(|| "invalid".to_string())
                            )));
                            saving.set(false);
                            return;
                        }
                        if !res.result_type.iter().any(|t| t == "BOOL") {
                            let got = if res.result_type.is_empty() {
                                "unknown".to_string()
                            } else {
                                res.result_type.join(" | ")
                            };
                            error.set(Some(format!(
                                "Skip condition must evaluate to BOOL, got {got}."
                            )));
                            saving.set(false);
                            return;
                        }
                    }
                    Err(e) => {
                        error.set(Some(format!("Skip condition: {}", e)));
                        saving.set(false);
                        return;
                    }
                }
            }
            let refs_vec: Vec<String> = refs()
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            let len = if schedule_type() == "JOIN" {
                Some(0u32)
            } else {
                Some(length())
            };
            let req = CreateMatchRequest {
                name: name(),
                field: if field().is_empty() {
                    None
                } else {
                    Some(field())
                },
                schedule_type: Some(schedule_type()),
                length: len,
                start_time: if start_time().is_empty() {
                    None
                } else {
                    local_datetime_to_utc_iso(&start_time()).or_else(|| Some(start_time()))
                },
                previous_match_id: if previous_match_id().is_empty() {
                    None
                } else {
                    Some(previous_match_id())
                },
                refs: Some(refs_vec),
                team1: if team1().is_empty() {
                    None
                } else {
                    Some(team1())
                },
                team2: if team2().is_empty() {
                    None
                } else {
                    Some(team2())
                },
                set_type: Some(set_type()),
                nsets: Some(nsets()),
                stones_per_set: Some(stones_per_set()),
                ribbon: Some(ribbon()),
                skip_condition: Some(skip_condition()),
            };
            match api::create_match(&tournament_url, &req).await {
                Ok(_) => {
                    saving.set(false);
                    on_save.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        });
    };
    let tournament_url_keydown = tournament_url.clone();
    let submit_create_rc: Rc<RefCell<Box<dyn FnMut()>>> =
        Rc::new(RefCell::new(Box::new(move || {
            if !validate_create_rc2.borrow_mut()() {
                return;
            }
            let tournament_url = tournament_url_keydown.clone();
            let on_save = on_save.clone();
            spawn(async move {
                saving.set(true);
                error.set(None);
                if matches!(schedule_type().as_str(), "BREAK" | "STATBREAK" | "JOIN") {
                    let is_join = schedule_type() == "JOIN";
                    let req = CreateBreakGroupRequest {
                        name: name(),
                        schedule_type: schedule_type(),
                        length: if is_join { 0 } else { length() },
                        fields: break_fields(),
                        start_time: if schedule_type() == "STATBREAK" {
                            local_datetime_to_utc_iso(&start_time()).or_else(|| Some(start_time()))
                        } else {
                            None
                        },
                    };
                    match api::create_break_group(&tournament_url, &req).await {
                        Ok(_) => {
                            saving.set(false);
                            on_save.call(());
                        }
                        Err(e) => {
                            error.set(Some(e));
                            saving.set(false);
                        }
                    }
                    return;
                }
                if (schedule_type() == "SAFE" || schedule_type() == "FAST")
                    && !skip_condition().trim().is_empty()
                {
                    if let Some(Err(msg)) = skip_condition_validity() {
                        error.set(Some(format!("Skip condition: {msg}")));
                        saving.set(false);
                        return;
                    }
                    if let Ok(res) = api::validate_dsl(&tournament_url, &skip_condition()).await {
                        if !res.valid {
                            error.set(Some(format!(
                                "Skip condition: {}",
                                res.error.unwrap_or_else(|| "invalid".to_string())
                            )));
                            saving.set(false);
                            return;
                        }
                        if !res.result_type.iter().any(|t| t == "BOOL") {
                            let got = if res.result_type.is_empty() {
                                "unknown".to_string()
                            } else {
                                res.result_type.join(" | ")
                            };
                            error.set(Some(format!(
                                "Skip condition must evaluate to BOOL, got {got}."
                            )));
                            saving.set(false);
                            return;
                        }
                    }
                }
                let refs_vec: Vec<String> = refs()
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();
                let len = if schedule_type() == "JOIN" {
                    Some(0u32)
                } else {
                    Some(length())
                };
                let req = CreateMatchRequest {
                    name: name(),
                    field: if field().is_empty() {
                        None
                    } else {
                        Some(field())
                    },
                    schedule_type: Some(schedule_type()),
                    length: len,
                    start_time: if start_time().is_empty() {
                        None
                    } else {
                        local_datetime_to_utc_iso(&start_time()).or_else(|| Some(start_time()))
                    },
                    previous_match_id: if previous_match_id().is_empty() {
                        None
                    } else {
                        Some(previous_match_id())
                    },
                    refs: Some(refs_vec),
                    team1: if team1().is_empty() {
                        None
                    } else {
                        Some(team1())
                    },
                    team2: if team2().is_empty() {
                        None
                    } else {
                        Some(team2())
                    },
                    set_type: Some(set_type()),
                    nsets: Some(nsets()),
                    stones_per_set: Some(stones_per_set()),
                    ribbon: Some(ribbon()),
                    skip_condition: Some(skip_condition()),
                };
                match api::create_match(&tournament_url, &req).await {
                    Ok(_) => {
                        saving.set(false);
                        on_save.call(());
                    }
                    Err(e) => {
                        error.set(Some(e));
                        saving.set(false);
                    }
                }
            });
        })));
    let submit_create_rc2 = submit_create_rc.clone();
    let form_keydown = move |ev: Event<KeyboardData>| {
        let key = ev.key().to_string();
        if key == "Enter" {
            if ev.modifiers().contains(Modifiers::SHIFT) {
                ev.prevent_default();
                ev.stop_propagation();
                submit_create_rc.borrow_mut()();
            } else {
                ev.prevent_default();
            }
        }
    };
    let modal_keydown = move |ev: Event<KeyboardData>| {
        let key = ev.key().to_string();
        if key == "Escape" {
            ev.prevent_default();
            on_close.call(());
        } else if key == "Enter" && ev.modifiers().contains(Modifiers::SHIFT) {
            ev.prevent_default();
            ev.stop_propagation();
            submit_create_rc2.borrow_mut()();
        }
    };

    rsx! {
        div {
            // Docked editor card (not a modal): the schedule stays visible and
            // interactive beside/below it.
            div {
                class: "card schedule-editor-card",
                tabindex: -1,
                onkeydown: modal_keydown,
                div { class: "card-header d-flex justify-content-between align-items-center",
                    h5 { class: "mb-0", "New Match" }
                    button {
                        class: "btn-close",
                        r#type: "button",
                        "aria-label": "Close",
                        onclick: move |_| on_close.call(()),
                    }
                }
                    div { class: "card-body",
                        if let Some(err) = error() {
                            div { class: "alert alert-danger", "{err}" }
                        }
                        form {
                            onsubmit: onsubmit,
                            onkeydown: form_keydown,

                            div { class: "row",
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Match Name" }
                                        input {
                                            id: "new-match-name-input",
                                            class: "form-control",
                                            "type": "text",
                                            value: "{name}",
                                            oninput: move |e| { let mut name = name; name.set(e.value()); },
                                            required: true,
                                        }
                                    }
                                }
                                if !matches!(schedule_type().as_str(), "BREAK" | "STATBREAK" | "JOIN") {
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Field" }
                                            select {
                                                class: "form-select",
                                                value: "{field}",
                                                onchange: move |e| on_field_change(e.value()),
                                                option { value: "", "Select Field" }
                                                for f in &data.fields {
                                                    option { value: "{f.name}", "{f.name}" }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            div { class: "row",
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Type" }
                                        select { class: "form-select", value: "{schedule_type}", onchange: {
                                            let prefill_prev = prefill_prev_match_id.clone();
                                            let compute_prev = compute_prev_from_start.clone();
                                            move |e: Event<FormData>| {
                                                let mut schedule_type = schedule_type;
                                                let v = e.value();
                                                schedule_type.set(v.clone());
                                                // Switching to a dynamic type: auto-select the previous
                                                // match on the card's field from the card's start time
                                                // (i.e. the clicked/dragged position), falling back to
                                                // the drag-derived default.
                                                if matches!(v.as_str(), "SAFE" | "FAST" | "JOIN") {
                                                    let prev = compute_prev(&field(), &start_time())
                                                        .or_else(|| prefill_prev.clone());
                                                    previous_match_id.set(prev.unwrap_or_default());
                                                }
                                            }
                                        },
                                            option { value: "STATIC", "Static" }
                                            option { value: "SAFE", "Safe" }
                                            option { value: "FAST", "Fast" }
                                            option { value: "BREAK", "Break" }
                                            option { value: "STATBREAK", "Static Break" }
                                            option { value: "JOIN", "Join" }
                                        }
                                    }
                                }
                                if schedule_type() != "JOIN" {
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Length (min)" }
                                            input { class: "form-control", "type": "number", min: "0", value: "{length}", oninput: move |e| { let mut length = length; length.set(e.value().parse().unwrap_or(60)); } }
                                        }
                                    }
                                }
                            }

                            if schedule_type() == "STATIC" || schedule_type() == "STATBREAK" {
                                div { class: "mb-3",
                                    label { class: "form-label", "Start Time" }
                                    input { class: "form-control", "type": "datetime-local", value: "{start_time}", oninput: move |e| { let mut start_time = start_time; start_time.set(e.value()); } }
                                }
                            } else if schedule_type() == "SAFE" || schedule_type() == "FAST" {
                                div { class: "mb-3",
                                    label { class: "form-label", "Previous Match" }
                                    select { class: "form-select", value: "{previous_match_id}", onchange: move |e| previous_match_id.set(e.value()),
                                        option { value: "", "None" }
                                        for m in &matches_on_field {
                                            option { value: "{m.uuid}", "{m.name}" }
                                        }
                                    }
                                }
                            }

                            if matches!(schedule_type().as_str(), "BREAK" | "STATBREAK" | "JOIN") {
                                div { class: "mb-3",
                                    label { class: "form-label", "Fields" }
                                    {
                                        let all_field_names: Vec<String> = data.fields.iter().map(|f| f.name.clone()).collect();
                                        let all_selected = !all_field_names.is_empty()
                                            && all_field_names.iter().all(|f| break_fields().contains(f));
                                        rsx! {
                                            SelectAllToggle {
                                                all_selected: all_selected,
                                                on_toggle: move |select: bool| {
                                                    if select {
                                                        break_fields.set(all_field_names.clone());
                                                    } else {
                                                        break_fields.set(Vec::new());
                                                    }
                                                },
                                            }
                                        }
                                    }
                                    div { class: "form-text mb-1",
                                        if schedule_type() == "JOIN" {
                                            "One join is created per selected field, appended at the end of each field's chain. Each field's schedule continues only once all joined fields reach it."
                                        } else {
                                            "One break is created per selected field. New breaks are appended at the end of each field's chain, and same-name breaks always start together."
                                        }
                                    }
                                    div { class: "d-flex flex-wrap gap-3",
                                        for f in &data.fields {
                                            {
                                                let fname = f.name.clone();
                                                let checked = break_fields().contains(&fname);
                                                rsx! {
                                                    div { class: "form-check form-check-inline", key: "{f.id}",
                                                        input {
                                                            class: "form-check-input",
                                                            "type": "checkbox",
                                                            id: "create-break-field-{f.id}",
                                                            checked: checked,
                                                            onchange: move |e| {
                                                                let mut v = break_fields();
                                                                if e.value() == "true" {
                                                                    if !v.contains(&fname) {
                                                                        v.push(fname.clone());
                                                                    }
                                                                } else {
                                                                    v.retain(|x| x != &fname);
                                                                }
                                                                break_fields.set(v);
                                                            }
                                                        }
                                                        label { class: "form-check-label", "for": "create-break-field-{f.id}", "{f.name}" }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            if schedule_type() == "STATIC" || schedule_type() == "SAFE" || schedule_type() == "FAST" {
                                div { class: "row",
                                    // Focus tracking feeds the timeline's Winner/Loser hover chips.
                                    div { class: "col-md-6",
                                        onfocusin: move |_| {
                                            let mut t = team_field_focus;
                                            t.set(Some("team1".to_string()));
                                        },
                                        TeamSelectionField {
                                            label: "Team 1".to_string(),
                                            team_options: data.team_options.clone(),
                                            tags: data.tags.clone(),
                                            matches: data.matches.clone(),
                                            value: team1(),
                                            on_change: move |s| team1.set(s),
                                            multiple: false,
                                            placeholder: "team, match winner/loser, or tag".to_string(),
                                            help_text: Some("team, match winner/loser, or tag".to_string()),
                                        }
                                    }
                                    div { class: "col-md-6",
                                        onfocusin: move |_| {
                                            let mut t = team_field_focus;
                                            t.set(Some("team2".to_string()));
                                        },
                                        TeamSelectionField {
                                            label: "Team 2".to_string(),
                                            team_options: data.team_options.clone(),
                                            tags: data.tags.clone(),
                                            matches: data.matches.clone(),
                                            value: team2(),
                                            on_change: move |s| team2.set(s),
                                            multiple: false,
                                            placeholder: "team, match winner/loser, or tag".to_string(),
                                            help_text: Some("team, match winner/loser, or tag".to_string()),
                                        }
                                    }
                                }
                                div {
                                    onfocusin: move |_| {
                                        let mut t = team_field_focus;
                                        t.set(Some("refs".to_string()));
                                    },
                                    TeamSelectionField {
                                        label: "Referees".to_string(),
                                        team_options: data.team_options.clone(),
                                        tags: data.tags.clone(),
                                        matches: data.matches.clone(),
                                        value: refs(),
                                        on_change: move |s| refs.set(s),
                                        multiple: true,
                                        placeholder: "(optional) teams, match winners/losers, or tags".to_string(),
                                        help_text: Some("(optional) teams, match winners/losers, or tags".to_string()),
                                    }
                                }
                                div { class: "row",
                                    div { class: "col-md-4",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Format" }
                                            select { class: "form-select", value: "{set_type}", onchange: move |e| { let mut set_type = set_type; set_type.set(e.value()); },
                                                option { value: "SETS", "Sets" }
                                                option { value: "STONES", "Stones" }
                                            }
                                        }
                                    }
                                    div { class: "col-md-4",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Number of sets" }
                                            input { class: "form-control", "type": "number", min: "1", value: "{nsets}", oninput: move |e| { let mut nsets = nsets; nsets.set(e.value().parse().unwrap_or(3)); } }
                                        }
                                    }
                                    if set_type() == "STONES" {
                                        div { class: "col-md-4",
                                            div { class: "mb-3",
                                                label { class: "form-label", "Stones per set" }
                                                input { class: "form-control", "type": "number", min: "1", value: "{stones_per_set}", oninput: move |e| { let mut stones_per_set = stones_per_set; stones_per_set.set(e.value().parse().unwrap_or(100)); } }
                                            }
                                        }
                                    }
                                }
                                div { class: "mb-3",
                                    div { class: "form-check",
                                        input { class: "form-check-input", "type": "checkbox", id: "create-ribbon", checked: "{ribbon}", onchange: move |e| { let mut ribbon = ribbon; ribbon.set(e.value() == "true"); } }
                                        label { class: "form-check-label", "for": "create-ribbon", "Ribbon game" }
                                    }
                                }
                                if schedule_type() == "SAFE" || schedule_type() == "FAST" {
                                    div { class: "mb-3",
                                        label { class: "form-label", "Skip condition" }
                                        div { class: "form-text mb-1",
                                            "Optional expression that evaluates to a boolean. If true, this match will be skipped. "
                                            a {
                                                href: "#",
                                                class: "text-decoration-none",
                                                onclick: move |ev: Event<MouseData>| {
                                                    ev.prevent_default();
                                                    skip_condition_help_open.set(true);
                                                },
                                                "(skip condition help)"
                                            }
                                        }
                                        AssEntry {
                                            id_suffix: "create".to_string(),
                                            value: skip_condition(),
                                            on_change: move |v| skip_condition.set(v),
                                            team_options: data.team_options.clone(),
                                            tags: data.tags.clone(),
                                            matches: data.matches.clone(),
                                            tournament_url: tournament_url.clone(),
                                            placeholder: "e.g. (== 0 (losses [Team]))".to_string(),
                                            expected_type: vec!["BOOL".to_string()],
                                            on_validity_change: move |v: Option<Result<(), String>>| skip_condition_validity.set(v),
                                        }
                                    }
                                }
                            }

                            div { class: "modal-footer",
                                button { class: "btn btn-secondary", "type": "button", onclick: move |_| on_close.call(()), "Cancel (Esc)" }
                                button { class: "btn btn-success", "type": "submit", disabled: "{saving}",
                                    if saving() { span { class: "spinner-border spinner-border-sm me-2" } }
                                    "Save (⇧↵)"
                                }
                            }
                        }
                    }
            }
            if skip_condition_help_open() {
                SkipConditionHelpModal { on_close: move |_| skip_condition_help_open.set(false) }
            }
        }
    }
}

/// Small "Select all" / "Select none" toggle for a checkbox group, shown next
/// to the group's label.
#[component]
fn SelectAllToggle(all_selected: bool, on_toggle: EventHandler<bool>) -> Element {
    rsx! {
        button {
            class: "btn btn-sm btn-outline-secondary py-0 ms-2",
            "type": "button",
            onclick: move |_| on_toggle.call(!all_selected),
            if all_selected { "Select none" } else { "Select all" }
        }
    }
}

/// Edit a structural group: every same-name BREAK/STATBREAK/JOIN row across
/// fields at once. Members are derived from the already-loaded schedule data;
/// edits go through the break-group endpoints (shared length / start time,
/// field add/remove, whole-group delete). JOIN groups expose only field
/// membership: no length or start time.
#[component]
fn BreakGroupModal(
    tournament_url: String,
    group_name: String,
    data: ScheduleSetupResponse,
    on_close: EventHandler<()>,
    on_save: EventHandler<()>,
) -> Element {
    let members: Vec<MatchSetupData> = data
        .matches
        .iter()
        .filter(|m| m.name == group_name && is_structural_match(m))
        .cloned()
        .collect();

    if members.is_empty() {
        return rsx! { div { "Break group not found" } };
    }
    let first = members[0].clone();
    let group_type = first
        .schedule_type
        .clone()
        .unwrap_or_else(|| "BREAK".to_string());
    let is_statbreak = group_type == "STATBREAK";
    let is_join = group_type == "JOIN";
    let type_label = if is_join {
        "Join"
    } else if is_statbreak {
        "Static Break"
    } else {
        "Break"
    };
    let type_noun = if is_join { "join" } else { "break" };

    let mut length = use_signal(|| first.nominal_length.unwrap_or(30));
    // Break↔Join whole-group conversion (STATBREAK groups don't convert).
    let mut sel_type = use_signal(|| group_type.clone());
    let mut start_time = use_signal(|| {
        first
            .nominal_start_time
            .as_deref()
            .and_then(utc_iso_to_local_datetime_input)
            .unwrap_or_default()
    });
    let mut fields_sel = use_signal(|| {
        members
            .iter()
            .filter_map(|m| m.field.clone())
            .filter(|f| !f.is_empty())
            .collect::<Vec<String>>()
    });
    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);

    let url_save = tournament_url.clone();
    let name_save = group_name.clone();
    let group_type_save = group_type.clone();
    let do_save = move |_| {
        if fields_sel().is_empty() {
            error.set(Some(format!(
                "A {type_noun} group needs at least one field. Use Delete to remove it entirely."
            )));
            return;
        }
        if is_statbreak && start_time().trim().is_empty() {
            error.set(Some("Start time is required for a static break.".to_string()));
            return;
        }
        let u = url_save.clone();
        let n = name_save.clone();
        let on_save = on_save.clone();
        let group_type_now = group_type_save.clone();
        spawn(async move {
            saving.set(true);
            error.set(None);
            let converting = sel_type() != group_type_now;
            let now_join = sel_type() == "JOIN";
            // JOIN groups only edit field membership: no length/start.
            let req = UpdateBreakGroupRequest {
                schedule_type: if converting { Some(sel_type()) } else { None },
                length: if now_join { None } else { Some(length()) },
                start_time: if is_statbreak {
                    local_datetime_to_utc_iso(&start_time()).or_else(|| Some(start_time()))
                } else {
                    None
                },
                fields: Some(fields_sel()),
            };
            match api::update_break_group(&u, &n, &req).await {
                Ok(_) => {
                    saving.set(false);
                    on_save.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        });
    };

    let url_delete = tournament_url.clone();
    let name_delete = group_name.clone();
    let do_delete = move |_| {
        let u = url_delete.clone();
        let n = name_delete.clone();
        let on_save = on_save.clone();
        spawn(async move {
            saving.set(true);
            error.set(None);
            match api::delete_break_group(&u, &n).await {
                Ok(_) => {
                    saving.set(false);
                    on_save.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        });
    };

    let modal_keydown = move |ev: Event<KeyboardData>| {
        if ev.key().to_string() == "Escape" {
            ev.prevent_default();
            on_close.call(());
        }
    };

    rsx! {
        // Docked editor card (not a modal): the schedule stays visible and
        // interactive beside/below it.
        div {
            class: "card schedule-editor-card",
            tabindex: -1,
            onkeydown: modal_keydown,
            div { class: "card-header d-flex justify-content-between align-items-center",
                h5 { class: "mb-0", "Edit {type_label}: {group_name}" }
                button {
                    class: "btn-close",
                    r#type: "button",
                    "aria-label": "Close",
                    onclick: move |_| on_close.call(()),
                }
            }
                    div { class: "card-body",
                        if let Some(err) = error() {
                            div { class: "alert alert-danger", "{err}" }
                        }
                        div { class: "form-text mb-2",
                            if is_join {
                                "Edits apply to every field's copy of this join. Each field's schedule continues only once all joined fields reach it."
                            } else {
                                "Edits apply to every field's copy of this break. Same-name breaks always start together."
                            }
                        }
                        // Whole-group Break↔Join conversion. Static breaks don't
                        // convert (create one deliberately, with a start time).
                        if !is_statbreak {
                            div { class: "mb-3",
                                label { class: "form-label", "Type" }
                                select {
                                    class: "form-select",
                                    value: "{sel_type}",
                                    onchange: move |e| sel_type.set(e.value()),
                                    option { value: "BREAK", selected: sel_type() == "BREAK", "Break" }
                                    option { value: "JOIN", selected: sel_type() == "JOIN", "Join" }
                                }
                            }
                        }
                        if sel_type() != "JOIN" {
                            div { class: "row",
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Length (min)" }
                                        input {
                                            class: "form-control",
                                            "type": "number",
                                            min: "0",
                                            value: "{length}",
                                            oninput: move |e| length.set(e.value().parse().unwrap_or(30)),
                                        }
                                    }
                                }
                                if is_statbreak {
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Start Time" }
                                            input {
                                                class: "form-control",
                                                "type": "datetime-local",
                                                value: "{start_time}",
                                                oninput: move |e| start_time.set(e.value()),
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        div { class: "mb-3",
                            label { class: "form-label", "Fields" }
                            {
                                let all_field_names: Vec<String> = data.fields.iter().map(|f| f.name.clone()).collect();
                                let all_selected = !all_field_names.is_empty()
                                    && all_field_names.iter().all(|f| fields_sel().contains(f));
                                rsx! {
                                    SelectAllToggle {
                                        all_selected: all_selected,
                                        on_toggle: move |select: bool| {
                                            if select {
                                                fields_sel.set(all_field_names.clone());
                                            } else {
                                                fields_sel.set(Vec::new());
                                            }
                                        },
                                    }
                                }
                            }
                            div { class: "d-flex flex-wrap gap-3",
                                for f in &data.fields {
                                    {
                                        let fname = f.name.clone();
                                        let checked = fields_sel().contains(&fname);
                                        rsx! {
                                            div { class: "form-check form-check-inline", key: "{f.id}",
                                                input {
                                                    class: "form-check-input",
                                                    "type": "checkbox",
                                                    id: "break-group-field-{f.id}",
                                                    checked: checked,
                                                    onchange: move |e| {
                                                        let mut v = fields_sel();
                                                        if e.value() == "true" {
                                                            if !v.contains(&fname) {
                                                                v.push(fname.clone());
                                                            }
                                                        } else {
                                                            v.retain(|x| x != &fname);
                                                        }
                                                        fields_sel.set(v);
                                                    }
                                                }
                                                label { class: "form-check-label", "for": "break-group-field-{f.id}", "{f.name}" }
                                            }
                                        }
                                    }
                                }
                            }
                            div { class: "form-text",
                                "Checking a new field adds this {type_noun} there; unchecking removes that field's copy."
                            }
                        }
                        div { class: "modal-footer",
                            button { class: "btn btn-secondary", "type": "button", onclick: move |_| on_close.call(()), "Cancel (Esc)" }
                            button { class: "btn btn-danger", "type": "button", disabled: "{saving}", onclick: do_delete, "Delete Group" }
                            button { class: "btn btn-primary", "type": "button", disabled: "{saving}", onclick: do_save,
                                if saving() { span { class: "spinner-border spinner-border-sm me-2" } }
                                "Save"
                            }
                        }
                    }
        }
    }
}

#[component]
fn FieldsModal(
    tournament_url: String,
    data: ScheduleSetupResponse,
    on_close: EventHandler<()>,
    on_change: EventHandler<()>,
) -> Element {
    let mut new_name = use_signal(|| "".to_string());
    let mut new_camera_urls = use_signal(|| vec!["".to_string()]);
    let mut error = use_signal(|| None::<String>);
    let url_sig = use_signal(|| tournament_url.clone());
    let mut recording_modal_field = use_signal(|| None::<u32>);
    let mut recording_modal_url = use_signal(|| None::<String>);
    let mut recording_modal_loading = use_signal(|| false);
    let mut recording_modal_error = use_signal(|| None::<String>);
    let mut preview_modal_field = use_signal(|| None::<u32>);
    let mut preview_modal_field_name = use_signal(|| None::<String>);
    let mut preview_modal_closed =
        use_signal(|| None::<std::sync::Arc<std::sync::atomic::AtomicBool>>);
    let mut preview_cameras = use_signal(|| vec![] as Vec<String>);
    let mut preview_selected_camera = use_signal(|| String::new());
    let preview_cache_bust = use_signal(|| "0".to_string());
    #[cfg(target_arch = "wasm32")]
    let mut preview_image_object_url = use_signal(|| None::<String>);
    #[cfg(target_arch = "wasm32")]
    let mut preview_metadata = use_signal(|| None::<api::PreviewMetadata>);
    let mut editing_field_id = use_signal(|| None::<u32>);
    let mut editing_name = use_signal(|| "".to_string());
    let mut editing_camera_urls = use_signal(|| vec!["".to_string()]);

    #[cfg(target_arch = "wasm32")]
    {
        let url_sig_eff = url_sig.clone();
        let preview_modal_field_eff = preview_modal_field.clone();
        let preview_modal_closed_eff = preview_modal_closed.clone();
        let preview_modal_field_name_eff = preview_modal_field_name.clone();
        let preview_cameras_eff = preview_cameras.clone();
        let preview_selected_camera_eff = preview_selected_camera.clone();
        let preview_image_object_url_eff = preview_image_object_url.clone();
        #[cfg(target_arch = "wasm32")]
        let preview_metadata_eff = preview_metadata.clone();
        use_effect(move || {
            let fid_opt = preview_modal_field_eff();
            let closed_opt = preview_modal_closed_eff();
            let field_name_opt = preview_modal_field_name_eff();
            let (closed, field_name, u) = match (fid_opt, closed_opt, field_name_opt) {
                (Some(_), Some(c), Some(fn_)) => (c.clone(), fn_.clone(), url_sig_eff().clone()),
                _ => return,
            };
            let mut cameras_sig = preview_cameras_eff.clone();
            let closed2 = closed.clone();
            let u2 = u.clone();
            let field_name2 = field_name.clone();
            spawn(async move {
                use std::sync::atomic::Ordering;
                while !closed.load(Ordering::SeqCst) {
                    if let Ok(list) = api::list_preview_cameras(&u, &field_name).await {
                        cameras_sig.set(list);
                    }
                    for _ in 0..3 {
                        if closed.load(Ordering::SeqCst) {
                            return;
                        }
                        gloo_timers::future::TimeoutFuture::new(1000).await;
                    }
                }
            });
            // Fetch preview frame with credentials and set img via object URL (Safari compatibility).
            let mut image_url_sig = preview_image_object_url_eff.clone();
            let cam_sig = preview_selected_camera_eff.clone();
            #[cfg(target_arch = "wasm32")]
            let mut meta_sig = preview_metadata_eff.clone();
            spawn(async move {
                use std::sync::atomic::Ordering;
                while !closed2.load(Ordering::SeqCst) {
                    let camera = cam_sig();
                    if !camera.is_empty() {
                        let cache_bust = format!("{}", js_sys::Date::now());
                        match api::fetch_preview_frame(&u2, &field_name2, &camera, &cache_bust)
                            .await
                        {
                            Ok(Some(bytes)) => {
                                let arr = js_sys::Uint8Array::from(&bytes[..]);
                                let blob = web_sys::Blob::new_with_u8_array_sequence_and_options(
                                    &js_sys::Array::of1(&arr),
                                    web_sys::BlobPropertyBag::new().type_("image/jpeg"),
                                )
                                .expect("Blob");
                                let new_url = web_sys::Url::create_object_url_with_blob(&blob)
                                    .expect("object URL");
                                if let Some(old) = image_url_sig() {
                                    web_sys::Url::revoke_object_url(&old).ok();
                                }
                                image_url_sig.set(Some(new_url));
                            }
                            Ok(None) => {}
                            Err(_) => {}
                        }
                        #[cfg(target_arch = "wasm32")]
                        if let Ok(Some(meta)) =
                            api::fetch_preview_metadata(&u2, &field_name2, &camera).await
                        {
                            meta_sig.set(Some(meta));
                        }
                    }
                    for _ in 0..2 {
                        if closed2.load(Ordering::SeqCst) {
                            if let Some(url) = image_url_sig() {
                                web_sys::Url::revoke_object_url(&url).ok();
                                image_url_sig.set(None);
                            }
                            #[cfg(target_arch = "wasm32")]
                            meta_sig.set(None);
                            return;
                        }
                        gloo_timers::future::TimeoutFuture::new(1000).await;
                    }
                }
                if let Some(url) = image_url_sig() {
                    web_sys::Url::revoke_object_url(&url).ok();
                    image_url_sig.set(None);
                }
                #[cfg(target_arch = "wasm32")]
                meta_sig.set(None);
            });
        });
    }

    #[cfg(target_arch = "wasm32")]
    use_effect(move || {
        let _ = preview_modal_field();
        let cameras = preview_cameras();
        if preview_modal_field().is_none() || cameras.is_empty() {
            return;
        }
        let sel = preview_selected_camera();
        if sel.is_empty() || !cameras.contains(&sel) {
            preview_selected_camera.set(cameras.first().cloned().unwrap_or_default());
        }
    });

    rsx! {
        div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5)",
            div { class: "modal-dialog modal-lg",
                div { class: "modal-content",
                    div { class: "modal-header d-flex justify-content-between align-items-center",
                        h5 { class: "modal-title mb-0", "Manage Fields" }
                        button { type: "button", class: "btn-close", "aria-label": "Close", onclick: move |_| on_close.call(()) }
                    }
                    div { class: "modal-body",
                        if let Some(err) = error() { div { class: "alert alert-danger", "{err}" } }

                        div { class: "card mb-3",
                            div { class: "card-header py-2", "Add field" }
                            div { class: "card-body py-3",
                                div { class: "mb-3",
                                    label { class: "form-label", "Field name" }
                                    input {
                                        class: "form-control",
                                        placeholder: "e.g. Blue",
                                        value: "{new_name}",
                                        oninput: move |e| new_name.set(e.value())
                                    }
                                }
                                div { class: "mb-2",
                                    label { class: "form-label", "Camera URLs (YouTube livestreams, optional)" }
                                    for (idx, _) in new_camera_urls().iter().enumerate() {
                                        div { class: "input-group mb-2",
                                            input {
                                                class: "form-control form-control-sm",
                                                placeholder: "https://youtube.com/...",
                                                value: "{new_camera_urls().get(idx).cloned().unwrap_or_default()}",
                                                oninput: move |e| {
                                                    let mut v = new_camera_urls();
                                                    if idx < v.len() { v[idx] = e.value(); }
                                                    else { v.resize(idx + 1, String::new()); v[idx] = e.value(); }
                                                    new_camera_urls.set(v);
                                                }
                                            }
                                            button {
                                                class: "btn btn-outline-secondary btn-sm",
                                                type: "button",
                                                onclick: move |_| {
                                                    let mut v = new_camera_urls();
                                                    if v.len() > 1 { v.remove(idx); new_camera_urls.set(v); }
                                                },
                                                "Remove"
                                            }
                                        }
                                    }
                                    button {
                                        class: "btn btn-sm btn-outline-secondary",
                                        type: "button",
                                        onclick: move |_| {
                                            let mut v = new_camera_urls();
                                            v.push("".to_string());
                                            new_camera_urls.set(v);
                                        },
                                        "Add camera URL"
                                    }
                                }
                                button {
                                    class: "btn btn-outline-success",
                                    onclick: move |_| {
                                        let u = url_sig().clone();
                                        let on_change = on_change.clone();
                                        let cams: Vec<String> = new_camera_urls()
                                            .iter()
                                            .filter(|s| !s.trim().is_empty())
                                            .map(|s| s.trim().to_string())
                                            .collect();
                                        let name = new_name().trim().to_string();
                                        spawn(async move {
                                            let req = CreateFieldRequest { name: name.clone(), camera_urls: cams };
                                            match api::create_field(&u, &req).await {
                                                Ok(_) => {
                                                    new_name.set("".to_string());
                                                    new_camera_urls.set(vec!["".to_string()]);
                                                    on_change.call(());
                                                }
                                                Err(e) => error.set(Some(e)),
                                            }
                                        });
                                    },
                                    "Add field"
                                }
                            }
                        }

                        h6 { class: "mb-2", "Existing fields" }
                        ul { class: "list-group",
                            {data.fields.iter().map(|f| {
                                let fid = f.id;
                                let fname = f.name.clone();
                                let fname_for_rec = fname.clone();
                                let fname_for_preview = fname.clone();
                                let cam_urls = f.camera_urls.clone();
                                let is_editing = editing_field_id() == Some(fid);
                                rsx! {
                                    li { key: "{fid}", class: "list-group-item d-flex flex-column gap-2",
                                        if is_editing {
                                            div { class: "d-flex flex-column gap-2",
                                                div {
                                                    label { class: "form-label small mb-0", "Field name" }
                                                    input {
                                                        class: "form-control form-control-sm",
                                                        placeholder: "Field name",
                                                        value: "{editing_name}",
                                                        oninput: move |e| editing_name.set(e.value())
                                                    }
                                                }
                                                div {
                                                    label { class: "form-label small mb-0", "Camera URLs" }
                                                    for (idx, _) in editing_camera_urls().iter().enumerate() {
                                                        div { class: "input-group input-group-sm mb-1",
                                                            input {
                                                                class: "form-control form-control-sm",
                                                                placeholder: "https://youtube.com/...",
                                                                value: "{editing_camera_urls().get(idx).cloned().unwrap_or_default()}",
                                                                oninput: move |e| {
                                                                    let mut v = editing_camera_urls();
                                                                    if idx < v.len() { v[idx] = e.value(); }
                                                                    else { v.resize(idx + 1, String::new()); v[idx] = e.value(); }
                                                                    editing_camera_urls.set(v);
                                                                }
                                                            }
                                                            button {
                                                                class: "btn btn-outline-secondary btn-sm",
                                                                type: "button",
                                                                onclick: move |_| {
                                                                    let mut v = editing_camera_urls();
                                                                    if v.len() > 1 { v.remove(idx); editing_camera_urls.set(v); }
                                                                },
                                                                "×"
                                                            }
                                                        }
                                                    }
                                                    button {
                                                        class: "btn btn-sm btn-outline-secondary",
                                                        type: "button",
                                                        onclick: move |_| {
                                                            let mut v = editing_camera_urls();
                                                            v.push("".to_string());
                                                            editing_camera_urls.set(v);
                                                        },
                                                        "Add camera URL"
                                                    }
                                                }
                                                div { class: "d-flex gap-1 mt-1",
                                                    button { class: "btn btn-sm btn-primary",
                                                        onclick: move |_| {
                                                            let u = url_sig().clone();
                                                            let name = editing_name().clone();
                                                            let cams: Vec<String> = editing_camera_urls()
                                                                .iter()
                                                                .filter(|s| !s.trim().is_empty())
                                                                .map(|s| s.trim().to_string())
                                                                .collect();
                                                            let on_change = on_change.clone();
                                                            spawn(async move {
                                                                let req = UpdateFieldRequest { name, camera_urls: cams, stream_start_times: None };
                                                                if api::update_field(&u, fid, &req).await.is_ok() {
                                                                    editing_field_id.set(None);
                                                                    on_change.call(());
                                                                } else {
                                                                    error.set(Some("Failed to update field".to_string()));
                                                                }
                                                            });
                                                        },
                                                        "Save"
                                                    }
                                                    button { class: "btn btn-sm btn-secondary",
                                                        onclick: move |_| editing_field_id.set(None),
                                                        "Cancel"
                                                    }
                                                }
                                            }
                                        } else {
                                            div { class: "d-flex justify-content-between align-items-center flex-wrap gap-2",
                                                div { class: "d-flex align-items-center gap-2 flex-wrap",
                                                    strong { "{fname}" }
                                                    if cam_urls.is_empty() {
                                                        span { class: "badge bg-secondary", "No cameras" }
                                                    } else {
                                                        for (cam_idx, url) in cam_urls.iter().enumerate() {
                                                            span { class: "d-inline",
                                                                a {
                                                                    href: "{url}",
                                                                    target: "_blank",
                                                                    rel: "noopener noreferrer",
                                                                    class: "small text-primary text-break",
                                                                    "Camera {cam_idx}"
                                                                }
                                                                if cam_idx < cam_urls.len() - 1 {
                                                                    span { class: "text-muted", " · " }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                                div { class: "btn-group btn-group-sm",
                                                    button { class: "btn btn-outline-info",
                                                        onclick: move |_| {
                                                            recording_modal_field.set(Some(fid));
                                                            recording_modal_url.set(None);
                                                            recording_modal_error.set(None);
                                                            recording_modal_loading.set(true);
                                                            let u = url_sig().clone();
                                                            let name = fname_for_rec.clone();
                                                            spawn(async move {
                                                                match api::camera_url(&u, &name).await {
                                                                    Ok(url) => {
                                                                        recording_modal_url.set(Some(url));
                                                                        recording_modal_loading.set(false);
                                                                    }
                                                                    Err(e) => {
                                                                        recording_modal_error.set(Some(e));
                                                                        recording_modal_loading.set(false);
                                                                    }
                                                                }
                                                            });
                                                        },
                                                        "Get recording link"
                                                    }
                                                    button { class: "btn btn-outline-secondary",
                                                        onclick: move |_| {
                                                            let u = url_sig().clone();
                                                            let name = fname_for_preview.clone();
                                                            preview_cameras.set(vec![]);
                                                            preview_selected_camera.set(String::new());
                                                            preview_modal_field_name.set(Some(name.clone()));
                                                            let closed = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
                                                            preview_modal_closed.set(Some(closed));
                                                            preview_modal_field.set(Some(fid));
                                                            spawn(async move {
                                                                let _ = api::request_preview(&u, &name).await;
                                                            });
                                                        },
                                                        "Preview camera"
                                                    }
                                                    button { class: "btn btn-outline-primary",
                                                        onclick: move |_| {
                                                            editing_field_id.set(Some(fid));
                                                            editing_name.set(fname.clone());
                                                            let urls = if cam_urls.is_empty() {
                                                                vec!["".to_string()]
                                                            } else {
                                                                cam_urls.clone()
                                                            };
                                                            editing_camera_urls.set(urls);
                                                        },
                                                        "Edit"
                                                    }
                                                    button { class: "btn btn-outline-danger",
                                                        onclick: move |_| {
                                                            let u = url_sig().clone();
                                                            let on_change = on_change.clone();
                                                            spawn(async move {
                                                                if let Ok(_) = api::delete_field(&u, fid).await {
                                                                    on_change.call(());
                                                                } else {
                                                                    error.set(Some("Cannot delete field with matches".to_string()));
                                                                }
                                                            });
                                                        },
                                                        "Delete"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            })}
                        }
                    }
                }
            }
        }

        {{
            let rec_fid_opt = recording_modal_field();
            match rec_fid_opt {
                Some(rec_fid) => {
                    let rec_field_label = data.fields.iter().find(|x| x.id == rec_fid).map(|x| x.name.as_str()).unwrap_or("");
                    let rec_url = recording_modal_url();
                    let rec_loading = recording_modal_loading();
                    let rec_err = recording_modal_error();
                    let qr_src = rec_url.as_ref().map(|u| format!("https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={}", urlencoding::encode(u)));
                    rsx! {
                        div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5); z-index: 1060;",
                            div { class: "modal-dialog modal-dialog-centered",
                                div { class: "modal-content",
                                    div { class: "modal-header d-flex justify-content-between align-items-center",
                                        h5 { class: "modal-title mb-0", "Recording link — {rec_field_label}" }
                                        button { type: "button", class: "btn-close", onclick: move |_| {
                                            recording_modal_field.set(None);
                                            recording_modal_url.set(None);
                                            recording_modal_error.set(None);
                                            recording_modal_loading.set(false);
                                        } }
                                    }
                                    div { class: "modal-body text-center",
                                        if rec_loading {
                                            p { class: "text-muted", "Loading..." }
                                        } else if let Some(ref e) = rec_err {
                                            p { class: "text-danger", "{e}" }
                                        } else if let (Some(ref url), Some(ref qr)) = (rec_url, qr_src) {
                                            img { src: "{qr}", alt: "QR code", style: "max-width: 200px; height: auto;" }
                                            a { href: "{url}", target: "_blank", rel: "noopener noreferrer", class: "d-block mt-2 small text-break", "{url}" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                None => rsx! { div {} },
            }
        }}

        {{
            let preview_fid_opt = preview_modal_field();
            match preview_fid_opt {
                Some(preview_fid) => {
                    let preview_field_name = data.fields.iter().find(|x| x.id == preview_fid).map(|x| x.name.as_str()).unwrap_or("");
                    let cameras = preview_cameras();
                    let selected = preview_selected_camera();
                    let cache_bust = preview_cache_bust();
                    let effective_camera = if selected.is_empty() && !cameras.is_empty() {
                        cameras.first().cloned().unwrap_or_default()
                    } else if cameras.contains(&selected) {
                        selected.clone()
                    } else {
                        cameras.first().cloned().unwrap_or_default()
                    };
                    let u = url_sig().clone();
                    let u_release = url_sig().clone();
                    let name_for_release = preview_field_name.to_string();
                    #[cfg(target_arch = "wasm32")]
                    let preview_img_src = preview_image_object_url()
                        .unwrap_or_else(|| "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1' height='1'%3E%3C/svg%3E".to_string());
                    #[cfg(not(target_arch = "wasm32"))]
                    let preview_img_src = api::preview_frame_url(&u, preview_field_name, &effective_camera, &preview_cache_bust());
                    #[cfg(target_arch = "wasm32")]
                    let preview_metadata_block = match preview_metadata() {
                        Some(meta) => {
                            let storage_str = match (meta.storage_usage, meta.storage_quota) {
                                (Some(usage), Some(quota)) if quota > 0.0 => {
                                    let u_mb = usage / 1_048_576.0;
                                    let q_mb = quota / 1_048_576.0;
                                    format!("{:.1} MB / {:.1} MB", u_mb, q_mb)
                                }
                                _ => String::new(),
                            };
                            let battery_str = meta
                                .battery_level
                                .map(|l| format!("{:.0}%", l * 100.0))
                                .unwrap_or_default();
                            if !storage_str.is_empty() || !battery_str.is_empty() {
                                rsx! {
                                    div { class: "mt-2 small text-muted d-flex gap-3 flex-wrap",
                                        if !storage_str.is_empty() { span { "Storage: {storage_str}" } }
                                        if !battery_str.is_empty() { span { "Battery: {battery_str}" } }
                                    }
                                }
                                .unwrap_or_default()
                            } else {
                                rsx! { div {} }.unwrap_or_default()
                            }
                        }
                        None => rsx! { div {} }.unwrap_or_default(),
                    };
                    #[cfg(not(target_arch = "wasm32"))]
                    let preview_metadata_block = rsx! { div {} }.unwrap_or_default();
                    rsx! {
                        div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5); z-index: 1060;",
                            div { class: "modal-dialog modal-dialog-centered modal-lg",
                                div { class: "modal-content",
                                    div { class: "modal-header d-flex justify-content-between align-items-center",
                                        h5 { class: "modal-title mb-0", "Preview — {preview_field_name}" }
                                        button { type: "button", class: "btn-close", onclick: move |_| {
                                            let u_rel = u_release.clone();
                                            let name_rel = name_for_release.clone();
                                            spawn(async move {
                                                let _ = api::release_preview(&u_rel, &name_rel).await;
                                            });
                                            #[cfg(target_arch = "wasm32")]
                                            {
                                                if let Some(url) = preview_image_object_url() {
                                                    web_sys::Url::revoke_object_url(&url).ok();
                                                    preview_image_object_url.set(None);
                                                }
                                                preview_metadata.set(None);
                                            }
                                            if let Some(closed) = preview_modal_closed() {
                                                closed.store(true, std::sync::atomic::Ordering::SeqCst);
                                            }
                                            preview_modal_field.set(None);
                                            preview_modal_field_name.set(None);
                                            preview_modal_closed.set(None);
                                        } }
                                    }
                                    div { class: "modal-body",
                                        if cameras.is_empty() {
                                            p { class: "text-muted", "Waiting for cameras..." }
                                        } else {
                                            div { class: "mb-2",
                                                label { class: "form-label small", "Camera" }
                                                select {
                                                    class: "form-select form-select-sm",
                                                    value: "{effective_camera}",
                                                    onchange: move |e| preview_selected_camera.set(e.value()),
                                                    for name in cameras.iter() {
                                                        option { value: "{name}", "{name}" }
                                                    }
                                                }
                                            }
                                            if effective_camera.is_empty() {
                                                p { class: "text-muted small", "Select a camera..." }
                                            } else {
                                                img {
                                                    src: "{preview_img_src}",
                                                    alt: "Preview",
                                                    class: "img-fluid w-100",
                                                    style: "max-height: 70vh; object-fit: contain;"
                                                }
                                                {preview_metadata_block}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                None => rsx! { div {} },
            }
        }}
    }
}

#[component]
fn TOMLImportModal(
    tournament_url: String,
    on_close: EventHandler<()>,
    on_import: EventHandler<()>,
) -> Element {
    let mut error = use_signal(|| None::<String>);
    let mut importing = use_signal(|| false);
    let on_file_change = move |ev: Event<FormData>| {
        let files = ev.files();
        if let Some(file) = files.first().cloned() {
            let u = tournament_url.clone();
            let on_import = on_import.clone();
            importing.set(true);
            error.set(None);
            spawn(async move {
                match file.read_string().await {
                    Ok(toml_content) => {
                        let req = ImportScheduleRequest { toml: toml_content };
                        match api::import_schedule(&u, &req).await {
                            Ok(_) => {
                                importing.set(false);
                                on_import.call(());
                            }
                            Err(e) => {
                                error.set(Some(e));
                                importing.set(false);
                            }
                        }
                    }
                    Err(e) => {
                        error.set(Some(e.to_string()));
                        importing.set(false);
                    }
                }
            });
        }
    };
    rsx! {
        div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5)",
            div { class: "modal-dialog modal-lg",
                div { class: "modal-content",
                    div { class: "modal-header",
                        h5 { class: "modal-title", "Import Schedule (TOML)" }
                    }
                    div { class: "modal-body",
                        if let Some(err) = error() {
                            div { class: "alert alert-danger", "{err}" }
                        }
                        p { class: "text-muted", "Select a TOML file exported from a tournament schedule." }
                        input {
                            r#type: "file",
                            class: "form-control",
                            accept: ".toml",
                            onchange: on_file_change,
                        }
                        if importing() {
                            div { class: "mt-2 text-muted", "Importing..." }
                        }
                    }
                    div { class: "modal-footer",
                        button { class: "btn btn-secondary", onclick: move |_| on_close.call(()), "Cancel" }
                    }
                }
            }
        }
    }
}

#[component]
fn ScheduleWarningsModal(tournament_url: String, on_close: EventHandler<()>) -> Element {
    use crate::types::ScheduleWarning;
    let mut warnings = use_signal(|| None::<Vec<ScheduleWarning>>);
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);

    let url_for_load = tournament_url.clone();
    use_hook(move || {
        let url = url_for_load.clone();
        spawn(async move {
            match api::fetch_schedule_warnings(&url).await {
                Ok(ws) => {
                    warnings.set(Some(ws));
                    loading.set(false);
                }
                Err(e) => {
                    error.set(Some(e));
                    loading.set(false);
                }
            }
        });
    });

    fn kind_label(kind: &str) -> &'static str {
        match kind {
            "unknown_team" => "Unknown team",
            "missing_team" => "Missing team",
            "duplicate_team" => "Duplicate team",
            "unknown_match_ref" => "Missing match",
            "cycle" => "Cyclic dependency",
            "double_booked" => "Double-booked teams",
            _ => "Warning",
        }
    }

    fn kind_class(kind: &str) -> &'static str {
        match kind {
            "cycle" => "list-group-item-danger",
            _ => "list-group-item-warning",
        }
    }

    rsx! {
        div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5)",
            div { class: "modal-dialog modal-lg",
                div { class: "modal-content",
                    div { class: "modal-header",
                        h5 { class: "modal-title", "Schedule Warnings" }
                    }
                    div { class: "modal-body",
                        if loading() {
                            div { class: "text-muted", "Checking schedule..." }
                        } else if let Some(err) = error() {
                            div { class: "alert alert-danger", "{err}" }
                        } else {
                            match warnings.read().as_ref() {
                                Some(ws) if ws.is_empty() => rsx! {
                                    div { class: "alert alert-success mb-0",
                                        "No warnings — every match resolves cleanly."
                                    }
                                },
                                Some(ws) => rsx! {
                                    ul { class: "list-group",
                                        for (i, w) in ws.iter().enumerate() {
                                            li { key: "{i}", class: "list-group-item {kind_class(&w.kind)}",
                                                div { class: "fw-semibold", "{kind_label(&w.kind)}" }
                                                div { "{w.message}" }
                                                if !w.matches.is_empty() {
                                                    div { class: "small text-muted mt-1",
                                                        "Matches: {w.matches.join(\", \")}"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                },
                                None => rsx! {},
                            }
                        }
                    }
                    div { class: "modal-footer",
                        button { class: "btn btn-secondary", onclick: move |_| on_close.call(()), "Close" }
                    }
                }
            }
        }
    }
}

#[component]
fn TagsModal(
    tournament_url: String,
    data: ScheduleSetupResponse,
    on_close: EventHandler<()>,
    on_change: EventHandler<()>,
) -> Element {
    let mut new_tag = use_signal(|| "".to_string());
    let mut error = use_signal(|| None::<String>);
    let url_sig = use_signal(|| tournament_url.clone());
    // Local state for dropdowns: tag_id -> team_id. Synced from data when modal has data so dropdowns show current values.
    let mut tag_teams = use_signal(|| HashMap::<u32, String>::new());
    let mut updating_tag_id = use_signal(|| None::<u32>);

    // Sync tag_teams from data whenever we have tags, so dropdowns show correct values on open and after refetch.
    let data_effect = data.clone();
    use_effect(move || {
        let tags = data_effect.tags.clone();
        let mut map = tag_teams.write();
        map.clear();
        for tag in tags {
            map.insert(tag.id, tag.team.unwrap_or_default());
        }
    });

    rsx! {
        div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5)",
            div { class: "modal-dialog modal-lg",
                div { class: "modal-content",
                    div { class: "modal-header d-flex justify-content-between align-items-center",
                        h5 { class: "modal-title mb-0", "Manage Tags" }
                        button { type: "button", class: "btn-close", "aria-label": "Close", onclick: move |_| on_close.call(()) }
                    }
                    div { class: "modal-body",
                        if let Some(err) = error() { div { class: "alert alert-danger", "{err}" } }

                        h6 { "Create Tag" }
                        div { class: "input-group mb-3",
                            input { class: "form-control", placeholder: "Tag Name (e.g. Pool A Winner)", value: "{new_tag}", oninput: move |e| { new_tag.set(e.value()); error.set(None); } }
                            button { class: "btn btn-outline-success",
                                onclick: move |_| {
                                    let u = url_sig();
                                    let on_change = on_change.clone();
                                    let name = new_tag().trim().to_string();
                                    if name.is_empty() {
                                        error.set(Some("Tag name is required.".to_string()));
                                        return;
                                    }
                                    if name.contains("::") {
                                        error.set(Some("Tag name cannot contain \"::\".".to_string()));
                                        return;
                                    }
                                    error.set(None);
                                    spawn(async move {
                                        let req = CreateTagRequest { name };
                                        match api::create_tag(&u, &req).await {
                                            Ok(_) => { new_tag.set("".to_string()); on_change.call(()); }
                                            Err(e) => error.set(Some(e)),
                                        }
                                    });
                                },
                                "Create"
                            }
                        }

                        h6 { "Existing Tags" }
                        ul { class: "list-group",
                            {data.tags.iter().map(|tag| {
                                let tag_id = tag.id;
                                let current_team = tag_teams().get(&tag_id).cloned().unwrap_or_default();
                                let tag_name = tag.name.clone();
                                let is_updating = updating_tag_id() == Some(tag_id);
                                rsx! {
                                    li { key: "{tag_id}", class: "list-group-item d-flex justify-content-between align-items-center gap-2 flex-wrap",
                                        span { class: "flex-grow-1", "{tag_name}" }
                                        div { class: "d-flex align-items-center gap-1",
                                            select {
                                                class: "form-select form-select-sm",
                                                style: "max-width: 12rem;",
                                                value: "{current_team}",
                                                disabled: is_updating,
                                                onchange: move |e| {
                                                    let u = url_sig();
                                                    let team_id = e.value();
                                                    let on_change = on_change.clone();
                                                    tag_teams.write().insert(tag_id, team_id.clone());
                                                    updating_tag_id.set(Some(tag_id));
                                                    error.set(None);
                                                    let prev_team = current_team.clone();
                                                    spawn(async move {
                                                        let req = UpdateTagsRequest { tag_id, team_id };
                                                        match api::update_tags(&u, &req).await {
                                                            Ok(_) => {
                                                                updating_tag_id.set(None);
                                                                on_change.call(());
                                                            }
                                                            Err(e) => {
                                                                tag_teams.write().insert(tag_id, prev_team);
                                                                updating_tag_id.set(None);
                                                                error.set(Some(e));
                                                            }
                                                        }
                                                    });
                                                },
                                                option { value: "", "No team" }
                                                for opt in &data.team_options {
                                                    option { value: "{opt.id}", "{opt.pseudonym.as_deref().unwrap_or(&opt.id)}" }
                                                }
                                            }
                                            if is_updating {
                                                span { class: "spinner-border spinner-border-sm text-secondary", role: "status", "aria-hidden": "true" }
                                            }
                                        }
                                        button { class: "btn btn-sm btn-outline-danger",
                                            disabled: is_updating,
                                            onclick: move |_| {
                                                let u = url_sig();
                                                let on_change = on_change.clone();
                                                spawn(async move {
                                                    match api::delete_tag(&u, tag_id).await {
                                                        Ok(_) => { error.set(None); on_change.call(()); }
                                                        Err(e) => error.set(Some(e)),
                                                    }
                                                });
                                            },
                                            "×"
                                        }
                                    }
                                }
                            })}
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn TableView(
    data: ScheduleSetupResponse,
    selected_field: String,
    highlight_team: String,
    edit_mode: bool,
    #[props(default = false)] debug_mode: bool,
    #[props(default = false)] show_as_happened: bool,
    tournament_url: String,
    on_edit_match: EventHandler<String>,
) -> Element {
    let tz_offset = schedule_tz_offset_minutes();
    // Lookup so debug rows can resolve previous_match / next_match uuids to match names
    // without forcing a backend change. Borrowed strs are fine here because `data` outlives
    // the rsx! children.
    let uuid_to_name: std::collections::HashMap<&str, &str> = data
        .matches
        .iter()
        .map(|m| (m.uuid.as_str(), m.name.as_str()))
        .collect();
    // Format helpers used by debug-mode cells. Closures capture `tz_offset` so each call
    // site doesn't have to repeat it.
    let fmt_dt = |opt: &Option<String>| -> String {
        opt.as_ref()
            .map(|s| format_datetime_local(s, tz_offset))
            .unwrap_or_else(|| "-".to_string())
    };
    let fmt_uuid_as_name = |opt: &Option<String>| -> String {
        opt.as_ref()
            .and_then(|u| uuid_to_name.get(u.as_str()).map(|n| n.to_string()))
            .unwrap_or_else(|| "-".to_string())
    };
    let fmt_str =
        |opt: &Option<String>| -> String { opt.clone().unwrap_or_else(|| "-".to_string()) };
    let fmt_u32 = |opt: &Option<u32>| -> String {
        opt.map(|n| n.to_string())
            .unwrap_or_else(|| "-".to_string())
    };
    // ... existing filter logic ...
    let matches: Vec<&MatchSetupData> = data
        .matches
        .iter()
        .filter(|m| {
            if m.status == "SKIPPED" {
                return false;
            }
            if selected_field != "all" {
                if let Some(f_name) = &m.field {
                    let field_id = data
                        .fields
                        .iter()
                        .find(|f| &f.name == f_name)
                        .map(|f| f.id.to_string());
                    if field_id.as_deref() != Some(selected_field.as_str()) {
                        return false;
                    }
                } else {
                    return false;
                }
            }
            true
        })
        .collect();

    let base_url = api::base_url();
    rsx! {
        div { class: "table-responsive schedule-table-view",
            table { class: "table table-striped table-hover table-sm align-middle",
                thead {
                    tr {
                        th { "Match" }
                        th { "Field" }
                        th { "Start" }
                        th { "Type" }
                        th { "Status" }
                        th { "Team 1" }
                        th { "Team 2" }
                        th { "Refs" }
                        if debug_mode {
                            th { "UUID" }
                            th { "Team 1 init" }
                            th { "Team 2 init" }
                            th { "Refs init" }
                            th { "Scheduled" }
                            th { "Nominal" }
                            th { "Confirmed" }
                            th { "Completed" }
                            th { "Length" }
                            th { "Set type" }
                            th { "Nsets" }
                            th { "Stones / set" }
                            th { "Stones rem" }
                            th { "Winner" }
                            th { "Ribbon" }
                            th { "Prev match" }
                            th { "Next match" }
                            th { "Skip cond" }
                        }
                        if edit_mode { th { "Edit" } }
                    }
                }
                tbody {
                    {matches.iter().map(|m| {
                        let match_id = m.uuid.clone();
                        // Team 1 column: only m.team1 / m.team1_initial (first token if comma-separated).
                        // t1_raw is the full pseudonym (for highlight-filter matching); t1_label is the
                        // possibly-shortened/truncated form that gets rendered in the cell.
                        let opt1 = m.team1.as_ref().and_then(|id| data.team_options.iter().find(|o| &o.id == id));
                        let t1_raw = opt1.and_then(|o| o.pseudonym.as_deref()).map(String::from)
                            .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string());
                        let t1_label = opt1.map(|o| short_or_truncate(o.pseudonym.as_deref().unwrap_or(o.id.as_str()), o.shortname.as_deref()))
                            .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string());
                        let photo1 = opt1.and_then(|o| o.profile_photo.clone());
                        // Team 2 column: only m.team2 / m.team2_initial (first token if comma-separated)
                        let opt2 = m.team2.as_ref().and_then(|id| data.team_options.iter().find(|o| &o.id == id));
                        let t2_raw = opt2.and_then(|o| o.pseudonym.as_deref()).map(String::from)
                            .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string());
                        let t2_label = opt2.map(|o| short_or_truncate(o.pseudonym.as_deref().unwrap_or(o.id.as_str()), o.shortname.as_deref()))
                            .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string());
                        let photo2 = opt2.and_then(|o| o.profile_photo.clone());
                        // Refs column: per-slot resolved id else initial/tag (#197).
                        let refs_entries: Vec<(String, String, Option<String>)> = refs_tokens(m)
                            .into_iter()
                            .map(|token| {
                                let opt = data.team_options.iter().find(|o| o.id == token);
                                let raw = opt.and_then(|o| o.pseudonym.as_deref()).map(String::from).unwrap_or_else(|| token.clone());
                                let label = opt.map(|o| short_or_truncate(o.pseudonym.as_deref().unwrap_or(o.id.as_str()), o.shortname.as_deref())).unwrap_or_else(|| token.clone());
                                let photo = opt.and_then(|o| o.profile_photo.clone());
                                (raw, label, photo)
                            })
                            .collect();
                        let refs_list: Vec<(String, Option<String>)> = refs_entries.iter().map(|(_, l, p)| (l.clone(), p.clone())).collect();
                        // Highlight: match against raw (full) pseudonyms so a query for the full team
                        // name still matches teams whose label was shortened/truncated.
                        let (highlight_playing, highlight_ref) = if highlight_team.is_empty() {
                            (false, false)
                        } else {
                            let ht = highlight_team.to_lowercase();
                            let playing = t1_raw.to_lowercase().contains(&ht)
                                || t2_raw.to_lowercase().contains(&ht);
                            let refs_joined_raw = refs_entries
                                .iter()
                                .map(|(r, _, _)| r.as_str())
                                .collect::<Vec<_>>()
                                .join(", ");
                            let reffing = !playing && refs_joined_raw.to_lowercase().contains(&ht);
                            (playing, reffing)
                        };
                        let tr_row_class = {
                            let mut s = String::new();
                            if highlight_playing {
                                s.push_str("schedule-table-row--highlight-playing ");
                            }
                            if highlight_ref {
                                s.push_str("schedule-table-row--highlight-ref ");
                            }
                            s
                        };
                        let t1 = if t1_label.contains(',') { t1_label.split(',').next().map(|s| s.trim().to_string()).unwrap_or_default() } else { t1_label };
                        let t2 = if t2_label.contains(',') { t2_label.split(',').next().map(|s| s.trim().to_string()).unwrap_or_default() } else { t2_label };
                        let (t1_kind, t1_label) = team_ref_display(&t1);
                        let (t2_kind, t2_label) = team_ref_display(&t2);
                        let refs_display_list: Vec<(String, Option<String>, u8, String)> = refs_list
                            .iter()
                            .map(|(d, p)| {
                                let (k, l) = team_ref_display(d);
                                (d.clone(), p.clone(), k, l)
                            })
                            .collect();
                        let schedule_type_display = m.schedule_type.as_deref().unwrap_or("-");
                        let structural = is_structural_match(m);
                        // Editor table: structural rows show real statuses like matches.
                        let (status_color, status_label) = if structural && !edit_mode {
                            ("#e9ecef".to_string(), "—".to_string())
                        } else if m.status.is_empty() {
                            ("#e9ecef".to_string(), "-".to_string())
                        } else {
                            status_color_and_label(&m.status)
                        };
                        rsx! {
                            tr { key: "{m.uuid}", class: "{tr_row_class}",
                                td {
                                    if edit_mode {
                                        "{m.name}"
                                    } else {
                                        Link { to: Route::MatchPageById { url: tournament_url.clone(), match_id: m.uuid.clone() }, class: "text-decoration-none", "{m.name}" }
                                    }
                                }
                                td { "{m.field.as_deref().unwrap_or(\"\")}" }
                                td {
                                    if let Some((start_utc, _)) = display_interval_utc(m, show_as_happened) {
                                        span { "{format_naive_utc_time_local(start_utc, tz_offset)}" }
                                    } else { "-" }
                                }
                                td { "{schedule_type_display}" }
                                td { class: "align-middle",
                                    if !structural || edit_mode {
                                        span {
                                            class: "schedule-timeline-status-tag",
                                            style: "background-color: {status_color};",
                                            "{status_label}"
                                        }
                                    } else {
                                        span { class: "text-muted small", "—" }
                                    }
                                }
                                td { class: "align-middle",
                                    div { class: "d-flex align-items-center gap-1",
                                        if t1_kind == 0 {
                                            if let Some(ph) = &photo1 {
                                                img { class: "rounded-circle", style: "width: 1.5em; height: 1.5em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                                            } else if !t1.is_empty() {
                                                span { class: "rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.5em; height: 1.5em; font-size: 0.75em; background: #6c757d; color: white;", "{t1.chars().next().unwrap_or('?')}" }
                                            }
                                        }
                                        if t1_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                                        if t1_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                                        span { "{t1_label}" }
                                    }
                                }
                                td { class: "align-middle",
                                    div { class: "d-flex align-items-center gap-1",
                                        if t2_kind == 0 {
                                            if let Some(ph) = &photo2 {
                                                img { class: "rounded-circle", style: "width: 1.5em; height: 1.5em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                                            } else if !t2.is_empty() {
                                                span { class: "rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.5em; height: 1.5em; font-size: 0.75em; background: #6c757d; color: white;", "{t2.chars().next().unwrap_or('?')}" }
                                            }
                                        }
                                        if t2_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                                        if t2_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                                        span { "{t2_label}" }
                                    }
                                }
                                td { class: "align-middle",
                                    div { class: "d-flex align-items-center flex-wrap gap-1",
                                        for (ref_display, ref_photo, r_kind, r_label) in &refs_display_list {
                                            span { class: "d-inline-flex align-items-center gap-1",
                                                if *r_kind == 0 {
                                                    if let Some(ph) = ref_photo {
                                                        img { class: "rounded-circle", style: "width: 1.25em; height: 1.25em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                                                    } else {
                                                        span { class: "rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.25em; height: 1.25em; font-size: 0.65em; background: #6c757d; color: white;", "{ref_display.chars().next().unwrap_or('?')}" }
                                                    }
                                                }
                                                if *r_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                                                if *r_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                                                span { "{r_label}" }
                                            }
                                        }
                                    }
                                }
                                if debug_mode {
                                    td { class: "small text-muted font-monospace", "{m.uuid}" }
                                    td { class: "small", "{fmt_str(&m.team1_initial)}" }
                                    td { class: "small", "{fmt_str(&m.team2_initial)}" }
                                    td { class: "small", "{fmt_str(&m.refs_initial)}" }
                                    td { class: "small", "{fmt_dt(&m.scheduled_start_time)}" }
                                    td { class: "small", "{fmt_dt(&m.nominal_start_time)}" }
                                    td { class: "small", "{fmt_dt(&m.confirmed_start_time)}" }
                                    td { class: "small", "{fmt_dt(&m.completed_time)}" }
                                    td { class: "small", "{fmt_u32(&m.nominal_length)}" }
                                    td { class: "small", "{fmt_str(&m.set_type)}" }
                                    td { class: "small", "{fmt_u32(&m.nsets)}" }
                                    td { class: "small", "{fmt_u32(&m.stones_per_set)}" }
                                    td { class: "small", "{fmt_u32(&m.stones_remaining)}" }
                                    td { class: "small", "{fmt_str(&m.match_winner)}" }
                                    td { class: "small", { if m.ribbon { "yes" } else { "no" } } }
                                    td { class: "small", "{fmt_uuid_as_name(&m.previous_match)}" }
                                    td { class: "small", "{fmt_uuid_as_name(&m.next_match)}" }
                                    td { class: "small", "{fmt_str(&m.skip_condition)}" }
                                }
                                if edit_mode {
                                    td {
                                        // Started/completed rows stay editable too: the edit card
                                        // surfaces the lock and the server rejects disallowed changes.
                                        button {
                                            class: "btn btn-sm btn-link",
                                            onclick: move |_| on_edit_match.call(match_id.clone()),
                                            "✎"
                                        }
                                    }
                                }
                            }
                        }
                    })}
                }
            }
        }
    }
}

// ... Scheduler structs ...

#[allow(dead_code)]
#[derive(Serialize)]
struct SchedulerEvent {
    id: String,
    text: String,
    start_date: String,
    end_date: String,
    section_id: String, // Field ID
    color: String,
    team1: String,
    team2: String,
}

#[allow(dead_code)]
#[derive(Serialize)]
struct SchedulerSection {
    key: String,
    label: String,
}

// Internal types for timeline events
#[allow(dead_code)]
#[derive(Clone, Debug, PartialEq)]
struct TimelineEvent {
    id: String,
    name: String,
    team1: String,
    team2: String,
    team1_photo: Option<String>,
    team2_photo: Option<String>,
    refs_display: String, // ref teams as pseudonyms (comma-separated)
    refs_list: Vec<(String, Option<String>)>, // (display_name, profile_photo) for refs
    start_time: chrono::NaiveDateTime,
    end_time: chrono::NaiveDateTime,
    length_min: i64,
    field_id: u32,
    field_name: String,
    color: String, // status color only (for tag); never overwritten for highlight
    status: String,
    schedule_type: Option<String>,
    lane_index: usize,
    num_lanes: usize,
    highlight_playing: bool, // all-teams highlight filter
    highlight_ref: bool,     // all-teams highlight filter
    ribbon: bool,
    /// Team-view role: "playing" | "reffing" | "".
    team_role: String,
    /// Team-view opponent full name (no shortname).
    opponent_label: Option<String>,
    opponent_photo: Option<String>,
    opponent_kind: u8,
}

#[derive(Clone, Debug)]
struct JoinGroup {
    name: String,
    time: chrono::NaiveDateTime,
    // For each JOIN match: (field_id, match_uuid)
    field_matches: Vec<(u32, String)>,
}


/// One match block on the timeline (used by the overlay layer so events are never
/// buried under later half-hour grid cells).
#[component]
fn TimelineEventCard(
    event: TimelineEvent,
    event_style: String,
    team_view: bool,
    edit_mode: bool,
    tournament_url: String,
    base_url: String,
    on_edit_match: EventHandler<String>,
    /// Edit-page interactions enabled (drag-to-move, alt-hover deps).
    #[props(default = false)]
    editor: bool,
    /// Selected by the bulk-length tool.
    #[props(default = false)]
    selected: bool,
    /// Alt-hover dependency highlight class (source / chain / team / ref).
    #[props(default)]
    dep_class: Option<String>,
    /// Alt-hover: nested outline rings (one per edge touching this block —
    /// outgoing for the hovered source, incoming for dependency targets),
    /// colored like the lines. Inline `box-shadow: …;`.
    #[props(default)]
    dep_shadow: Option<String>,
    /// Pointerdown on the block (id, client_x, client_y) → may start a move drag.
    #[props(default)]
    on_move_pointer_down: EventHandler<(String, f64, f64)>,
    /// Hover tracking for the alt-dependency view: (id, entered, alt_held).
    #[props(default)]
    on_hover: EventHandler<(String, bool, bool)>,
    /// Show Winner/Loser overlay chips on hover (editor card open, team input focused).
    #[props(default = false)]
    result_pick_active: bool,
    /// Fired with `<MatchName>::winner|loser` when a chip is clicked.
    #[props(default)]
    on_pick_result: EventHandler<String>,
) -> Element {
    let navigator = use_navigator();
    let event_id_clone = event.id.clone();
    let (_, status_label) = status_color_and_label(&event.status);
    let is_break = is_break_like_type(event.schedule_type.as_deref());
    let is_structural = is_structural_type(event.schedule_type.as_deref());
    let event_title = if is_break {
        event.name.clone()
    } else if team_view {
        if let Some(opp) = event.opponent_label.as_ref() {
            format!("{} — vs {}", event.name, opp)
        } else {
            format!("{} — reffing", event.name)
        }
    } else {
        format!("{} - {} vs {}", event.name, event.team1, event.team2)
    };
    let url_clone = tournament_url.clone();
    let event_class = format!(
        "schedule-timeline-event{}{}{}{}{}",
        if event.highlight_playing {
            " schedule-timeline-event--highlight-playing"
        } else {
            ""
        },
        if event.highlight_ref {
            " schedule-timeline-event--highlight-ref"
        } else {
            ""
        },
        // Editor: structural blocks drop the neutral chrome and show statuses.
        if is_structural && !editor {
            " schedule-timeline-event--structural"
        } else {
            ""
        },
        if selected {
            " schedule-timeline-event--bulk-selected"
        } else {
            ""
        },
        dep_class
            .as_deref()
            .map(|c| format!(" {c}"))
            .unwrap_or_default()
    );
    let (t1_kind, t1_label) = team_ref_display(&event.team1);
    let (t2_kind, t2_label) = team_ref_display(&event.team2);
    let event_refs: Vec<(String, Option<String>, u8, String)> = event
        .refs_list
        .iter()
        .map(|(d, p)| {
            let (k, l) = team_ref_display(d);
            (d.clone(), p.clone(), k, l)
        })
        .collect();
    // Started/completed matches stay fully interactive on the editor (click,
    // drag, bulk-select); the server rejects disallowed changes and the
    // existing error alert + refetch handles it. The lock is surfaced as a
    // hint here and as a warning in the edit card.
    let edit_locked = !is_break
        && matches!(
            event.status.as_str(),
            "IN_PROGRESS" | "COMPLETED" | "SKIPPED"
        );
    let timeline_title = if edit_mode && edit_locked {
        format!("{event_title} — match has started; the server will reject most changes")
    } else {
        event_title.clone()
    };
    let role_badge = match event.team_role.as_str() {
        "playing" => Some(("Playing", "schedule-role-badge schedule-role-badge--playing")),
        "reffing" => Some(("Reffing", "schedule-role-badge schedule-role-badge--reffing")),
        _ => None,
    };
    let opp_label = event.opponent_label.clone();
    let opp_photo = event.opponent_photo.clone();
    let opp_kind = event.opponent_kind;
    let field_label = event.field_name.clone();
    let start_time_label = event.start_time.format("%H:%M").to_string();

    let event_id_for_drag = event.id.clone();
    let event_id_for_enter = event.id.clone();
    let event_id_for_leave = event.id.clone();
    let can_drag = editor;
    // Hovered dependency source: append the per-edge outline rings.
    let event_style = match dep_shadow.as_deref() {
        Some(shadow) => format!("{event_style} {shadow}"),
        None => event_style.clone(),
    };
    rsx! {
        div {
            class: "{event_class}",
            style: "{event_style}",
            title: "{timeline_title}",
            // Hit-test anchor: the grid's pointermove reconciles hovered_block
            // from the real DOM (see reconcile_hover_from_point), so a missed
            // mouseleave can never wedge the alt-dependency outlines.
            "data-event-id": "{event.id}",
            cursor: if can_drag { "grab" } else if is_break && !edit_mode { "default" } else { "pointer" },
            onpointerdown: move |ev: Event<PointerData>| {
                if !editor {
                    return;
                }
                // Never let a press on a block start an empty-space create drag.
                ev.stop_propagation();
                if !can_drag || ev.pointer_type() != "mouse" {
                    return;
                }
                let c = ev.client_coordinates();
                on_move_pointer_down.call((event_id_for_drag.clone(), c.x, c.y));
            },
            onmouseenter: move |ev: Event<MouseData>| {
                if editor {
                    on_hover.call((event_id_for_enter.clone(), true, ev.modifiers().alt()));
                }
            },
            onmouseleave: move |ev: Event<MouseData>| {
                if editor {
                    on_hover.call((event_id_for_leave.clone(), false, ev.modifiers().alt()));
                }
            },
            onclick: move |_| {
                if is_break && !edit_mode {
                } else if edit_mode {
                    on_edit_match.call(event_id_clone.clone());
                } else {
                    navigator.push(Route::MatchPageById {
                        url: url_clone.clone(),
                        match_id: event_id_clone.clone(),
                    });
                }
            },
            if !is_structural || editor {
                span {
                    class: "schedule-timeline-status-tag schedule-timeline-status-tag--corner",
                    style: "background-color: {event.color};",
                    "{status_label}"
                }
            }
            // Winner/Loser insertion chips: shown on hover while an editor card has a
            // team-ish input focused. Structural blocks (breaks/joins) have no winner.
            if editor && result_pick_active && !is_structural {
                div {
                    class: "schedule-result-chips",
                    // Never start a move drag from a chip press.
                    onpointerdown: move |ev: Event<PointerData>| ev.stop_propagation(),
                    button {
                        r#type: "button",
                        class: "schedule-result-chip schedule-result-chip--winner",
                        onclick: {
                            let name = event.name.clone();
                            move |ev: Event<MouseData>| {
                                ev.stop_propagation();
                                on_pick_result.call(format!("{name}::winner"));
                            }
                        },
                        "Winner"
                    }
                    button {
                        r#type: "button",
                        class: "schedule-result-chip schedule-result-chip--loser",
                        onclick: {
                            let name = event.name.clone();
                            move |ev: Event<MouseData>| {
                                ev.stop_propagation();
                                on_pick_result.call(format!("{name}::loser"));
                            }
                        },
                        "Loser"
                    }
                }
            }
            if team_view && !is_break {
                div { class: "schedule-timeline-event-team-row",
                    div { class: "schedule-timeline-event-main",
                        div { class: "schedule-timeline-event-header d-flex align-items-center flex-wrap gap-1",
                            div { class: "schedule-timeline-event-name", "{event.name}" }
                            if let Some(opp) = opp_label.as_ref() {
                                span { class: "schedule-timeline-event-teams schedule-timeline-inline-vs d-inline-flex align-items-center flex-wrap gap-1",
                                    span { "vs" }
                                    if opp_kind == 0 {
                                        if let Some(ph) = &opp_photo {
                                            img { class: "rounded-circle", style: "width: 1.25em; height: 1.25em; object-fit: cover; flex-shrink: 0;", src: "{base_url}/static/{ph}", alt: "" }
                                        } else {
                                            span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.25em; height: 1.25em; font-size: 0.7em; background: #6c757d; color: white; flex-shrink: 0;", "{opp.chars().next().unwrap_or('?')}" }
                                        }
                                    }
                                    if opp_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                                    if opp_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                                    span { class: "schedule-opponent-full", "{opp}" }
                                }
                            }
                            if let Some((role_text, role_class)) = role_badge {
                                span { class: "{role_class}", "{role_text}" }
                            }
                        }
                        if opp_label.is_none() {
                            div { class: "schedule-timeline-event-teams schedule-timeline-event-teams--full d-flex align-items-center flex-wrap gap-1",
                                span { class: "d-inline-flex align-items-center gap-1",
                                    if let Some(ph) = &event.team1_photo {
                                        img { class: "rounded-circle", style: "width: 1.25em; height: 1.25em; object-fit: cover; flex-shrink: 0;", src: "{base_url}/static/{ph}", alt: "" }
                                    } else if !event.team1.is_empty() {
                                        span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.25em; height: 1.25em; font-size: 0.7em; background: #6c757d; color: white; flex-shrink: 0;", "{event.team1.chars().next().unwrap_or('?')}" }
                                    }
                                    span { class: "schedule-opponent-full", "{event.team1}" }
                                }
                                span { "vs" }
                                span { class: "d-inline-flex align-items-center gap-1",
                                    if let Some(ph) = &event.team2_photo {
                                        img { class: "rounded-circle", style: "width: 1.25em; height: 1.25em; object-fit: cover; flex-shrink: 0;", src: "{base_url}/static/{ph}", alt: "" }
                                    } else if !event.team2.is_empty() {
                                        span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.25em; height: 1.25em; font-size: 0.7em; background: #6c757d; color: white; flex-shrink: 0;", "{event.team2.chars().next().unwrap_or('?')}" }
                                    }
                                    span { class: "schedule-opponent-full", "{event.team2}" }
                                }
                            }
                        }
                        if !event.refs_list.is_empty() {
                            div { class: "schedule-timeline-event-refs d-flex align-items-center flex-wrap gap-1 mt-1",
                                span { class: "me-1", "Refs:" }
                                for (ref_display, ref_photo, r_kind, r_label) in &event_refs {
                                    span { class: "d-inline-flex align-items-center gap-1",
                                        if *r_kind == 0 {
                                            if let Some(ph) = ref_photo {
                                                img { class: "rounded-circle", style: "width: 1.15em; height: 1.15em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                                            } else {
                                                span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.15em; height: 1.15em; font-size: 0.65em; background: #6c757d; color: white;", "{ref_display.chars().next().unwrap_or('?')}" }
                                            }
                                        }
                                        if *r_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                                        if *r_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                                        span { "{r_label}" }
                                    }
                                }
                            }
                        }
                    }
                    div { class: "schedule-timeline-event-meta",
                        div { class: "schedule-timeline-event-field", "{field_label}" }
                        div { class: "schedule-timeline-event-start", "{start_time_label}" }
                    }
                }
            } else if !is_break {
                div { class: "schedule-timeline-event-header d-flex align-items-center flex-wrap gap-1",
                    div { class: "schedule-timeline-event-name", "{event.name}" }
                }
                div { class: "schedule-timeline-event-teams d-flex align-items-center flex-wrap gap-1",
                    span { class: "d-inline-flex align-items-center gap-1",
                        if t1_kind == 0 {
                            if let Some(ph) = &event.team1_photo {
                                img { class: "rounded-circle", style: "width: 1.25em; height: 1.25em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                            } else {
                                span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.25em; height: 1.25em; font-size: 0.7em; background: #6c757d; color: white;", "{event.team1.chars().next().unwrap_or('?')}" }
                            }
                        }
                        if t1_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                        if t1_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                        span { "{t1_label}" }
                    }
                    span { " vs " }
                    span { class: "d-inline-flex align-items-center gap-1",
                        if t2_kind == 0 {
                            if let Some(ph) = &event.team2_photo {
                                img { class: "rounded-circle", style: "width: 1.25em; height: 1.25em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                            } else {
                                span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.25em; height: 1.25em; font-size: 0.7em; background: #6c757d; color: white;", "{event.team2.chars().next().unwrap_or('?')}" }
                            }
                        }
                        if t2_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                        if t2_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                        span { "{t2_label}" }
                    }
                }
                if !event.refs_list.is_empty() {
                    div { class: "schedule-timeline-event-refs d-flex align-items-center flex-wrap gap-1 mt-1",
                        span { class: "me-1", "Refs:" }
                        for (ref_display, ref_photo, r_kind, r_label) in &event_refs {
                            span { class: "d-inline-flex align-items-center gap-1",
                                if *r_kind == 0 {
                                    if let Some(ph) = ref_photo {
                                        img { class: "rounded-circle", style: "width: 1.1em; height: 1.1em; object-fit: cover;", src: "{base_url}/static/{ph}", alt: "" }
                                    } else {
                                        span { class: "team-token-avatar rounded-circle d-inline-flex align-items-center justify-content-center", style: "width: 1.1em; height: 1.1em; font-size: 0.65em; background: #6c757d; color: white;", "{ref_display.chars().next().unwrap_or('?')}" }
                                    }
                                }
                                if *r_kind == 1 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" } }
                                if *r_kind == 2 { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" } }
                                span { "{r_label}" }
                            }
                        }
                    }
                }
            } else {
                div { class: "schedule-timeline-event-header d-flex align-items-center flex-wrap gap-1",
                    div { class: "schedule-timeline-event-name", "{event.name}" }
                }
            }
            if event.ribbon {
                span {
                    class: "schedule-timeline-ribbon-icon",
                    title: "This is a ribbon game",
                    img { src: "{base_url}/static/ribbon.svg", alt: "Ribbon game" }
                }
            }
        }
    }
}

/// In-flight drag gesture on the edit-page timeline.
#[derive(Clone, Debug, PartialEq)]
enum TimelineDrag {
    /// Drag on empty grid space: gcal-style growing create placeholder.
    Create {
        col: usize,
        anchor_min: i64,
        cur_min: i64,
    },
    /// Drag of an existing block (ghost follows the cursor).
    Move {
        id: String,
        schedule_type: String,
        name: String,
        duration_min: i64,
        grab_offset_min: i64,
        orig_col: usize,
        orig_start_min: i64,
        cur_col: usize,
        cur_start_min: i64,
        moved: bool,
    },
}

/// Snap minutes-from-midnight to the nearest 5-minute increment.
fn snap5(min: i64) -> i64 {
    (((min as f64) / 5.0).round() as i64 * 5).clamp(0, 24 * 60)
}

/// Convert viewport client coordinates to (field column, minutes from 00:00)
/// using the events-layer bounding rect — the same math block placement uses,
/// so zoom (`--slot-height`) and scrolling are automatically respected.
#[allow(unused_variables)]
fn grid_pos_from_client(client_x: f64, client_y: f64, num_fields: usize) -> Option<(usize, i64)> {
    #[cfg(target_arch = "wasm32")]
    {
        let doc = web_sys::window()?.document()?;
        let el = doc.get_element_by_id("schedule-timeline-events-layer")?;
        let rect = el.get_bounding_client_rect();
        if rect.width() <= 0.0 || rect.height() <= 0.0 {
            return None;
        }
        let x = client_x - rect.left();
        let y = client_y - rect.top();
        if x < 0.0 || x > rect.width() || y < 0.0 || y > rect.height() {
            return None;
        }
        let col = ((x / rect.width()) * num_fields as f64).floor() as isize;
        let col = col.clamp(0, num_fields.saturating_sub(1) as isize) as usize;
        let minutes = ((y / rect.height()) * (24.0 * 60.0)).round() as i64;
        Some((col, minutes.clamp(0, 24 * 60)))
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        None
    }
}

/// Format minutes-from-midnight as "HH:MM".
fn fmt_minutes(min: i64) -> String {
    format!("{:02}:{:02}", (min / 60).clamp(0, 23), min % 60)
}

/// The match on `field_name` whose displayed start (local) is the latest one
/// at-or-before `before_local`. Returns (uuid, name, display end local).
/// Used for drag-created previous-match defaults and dynamic-move snapping.
fn latest_match_before(
    matches: &[MatchSetupData],
    field_name: &str,
    before_local: chrono::NaiveDateTime,
    exclude_uuid: &str,
    show_as_happened: bool,
    tz_offset_minutes: i64,
) -> Option<(String, String, chrono::NaiveDateTime)> {
    matches
        .iter()
        .filter(|m| m.status != "SKIPPED")
        .filter(|m| m.uuid != exclude_uuid)
        .filter(|m| m.field.as_deref() == Some(field_name))
        .filter_map(|m| {
            let (s, e) = display_interval_utc(m, show_as_happened)?;
            let s_local = s + chrono::Duration::minutes(tz_offset_minutes);
            let e_local = e + chrono::Duration::minutes(tz_offset_minutes);
            if s_local <= before_local {
                Some((m.uuid.clone(), m.name.clone(), s_local, e_local))
            } else {
                None
            }
        })
        .max_by_key(|(_, _, s, _)| *s)
        .map(|(u, n, _, e)| (u, n, e))
}

/// Strip a `Name::winner` / `Name::loser` reference token to the match name.
fn ref_token_match_name(token: &str) -> Option<&str> {
    let t = token.trim();
    t.strip_suffix("::winner")
        .or_else(|| t.strip_suffix("::loser"))
        .map(str::trim)
}

/// Line color for a dependency edge kind
/// (0 = chain / previous match, 1 = team1 result, 2 = team2 result, _ = ref result).
fn dep_edge_color(kind: u8) -> &'static str {
    match kind {
        0 => "#0d6efd",
        1 | 2 => "#d63384",
        _ => "#fd7e14",
    }
}

/// Stacked outline rings for a block in the alt-dependency view: one 3px ring
/// per edge (in order), colored like its line, with 1px translucent-white
/// separators so adjacent same-color rings stay countable. Returns an inline
/// `box-shadow: …;` declaration.
fn dep_ring_shadow(kinds: &[u8]) -> String {
    let shadows: Vec<String> = kinds
        .iter()
        .enumerate()
        .map(|(i, kind)| {
            let inner = (i as u32) * 4;
            format!(
                "0 0 0 {}px {}, 0 0 0 {}px rgba(255,255,255,0.9)",
                inner + 3,
                dep_edge_color(*kind),
                inner + 4
            )
        })
        .collect();
    format!("box-shadow: {};", shadows.join(", "))
}

/// Block geometry in the events-layer coordinate space, as produced by the
/// overlay layout: `left`/`width` are lane-adjusted percentages of the layer
/// width; `top`/`height` are in slot units (percent-of-height = slots /
/// slots_per_day * 100). Both the block styles and the dependency lines are
/// derived from the same values, so endpoints land exactly on block edges.
/// (The blocks' cosmetic ±1px horizontal inset cancels out at the midpoint:
/// (left+1px) + (width−2px)/2 == left + width/2.)
#[derive(Clone, Copy, Debug, PartialEq)]
struct DepBlockGeom {
    /// Lane-adjusted left edge, % of layer width.
    left: f64,
    /// Lane-adjusted width, % of layer width.
    width: f64,
    /// Top edge in slot units.
    top_slots: f64,
    /// Height in slot units.
    height_slots: f64,
}

/// Endpoints for the `index`-th of `n_lines` visible dependency lines leaving
/// the hovered block `from` toward `to`, in events-layer percentages
/// (x: % of width, y: % of height).
///
/// - The line leaves the vertical edge of `from` facing `to` (top edge if the
///   target's center is above, else bottom edge) and arrives at the opposing
///   edge of `to`, both at the blocks' horizontal midpoints.
/// - When several lines leave the hovered block, their origins fan out around
///   its midpoint in ~0.6%-of-layer steps, clamped so the whole fan stays
///   within the middle 80% of the block's width. `index`/`n_lines` must count
///   only *visible* lines, otherwise a lone line gets a spurious offset.
fn dep_line_endpoints(
    index: usize,
    n_lines: usize,
    from: DepBlockGeom,
    to: DepBlockGeom,
    slots_per_day: usize,
) -> (f64, f64, f64, f64) {
    let step = if n_lines > 1 {
        (0.6_f64).min(from.width * 0.8 / (n_lines as f64 - 1.0))
    } else {
        0.0
    };
    let offset = (index as f64 - (n_lines as f64 - 1.0) / 2.0) * step;
    let fx = from.left + from.width / 2.0 + offset;
    let tx = to.left + to.width / 2.0;
    let slot_pct = |slots: f64| slots / slots_per_day as f64 * 100.0;
    let fcy = slot_pct(from.top_slots + from.height_slots / 2.0);
    let tcy = slot_pct(to.top_slots + to.height_slots / 2.0);
    let (fy, ty) = if tcy < fcy {
        // Target above: leave from the top edge, arrive at the target's bottom.
        (
            slot_pct(from.top_slots),
            slot_pct(to.top_slots + to.height_slots),
        )
    } else {
        (
            slot_pct(from.top_slots + from.height_slots),
            slot_pct(to.top_slots),
        )
    };
    (fx, fy, tx, ty)
}

#[cfg(test)]
mod dep_geometry_tests {
    use super::*;

    const SLOTS: usize = 48; // 24h of 30-min slots

    fn geom(left: f64, width: f64, top_slots: f64, height_slots: f64) -> DepBlockGeom {
        DepBlockGeom {
            left,
            width,
            top_slots,
            height_slots,
        }
    }

    #[test]
    fn single_line_starts_at_block_edge_midpoint() {
        // Hovered block: second of four field columns (25% wide), 10:00–11:00.
        let from = geom(25.0, 25.0, 20.0, 2.0);
        // Target above it, first column, 08:00–09:00.
        let to = geom(0.0, 25.0, 16.0, 2.0);
        let (fx, fy, tx, ty) = dep_line_endpoints(0, 1, from, to, SLOTS);
        assert_eq!(fx, 25.0 + 12.5); // exact horizontal midpoint, no fan offset
        assert_eq!(fy, 20.0 / 48.0 * 100.0); // top edge (target is above)
        assert_eq!(tx, 12.5);
        assert_eq!(ty, (16.0 + 2.0) / 48.0 * 100.0); // target bottom edge
    }

    #[test]
    fn single_line_respects_lane_inset() {
        // Hovered block in the right lane of a two-lane column: lane-adjusted
        // left/width, so the midpoint is the lane's center, not the column's.
        let from = geom(37.5, 12.5, 20.0, 2.0);
        let to = geom(0.0, 25.0, 30.0, 2.0);
        let (fx, fy, _, ty) = dep_line_endpoints(0, 1, from, to, SLOTS);
        assert_eq!(fx, 37.5 + 6.25);
        assert_eq!(fy, (20.0 + 2.0) / 48.0 * 100.0); // bottom edge (target below)
        assert_eq!(ty, 30.0 / 48.0 * 100.0);
    }

    #[test]
    fn fan_is_centered_and_stays_inside_block() {
        let from = geom(0.0, 25.0, 20.0, 2.0);
        let to = geom(50.0, 25.0, 16.0, 2.0);
        let n = 3;
        let xs: Vec<f64> = (0..n)
            .map(|i| dep_line_endpoints(i, n, from, to, SLOTS).0)
            .collect();
        // Centered on the midpoint, symmetric, evenly spaced.
        assert_eq!(xs[1], 12.5);
        assert!((xs[1] - xs[0] - (xs[2] - xs[1])).abs() < 1e-9);
        // Whole fan within the block's width.
        assert!(xs[0] > 0.0 && xs[2] < 25.0);
    }

    #[test]
    fn ring_shadow_one_ring_per_edge_in_order() {
        let css = dep_ring_shadow(&[0, 1, 3]);
        assert!(css.starts_with("box-shadow: "));
        assert_eq!(css.matches("rgba(255,255,255,0.9)").count(), 3);
        // Ring order (inner→outer) follows edge order; colors match the lines.
        let chain = css.find("#0d6efd").unwrap();
        let team = css.find("#d63384").unwrap();
        let refc = css.find("#fd7e14").unwrap();
        assert!(chain < team && team < refc);
    }
}

#[component]
fn ScheduleTimeline(
    data: ScheduleSetupResponse,
    selected_field: String,
    highlight_team: String,
    edit_mode: bool,
    /// Edit-mode-only: place blocks at exact real times instead of the
    /// "planned or earlier" viewer rule (see `display_interval_utc`).
    show_as_happened: bool,
    vertical_scale: Signal<f64>,
    /// When non-empty, single-column team view filtered to this team.
    #[props(default)]
    focus_team_id: String,
    tournament_url: String,
    on_edit_match: EventHandler<String>,
    /// Edit-page interactions: drag-to-create, drag-to-move, alt-hover dependency lines.
    #[props(default = false)]
    editor: bool,
    /// Bulk-length selection mode is active (drags disabled; clicks toggle selection).
    #[props(default = false)]
    bulk_select_active: bool,
    /// Blocks currently selected by the bulk-length tool.
    #[props(default)]
    selected_ids: Vec<String>,
    /// Pending-create placeholder shown while the create card is open (editor only).
    #[props(default)]
    pending_create: Option<PendingCreateGhost>,
    /// Show Winner/Loser chips on hovered match blocks (editor card is open with
    /// a team-ish input focused last).
    #[props(default = false)]
    result_pick_active: bool,
    /// Fired with a `<MatchName>::winner|loser` token when a chip is clicked.
    #[props(default)]
    on_pick_result: EventHandler<String>,
    /// Fired when a drag-to-create gesture (or plain empty-space click) completes.
    #[props(default)]
    on_drag_create: EventHandler<DragCreatePayload>,
    /// Fired when a drag-to-move gesture commits.
    #[props(default)]
    on_move_match: EventHandler<MoveCommitPayload>,
    key_nav: Signal<Option<String>>,
    on_key_nav_consumed: EventHandler<()>,
) -> Element {
    let team_view = !focus_team_id.is_empty();
    use chrono::NaiveDateTime;
    use chrono::Timelike;
    let navigator = use_navigator();

    // Get browser timezone offset in minutes (local = utc + offset). Only used on wasm.
    fn get_tz_offset_minutes() -> i64 {
        #[cfg(target_arch = "wasm32")]
        {
            let date = js_sys::Date::new_0();
            let offset = date.get_timezone_offset();
            -offset as i64 // get_timezone_offset returns UTC - local, so local = utc - offset
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            0_i64
        }
    }

    fn parse_schedule_time_to_local(s: &str, tz_offset_minutes: i64) -> Option<NaiveDateTime> {
        let utc_dt = {
            if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(s) {
                dt.naive_utc()
            } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f") {
                dt
            } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M") {
                dt
            } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M") {
                dt
            } else if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
                dt
            } else {
                return None;
            }
        };
        let local = utc_dt + chrono::Duration::minutes(tz_offset_minutes);
        Some(local)
    }

    let tz_offset_minutes = get_tz_offset_minutes();
    let scale = vertical_scale();
    let slot_height_rem = BASE_SLOT_HEIGHT_REM * scale;
    let scroll_el_id = if team_view {
        "schedule-timeline-scroll-team"
    } else {
        "schedule-timeline-scroll"
    };
    // After a zoom, apply this scrollTop once layout has the new slot-height.
    let mut pending_scroll_top = use_signal(|| None::<i32>);
    {
        let scroll_el_id = scroll_el_id;
        use_effect(move || {
            let _ = vertical_scale(); // re-run when scale changes
            if let Some(st) = pending_scroll_top() {
                pending_scroll_top.set(None);
                let id = scroll_el_id.to_string();
                #[cfg(target_arch = "wasm32")]
                {
                    // Double-rAF: wait until dioxus has committed the new --slot-height.
                    wasm_bindgen_futures::spawn_local(async move {
                        gloo_timers::future::TimeoutFuture::new(0).await;
                        apply_scroll_top(&id, st);
                    });
                }
                #[cfg(not(target_arch = "wasm32"))]
                {
                    let _ = (id, st);
                }
            }
        });
    }

    // Tick so the now-line moves without a full schedule refetch.
    let mut now_tick = use_signal(|| 0u32);
    #[cfg(target_arch = "wasm32")]
    let now_tick_interval = use_signal(|| None as Option<Interval>);
    #[cfg(target_arch = "wasm32")]
    {
        let mut now_tick = now_tick;
        let mut now_tick_interval = now_tick_interval;
        use_effect(move || {
            if now_tick_interval.read().is_some() {
                return;
            }
            let handle = Interval::new(30_000, move || {
                now_tick.set(now_tick().wrapping_add(1));
            });
            now_tick_interval.set(Some(handle));
        });
    }
    let _ = now_tick();

    // ------------------------------------------------------------------
    // Edit-page interaction state (drag-to-create / drag-to-move / alt-hover).
    // ------------------------------------------------------------------
    let mut drag_state = use_signal(|| None::<TimelineDrag>);
    // A completed move drag must swallow the click that the browser fires on release.
    let mut suppress_next_click = use_signal(|| false);
    // Alt-hover dependency view state.
    let mut hovered_block = use_signal(|| None::<String>);
    let mut alt_down = use_signal(|| false);

    // Window-level Alt tracking so pressing/releasing Alt while stationary
    // over a block immediately shows/hides the dependency lines. The `alive`
    // cell stops the leaked (`forget`) listeners from writing to dropped
    // signals after the component unmounts.
    #[cfg(target_arch = "wasm32")]
    {
        let alive = use_hook(|| Rc::new(std::cell::Cell::new(true)));
        use_drop({
            let alive = alive.clone();
            move || alive.set(false)
        });
        let mut installed = use_signal(|| false);
        use_effect(move || {
            if !editor || installed() {
                return;
            }
            installed.set(true);
            use wasm_bindgen::JsCast;
            use wasm_bindgen::closure::Closure;
            if let Some(window) = web_sys::window() {
                let mut alt_sig_down = alt_down;
                let alive_down = alive.clone();
                let on_down = Closure::wrap(Box::new(move |e: web_sys::KeyboardEvent| {
                    if alive_down.get() && e.key() == "Alt" && !*alt_sig_down.peek() {
                        alt_sig_down.set(true);
                    }
                }) as Box<dyn FnMut(_)>);
                let mut alt_sig_up = alt_down;
                let alive_up = alive.clone();
                let on_up = Closure::wrap(Box::new(move |e: web_sys::KeyboardEvent| {
                    if alive_up.get() && e.key() == "Alt" && *alt_sig_up.peek() {
                        alt_sig_up.set(false);
                    }
                }) as Box<dyn FnMut(_)>);
                // Window blur (e.g. Alt+Tab away): the Alt keyup never arrives, so
                // reset the whole alt-hover state or the rings/lines stay stuck.
                let mut alt_sig_blur = alt_down;
                let mut hovered_blur = hovered_block;
                let alive_blur = alive.clone();
                let on_blur = Closure::wrap(Box::new(move |_e: web_sys::FocusEvent| {
                    if !alive_blur.get() {
                        return;
                    }
                    if *alt_sig_blur.peek() {
                        alt_sig_blur.set(false);
                    }
                    if hovered_blur.peek().is_some() {
                        hovered_blur.set(None);
                    }
                }) as Box<dyn FnMut(_)>);
                let _ = window.add_event_listener_with_callback(
                    "keydown",
                    on_down.as_ref().unchecked_ref(),
                );
                let _ = window
                    .add_event_listener_with_callback("keyup", on_up.as_ref().unchecked_ref());
                let _ = window
                    .add_event_listener_with_callback("blur", on_blur.as_ref().unchecked_ref());
                on_down.forget();
                on_up.forget();
                on_blur.forget();
            }
        });
    }

    // All match dates in local time (unique, sorted) for prev/next navigation
    // Use plan times for day navigation so the calendar of the day stays stable.
    let dates_with_matches: Vec<chrono::NaiveDate> = {
        let mut dates: Vec<chrono::NaiveDate> = data
            .matches
            .iter()
            .filter(|m| m.status != "SKIPPED")
            .filter_map(|m| plan_start_str(m).or_else(|| actual_start_str(m)))
            .filter_map(|s| parse_schedule_time_to_local(s, tz_offset_minutes))
            .map(|dt| dt.date())
            .collect();
        dates.sort();
        dates.dedup();
        dates
    };

    // Today in local time
    let today_local =
        (chrono::Utc::now() + chrono::Duration::minutes(tz_offset_minutes)).date_naive();

    // Default visible date: today if it has matches, else first day with matches
    let mut visible_date_signal = use_signal(|| {
        if dates_with_matches.contains(&today_local) {
            today_local
        } else {
            dates_with_matches.first().copied().unwrap_or(today_local)
        }
    });

    // React to keyboard nav (n/p/t) from Schedule
    let dates_for_nav = dates_with_matches.clone();
    use_effect(move || {
        let cmd = key_nav();
        if let Some(c) = cmd.as_deref() {
            let dates = dates_for_nav.clone();
            let current = visible_date_signal();
            match c {
                "next" => {
                    if let Some(idx) = dates.iter().position(|&d| d == current) {
                        if let Some(&next_date) = dates.get(idx + 1) {
                            visible_date_signal.set(next_date);
                        }
                    }
                }
                "prev" => {
                    if let Some(idx) = dates
                        .iter()
                        .position(|&d| d == current)
                        .and_then(|i| i.checked_sub(1))
                    {
                        if let Some(&prev_date) = dates.get(idx) {
                            visible_date_signal.set(prev_date);
                        }
                    }
                }
                "today" => {
                    if dates.contains(&today_local) {
                        visible_date_signal.set(today_local);
                    } else if let Some(&first) = dates.first() {
                        visible_date_signal.set(first);
                    }
                }
                _ => {}
            }
            on_key_nav_consumed.call(());
        }
    });

    // Team view uses one synthetic column; all-teams uses real fields.
    const TEAM_VIEW_FIELD_ID: u32 = u32::MAX;
    let team_view_field = FieldSetupData {
        id: TEAM_VIEW_FIELD_ID,
        name: data
            .team_options
            .iter()
            .find(|o| o.id == focus_team_id)
            .map(|o| team_full_label(o))
            .unwrap_or_else(|| {
                if focus_team_id.is_empty() {
                    "Team".to_string()
                } else {
                    focus_team_id.clone()
                }
            }),
        camera_urls: vec![],
    };
    let visible_fields: Vec<&FieldSetupData> = if team_view {
        vec![&team_view_field]
    } else if selected_field == "all" {
        data.fields.iter().collect()
    } else {
        data.fields
            .iter()
            .filter(|f| f.id.to_string() == selected_field)
            .collect()
    };

    // Time scale: full day (00:00 to 24:00), 30-minute slots.
    //
    // Important: the schedule times come back as RFC3339 with offsets. We currently
    // convert to UTC for layout. If we used a narrow window like 06:00–22:00,
    // tournaments in some timezones could have all matches fall outside the window
    // after UTC conversion, making the timeline appear empty with no errors.
    const SLOT_MINUTES: i64 = 30;
    const FIRST_HOUR: u32 = 0;
    const LAST_HOUR: u32 = 24;
    let slots_per_day = ((LAST_HOUR - FIRST_HOUR) * 60 / SLOT_MINUTES as u32) as usize;

    // Get current visible date value (reactive - will update when signal changes)
    let current_visible_date = visible_date_signal();

    // Build timeline events (non-join matches).
    // Placement uses `display_interval_utc`: the "planned or earlier" viewer rule,
    // or exact real times when the edit-mode "as happened" toggle is on.
    let mut timeline_events: Vec<TimelineEvent> = data
        .matches
        .iter()
        .filter(|m| m.status != "SKIPPED")
        .filter(|m| m.schedule_type.as_deref() != Some("JOIN"))
        .filter({
            // Team view: only matches the focus team plays or refs. Structural
            // rows (breaks/joins) have no participants, so they never appear.
            let team_options = data.team_options.clone();
            let focus_team_id = focus_team_id.clone();
            move |m: &&MatchSetupData| {
                if !team_view {
                    return true;
                }
                !is_structural_match(m)
                    && match_involves_team(m, &focus_team_id, &team_options)
            }
        })
        .filter_map(|m| {
            let (start_utc, end_utc) = display_interval_utc(m, show_as_happened)?;
            let start_dt = start_utc + chrono::Duration::minutes(tz_offset_minutes);
            let end_dt = end_utc + chrono::Duration::minutes(tz_offset_minutes);
            let length_min = (end_dt - start_dt).num_minutes().max(1);

            let (field_id, field_name) = if team_view {
                (
                    TEAM_VIEW_FIELD_ID,
                    m.field.clone().unwrap_or_else(|| "TBA".to_string()),
                )
            } else {
                let field_name = m.field.as_ref()?;
                let field = data.fields.iter().find(|f| &f.name == field_name)?;
                // Check if field is visible
                if selected_field != "all" && field.id.to_string() != selected_field {
                    return None;
                }
                (field.id, field.name.clone())
            };

            // Display labels: all-teams uses shortnames for density; team view uses full names.
            let opt1 = m
                .team1
                .as_ref()
                .and_then(|id| data.team_options.iter().find(|o| &o.id == id));
            let t1_raw = opt1
                .and_then(|o| o.pseudonym.as_deref())
                .map(String::from)
                .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string());
            let t1 = if team_view {
                opt1.map(team_full_label)
                    .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string())
            } else {
                opt1.map(|o| {
                    short_or_truncate(
                        o.pseudonym.as_deref().unwrap_or(o.id.as_str()),
                        o.shortname.as_deref(),
                    )
                })
                .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string())
            };
            let opt2 = m
                .team2
                .as_ref()
                .and_then(|id| data.team_options.iter().find(|o| &o.id == id));
            let t2_raw = opt2
                .and_then(|o| o.pseudonym.as_deref())
                .map(String::from)
                .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string());
            let t2 = if team_view {
                opt2.map(team_full_label)
                    .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string())
            } else {
                opt2.map(|o| {
                    short_or_truncate(
                        o.pseudonym.as_deref().unwrap_or(o.id.as_str()),
                        o.shortname.as_deref(),
                    )
                })
                .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string())
            };

            // Team profile photos
            let team1_photo = opt1.and_then(|o| o.profile_photo.clone());
            let team2_photo = opt2.and_then(|o| o.profile_photo.clone());
            // Refs: per-slot resolved id else initial (#197).
            let ref_toks = refs_tokens(m);
            let refs_list: Vec<(String, Option<String>)> = ref_toks
                .iter()
                .map(|token| {
                    if team_view {
                        let (label, photo, _) = resolve_team_display(token, &data.team_options);
                        (label, photo)
                    } else {
                        let opt = data.team_options.iter().find(|o| &o.id == token);
                        let display = opt
                            .map(|o| {
                                short_or_truncate(
                                    o.pseudonym.as_deref().unwrap_or(o.id.as_str()),
                                    o.shortname.as_deref(),
                                )
                            })
                            .unwrap_or_else(|| token.clone());
                        let photo = opt.and_then(|o| o.profile_photo.clone());
                        (display, photo)
                    }
                })
                .collect();
            let refs_display = refs_list
                .iter()
                .map(|(d, _)| d.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            let refs_display_raw = ref_toks
                .iter()
                .map(|token| {
                    let opt = data.team_options.iter().find(|o| &o.id == token);
                    opt.and_then(|o| o.pseudonym.as_deref())
                        .map(String::from)
                        .unwrap_or_else(|| token.clone())
                })
                .collect::<Vec<_>>()
                .join(", ");

            // Status tag palette only (never overwritten for highlight; highlight is on the block).
            // Editor: structural blocks (breaks/joins) show real statuses like matches.
            let (color, _) = if is_structural_match(m) && !editor {
                ("#e9ecef".to_string(), "—".to_string())
            } else {
                status_color_and_label(&m.status)
            };

            // Highlight: match against the raw (untruncated) pseudonyms so the user's full-name
            // query still matches teams whose rendered label was shortened.
            let (highlight_playing, highlight_ref) = if team_view || highlight_team.is_empty() {
                (false, false)
            } else {
                let ht = highlight_team.to_lowercase();
                let playing =
                    t1_raw.to_lowercase().contains(&ht) || t2_raw.to_lowercase().contains(&ht);
                let reffing = !playing && refs_display_raw.to_lowercase().contains(&ht);
                (playing, reffing)
            };

            let playing = team_view && team_is_playing(m, &focus_team_id);
            let reffing =
                team_view && team_is_reffing(m, &focus_team_id, &data.team_options);
            let team_role = if playing {
                "playing".to_string()
            } else if reffing {
                "reffing".to_string()
            } else {
                String::new()
            };
            let (opponent_label, opponent_photo, opponent_kind) = if playing {
                opponent_for_focus(m, &focus_team_id, &data.team_options)
                    .map(|(l, p, k)| (Some(l), p, k))
                    .unwrap_or((None, None, 0))
            } else {
                (None, None, 0)
            };

            Some(TimelineEvent {
                id: m.uuid.clone(),
                name: m.name.clone(),
                team1: t1,
                team2: t2,
                team1_photo,
                team2_photo,
                refs_display,
                refs_list,
                start_time: start_dt,
                end_time: end_dt,
                length_min,
                field_id,
                field_name,
                color: color.to_string(),
                status: m.status.clone(),
                schedule_type: m.schedule_type.clone(),
                lane_index: 0, // Will be computed below
                num_lanes: 1,  // Will be computed below
                highlight_playing,
                highlight_ref,
                ribbon: m.ribbon,
                team_role,
                opponent_label,
                opponent_photo,
                opponent_kind,
            })
        })
        .collect();

    // Helper: get start_slot and end_slot for an event (for which row to render in; still slot-based)
    let _event_slots = |e: &TimelineEvent| -> (usize, usize) {
        let start_slot = {
            if e.start_time.date() != current_visible_date {
                0
            } else {
                let hour = e.start_time.hour();
                let minute = e.start_time.minute();
                if hour < FIRST_HOUR || hour >= LAST_HOUR {
                    0
                } else {
                    let total_minutes = (hour - FIRST_HOUR) * 60 + minute;
                    (total_minutes as i64 / SLOT_MINUTES) as usize
                }
            }
        };
        let end_slot = {
            if e.end_time.date() != current_visible_date {
                slots_per_day
            } else {
                let hour = e.end_time.hour();
                let minute = e.end_time.minute();
                if hour < FIRST_HOUR || hour >= LAST_HOUR {
                    slots_per_day
                } else {
                    let total_minutes = (hour - FIRST_HOUR) * 60 + minute;
                    ((total_minutes as i64 / SLOT_MINUTES) as usize).max(start_slot + 1)
                }
            }
        };
        (start_slot, end_slot)
    };

    // True iff two events overlap in time (using exact start/end, not slots)
    let events_overlap = |a: &TimelineEvent, b: &TimelineEvent| -> bool {
        a.start_time < b.end_time && b.start_time < a.end_time
    };

    // Compute lanes using exact time overlap (not slot-based), so only actually overlapping events share width
    for field in &visible_fields {
        let field_event_indices: Vec<usize> = timeline_events
            .iter()
            .enumerate()
            .filter(|(_, e)| {
                e.field_id == field.id
                    && e.start_time.date() == current_visible_date
                    && e.schedule_type.as_deref() != Some("JOIN")
                    && e.status != "SKIPPED"
            })
            .map(|(i, _)| i)
            .collect();

        if field_event_indices.is_empty() {
            continue;
        }

        let mut sorted_indices = field_event_indices.clone();
        sorted_indices.sort_by_key(|&idx| timeline_events[idx].start_time);

        // Assign lane: first lane L such that no already-placed event that overlaps this one (in time) uses L
        for (k, &idx) in sorted_indices.iter().enumerate() {
            let event = &timeline_events[idx];
            let occupied_lanes: std::collections::HashSet<usize> = sorted_indices[..k]
                .iter()
                .filter(|&&i| events_overlap(event, &timeline_events[i]))
                .map(|&i| timeline_events[i].lane_index)
                .collect();
            let mut assigned_lane = 0;
            while occupied_lanes.contains(&assigned_lane) {
                assigned_lane += 1;
            }
            timeline_events[idx].lane_index = assigned_lane;
        }

        // num_lanes per event = 1 + max lane among all events that overlap this one (in time)
        for &idx in &field_event_indices {
            let event = &timeline_events[idx];
            let max_lane = field_event_indices
                .iter()
                .filter(|&&i| events_overlap(event, &timeline_events[i]))
                .map(|&i| timeline_events[i].lane_index)
                .max()
                .unwrap_or(0);
            timeline_events[idx].num_lanes = (max_lane + 1).max(1);
        }
    }

    // Build join groups (all-teams view only)
    let join_groups: Vec<JoinGroup> = if team_view {
        Vec::new()
    } else {
        use std::collections::HashMap;
        let mut groups: HashMap<String, Vec<&MatchSetupData>> = HashMap::new();

        for m in &data.matches {
            if m.status == "SKIPPED" {
                continue;
            }
            if m.schedule_type.as_deref() == Some("JOIN") {
                groups
                    .entry(m.name.clone())
                    .or_insert_with(Vec::new)
                    .push(m);
            }
        }

        groups
            .into_iter()
            .filter_map(|(name, matches)| {
                if matches.is_empty() {
                    return None;
                }

                // Get time from first match (same display rule as match blocks)
                let (start_utc, _) = display_interval_utc(matches[0], show_as_happened)?;
                let time_dt = start_utc + chrono::Duration::minutes(tz_offset_minutes);

                // Build per-field join matches (field_id -> match_uuid)
                let field_matches: Vec<(u32, String)> = matches
                    .iter()
                    .filter_map(|m| {
                        let field_name = m.field.as_ref()?;
                        let field_id = data
                            .fields
                            .iter()
                            .find(|f| &f.name == field_name)
                            .map(|f| f.id)?;
                        Some((field_id, m.uuid.clone()))
                    })
                    .filter(|(field_id, _)| {
                        selected_field == "all" || field_id.to_string() == selected_field
                    })
                    .collect();

                if field_matches.is_empty() {
                    return None;
                }

                Some(JoinGroup {
                    name: name.clone(),
                    time: time_dt,
                    field_matches,
                })
            })
            .collect()
    };

    // Pre-compute slot time strings
    let slot_times: Vec<String> = (0..slots_per_day)
        .map(|slot| {
            let minutes = (slot as u32) * SLOT_MINUTES as u32;
            let hour = FIRST_HOUR + minutes / 60;
            let minute = minutes % 60;
            format!("{:02}:{:02}", hour, minute)
        })
        .collect();

    // Pre-compute join line data with slots and exact top offset within slot (to-the-minute)
    #[allow(dead_code)]
    struct JoinLineData {
        slot: usize,
        /// Fraction of slot height (0..1) for vertical position within the slot
        top_fraction: f64,
        join: JoinGroup,
        time_str: String,
        start_col_idx: usize,
        end_col_idx: usize,
        // (visible field column index, match uuid)
        field_items: Vec<(usize, String)>,
    }

    let join_lines_data: Vec<JoinLineData> = join_groups
        .iter()
        .filter_map(|join| {
            let date = join.time.date();
            if date != current_visible_date {
                return None;
            }
            let hour = join.time.hour();
            let minute = join.time.minute();
            if hour < FIRST_HOUR || hour >= LAST_HOUR {
                return None;
            }
            let total_minutes = (hour - FIRST_HOUR) * 60 + minute;
            let slot = (total_minutes as i64 / SLOT_MINUTES) as usize;
            let minutes_within_slot = (total_minutes as i64) % SLOT_MINUTES;
            let top_fraction = (minutes_within_slot as f64) / (SLOT_MINUTES as f64);
            let time_str = join.time.format("%H:%M").to_string();

            let field_items: Vec<(usize, String)> = visible_fields
                .iter()
                .enumerate()
                .filter_map(|(col_idx, f)| {
                    join.field_matches
                        .iter()
                        .find(|(fid, _)| *fid == f.id)
                        .map(|(_, mid)| (col_idx, mid.clone()))
                })
                .collect();

            if field_items.is_empty() {
                return None;
            }

            let start_col_idx = field_items.iter().map(|(c, _)| *c).min().unwrap_or(0);
            let end_col_idx = field_items.iter().map(|(c, _)| *c).max().unwrap_or(0);

            Some(JoinLineData {
                slot,
                top_fraction,
                join: join.clone(),
                time_str,
                start_col_idx,
                end_col_idx,
                field_items,
            })
        })
        .collect();

    // Target row for auto-scroll: first match of the day, or current time if viewing today
    let first_match_slot = {
        let event_slots = timeline_events
            .iter()
            .filter(|e| e.start_time.date() == current_visible_date)
            .map(|e| {
                let h = e.start_time.hour();
                let m = e.start_time.minute();
                ((h - FIRST_HOUR) * 60 + m) as i64 / SLOT_MINUTES
            })
            .map(|s| s as usize);
        let join_slots = join_lines_data.iter().map(|j| j.slot);
        event_slots.chain(join_slots).min().unwrap_or(0)
    };
    let target_slot = if current_visible_date == today_local {
        let now_local = chrono::Utc::now() + chrono::Duration::minutes(tz_offset_minutes);
        let hour = now_local.hour();
        let minute = now_local.minute();
        let slot = ((hour - FIRST_HOUR) * 60 + minute) as i64 / SLOT_MINUTES;
        (slot as usize).min(slots_per_day.saturating_sub(1))
    } else {
        first_match_slot
    };

    // Auto-scroll only the timeline body to target row (do not scroll the page)
    let scroll_el_id = if team_view {
        "schedule-timeline-scroll-team"
    } else {
        "schedule-timeline-scroll"
    };
    use_effect(move || {
        let _ = visible_date_signal(); // re-run effect when date changes
        let slot = target_slot;
        let scroll_el_id = scroll_el_id;
        #[cfg(target_arch = "wasm32")]
        {
            let id = format!("schedule-timeline-slot-{}", slot);
            wasm_bindgen_futures::spawn_local(async move {
                gloo_timers::future::TimeoutFuture::new(100).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        let scroll_el = doc
                            .get_element_by_id(scroll_el_id)
                            .or_else(|| doc.get_element_by_id("schedule-timeline-scroll"));
                        if let (Some(scroll_el), Some(target_el)) =
                            (scroll_el, doc.get_element_by_id(&id))
                        {
                            let scroll_rect = scroll_el.get_bounding_client_rect();
                            let target_rect = target_el.get_bounding_client_rect();
                            let delta = target_rect.top() - scroll_rect.top();
                            let new_scroll_top = scroll_el.scroll_top() + delta as i32;
                            scroll_el.set_scroll_top(new_scroll_top.max(0));
                        }
                    }
                }
            });
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            let _ = (slot, scroll_el_id);
        }
    });

    const TIME_COL_WIDTH_PX: u32 = 80;
    let base_url = api::base_url();

    // Now-line: slot index + fraction within the slot when viewing today.
    let now_line_style: Option<String> = if current_visible_date == today_local {
        let now_local = chrono::Utc::now() + chrono::Duration::minutes(tz_offset_minutes);
        let total_minutes =
            (now_local.hour().saturating_sub(FIRST_HOUR) as i64) * 60 + (now_local.minute() as i64);
        let slots_f = total_minutes as f64 / SLOT_MINUTES as f64;
        Some(format!(
            "top: calc(var(--header-height) + var(--slot-height) * {:.4});",
            slots_f.clamp(0.0, slots_per_day as f64)
        ))
    } else {
        None
    };

    // ------------------------------------------------------------------
    // Edit-page interactions: precomputed lookups + handlers.
    // ------------------------------------------------------------------
    let num_fields_total = visible_fields.len().max(1);
    let field_names: Vec<String> = visible_fields.iter().map(|f| f.name.clone()).collect();
    let field_col_index: HashMap<u32, usize> = visible_fields
        .iter()
        .enumerate()
        .map(|(i, f)| (f.id, i))
        .collect();
    let day_start = current_visible_date.and_hms_opt(0, 0, 0).unwrap_or_default();
    // Per-block drag info for today's visible blocks: (schedule_type, name, start_min, duration_min, col).
    let drag_block_info: HashMap<String, (String, String, i64, i64, usize)> = timeline_events
        .iter()
        .filter(|e| e.start_time.date() == current_visible_date)
        .filter_map(|e| {
            let col = *field_col_index.get(&e.field_id)?;
            let start_min = (e.start_time.hour() as i64) * 60 + e.start_time.minute() as i64;
            let dur = (e.end_time - e.start_time).num_minutes().max(1);
            Some((
                e.id.clone(),
                (
                    e.schedule_type.clone().unwrap_or_else(|| "STATIC".into()),
                    e.name.clone(),
                    start_min,
                    dur,
                    col,
                ),
            ))
        })
        .collect();

    // Block pointerdown → start a move drag (cards stop propagation, so the
    // container's pointerdown only ever starts create drags on empty space).
    let on_block_drag_start: EventHandler<(String, f64, f64)> = EventHandler::new({
        let info = drag_block_info.clone();
        move |(id, cx, cy): (String, f64, f64)| {
            if !editor || bulk_select_active {
                return;
            }
            // Any fresh press clears a stale click-suppression flag (e.g. after a
            // drag that released over empty space, where no click ever fired).
            if suppress_next_click.peek().to_owned() {
                suppress_next_click.set(false);
            }
            let Some((st, name, start_min, dur, col)) = info.get(&id).cloned() else {
                return;
            };
            if st == "JOIN" {
                return;
            }
            let Some((_, ptr_min)) = grid_pos_from_client(cx, cy, num_fields_total) else {
                return;
            };
            drag_state.set(Some(TimelineDrag::Move {
                id,
                schedule_type: st,
                name,
                duration_min: dur,
                grab_offset_min: ptr_min - start_min,
                orig_col: col,
                orig_start_min: start_min,
                cur_col: col,
                cur_start_min: start_min,
                moved: false,
            }));
        }
    });

    // Card hover → track hovered block + Alt state for the dependency view.
    let on_block_hover: EventHandler<(String, bool, bool)> =
        EventHandler::new(move |(id, entered, alt): (String, bool, bool)| {
            if entered {
                hovered_block.set(Some(id));
            } else if hovered_block.peek().as_deref() == Some(id.as_str()) {
                hovered_block.set(None);
            }
            if *alt_down.peek() != alt {
                alt_down.set(alt);
            }
        });

    // Swallow the click that follows a completed move drag so it doesn't open
    // the edit modal.
    let wrapped_on_edit: EventHandler<String> = EventHandler::new(move |id: String| {
        if suppress_next_click() {
            suppress_next_click.set(false);
            return;
        }
        on_edit_match.call(id);
    });

    // Alt-hover dependency edges for the hovered block: (from, to, kind)
    // kind: 0 = chain (previous_match), 1 = team1 result, 2 = team2 result, 3 = ref result.
    let dep_edges: Vec<(String, String, u8)> = if editor && alt_down() {
        if let Some(hid) = hovered_block() {
            let name_to_uuid: HashMap<&str, &str> = data
                .matches
                .iter()
                .filter(|m| !is_structural_match(m))
                .map(|m| (m.name.as_str(), m.uuid.as_str()))
                .collect();
            let mut edges: Vec<(String, String, u8)> = Vec::new();
            if let Some(m) = data.matches.iter().find(|m| m.uuid == hid) {
                if let Some(prev) = m.previous_match.as_deref() {
                    if !prev.is_empty() {
                        edges.push((hid.clone(), prev.to_string(), 0));
                    }
                }
                for (tok, kind) in [(m.team1_initial.as_deref(), 1u8), (m.team2_initial.as_deref(), 2u8)] {
                    if let Some(name) = tok.and_then(ref_token_match_name) {
                        if let Some(&uuid) = name_to_uuid.get(name) {
                            edges.push((hid.clone(), uuid.to_string(), kind));
                        }
                    }
                }
                for tok in m.refs_initial.as_deref().unwrap_or("").split(',') {
                    if let Some(name) = ref_token_match_name(tok) {
                        if let Some(&uuid) = name_to_uuid.get(name) {
                            edges.push((hid.clone(), uuid.to_string(), 3));
                        }
                    }
                }
            }
            edges
        } else {
            Vec::new()
        }
    } else {
        Vec::new()
    };
    // Highlight classes for dependency targets (and the hovered source).
    let dep_class_map: HashMap<String, &'static str> = {
        let mut map = HashMap::new();
        if !dep_edges.is_empty() {
            for (from, to, kind) in &dep_edges {
                map.insert(from.clone(), "schedule-timeline-event--dep-source");
                let class = match kind {
                    0 => "schedule-timeline-event--dep-chain",
                    1 | 2 => "schedule-timeline-event--dep-team",
                    _ => "schedule-timeline-event--dep-ref",
                };
                // Chain highlight wins if a block is referenced multiple ways.
                map.entry(to.clone()).or_insert(class);
            }
        }
        map
    };
    // Nested outline rings, one ring per edge, colored like its line: the
    // hovered source gets a ring per OUTGOING edge, and every dependency
    // target gets a ring per INCOMING edge from the hovered match (e.g. a
    // match used as `A::winner` team and `A::loser` ref shows two rings on A).
    let dep_shadow_by_id: HashMap<String, String> = {
        let mut kinds_by_id: HashMap<String, Vec<u8>> = HashMap::new();
        for (from, to, kind) in &dep_edges {
            kinds_by_id.entry(from.clone()).or_default().push(*kind);
            kinds_by_id.entry(to.clone()).or_default().push(*kind);
        }
        kinds_by_id
            .into_iter()
            .map(|(id, kinds)| (id, dep_ring_shadow(&kinds)))
            .collect()
    };

    // Ghost placement for the in-flight drag:
    // (col, start_min, duration_min, title, sub_label, blocked, prev_gap_line: Option<(col, min)>)
    #[allow(clippy::type_complexity)]
    let ghost_render: Option<(usize, i64, i64, String, String, bool, Option<(usize, i64)>)> =
        if editor {
            match drag_state() {
                Some(TimelineDrag::Create {
                    col,
                    anchor_min,
                    cur_min,
                }) => {
                    let start = anchor_min.min(cur_min);
                    let dur = (anchor_min - cur_min).abs().max(10);
                    let title =
                        format!("{} – {}", fmt_minutes(start), fmt_minutes(start + dur));
                    Some((col, start, dur, title, "new match".to_string(), false, None))
                }
                Some(TimelineDrag::Move {
                    ref id,
                    ref schedule_type,
                    duration_min,
                    cur_col,
                    cur_start_min,
                    moved,
                    ..
                }) if moved => {
                    let field_name = field_names.get(cur_col).cloned().unwrap_or_default();
                    if matches!(schedule_type.as_str(), "STATIC" | "STATBREAK") {
                        // Static blocks place freely (5-min snapped).
                        let title = format!(
                            "{} – {}",
                            fmt_minutes(cur_start_min),
                            fmt_minutes(cur_start_min + duration_min)
                        );
                        Some((cur_col, cur_start_min, duration_min, title, field_name, false, None))
                    } else {
                        // Dynamic blocks snap to the gap after the would-be previous match.
                        let drop_local = day_start + chrono::Duration::minutes(cur_start_min);
                        match latest_match_before(
                            &data.matches,
                            &field_name,
                            drop_local,
                            id,
                            show_as_happened,
                            tz_offset_minutes,
                        ) {
                            Some((_, prev_name, prev_end_local)) => {
                                let snap_min = if prev_end_local.date() == current_visible_date {
                                    ((prev_end_local - day_start).num_minutes()).clamp(0, 24 * 60)
                                } else {
                                    0
                                };
                                let line = if prev_end_local.date() == current_visible_date {
                                    Some((cur_col, snap_min))
                                } else {
                                    None
                                };
                                Some((
                                    cur_col,
                                    snap_min,
                                    duration_min,
                                    format!("after: {prev_name}"),
                                    field_name,
                                    false,
                                    line,
                                ))
                            }
                            None => Some((
                                cur_col,
                                cur_start_min,
                                duration_min,
                                "needs a previous match".to_string(),
                                field_name,
                                true,
                                None,
                            )),
                        }
                    }
                }
                _ => None,
            }
        } else {
            None
        };

    // Container pointer handlers (edit page only; touch is reserved for scroll/pinch-zoom).
    let grid_pointer_down = {
        move |ev: Event<PointerData>| {
            if !editor || bulk_select_active {
                return;
            }
            if ev.pointer_type() != "mouse" {
                return;
            }
            if drag_state.peek().is_some() {
                return;
            }
            if suppress_next_click.peek().to_owned() {
                suppress_next_click.set(false);
            }
            let c = ev.client_coordinates();
            let Some((col, min)) = grid_pos_from_client(c.x, c.y, num_fields_total) else {
                return;
            };
            let snapped = snap5(min);
            drag_state.set(Some(TimelineDrag::Create {
                col,
                anchor_min: snapped,
                cur_min: snapped,
            }));
        }
    };
    // Reconcile hovered_block against the element actually under the pointer.
    // mouseenter/mouseleave are non-bubbling synthetic events and a dropped
    // mouseleave used to leave hovered_block stuck (alt-dependency outlines
    // persisting after the cursor left the block); hit-testing on every grid
    // pointermove makes the state self-healing.
    #[cfg(target_arch = "wasm32")]
    let mut reconcile_hover_from_point = {
        let mut hovered_block = hovered_block;
        move |client_x: f64, client_y: f64| {
            let under: Option<String> = web_sys::window()
                .and_then(|w| w.document())
                .and_then(|d| d.element_from_point(client_x as f32, client_y as f32))
                .and_then(|el| {
                    use wasm_bindgen::JsCast;
                    el.dyn_into::<web_sys::Element>().ok()
                })
                .and_then(|el| el.closest("[data-event-id]").ok().flatten())
                .and_then(|el| el.get_attribute("data-event-id"));
            if *hovered_block.peek() != under {
                hovered_block.set(under);
            }
        }
    };
    #[cfg(not(target_arch = "wasm32"))]
    let reconcile_hover_from_point = move |_client_x: f64, _client_y: f64| {};

    let grid_pointer_move = {
        move |ev: Event<PointerData>| {
            if !editor {
                return;
            }
            let alt = ev.modifiers().alt();
            if *alt_down.peek() != alt {
                alt_down.set(alt);
            }
            // Keep the alt-hover target honest whenever the dependency view is
            // active (cheap: one elementFromPoint per move while Alt is held).
            if alt {
                let c = ev.client_coordinates();
                reconcile_hover_from_point(c.x, c.y);
            }
            let Some(state) = drag_state.peek().clone() else {
                return;
            };
            let c = ev.client_coordinates();
            let Some((col, min)) = grid_pos_from_client(c.x, c.y, num_fields_total) else {
                return;
            };
            match state {
                TimelineDrag::Create {
                    col: c0,
                    anchor_min,
                    cur_min,
                } => {
                    let cur = snap5(min);
                    if cur != cur_min {
                        drag_state.set(Some(TimelineDrag::Create {
                            col: c0,
                            anchor_min,
                            cur_min: cur,
                        }));
                    }
                }
                TimelineDrag::Move {
                    id,
                    schedule_type,
                    name,
                    duration_min,
                    grab_offset_min,
                    orig_col,
                    orig_start_min,
                    cur_col,
                    cur_start_min,
                    moved,
                } => {
                    let new_start =
                        snap5(min - grab_offset_min).clamp(0, 24 * 60 - duration_min.min(24 * 60));
                    let new_moved = moved || new_start != orig_start_min || col != orig_col;
                    if new_start != cur_start_min || col != cur_col || new_moved != moved {
                        drag_state.set(Some(TimelineDrag::Move {
                            id,
                            schedule_type,
                            name,
                            duration_min,
                            grab_offset_min,
                            orig_col,
                            orig_start_min,
                            cur_col: col,
                            cur_start_min: new_start,
                            moved: new_moved,
                        }));
                    }
                }
            }
        }
    };
    let grid_pointer_up = {
        let matches_for_drop = data.matches.clone();
        let field_names_for_drop = field_names.clone();
        move |_ev: Event<PointerData>| {
            let Some(state) = drag_state.peek().clone() else {
                return;
            };
            drag_state.set(None);
            match state {
                TimelineDrag::Create {
                    col,
                    anchor_min,
                    cur_min,
                } => {
                    let start = anchor_min.min(cur_min);
                    let extent = (anchor_min - cur_min).abs();
                    let Some(field_name) = field_names_for_drop.get(col).cloned() else {
                        return;
                    };
                    let start_local = day_start + chrono::Duration::minutes(start);
                    // Plain click (no drag) = default length; otherwise min 10 minutes.
                    let length_min = if extent == 0 {
                        None
                    } else {
                        Some(extent.max(10) as u32)
                    };
                    let prev_match_id = latest_match_before(
                        &matches_for_drop,
                        &field_name,
                        start_local,
                        "",
                        show_as_happened,
                        tz_offset_minutes,
                    )
                    .map(|(u, _, _)| u);
                    on_drag_create.call(DragCreatePayload {
                        field_name,
                        start_local,
                        length_min,
                        prev_match_id,
                    });
                }
                TimelineDrag::Move {
                    id,
                    schedule_type,
                    name,
                    cur_col,
                    cur_start_min,
                    orig_col,
                    orig_start_min,
                    moved,
                    ..
                } => {
                    if !moved || (cur_col == orig_col && cur_start_min == orig_start_min) {
                        // Plain click: let the block's own click handler open the modal.
                        return;
                    }
                    suppress_next_click.set(true);
                    let Some(field_name) = field_names_for_drop.get(cur_col).cloned() else {
                        return;
                    };
                    let start_local = day_start + chrono::Duration::minutes(cur_start_min);
                    if matches!(schedule_type.as_str(), "STATIC" | "STATBREAK") {
                        let start_utc = start_local - chrono::Duration::minutes(tz_offset_minutes);
                        on_move_match.call(MoveCommitPayload {
                            match_id: id,
                            schedule_type,
                            group_name: name,
                            new_field: Some(field_name),
                            new_start_utc: Some(
                                start_utc.format("%Y-%m-%dT%H:%M:%SZ").to_string(),
                            ),
                            new_prev_id: None,
                        });
                    } else {
                        let new_prev_id = latest_match_before(
                            &matches_for_drop,
                            &field_name,
                            start_local,
                            &id,
                            show_as_happened,
                            tz_offset_minutes,
                        )
                        .map(|(u, _, _)| u);
                        on_move_match.call(MoveCommitPayload {
                            match_id: id,
                            schedule_type,
                            group_name: name,
                            new_field: Some(field_name),
                            new_start_utc: None,
                            new_prev_id,
                        });
                    }
                }
            }
        }
    };
    let grid_pointer_cancel = move |_ev: Event<PointerData>| {
        if drag_state.peek().is_some() {
            drag_state.set(None);
        }
        // Pointer left the grid: nothing can be alt-hovered anymore.
        if hovered_block.peek().is_some() {
            hovered_block.set(None);
        }
    };

    rsx! {
        div { class: "schedule-timeline-wrapper", id: "schedule-timeline-wrapper",
            div { class: "schedule-timeline-nav",
                {
                    let dates = dates_with_matches.clone();
                    let current = visible_date_signal();
                    let current_idx = dates.iter().position(|&d| d == current);
                    let has_prev = current_idx.and_then(|i| i.checked_sub(1)).and_then(|i| dates.get(i)).is_some();
                    let has_next = current_idx.map(|i| i + 1 < dates.len()).unwrap_or(false);
                    let dates_prev = dates_with_matches.clone();
                    let dates_today = dates_with_matches.clone();
                    let dates_next = dates_with_matches.clone();
                    rsx! {
                        button {
                            class: "btn btn-sm btn-outline-secondary",
                            disabled: !has_prev,
                            onclick: move |_| {
                                let d = dates_prev.clone();
                                let current = visible_date_signal();
                                if let Some(idx) = d.iter().position(|&d2| d2 == current).and_then(|i| i.checked_sub(1)) {
                                    if let Some(&prev_date) = d.get(idx) {
                                        visible_date_signal.set(prev_date);
                                    }
                                }
                            },
                            "← Prev"
                        }
                        button {
                            class: "btn btn-sm btn-outline-secondary",
                            onclick: move |_| {
                                if dates_today.contains(&today_local) {
                                    visible_date_signal.set(today_local);
                                } else if let Some(&first) = dates_today.first() {
                                    visible_date_signal.set(first);
                                }
                            },
                            "Today"
                        }
                        button {
                            class: "btn btn-sm btn-outline-secondary",
                            disabled: !has_next,
                            onclick: move |_| {
                                let d = dates_next.clone();
                                let current = visible_date_signal();
                                if let Some(idx) = d.iter().position(|&d2| d2 == current) {
                                    if let Some(&next_date) = d.get(idx + 1) {
                                        visible_date_signal.set(next_date);
                                    }
                                }
                            },
                            "Next →"
                        }
                        span { class: "schedule-timeline-date",
                            " {visible_date_signal().format(\"%A, %B %d\")}"
                        }
                        span {
                            class: "ms-auto small text-muted",
                            title: "Pinch on mobile, or Shift+scroll on desktop, to zoom the time axis",
                            if (scale - 1.0).abs() > 0.02 {
                                "{(scale * 100.0) as i32}%"
                            } else {
                                ""
                            }
                        }
                    }
                }
            }
            {
                let scroll_id = scroll_el_id;
                rsx! {
            div {
                class: "schedule-timeline-scroll",
                id: "{scroll_id}",
                // Shift+scroll zooms vertical time scale, centered on viewport middle.
                onwheel: move |ev: Event<WheelData>| {
                    let mods = ev.modifiers();
                    if mods.shift() {
                        ev.prevent_default();
                        let dy = ev.delta().strip_units().y;
                        // Shift+wheel often reports horizontal delta on trackpads; accept either.
                        let dx = ev.delta().strip_units().x;
                        let delta = if dy.abs() >= dx.abs() { dy } else { dx };
                        let factor = if delta < 0.0 {
                            1.08
                        } else if delta > 0.0 {
                            1.0 / 1.08
                        } else {
                            return;
                        };
                        let old = vertical_scale();
                        let next = (old * factor).clamp(MIN_VERTICAL_SCALE, MAX_VERTICAL_SCALE);
                        if (next - old).abs() < 1e-6 {
                            return;
                        }
                        let ratio = next / old;
                        if let Some(st) = scroll_top_after_centered_zoom(scroll_el_id, ratio) {
                            pending_scroll_top.set(Some(st));
                        }
                        vertical_scale.set(next);
                        ls_set(VERTICAL_SCALE_KEY, &format!("{next:.3}"));
                    }
                },
                // Pinch-to-zoom via two-finger touch distance, centered on viewport middle.
                onmounted: {
                    let scroll_id = scroll_id.to_string();
                    move |_cx| {
                    #[cfg(target_arch = "wasm32")]
                    {
                        use wasm_bindgen::closure::Closure;
                        use wasm_bindgen::JsCast;
                        if let Some(window) = web_sys::window() {
                            if let Some(doc) = window.document() {
                                if let Some(scroll_el) = doc.get_element_by_id(&scroll_id) {
                                    if scroll_el.get_attribute("data-pinch-zoom").as_deref() == Some("1") {
                                        return;
                                    }
                                    let _ = scroll_el.set_attribute("data-pinch-zoom", "1");
                                    let last_dist = Rc::new(RefCell::new(None::<f64>));
                                    let last_dist_move = last_dist.clone();
                                    let last_dist_end = last_dist.clone();
                                    let mut scale_sig = vertical_scale;
                                    let mut pending = pending_scroll_top;
                                    let scroll_id_move = scroll_id.clone();

                                    let on_touch_start = Closure::wrap(Box::new(move |e: web_sys::TouchEvent| {
                                        if e.touches().length() == 2 {
                                            let t0 = e.touches().get(0).unwrap();
                                            let t1 = e.touches().get(1).unwrap();
                                            let dx = t0.client_x() as f64 - t1.client_x() as f64;
                                            let dy = t0.client_y() as f64 - t1.client_y() as f64;
                                            *last_dist.borrow_mut() = Some((dx * dx + dy * dy).sqrt());
                                        }
                                    }) as Box<dyn FnMut(_)>);

                                    let on_touch_move = Closure::wrap(Box::new(move |e: web_sys::TouchEvent| {
                                        if e.touches().length() == 2 {
                                            let t0 = e.touches().get(0).unwrap();
                                            let t1 = e.touches().get(1).unwrap();
                                            let dx = t0.client_x() as f64 - t1.client_x() as f64;
                                            let dy = t0.client_y() as f64 - t1.client_y() as f64;
                                            let dist = (dx * dx + dy * dy).sqrt();
                                            if let Some(prev) = *last_dist_move.borrow() {
                                                if prev > 1.0 {
                                                    e.prevent_default();
                                                    let ratio = (dist / prev).clamp(0.92, 1.08);
                                                    let old = scale_sig();
                                                    let next = (old * ratio)
                                                        .clamp(MIN_VERTICAL_SCALE, MAX_VERTICAL_SCALE);
                                                    if (next - old).abs() >= 1e-6 {
                                                        let scale_ratio = next / old;
                                                        if let Some(st) = scroll_top_after_centered_zoom(
                                                            &scroll_id_move,
                                                            scale_ratio,
                                                        ) {
                                                            pending.set(Some(st));
                                                        }
                                                        scale_sig.set(next);
                                                        ls_set(VERTICAL_SCALE_KEY, &format!("{next:.3}"));
                                                    }
                                                }
                                            }
                                            *last_dist_move.borrow_mut() = Some(dist);
                                        }
                                    }) as Box<dyn FnMut(_)>);

                                    let on_touch_end = Closure::wrap(Box::new(move |_e: web_sys::TouchEvent| {
                                        *last_dist_end.borrow_mut() = None;
                                    }) as Box<dyn FnMut(_)>);

                                    let _ = scroll_el.add_event_listener_with_callback(
                                        "touchstart",
                                        on_touch_start.as_ref().unchecked_ref(),
                                    );
                                    let _ = scroll_el.add_event_listener_with_callback(
                                        "touchmove",
                                        on_touch_move.as_ref().unchecked_ref(),
                                    );
                                    let _ = scroll_el.add_event_listener_with_callback(
                                        "touchend",
                                        on_touch_end.as_ref().unchecked_ref(),
                                    );
                                    on_touch_start.forget();
                                    on_touch_move.forget();
                                    on_touch_end.forget();
                                }
                            }
                        }
                    }
                    }
                },
                div {
                    class: if team_view { "schedule-timeline schedule-timeline--team-view" } else { "schedule-timeline" },
                    // Important: this is the positioning container for join overlays.
                    style: "position: relative; --num-fields: {visible_fields.len()}; --time-col-width: {TIME_COL_WIDTH_PX}px; --slot-height: {slot_height_rem}rem;",
                    // Edit page: drag-to-create on empty grid space, drag-to-move ghosts.
                    // Blocks stop pointerdown propagation, so a drag starting here is
                    // always on empty space. Touch pointers are ignored (pinch-zoom).
                    onpointerdown: grid_pointer_down,
                    onpointermove: grid_pointer_move,
                    onpointerup: grid_pointer_up,
                    onpointerleave: grid_pointer_cancel,
                    onpointercancel: grid_pointer_cancel,
                    // Now line across the grid when viewing today (#196)
                    if let Some(style) = now_line_style.clone() {
                        div {
                            class: "schedule-now-line",
                            style: "{style}",
                            span { class: "schedule-now-line-label", "Now" }
                        }
                    }
                    div { class: "schedule-timeline-header",
                    div { class: "schedule-timeline-time-col", "Time" }
                    for field in &visible_fields {
                        div { class: "schedule-timeline-field-col", "{field.name}" }
                    }
                }
                div { class: "schedule-timeline-body",
                    for (slot, time_str) in (0..slots_per_day).zip(slot_times.iter()) {
                        {
                            let row_id = format!("schedule-timeline-slot-{}", slot);
                            rsx! {
                                div { class: "schedule-timeline-row", key: "{slot}",
                                    div { class: "schedule-timeline-time-col", id: "{row_id}", "{time_str}" }
                                    for (col_idx, field) in visible_fields.iter().enumerate() {
                                        div {
                                            class: "schedule-timeline-cell",
                                            key: "{field.id}-{slot}",
                                            {
                                                // Joins only in-cell; match blocks live in the overlay layer.
                                                let join_in_cell = join_lines_data.iter().find_map(|jl| {
                                                    if jl.slot != slot { return None; }
                                                    jl.field_items.iter()
                                                        .find(|(c, _)| *c == col_idx)
                                                        .map(|(_, mid)| (jl.join.name.clone(), mid.clone(), jl.top_fraction))
                                                });
                                                rsx! {
                                                    if let Some((join_name, match_id, join_top_fraction)) = join_in_cell {
                                                        div {
                                                            class: "schedule-timeline-join-in-cell",
                                                            style: format!("top: calc(var(--slot-height) * {});", join_top_fraction),
                                                            div { class: "schedule-timeline-join-line-in-cell" }
                                                            if edit_mode {
                                                                div {
                                                                    class: "schedule-timeline-join-label",
                                                                    // Don't let a click on the join label start a create drag.
                                                                    onpointerdown: move |ev: Event<PointerData>| ev.stop_propagation(),
                                                                    onclick: move |_| on_edit_match.call(match_id.clone()),
                                                                    "{join_name}"
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
                        }
                    }
                }
                // Match blocks: single overlay above the grid so multi-slot events are never
                // covered by later half-hour cell backgrounds (CSS grid paint order).
                {
                    let num_fields = visible_fields.len().max(1);
                    let field_index: std::collections::HashMap<u32, usize> = visible_fields
                        .iter()
                        .enumerate()
                        .map(|(i, f)| (f.id, i))
                        .collect();
                    // Geometry per visible block: (left%, width%, top slots, height slots).
                    // Reused by the dependency-line SVG below.
                    let mut geom_by_id: HashMap<String, (f64, f64, f64, f64)> = HashMap::new();
                    let overlay_events: Vec<(TimelineEvent, String)> = timeline_events
                        .iter()
                        .filter(|e| e.start_time.date() == current_visible_date)
                        .filter(|e| e.schedule_type.as_deref() != Some("JOIN"))
                        .filter(|e| e.status != "SKIPPED")
                        .filter_map(|e| {
                            let col = *field_index.get(&e.field_id)?;
                            let hour = e.start_time.hour();
                            let minute = e.start_time.minute();
                            if hour < FIRST_HOUR || hour >= LAST_HOUR {
                                return None;
                            }
                            let start_min = (hour - FIRST_HOUR) * 60 + minute;
                            let start_slots = start_min as f64 / SLOT_MINUTES as f64;
                            let duration_min = (e.end_time - e.start_time).num_minutes().max(1) as f64;
                            let duration_slots = duration_min / SLOT_MINUTES as f64;
                            let lane_w = 100.0 / e.num_lanes.max(1) as f64;
                            let lane_l = e.lane_index as f64 * lane_w;
                            // Position within the fields area (overlay excludes the time column).
                            let col_w = 100.0 / num_fields as f64;
                            let left = col as f64 * col_w + (lane_l / 100.0) * col_w;
                            let width = (lane_w / 100.0) * col_w;
                            geom_by_id.insert(e.id.clone(), (left, width, start_slots, duration_slots));
                            let is_structural = is_structural_type(e.schedule_type.as_deref());
                            // Editor: structural blocks look like matches (statuses shown).
                            let bg = if is_structural && !editor { "#f1f3f5" } else { "#ffffff" };
                            let style = format!(
                                "background-color: {bg}; position: absolute; box-sizing: border-box; \
                                 left: calc({left}% + 1px); width: calc({width}% - 2px); \
                                 top: calc(var(--slot-height) * {start_slots}); \
                                 height: calc(var(--slot-height) * {duration_slots}); z-index: 5;"
                            );
                            Some((e.clone(), style))
                        })
                        .collect();
                    // Dependency lines: only edges whose BOTH endpoints are visible in
                    // the current day/filter. Filter first, then assign fan offsets by
                    // the index among visible lines — indexing over all edges gave a
                    // lone visible line a spurious offset whenever a sibling edge's
                    // target was hidden (other day / other field filter).
                    let visible_dep_edges: Vec<(DepBlockGeom, DepBlockGeom, u8)> = dep_edges
                        .iter()
                        .filter_map(|(from, to, kind)| {
                            let (fl, fw, ft, fh) = *geom_by_id.get(from)?;
                            let (tl, tw, tt, th) = *geom_by_id.get(to)?;
                            Some((
                                DepBlockGeom {
                                    left: fl,
                                    width: fw,
                                    top_slots: ft,
                                    height_slots: fh,
                                },
                                DepBlockGeom {
                                    left: tl,
                                    width: tw,
                                    top_slots: tt,
                                    height_slots: th,
                                },
                                *kind,
                            ))
                        })
                        .collect();
                    let n_dep_lines = visible_dep_edges.len();
                    let dep_lines: Vec<(f64, f64, f64, f64, u8)> = visible_dep_edges
                        .iter()
                        .enumerate()
                        .map(|(i, (from_geom, to_geom, kind))| {
                            let (fx, fy, tx, ty) = dep_line_endpoints(
                                i,
                                n_dep_lines,
                                *from_geom,
                                *to_geom,
                                slots_per_day,
                            );
                            (fx, fy, tx, ty, *kind)
                        })
                        .collect();
                    let dep_lines_active = !dep_lines.is_empty();
                    // Ghost block + snap-gap indicator for the in-flight drag.
                    let ghost_block = ghost_render.as_ref().map(
                        |(col, start_min, dur_min, title, sub, blocked, gap_line)| {
                            let col_w = 100.0 / num_fields as f64;
                            let left = *col as f64 * col_w;
                            let top_slots = *start_min as f64 / SLOT_MINUTES as f64;
                            let height_slots = *dur_min as f64 / SLOT_MINUTES as f64;
                            let style = format!(
                                "left: calc({left}% + 1px); width: calc({col_w}% - 2px); \
                                 top: calc(var(--slot-height) * {top_slots}); \
                                 height: calc(var(--slot-height) * {height_slots});"
                            );
                            let gap_style = gap_line.map(|(gcol, gmin)| {
                                let gleft = gcol as f64 * col_w;
                                let gtop = gmin as f64 / SLOT_MINUTES as f64;
                                format!(
                                    "left: calc({gleft}% + 1px); width: calc({col_w}% - 2px); \
                                     top: calc(var(--slot-height) * {gtop});"
                                )
                            });
                            (style, title.clone(), sub.clone(), *blocked, gap_style)
                        },
                    );
                    // Pending-create placeholders: mirror the open create card's
                    // field(s)/start/length so the drag target stays visible. Group
                    // forms render one placeholder per checked field; joins render
                    // as a thin line-like strip.
                    let pending_blocks: Vec<(String, String, String, bool)> = pending_create
                        .as_ref()
                        .map(|g| {
                            if g.start_local.date() != current_visible_date {
                                return Vec::new();
                            }
                            let start_min = (g.start_local.hour() as i64) * 60
                                + g.start_local.minute() as i64;
                            let dur = g.length_min.max(10);
                            let top_slots = start_min as f64 / SLOT_MINUTES as f64;
                            let col_w = 100.0 / num_fields as f64;
                            let (title, sub) = if g.is_join {
                                (fmt_minutes(start_min), "new join".to_string())
                            } else {
                                (
                                    format!(
                                        "{} – {}",
                                        fmt_minutes(start_min),
                                        fmt_minutes(start_min + dur)
                                    ),
                                    "new match".to_string(),
                                )
                            };
                            g.field_names
                                .iter()
                                .filter_map(|fname| {
                                    let col = field_names.iter().position(|n| n == fname)?;
                                    let left = col as f64 * col_w;
                                    let height = if g.is_join {
                                        // Thin strip standing in for the join line.
                                        "height: 6px; padding: 0;".to_string()
                                    } else {
                                        let height_slots = dur as f64 / SLOT_MINUTES as f64;
                                        format!(
                                            "height: calc(var(--slot-height) * {height_slots});"
                                        )
                                    };
                                    Some((
                                        format!(
                                            "left: calc({left}% + 1px); width: calc({col_w}% - 2px); \
                                             top: calc(var(--slot-height) * {top_slots}); {height}"
                                        ),
                                        title.clone(),
                                        sub.clone(),
                                        g.is_join,
                                    ))
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    let base_url = base_url.clone();
                    let tournament_url = tournament_url.clone();
                    rsx! {
                        div {
                            class: "schedule-timeline-events-layer",
                            id: "schedule-timeline-events-layer",
                            style: format!(
                                "position: absolute; left: var(--time-col-width); right: 0; \
                                 top: var(--header-height); \
                                 height: calc(var(--slot-height) * {}); \
                                 pointer-events: none; z-index: 12;",
                                slots_per_day
                            ),
                            for (ev, style) in overlay_events {
                                div {
                                    style: "pointer-events: auto;",
                                    TimelineEventCard {
                                        event: ev.clone(),
                                        event_style: style,
                                        team_view: team_view,
                                        edit_mode: edit_mode,
                                        tournament_url: tournament_url.clone(),
                                        base_url: base_url.clone(),
                                        on_edit_match: wrapped_on_edit,
                                        editor: editor,
                                        selected: selected_ids.contains(&ev.id),
                                        dep_class: dep_class_map.get(&ev.id).map(|c| c.to_string()),
                                        // Rings for the hovered source AND every dependency target.
                                        dep_shadow: dep_shadow_by_id.get(&ev.id).cloned(),
                                        on_move_pointer_down: on_block_drag_start,
                                        on_hover: on_block_hover,
                                        result_pick_active: result_pick_active,
                                        on_pick_result: on_pick_result,
                                    }
                                }
                            }
                            for (pi, (pending_style, pending_title, pending_sub, pending_thin)) in pending_blocks.into_iter().enumerate() {
                                div {
                                    key: "pending-{pi}",
                                    class: "schedule-drag-ghost schedule-drag-ghost--pending",
                                    style: "{pending_style}",
                                    // Join strips are too thin for inner labels.
                                    if !pending_thin {
                                        div { class: "schedule-drag-ghost-title", "{pending_title}" }
                                        div { class: "schedule-drag-ghost-sub", "{pending_sub}" }
                                    }
                                }
                            }
                            if let Some((ghost_style, ghost_title, ghost_sub, ghost_blocked, gap_style)) = ghost_block {
                                if let Some(gs) = gap_style {
                                    div { class: "schedule-drag-gap-line", style: "{gs}" }
                                }
                                div {
                                    class: if ghost_blocked { "schedule-drag-ghost schedule-drag-ghost--blocked" } else { "schedule-drag-ghost" },
                                    style: "{ghost_style}",
                                    div { class: "schedule-drag-ghost-title", "{ghost_title}" }
                                    div { class: "schedule-drag-ghost-sub", "{ghost_sub}" }
                                }
                            }
                            if dep_lines_active {
                                svg {
                                    class: "schedule-dep-lines",
                                    style: "position: absolute; left: 0; top: 0; width: 100%; height: 100%; pointer-events: none; z-index: 30; overflow: visible;",
                                    for (i, (x1, y1, x2, y2, kind)) in dep_lines.iter().enumerate() {
                                        g {
                                            key: "{i}",
                                        line {
                                            x1: "{x1}%",
                                            y1: "{y1}%",
                                            x2: "{x2}%",
                                            y2: "{y2}%",
                                            stroke: match kind {
                                                0 => "#0d6efd",
                                                1 | 2 => "#d63384",
                                                _ => "#fd7e14",
                                            },
                                            stroke_width: "2.5",
                                            stroke_dasharray: match kind {
                                                0 => "none",
                                                1 => "none",
                                                2 => "7 4",
                                                _ => "3 3",
                                            },
                                        }
                                        circle {
                                            cx: "{x2}%",
                                            cy: "{y2}%",
                                            r: "3.5",
                                            fill: match kind {
                                                0 => "#0d6efd",
                                                1 | 2 => "#d63384",
                                                _ => "#fd7e14",
                                            },
                                        }
                                        }
                                    }
                                }
                                div { class: "schedule-dep-legend",
                                    span { class: "schedule-dep-legend-item",
                                        span { class: "schedule-dep-legend-swatch", style: "background:#0d6efd;" }
                                        "previous match"
                                    }
                                    span { class: "schedule-dep-legend-item",
                                        span { class: "schedule-dep-legend-swatch", style: "background:#d63384;" }
                                        "team result (dashed = team 2)"
                                    }
                                    span { class: "schedule-dep-legend-item",
                                        span { class: "schedule-dep-legend-swatch", style: "background:#fd7e14;" }
                                        "ref result"
                                    }
                                }
                            }
                        }
                    } // overlay block
                } // schedule-timeline
            } // schedule-timeline-scroll
                } // inner rsx! for scroll_id
            } // scroll_id block
        } // wrapper
    } // outer rsx!
    }
}

#[component]
fn EditMatchModal(
    tournament_url: String,
    match_id: String,
    data: ScheduleSetupResponse,
    on_close: EventHandler<()>,
    on_save: EventHandler<()>,
    /// Last-focused team-ish input ("team1" | "team2" | "refs").
    team_field_focus: Signal<Option<String>>,
    /// Winner/Loser token queued by the timeline chips for insertion.
    insert_team_ref: Signal<Option<String>>,
) -> Element {
    let match_data = data.matches.iter().find(|m| m.uuid == match_id).cloned();

    if match_data.is_none() {
        return rsx! { div { "Match not found" } };
    }

    let m = match_data.unwrap();

    // Started/completed/skipped: still fully editable in the UI; the server
    // rejects disallowed changes (surfaced via the warning + error alerts).
    let match_locked = matches!(
        m.status.as_str(),
        "IN_PROGRESS" | "COMPLETED" | "SKIPPED"
    );

    // Original schedule type for edit: only allow transitions STATIC→SAFE/FAST, SAFE→FAST
    let original_schedule_type = m.schedule_type.as_deref().unwrap_or("STATIC");

    let name = use_signal(|| m.name.clone());
    let mut field = use_signal(|| m.field.clone().unwrap_or_default());
    let schedule_type = use_signal(|| m.schedule_type.clone().unwrap_or("STATIC".to_string()));
    let length = use_signal(|| m.nominal_length.unwrap_or(60));

    let start_time_init = if let Some(t) = &m.nominal_start_time {
        utc_iso_to_local_datetime_input(t).unwrap_or_else(|| t.chars().take(16).collect::<String>())
    } else {
        "".to_string()
    };

    let start_time = use_signal(|| start_time_init);
    let mut previous_match_id = use_signal(|| m.previous_match.clone().unwrap_or_default());
    let mut refs = use_signal(|| {
        m.refs_initial
            .clone()
            .or(m.refs.clone())
            .unwrap_or_default()
    });
    let mut team1 = use_signal(|| {
        m.team1_initial
            .clone()
            .or(m.team1.clone())
            .unwrap_or_default()
    });
    let mut team2 = use_signal(|| {
        m.team2_initial
            .clone()
            .or(m.team2.clone())
            .unwrap_or_default()
    });
    let set_type = use_signal(|| m.set_type.clone().unwrap_or("SETS".to_string()));
    let nsets = use_signal(|| m.nsets.unwrap_or(3));
    let stones_per_set = use_signal(|| m.stones_per_set.unwrap_or(100));
    let ribbon = use_signal(|| m.ribbon);
    let mut skip_condition = use_signal(|| m.skip_condition.clone().unwrap_or_default());
    let mut skip_condition_help_open = use_signal(|| false);
    let mut skip_condition_validity = use_signal(|| None::<Result<(), String>>);

    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);
    let url_sig = use_signal(|| tournament_url.clone());

    // Sync field and previous_match_id from match data when modal opens (fixes initial display)
    let match_id_effect = match_id.clone();
    let data_effect = data.clone();
    use_effect(move || {
        if let Some(m) = data_effect
            .matches
            .iter()
            .find(|x| x.uuid == match_id_effect)
        {
            field.set(m.field.clone().unwrap_or_default());
            previous_match_id.set(m.previous_match.clone().unwrap_or_default());
        }
    });

    let matches_on_field_edit = matches_on_field_sorted(&data.matches, &field(), Some(&match_id));

    // When field changes, clear previous match so user must pick one on the new
    // field. Never auto-assign length/format from another match — the current
    // values stay unless the user types new ones.
    let mut on_field_change_edit = move |new_field: String| {
        field.set(new_field);
        previous_match_id.set("".to_string());
    };

    // Consume Winner/Loser tokens queued by the timeline chips into whichever
    // team-ish input was focused last (refs appends; team1/team2 replace).
    {
        let mut insert_team_ref = insert_team_ref;
        use_effect(move || {
            if let Some(tok) = insert_team_ref() {
                match team_field_focus.peek().as_deref() {
                    Some("team1") => team1.set(tok.clone()),
                    Some("team2") => team2.set(tok.clone()),
                    Some("refs") => {
                        let cur = refs
                            .peek()
                            .trim()
                            .trim_end_matches(',')
                            .trim()
                            .to_string();
                        refs.set(if cur.is_empty() {
                            tok.clone()
                        } else {
                            format!("{cur}, {tok}")
                        });
                    }
                    _ => {}
                }
                insert_team_ref.set(None);
            }
        });
    }

    let u_save = tournament_url.clone();
    let m_id_save = match_id.clone();
    let data_save = data.clone();
    let do_save_rc: Rc<RefCell<Box<dyn FnMut()>>> = Rc::new(RefCell::new(Box::new(move || {
        // Validation: BREAK, JOIN, FAST, SAFE require previous match and same field
        let st = schedule_type();
        if st == "BREAK" || st == "JOIN" || st == "FAST" || st == "SAFE" {
            let prev_id = previous_match_id().trim().to_string();
            if prev_id.is_empty() {
                error.set(Some(
                    "Previous match is required for Break, Join, Fast, and Safe matches."
                        .to_string(),
                ));
                return;
            }
            let current_field = field();
            if let Some(prev_m) = data_save.matches.iter().find(|x| x.uuid == prev_id) {
                if prev_m.field.as_deref() != Some(current_field.as_str()) {
                    error.set(Some(
                        "Previous match must be on the same field.".to_string(),
                    ));
                    return;
                }
            }
        }
        let tournament_url = u_save.clone();
        let match_id = m_id_save.clone();
        let on_save = on_save.clone();
        saving.set(true);
        error.set(None);
        spawn(async move {
            if (schedule_type() == "SAFE" || schedule_type() == "FAST")
                && !skip_condition().trim().is_empty()
            {
                if let Some(Err(msg)) = skip_condition_validity() {
                    error.set(Some(format!("Skip condition: {msg}")));
                    saving.set(false);
                    return;
                }
                match api::validate_dsl(&tournament_url, &skip_condition()).await {
                    Ok(res) => {
                        if !res.valid {
                            error.set(Some(format!(
                                "Skip condition: {}",
                                res.error.unwrap_or_else(|| "invalid".to_string())
                            )));
                            saving.set(false);
                            return;
                        }
                        if !res.result_type.iter().any(|t| t == "BOOL") {
                            let got = if res.result_type.is_empty() {
                                "unknown".to_string()
                            } else {
                                res.result_type.join(" | ")
                            };
                            error.set(Some(format!(
                                "Skip condition must evaluate to BOOL, got {got}."
                            )));
                            saving.set(false);
                            return;
                        }
                    }
                    Err(e) => {
                        error.set(Some(format!("Skip condition: {}", e)));
                        saving.set(false);
                        return;
                    }
                }
            }
            let refs_vec: Vec<String> = refs()
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            let len = if schedule_type() == "JOIN" {
                Some(0u32)
            } else {
                Some(length())
            };
            let req = UpdateMatchRequest {
                field: Some(field()),
                schedule_type: Some(schedule_type()),
                length: len,
                start_time: if start_time().is_empty() {
                    None
                } else {
                    local_datetime_to_utc_iso(&start_time()).or_else(|| Some(start_time()))
                },
                previous_match_id: if schedule_type() == "STATIC" {
                    None
                } else {
                    Some(previous_match_id())
                },
                refs: Some(refs_vec),
                team1: Some(team1()),
                team2: Some(team2()),
                set_type: Some(set_type()),
                nsets: Some(nsets()),
                stones_per_set: Some(stones_per_set()),
                ribbon: Some(ribbon()),
                skip_condition: Some(skip_condition()),
            };
            match api::update_match(&tournament_url, &match_id, &req).await {
                Ok(_) => {
                    saving.set(false);
                    on_save.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        });
    })));
    let do_save_rc2 = do_save_rc.clone();
    let do_save_rc3 = do_save_rc.clone();
    let onsubmit = move |ev: Event<FormData>| {
        ev.prevent_default();
        do_save_rc.borrow_mut()();
    };
    let form_keydown = move |ev: Event<KeyboardData>| {
        let key = ev.key().to_string();
        if key == "Enter" {
            if ev.modifiers().contains(Modifiers::SHIFT) {
                ev.prevent_default();
                ev.stop_propagation();
                do_save_rc2.borrow_mut()();
            } else {
                ev.prevent_default();
            }
        }
    };
    let modal_keydown = move |ev: Event<KeyboardData>| {
        let key = ev.key().to_string();
        if key == "Escape" {
            ev.prevent_default();
            on_close.call(());
        } else if key == "Enter" && ev.modifiers().contains(Modifiers::SHIFT) {
            ev.prevent_default();
            ev.stop_propagation();
            do_save_rc3.borrow_mut()();
        }
    };

    rsx! {
        div {
            // Docked editor card (not a modal): the schedule stays visible and
            // interactive beside/below it.
            div {
                class: "card schedule-editor-card",
                tabindex: -1,
                onkeydown: modal_keydown,
                div { class: "card-header d-flex justify-content-between align-items-center",
                    h5 { class: "mb-0", "Edit Match: {name}" }
                    button {
                        class: "btn-close",
                        r#type: "button",
                        "aria-label": "Close",
                        onclick: move |_| on_close.call(()),
                    }
                }
                        div { class: "card-body",
                        // Locked matches stay editable client-side; the server decides
                        // what (if anything) it will accept and the error shows here.
                        if match_locked {
                            div { class: "alert alert-warning py-2",
                                "This match has started or finished — changes will be rejected by the server."
                            }
                        }
                        if let Some(err) = error() {
                            div { class: "alert alert-danger", "{err}" }
                        }
                        form {
                            onsubmit: onsubmit,
                            onkeydown: form_keydown,

                            div { class: "row",
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Match Name" }
                                        input {
                                            class: "form-control",
                                            "type": "text",
                                            value: "{name}",
                                            disabled: true,
                                            readonly: true,
                                            title: "Match names are immutable once created.",
                                        }
                                    }
                                }
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Field" }
                                        select {
                                            class: "form-select",
                                            value: "{field}",
                                            onchange: move |e| on_field_change_edit(e.value()),
                                            option { value: "", "Select Field" }
                                            for f in &data.fields {
                                                option { value: "{f.name}", "{f.name}" }
                                            }
                                        }
                                    }
                                }
                            }

                            div { class: "row",
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Match Type" }
                                        select { class: "form-select", value: "{schedule_type}", onchange: move |e| {
                                            let mut schedule_type = schedule_type;
                                            let mut previous_match_id = previous_match_id;
                                            let v = e.value();
                                            schedule_type.set(v.clone());
                                            if v == "STATIC" {
                                                previous_match_id.set("".to_string());
                                            }
                                        },
                                            // Allowed transitions: STATIC→SAFE/FAST, SAFE→FAST; others cannot change type
                                            option { value: "STATIC", disabled: original_schedule_type != "STATIC", "Static" }
                                            option { value: "SAFE", disabled: original_schedule_type != "STATIC" && original_schedule_type != "SAFE", "Safe" }
                                            option { value: "FAST", disabled: original_schedule_type != "STATIC" && original_schedule_type != "SAFE" && original_schedule_type != "FAST", "Fast" }
                                            option { value: "BREAK", disabled: original_schedule_type != "BREAK", "Break" }
                                            option { value: "JOIN", disabled: original_schedule_type != "JOIN", "Join" }
                                        }
                                    }
                                }
                                if schedule_type() != "JOIN" {
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Length (min)" }
                                            input { class: "form-control", "type": "number", min: "0", value: "{length}", oninput: move |e| { let mut length = length; length.set(e.value().parse().unwrap_or(60)); } }
                                        }
                                    }
                                }
                            }

                            if schedule_type() == "STATIC" {
                                div { class: "mb-3",
                                    label { class: "form-label", "Start Time" }
                                    input { class: "form-control", "type": "datetime-local", value: "{start_time}", oninput: move |e| { let mut start_time = start_time; start_time.set(e.value()); } }
                                }
                            } else if schedule_type() == "SAFE" || schedule_type() == "FAST" || schedule_type() == "BREAK" || schedule_type() == "JOIN" {
                                div { class: "mb-3",
                                    label { class: "form-label", "Previous Match" }
                                    select { class: "form-select", value: "{previous_match_id}", onchange: move |e| { let mut previous_match_id = previous_match_id; previous_match_id.set(e.value()); },
                                        option { value: "", "None" }
                                        for m in &matches_on_field_edit {
                                            option { value: "{m.uuid}", "{m.name}" }
                                        }
                                    }
                                }
                            }

                            if schedule_type() == "STATIC" || schedule_type() == "SAFE" || schedule_type() == "FAST" {
                                div { class: "row",
                                    // Focus tracking feeds the timeline's Winner/Loser hover chips.
                                    div { class: "col-md-6",
                                        onfocusin: move |_| {
                                            let mut t = team_field_focus;
                                            t.set(Some("team1".to_string()));
                                        },
                                        TeamSelectionField {
                                            label: "Team 1".to_string(),
                                            team_options: data.team_options.clone(),
                                            tags: data.tags.clone(),
                                            matches: data.matches.clone(),
                                            value: team1(),
                                            on_change: move |s| team1.set(s),
                                            multiple: false,
                                            placeholder: "team 1".to_string(),
                                            help_text: Some("Team, match winner/loser, or tag".to_string()),
                                        }
                                    }
                                    div { class: "col-md-6",
                                        onfocusin: move |_| {
                                            let mut t = team_field_focus;
                                            t.set(Some("team2".to_string()));
                                        },
                                        TeamSelectionField {
                                            label: "Team 2".to_string(),
                                            team_options: data.team_options.clone(),
                                            tags: data.tags.clone(),
                                            matches: data.matches.clone(),
                                            value: team2(),
                                            on_change: move |s| team2.set(s),
                                            multiple: false,
                                            placeholder: "team 2".to_string(),
                                            help_text: Some("Team, match winner/loser, or tag".to_string()),
                                        }
                                    }
                                }
                                div {
                                    onfocusin: move |_| {
                                        let mut t = team_field_focus;
                                        t.set(Some("refs".to_string()));
                                    },
                                    TeamSelectionField {
                                        label: "Referees".to_string(),
                                        team_options: data.team_options.clone(),
                                        tags: data.tags.clone(),
                                        matches: data.matches.clone(),
                                        value: refs(),
                                        on_change: move |s| refs.set(s),
                                        multiple: true,
                                        placeholder: "(optional) teams, match winners/losers, or tags".to_string(),
                                        help_text: Some("(optional) teams, match winners/losers, or tags".to_string()),
                                    }
                                }
                                div { class: "row",
                                    div { class: "col-md-4",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Format" }
                                            select { class: "form-select", value: "{set_type}", onchange: move |e| { let mut set_type = set_type; set_type.set(e.value()); },
                                                option { value: "SETS", "Sets" }
                                                option { value: "STONES", "Stones" }
                                            }
                                        }
                                    }
                                    div { class: "col-md-4",
                                        div { class: "mb-3",
                                            label { class: "form-label", "Number of sets" }
                                            input { class: "form-control", "type": "number", min: "1", value: "{nsets}", oninput: move |e| { let mut nsets = nsets; nsets.set(e.value().parse().unwrap_or(3)); } }
                                        }
                                    }
                                    if set_type() == "STONES" {
                                        div { class: "col-md-4",
                                            div { class: "mb-3",
                                                label { class: "form-label", "Stones per set" }
                                                input { class: "form-control", "type": "number", min: "1", value: "{stones_per_set}", oninput: move |e| { let mut stones_per_set = stones_per_set; stones_per_set.set(e.value().parse().unwrap_or(100)); } }
                                            }
                                        }
                                    }
                                }
                                div { class: "mb-3",
                                    div { class: "form-check",
                                        input { class: "form-check-input", "type": "checkbox", id: "edit-ribbon", checked: "{ribbon}", onchange: move |e| { let mut ribbon = ribbon; ribbon.set(e.value() == "true"); } }
                                        label { class: "form-check-label", "for": "edit-ribbon", "Ribbon game" }
                                    }
                                }
                                if schedule_type() == "SAFE" || schedule_type() == "FAST" {
                                    div { class: "mb-3",
                                        label { class: "form-label", "Skip condition" }
                                        div { class: "form-text mb-1",
                                            "Optional expression that evaluates to a boolean. If true, this match will be skipped. "
                                            a {
                                                href: "#",
                                                class: "text-decoration-none",
                                                onclick: move |ev: Event<MouseData>| {
                                                    ev.prevent_default();
                                                    skip_condition_help_open.set(true);
                                                },
                                                "(skip condition help)"
                                            }
                                        }
                                        AssEntry {
                                            id_suffix: "edit".to_string(),
                                            value: skip_condition(),
                                            on_change: move |v| skip_condition.set(v),
                                            team_options: data.team_options.clone(),
                                            tags: data.tags.clone(),
                                            matches: data.matches.clone(),
                                            tournament_url: tournament_url.clone(),
                                            placeholder: "e.g. (== 0 (losses [Team]))".to_string(),
                                            expected_type: vec!["BOOL".to_string()],
                                            on_validity_change: move |v: Option<Result<(), String>>| skip_condition_validity.set(v),
                                        }
                                    }
                                }
                            }

                            div { class: "modal-footer",
                                button { class: "btn btn-secondary", "type": "button", onclick: move |_| on_close.call(()), "Cancel (Esc)" }
                                button { class: "btn btn-danger", "type": "button",
                                    onclick: move |_| {
                                        // Delete match
                                        let u = url_sig();
                                        let mid = match_id.clone();
                                        let cb = on_save.clone();
                                        async move {
                                            if let Ok(_) = api::delete_match(&u, &mid).await {
                                                cb.call(());
                                            }
                                        }
                                    },
                                    "Delete"
                                }
                                button { class: "btn btn-primary", "type": "submit", disabled: "{saving}",
                                    if saving() { span { class: "spinner-border spinner-border-sm me-2" } }
                                    "Save (⇧↵)"
                                }
                            }
                        }
                    }
            }
            if skip_condition_help_open() {
                SkipConditionHelpModal { on_close: move |_| skip_condition_help_open.set(false) }
            }
        }
    }
}

/// Status color and label for timeline blocks and table status column (same logic in both places).
fn status_color_and_label(status: &str) -> (String, String) {
    let color = match status {
        "COMPLETED" => "#7acb8b",
        "IN_PROGRESS" => "#ffd666",
        "TIME_FINALIZED" => "#a5adb5",
        "READY_TO_START" => "#82b1ff",
        _ => "#6cc5d4",
    };
    let label: String = match status {
        "COMPLETED" => "Completed".to_string(),
        "IN_PROGRESS" => "In Progress".to_string(),
        "TIME_FINALIZED" => "Time Finalized".to_string(),
        "NOT_STARTED" => "Not Started".to_string(),
        "READY_TO_START" => "Ready to Start".to_string(),
        other => {
            let mut s = other.replace('_', " ").to_lowercase();
            if let Some(first) = s.get_mut(0..1) {
                first.make_ascii_uppercase();
            }
            s
        }
    };
    (color.to_string(), label)
}

/// Returns (kind, label): 0 = team (avatar + label), 1 = tag (tag icon + label), 2 = link (reference icon + "MatchName winner/loser").
fn team_ref_display(raw: &str) -> (u8, String) {
    if raw.ends_with("::winner") {
        let name = raw.strip_suffix("::winner").unwrap_or(raw).trim();
        (2, format!("{} winner", name))
    } else if raw.ends_with("::loser") {
        let name = raw.strip_suffix("::loser").unwrap_or(raw).trim();
        (2, format!("{} loser", name))
    } else if raw.len() >= 5
        && raw
            .get(..5)
            .map(|s| s.eq_ignore_ascii_case("tag::"))
            .unwrap_or(false)
    {
        (1, raw.get(5..).unwrap_or("").trim().to_string())
    } else {
        (0, raw.to_string())
    }
}
