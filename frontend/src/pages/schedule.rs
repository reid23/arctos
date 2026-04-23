use crate::api;
use crate::types::*;
use crate::Route;
use dioxus::html::ModifiersInteraction;
use dioxus::prelude::*;
#[cfg(target_arch = "wasm32")]
use gloo_timers::callback::Interval;
use serde::Serialize;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use super::TeamSelectionField;
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
    Some(local.with_timezone(&Utc).format("%Y-%m-%dT%H:%M:%SZ").to_string())
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

/// Effective start time for timeline/date nav: confirmed when set, else nominal.
fn effective_start_str(m: &MatchSetupData) -> Option<&str> {
    m.confirmed_start_time.as_deref().or(m.nominal_start_time.as_deref())
}

/// Format ISO timestamp in user's local time, without seconds (e.g. "14:30" or "2025-02-16 14:30").
fn format_time_local(iso: &str, tz_offset_minutes: i64) -> String {
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
    local.format("%H:%M").to_string()
}

#[component]
pub fn Schedule(url: String) -> Element {
    let url_data = url.clone();
    let mut setup_data = use_resource(move || {
        let u = url_data.clone();
        async move { api::schedule_setup(&u).await }
    });

    let mut view_mode = use_signal(|| "timeline".to_string());
    let mut edit_mode = use_signal(|| false);
    let mut selected_field = use_signal(|| "all".to_string());
    let mut highlight_team = use_signal(|| "".to_string());
    
    let mut is_to = use_signal(|| false);
    
    let mut active_modal = use_signal(|| "none".to_string());
    let mut selected_match_id = use_signal(|| "".to_string());
    let mut key_nav = use_signal(|| None::<String>);
    let refresh_trigger = use_signal(|| 0u32);
    #[cfg(target_arch = "wasm32")]
    let schedule_refresh_interval = use_signal(|| None as Option<Interval>);

    use_effect(move || {
        if let Some(Ok(data)) = setup_data.value().read().as_ref() {
            is_to.set(data.is_to);
        }
    });

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
        let mut setup_data = setup_data;
        setup_data.restart();
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
                    active_modal.set("none".to_string());
                } else {
                    match key_str.as_str() {
                        "n" | "N" => {
                            ev.prevent_default();
                            if view_mode() == "timeline" {
                                key_nav.set(Some("next".to_string()));
                            }
                        }
                        "p" | "P" => {
                            ev.prevent_default();
                            if view_mode() == "timeline" {
                                key_nav.set(Some("prev".to_string()));
                            }
                        }
                        "t" | "T" => {
                            ev.prevent_default();
                            if edit_mode() && is_to {
                                active_modal.set("tags".to_string());
                            } else if view_mode() == "timeline" {
                                key_nav.set(Some("today".to_string()));
                            }
                        }
                        "a" | "A" => {
                            ev.prevent_default();
                            view_mode.set("table".to_string());
                        }
                        "l" | "L" => {
                            ev.prevent_default();
                            view_mode.set("timeline".to_string());
                        }
                        "e" | "E" => {
                            ev.prevent_default();
                            if is_to {
                                edit_mode.set(!edit_mode());
                            }
                        }
                        "m" | "M" => {
                            if edit_mode() && is_to {
                                ev.prevent_default();
                                active_modal.set("match_create".to_string());
                            }
                        }
                        "f" | "F" => {
                            if edit_mode() && is_to {
                                ev.prevent_default();
                                active_modal.set("fields".to_string());
                            }
                        }
                        "x" | "X" => {
                            if edit_mode() && is_to {
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
                            }
                        }
                        "i" | "I" => {
                            if edit_mode() && is_to {
                                ev.prevent_default();
                                active_modal.set("toml_import".to_string());
                            }
                        }
                        "r" | "R" => {
                            if edit_mode() && is_to {
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
                    class: "container-fluid mt-3 position-relative schedule-keyboard-focus",
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
                                    li { class: "breadcrumb-item active", "Schedule" }
                                }
                            }
                        }
                    }

                    div { class: "card mb-3 bg-light",
                        div { class: "card-body p-2",
                            div { class: "d-flex flex-wrap justify-content-between align-items-center gap-2",
                                div { class: "d-flex flex-wrap align-items-center gap-2",
                                    select {
                                        class: "form-select form-select-sm d-inline-block w-auto",
                                        value: "{selected_field}",
                                        onchange: move |e| selected_field.set(e.value()),
                                        option { value: "all", "All Fields" }
                                        for f in &data.fields {
                                            option { value: "{f.id}", "{f.name}" }
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
                                    div { class: "btn-group btn-group-sm",
                                        button {
                                            class: if view_mode() == "timeline" { "btn btn-primary" } else { "btn btn-outline-primary" },
                                            onclick: move |_| view_mode.set("timeline".to_string()),
                                            "Timeline"
                                        }
                                        button {
                                            class: if view_mode() == "table" { "btn btn-primary" } else { "btn btn-outline-primary" },
                                            onclick: move |_| view_mode.set("table".to_string()),
                                            "Table"
                                        }
                                    }
                                }
                                if data.is_to {
                                    div { class: "d-flex flex-wrap align-items-center gap-1",
                                        if edit_mode() {
                                            button { class: "btn btn-sm btn-outline-secondary", onclick: move |_| active_modal.set("tags".to_string()), "Tags" }
                                            button { class: "btn btn-sm btn-outline-secondary", onclick: move |_| active_modal.set("fields".to_string()), "Fields" }
                                            button { class: "btn btn-sm btn-outline-success", onclick: move |_| active_modal.set("match_create".to_string()), "+ Match" }
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
                                        }
                                        div { class: "form-check form-switch mb-0 ms-1",
                                            input {
                                                class: "form-check-input",
                                                type: "checkbox",
                                                role: "switch",
                                                id: "editModeSwitch",
                                                checked: "{edit_mode}",
                                                onchange: move |e| edit_mode.set(e.value() == "true")
                                            }
                                            label { class: "form-check-label small", "for": "editModeSwitch", "Edit" }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if view_mode() == "timeline" {
                        ScheduleTimeline {
                            data: data.clone(),
                            selected_field: selected_field(),
                            highlight_team: highlight_team(),
                            edit_mode: edit_mode(),
                            tournament_url: url.clone(),
                            on_edit_match: move |id: String| {
                                selected_match_id.set(id);
                                active_modal.set("match_edit".to_string());
                            },
                            key_nav: key_nav,
                            on_key_nav_consumed: move |_| key_nav.set(None),
                        }
                    } else {
                        TableView { 
                            data: data.clone(), 
                            selected_field: selected_field(), 
                            highlight_team: highlight_team(),
                            edit_mode: edit_mode(),
                            tournament_url: url.clone(),
                            on_edit_match: move |id: String| {
                                selected_match_id.set(id);
                                active_modal.set("match_edit".to_string());
                            }
                        }
                    }
                    
                    // Modals (key forces remount so Edit modal gets fresh state from match)
                    if active_modal() == "match_edit" {
                        div { key: "{selected_match_id()}",
                            EditMatchModal { 
                                tournament_url: url.clone(), 
                                match_id: selected_match_id(),
                                data: data.clone(),
                                on_close: move |_| active_modal.set("none".to_string()),
                                on_save: move |_| {
                                    active_modal.set("none".to_string());
                                    refresh();
                                }
                            }
                        }
                    }
                    if active_modal() == "match_create" {
                        CreateMatchModal {
                            tournament_url: url.clone(),
                            data: data.clone(),
                            on_close: move |_| active_modal.set("none".to_string()),
                            on_save: move |_| {
                                active_modal.set("none".to_string());
                                refresh();
                            }
                        }
                    }
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
                }
            }
        }
        None => {
             // Check if it was an error or loading
             match val.read().as_ref() {
                Some(Err(e)) => rsx! { div { class: "alert alert-danger", "Error: {e}" } },
                _ => rsx! { div { class: "text-center mt-5", "Loading..." } }
             }
        }
    }
}

// ... Toolbar ...
// ... TableView ...
// ... TimelineView ...
// ... EditMatchModal ...

/// Matches on the given field, sorted by nominal_start_time descending (most recent first).
/// DSL function names and signatures for skip-condition docs popup (from app/utils/parser.py).
const DSL_FUNCTIONS: &[(&str, &str)] = &[
    ("wins", "(wins TEAM) -> INT"),
    ("losses", "(losses TEAM) -> INT"),
    ("winner", "(winner MATCH) -> TEAM"),
    ("loser", "(loser MATCH) -> TEAM"),
    ("points-won", "(points-won TEAM MATCH) -> INT"),
    ("points-lost", "(points-lost TEAM MATCH) -> INT"),
    ("is-skipped", "(is-skipped MATCH) -> BOOL"),
    ("+", "(+ INT INT) -> INT"),
    ("-", "(- INT INT) -> INT"),
    ("*", "(* INT INT) -> INT"),
    ("/", "(/ INT INT) -> INT"),
    (">", "(> INT INT) -> BOOL"),
    ("<", "(< INT INT) -> BOOL"),
    (">=", "(>= INT INT) -> BOOL"),
    ("<=", "(<= INT INT) -> BOOL"),
    ("==", "(== ANY ANY) -> BOOL"),
    ("or", "(or BOOL BOOL) -> BOOL"),
    ("and", "(and BOOL BOOL) -> BOOL"),
    ("not", "(not BOOL) -> BOOL"),
    ("if", "(if COND IF_TRUE IF_FALSE)"),
];

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
    v.sort_by(|a, b| b.nominal_start_time.as_deref().cmp(&a.nominal_start_time.as_deref()));
    v
}

/// Index in `new` (byte offset) where the single added character is, when new.len() == old.len() + 1.
fn skip_condition_new_char_index(old: &str, new: &str) -> Option<usize> {
    if new.len() != old.len() + 1 {
        return None;
    }
    let mut new_chars = new.char_indices();
    let mut old_chars = old.char_indices();
    loop {
        match (new_chars.next(), old_chars.next()) {
            (Some((i, c_new)), Some((_, c_old))) => {
                if c_new != c_old {
                    return Some(i);
                }
            }
            (Some((i, _)), None) => return Some(i),
            (None, _) => return None,
        }
    }
}

/// Find matching close bracket from open_pos (byte index of open char). Returns byte index of close char.
fn skip_condition_find_matching_close(s: &str, open_byte_pos: usize, open_c: char, close_c: char) -> Option<usize> {
    let rest = s.get(open_byte_pos..)?;
    let mut depth = 1u32;
    for (i, c) in rest.char_indices() {
        if c == open_c {
            depth += 1;
        } else if c == close_c {
            depth = depth.saturating_sub(1);
            if depth == 0 {
                return Some(open_byte_pos + i);
            }
        }
    }
    None
}

/// Innermost unclosed bracket: (content_start_byte, content_end_byte). content_end_byte = position of close or s.len().
#[allow(dead_code)]
fn skip_condition_innermost_unclosed(s: &str, open_c: char, close_c: char) -> Option<(usize, usize)> {
    let open_len = open_c.len_utf8();
    let mut search_end = s.len();
    loop {
        let Some(open_pos) = s[..search_end].rfind(open_c) else {
            break;
        };
        let close_pos = skip_condition_find_matching_close(s, open_pos, open_c, close_c);
        match close_pos {
            None => return Some((open_pos + open_len, s.len())),
            Some(_end) => search_end = open_pos,
        }
    }
    None
}

/// Cursor position in character index (e.g. from selection_start).
fn skip_condition_cursor_byte(s: &str, cursor_char: usize) -> usize {
    s.char_indices()
        .nth(cursor_char)
        .map(|(i, _)| i)
        .unwrap_or(s.len())
}

/// Innermost bracket that contains the cursor. Uses largest content_start (most recent open before cursor).
#[derive(Clone, Copy)]
enum InnermostBracket {
    Paren(usize, usize), // content_start_byte, content_end_byte
    Square(usize, usize),
    Curly(usize, usize),
}

fn skip_condition_innermost_around_cursor(s: &str, cursor_char: usize) -> Option<InnermostBracket> {
    let cursor_byte = skip_condition_cursor_byte(s, cursor_char);
    let mut best: Option<(usize, InnermostBracket)> = None; // (content_start, bracket)
    if let Some(open_pos) = s[..cursor_byte].rfind('(') {
        let close_pos = skip_condition_find_matching_close(s, open_pos, '(', ')');
        let content_end = close_pos.unwrap_or(s.len());
        if close_pos.is_none() || content_end >= cursor_byte {
            let content_start = open_pos + 1;
            if content_start <= cursor_byte {
                if best.map_or(true, |(cs, _)| content_start > cs) {
                    best = Some((content_start, InnermostBracket::Paren(content_start, content_end)));
                }
            }
        }
    }
    if let Some(open_pos) = s[..cursor_byte].rfind('[') {
        let close_pos = skip_condition_find_matching_close(s, open_pos, '[', ']');
        let content_end = close_pos.unwrap_or(s.len());
        if close_pos.is_none() || content_end >= cursor_byte {
            let content_start = open_pos + 1;
            if content_start <= cursor_byte {
                if best.map_or(true, |(cs, _)| content_start > cs) {
                    best = Some((content_start, InnermostBracket::Square(content_start, content_end)));
                }
            }
        }
    }
    if let Some(open_pos) = s[..cursor_byte].rfind('{') {
        let close_pos = skip_condition_find_matching_close(s, open_pos, '{', '}');
        let content_end = close_pos.unwrap_or(s.len());
        if close_pos.is_none() || content_end >= cursor_byte {
            let content_start = open_pos + 1;
            if content_start <= cursor_byte {
                if best.map_or(true, |(cs, _)| content_start > cs) {
                    best = Some((content_start, InnermostBracket::Curly(content_start, content_end)));
                }
            }
        }
    }
    best.map(|(_, b)| b)
}

/// Skip condition help modal (same content as Flask #dslHelpModal).
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
                        p { "The skip condition uses a Lisp-like language to express boolean conditions. If it evaluates to true, the match will be skipped." }

                        h6 { class: "mt-3", "Basic Values" }
                        ul {
                            li { code { "true" } " - True" }
                            li { code { "false" } " - False" }
                            li { code { "nil" } " - Nil" }
                            li { code { "[TeamName]" } " - Team name (username, " code { "tag::TagName" } ", or " code { "MatchName::winner" } "/" code { "MatchName::loser" } ")" }
                            li { code { "{{MatchName}}" } " - Match name" }
                        }

                        h6 { class: "mt-3", "Basic Operations" }
                        ul {
                            li { code { "(== A B)" } " - Equality comparison" }
                            li { code { "(> A B)" } ", " code { "(< A B)" } ", " code { "(>= A B)" } ", " code { "(<= A B)" } " - Numeric comparisons" }
                            li { code { "(and A B)" } ", " code { "(or A B)" } ", " code { "(not A)" } " - Logical operations" }
                        }

                        h6 { class: "mt-3", "Team Operations" }
                        ul {
                            li { code { "(wins [TeamName])" } " - Number of wins for a team" }
                            li { code { "(losses [TeamName])" } " - Number of losses for a team" }
                            li { code { "(points-won [TeamName])" } " - Total points won by a team" }
                            li { code { "(points-lost [TeamName])" } " - Total points lost by a team" }
                            li { code { "(points-won [TeamName] {{MatchName}})" } " - Points won in a specific match" }
                            li { code { "(points-lost [TeamName] {{MatchName}})" } " - Points lost in a specific match" }
                            li { code { "(is-skipped {{MatchName}})" } " - True if match status is SKIPPED, false if IN_PROGRESS or COMPLETED" }
                        }

                        h6 { class: "mt-3", "Match Operations" }
                        ul {
                            li { code { "(winner {{MatchName}})" } " - Winner team of a match (returns team or NIL)" }
                            li { code { "(loser {{MatchName}})" } " - Loser team of a match (returns team or NIL)" }
                        }

                        h6 { class: "mt-3", "Other Operations" }
                        ul {
                            li { code { "(if CONDITION IF_TRUE IF_FALSE)" } " - If condition is true, return IF_TRUE, otherwise return IF_FALSE" }
                            li { code { "(lambda (*args) (output))" } " - Define a lambda function" }
                            li { code { "(cons *_)" } " - Create a list from the arguments" }
                            li { code { "(car LIST)" } " - Get the first element of a list" }
                            li { code { "(cdr LIST)" } " - Get the rest of a list" }
                            li { code { "(get INDEX LIST)" } " - Get the element at index" }
                            li { code { "(or-default VAL DEFAULT)" } " - Returns VAL if VAL is not NIL else DEFAULT" }
                            li { code { "(len LIST)" } " - Length of a list" }
                            li { code { "(map LIST FUNC)" } " - Apply a function to each element of a list" }
                            li { code { "(reduce LIST FUNC)" } " - Reduce a list to a single value" }
                            li { code { "(max LIST)" } ", " code { "(min LIST)" } " - Max/min value in a list" }
                            li { code { "(max_by LIST FUNC)" } ", " code { "(min_by LIST FUNC)" } " - Max/min by a function" }
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
) -> Element {
    let name = use_signal(|| "".to_string());
    let mut field = use_signal(|| "".to_string());
    let schedule_type = use_signal(|| "STATIC".to_string());
    let mut length = use_signal(|| 60u32);
    let mut start_time = use_signal(|| "".to_string());
    let mut previous_match_id = use_signal(|| "".to_string());
    let mut refs = use_signal(|| "".to_string());
    let mut team1 = use_signal(|| "".to_string());
    let mut team2 = use_signal(|| "".to_string());
    let mut set_type = use_signal(|| "SETS".to_string());
    let mut nsets = use_signal(|| 3u32);
    let mut stones_per_set = use_signal(|| 100u32);
    let ribbon = use_signal(|| false);
    let mut skip_condition = use_signal(|| "".to_string());
    let mut skip_condition_error = use_signal(|| None::<String>);
    let mut skip_condition_simplified = use_signal(|| None::<String>);
    let mut skip_condition_cursor = use_signal(|| None::<usize>);
    let mut skip_condition_cursor_pos = use_signal(|| None::<usize>);
    let mut skip_docs_visible = use_signal(|| false);
    let mut skip_bracket_ac_visible = use_signal(|| false);
    let mut skip_bracket_ac_team = use_signal(|| true);
    let mut skip_bracket_ac_index = use_signal(|| 0usize);
    let mut skip_condition_help_open = use_signal(|| false);

    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);

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

    #[cfg(target_arch = "wasm32")]
    use_effect(move || {
        let pos = skip_condition_cursor();
        if let Some(p) = pos {
            skip_condition_cursor.set(None);
            let id = "skip-condition-input-create".to_string();
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                            if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                let _ = input.set_selection_range(p as u32, p as u32);
                                let _ = input.focus();
                            }
                        }
                    }
                }
            });
        }
    });

    let tournament_url_val = tournament_url.clone();
    use_effect(move || {
        let expr = skip_condition();
        let _ = expr.clone();
        if expr.trim().is_empty() {
            return;
        }
        let expr_captured = expr.clone();
        let url = tournament_url_val.clone();
        #[cfg(target_arch = "wasm32")]
        spawn(async move {
            gloo_timers::future::TimeoutFuture::new(3000).await;
            let current = skip_condition();
            if current == expr_captured {
                match api::validate_dsl(&url, &expr_captured).await {
                    Ok(res) => {
                        skip_condition_error.set(if res.valid { None } else { res.error.clone() });
                        skip_condition_simplified.set(if res.valid { res.simplified } else { None });
                    }
                    Err(e) => {
                        skip_condition_error.set(Some(e));
                        skip_condition_simplified.set(None);
                    }
                }
            }
        });
    });

    let matches_on_field = matches_on_field_sorted(&data.matches, &field(), None);

    let data_field = data.clone();
    let mut on_field_change = move |new_field: String| {
        field.set(new_field.clone());
        previous_match_id.set("".to_string());
        if !new_field.is_empty() {
            let list = matches_on_field_sorted(&data_field.matches, &new_field, None);
            if schedule_type() != "STATIC" {
                if let Some(m) = list.first() {
                    previous_match_id.set(m.uuid.clone());
                }
                if let Some(m) = list.first() {
                    length.set(m.nominal_length.unwrap_or(60));
                    set_type.set(m.set_type.clone().unwrap_or_else(|| "SETS".to_string()));
                    nsets.set(m.nsets.unwrap_or(3));
                    stones_per_set.set(m.stones_per_set.unwrap_or(100));
                }
            } else if let Some(m) = list.first().and_then(|x| x.nominal_start_time.as_ref()) {
                if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(m) {
                    start_time.set(dt.format("%Y-%m-%dT%H:%M").to_string());
                }
            }
        }
    };
    let data_prev = data.clone();
    let mut on_previous_match_change = move |new_prev_id: String| {
        previous_match_id.set(new_prev_id.clone());
        if !new_prev_id.is_empty() {
            if let Some(prev) = data_prev.matches.iter().find(|m| m.uuid == new_prev_id) {
                length.set(prev.nominal_length.unwrap_or(60));
                set_type.set(prev.set_type.clone().unwrap_or_else(|| "SETS".to_string()));
                nsets.set(prev.nsets.unwrap_or(3));
                stones_per_set.set(prev.stones_per_set.unwrap_or(100));
            }
        }
    };

    let data_create_validate = data.clone();
    let validate_create_rc: Rc<RefCell<Box<dyn FnMut() -> bool>>> = Rc::new(RefCell::new(Box::new(move || {
        let st = schedule_type();
        if st == "BREAK" || st == "JOIN" || st == "FAST" || st == "SAFE" {
            let prev_id = previous_match_id().trim().to_string();
            if prev_id.is_empty() {
                error.set(Some("Previous match is required for Break, Join, Fast, and Safe matches.".to_string()));
                return false;
            }
            let current_field = field();
            if current_field.is_empty() {
                error.set(Some("Field is required when using a previous match.".to_string()));
                return false;
            }
            if let Some(prev_m) = data_create_validate.matches.iter().find(|x| x.uuid == prev_id) {
                if prev_m.field.as_deref() != Some(current_field.as_str()) {
                    error.set(Some("Previous match must be on the same field.".to_string()));
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
            if (schedule_type() == "SAFE" || schedule_type() == "FAST") && !skip_condition().trim().is_empty() {
                match api::validate_dsl(&tournament_url, &skip_condition()).await {
                    Ok(res) => {
                        if !res.valid {
                            skip_condition_error.set(res.error);
                            skip_condition_simplified.set(None);
                            saving.set(false);
                            return;
                        }
                        skip_condition_simplified.set(res.simplified);
                    }
                    Err(e) => {
                        skip_condition_error.set(Some(e));
                        skip_condition_simplified.set(None);
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
                field: if field().is_empty() { None } else { Some(field()) },
                field_id: None,
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
                team1: if team1().is_empty() { None } else { Some(team1()) },
                team2: if team2().is_empty() { None } else { Some(team2()) },
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
    let submit_create_rc: Rc<RefCell<Box<dyn FnMut()>>> = Rc::new(RefCell::new(Box::new(move || {
        if !validate_create_rc2.borrow_mut()() {
            return;
        }
        let tournament_url = tournament_url_keydown.clone();
        let on_save = on_save.clone();
        spawn(async move {
            saving.set(true);
            error.set(None);
            if (schedule_type() == "SAFE" || schedule_type() == "FAST") && !skip_condition().trim().is_empty() {
                if let Ok(res) = api::validate_dsl(&tournament_url, &skip_condition()).await {
                    if !res.valid {
                        skip_condition_error.set(res.error);
                        skip_condition_simplified.set(None);
                        saving.set(false);
                        return;
                    }
                    skip_condition_simplified.set(res.simplified);
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
                field: if field().is_empty() { None } else { Some(field()) },
                field_id: None,
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
                team1: if team1().is_empty() { None } else { Some(team1()) },
                team2: if team2().is_empty() { None } else { Some(team2()) },
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

    let sc_val = skip_condition();
    let cursor_char = skip_condition_cursor_pos();
    let innermost = cursor_char.and_then(|c| skip_condition_innermost_around_cursor(&sc_val, c));
    let cursor_byte = cursor_char.map(|c| skip_condition_cursor_byte(&sc_val, c)).unwrap_or(0);
    let show_skip_docs = matches!(innermost, Some(InnermostBracket::Paren(_, _)));
    let docs_prefix = match &innermost {
        Some(InnermostBracket::Paren(cs, ce)) => {
            sc_val[*cs..*ce].trim().split_whitespace().next().unwrap_or("").to_lowercase()
        }
        _ => String::new(),
    };
    let docs_filtered: Vec<_> = if show_skip_docs {
        DSL_FUNCTIONS
            .iter()
            .filter(|(n, _)| n.to_lowercase().starts_with(docs_prefix.as_str()))
            .take(12)
            .collect()
    } else {
        vec![]
    };
    let (bracket_is_team, bracket_query) = match &innermost {
        Some(InnermostBracket::Square(cs, ce)) => {
            let end = (*ce).min(cursor_byte).max(*cs);
            (true, sc_val[*cs..end].trim().to_lowercase())
        }
        Some(InnermostBracket::Curly(cs, ce)) => {
            let end = (*ce).min(cursor_byte).max(*cs);
            (false, sc_val[*cs..end].trim().to_lowercase())
        }
        _ => (true, String::new()),
    };
    let show_bracket_ac = matches!(innermost, Some(InnermostBracket::Square(_, _)) | Some(InnermostBracket::Curly(_, _)));
    let bracket_ac_idx_raw = skip_bracket_ac_index();
    // (insert_value, display_value, profile_photo for teams). Teams: insert id; matches: insert name.
    // Team bracket also includes MatchName::winner and MatchName::loser.
    let bracket_options: Vec<(String, String, Option<String>)> = if show_bracket_ac && bracket_is_team {
        let team_opts: Vec<_> = data
            .team_options
            .iter()
            .filter(|t| {
                let disp = t.pseudonym.as_deref().unwrap_or(t.id.as_str());
                disp.to_lowercase().contains(bracket_query.as_str())
                    || t.id.to_lowercase().contains(bracket_query.as_str())
            })
            .map(|t| {
                (
                    t.id.clone(),
                    t.pseudonym.clone().unwrap_or_else(|| t.id.clone()),
                    t.profile_photo.clone(),
                )
            })
            .take(15)
            .collect();
        let match_qual_opts: Vec<_> = data
            .matches
            .iter()
            .flat_map(|m| {
                let w = (format!("{}::winner", m.name), format!("{}::winner", m.name), None);
                let l = (format!("{}::loser", m.name), format!("{}::loser", m.name), None);
                [w, l]
            })
            .filter(|(s, _, _)| s.to_lowercase().contains(bracket_query.as_str()))
            .take(15)
            .collect();
        let tag_opts: Vec<_> = data
            .tags
            .iter()
            .filter(|t| t.name.to_lowercase().contains(bracket_query.as_str()))
            .map(|t| (format!("tag::{}", t.name), t.name.clone(), None))
            .take(15)
            .collect();
        team_opts
            .into_iter()
            .chain(match_qual_opts)
            .chain(tag_opts)
            .take(20)
            .collect()
    } else if show_bracket_ac {
        data.matches
            .iter()
            .filter(|m| m.name.to_lowercase().contains(bracket_query.as_str()))
            .map(|m| (m.name.clone(), m.name.clone(), None))
            .take(15)
            .collect()
    } else {
        vec![]
    };
    let bracket_ac_idx = bracket_ac_idx_raw.min(bracket_options.len().saturating_sub(1));
    let docs_items: Vec<_> = if show_skip_docs && !docs_filtered.is_empty() {
        docs_filtered
            .iter()
            .map(|(dn, ds)| {
                let fname = dn.to_string();
                rsx! {
                    li {
                        class: "py-1 px-2 rounded",
                        onclick: move |_| {
                            let v = skip_condition();
                            if let Some(i) = v.rfind('(') {
                                skip_condition.set(format!("{}{}", &v[..=i], fname));
                            }
                            skip_docs_visible.set(false);
                        },
                        span { class: "fw-medium text-primary", "{dn}" }
                        span { class: "text-muted ms-1", " {ds}" }
                    }
                }
            })
            .collect()
    } else {
        vec![]
    };
    let base_url_create = api::base_url();
    let bracket_option_items: Vec<_> = if show_bracket_ac && !bracket_options.is_empty() {
        bracket_options
            .iter()
            .enumerate()
            .map(|(idx, (insert_val, display_val, photo))| {
                let opt_insert = insert_val.clone();
                let opt_display = display_val.clone();
                let opt_photo = photo.clone();
                let is_team = bracket_is_team;
                let is_cur = bracket_ac_idx == idx;
                let li_class = if is_cur {
                    "py-1 px-2 rounded bg-primary text-white"
                } else {
                    "py-1 px-2 rounded"
                };
                let avatar_node = if is_team {
                    if opt_insert.ends_with("::winner") || opt_insert.ends_with("::loser") {
                        rsx! {
                            img { class: "icon-primary-svg me-1", src: "{base_url_create}/static/reference.svg", alt: "Reference", style: "width: 1.25em; height: 1.25em;" }
                        }
                    } else if opt_insert.starts_with("tag::") {
                        rsx! {
                            img { class: "icon-primary-svg me-1", src: "{base_url_create}/static/tag.svg", alt: "Tag", style: "width: 1.25em; height: 1.25em;" }
                        }
                    } else if let Some(photo) = &opt_photo {
                        rsx! {
                            img {
                                src: "{base_url_create}/static/{photo}",
                                alt: "{opt_display}",
                                class: "team-token-avatar small me-1 rounded-circle",
                                style: "width: 1.5em; height: 1.5em; object-fit: cover;"
                            }
                        }
                    } else {
                        rsx! {
                            span { class: "team-token-avatar small me-1", "{opt_display.chars().next().unwrap_or('?')}" }
                        }
                    }
                } else {
                    rsx! { }
                };
                rsx! {
                    li {
                        class: "{li_class}",
                        onclick: move |_| {
                            let v = skip_condition();
                            let cursor_char = skip_condition_cursor_pos().unwrap_or(0);
                            let cursor_byte = skip_condition_cursor_byte(&v, cursor_char);
                            let inn = skip_condition_innermost_around_cursor(&v, cursor_char);
                            let Some(cs) = inn.and_then(|b| match b {
                                InnermostBracket::Square(cs, _) | InnermostBracket::Curly(cs, _) => Some(cs),
                                _ => None,
                            }) else {
                                skip_bracket_ac_visible.set(false);
                                return;
                            };
                            let new_v = format!("{}{}{}", &v[..cs], opt_insert, &v[cursor_byte..]);
                            skip_condition.set(new_v.clone());
                            let cs_chars = v[..cs].chars().count();
                            let new_cursor_char = cs_chars + opt_insert.chars().count();
                            skip_condition_cursor.set(Some(new_cursor_char));
                            skip_bracket_ac_visible.set(false);
                        },
                        {avatar_node}
                        span { "{display_val}" }
                    }
                }
            })
            .collect()
    } else {
        vec![]
    };

    rsx! {
        div {
            div {
                class: "modal d-block",
                tabindex: -1,
                style: "background: rgba(0,0,0,0.5)",
                onkeydown: modal_keydown,
                div { class: "modal-dialog modal-lg",
                    div { class: "modal-content",
                        div { class: "modal-header",
                            h5 { class: "modal-title", "New Match" }
                        }
                    div { class: "modal-body",
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

                            div { class: "row",
                                div { class: "col-md-6",
                                    div { class: "mb-3",
                                        label { class: "form-label", "Type" }
                                        select { class: "form-select", value: "{schedule_type}", onchange: move |e| { let mut schedule_type = schedule_type; schedule_type.set(e.value()); },
                                            option { value: "STATIC", "Static" }
                                            option { value: "SAFE", "Safe" }
                                            option { value: "FAST", "Fast" }
                                            option { value: "BREAK", "Break" }
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

                            if schedule_type() == "STATIC" {
                                div { class: "mb-3",
                                    label { class: "form-label", "Start Time" }
                                    input { class: "form-control", "type": "datetime-local", value: "{start_time}", oninput: move |e| { let mut start_time = start_time; start_time.set(e.value()); } }
                                }
                            } else if schedule_type() == "SAFE" || schedule_type() == "FAST" || schedule_type() == "BREAK" || schedule_type() == "JOIN" {
                                div { class: "mb-3",
                                    label { class: "form-label", "Previous Match" }
                                    select { class: "form-select", value: "{previous_match_id}", onchange: move |e| on_previous_match_change(e.value()),
                                        option { value: "", "None" }
                                        for m in &matches_on_field {
                                            option { value: "{m.uuid}", "{m.name}" }
                                        }
                                    }
                                }
                            }

                            if schedule_type() == "STATIC" || schedule_type() == "SAFE" || schedule_type() == "FAST" {
                                div { class: "row",
                                    div { class: "col-md-6",
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
                                    div { class: "mb-3 position-relative",
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
                                        input {
                                            id: "skip-condition-input-create",
                                            class: "form-control font-monospace",
                                            "type": "text",
                                            placeholder: "e.g. (== 0 (losses [Team]))",
                                            value: "{skip_condition}",
                                            oninput: move |e| {
                                                let new_val = e.value();
                                                let old = skip_condition();
                                                let (out, cursor_after_open) = if let Some(byte_i) = skip_condition_new_char_index(&old, &new_val) {
                                                    let open_c = new_val[byte_i..].chars().next().unwrap_or('\0');
                                                    let closing = match open_c {
                                                        '(' => ")",
                                                        '[' => {
                                                            skip_bracket_ac_visible.set(true);
                                                            skip_bracket_ac_team.set(true);
                                                            skip_bracket_ac_index.set(0);
                                                            "]"
                                                        }
                                                        '{' => {
                                                            skip_bracket_ac_visible.set(true);
                                                            skip_bracket_ac_team.set(false);
                                                            skip_bracket_ac_index.set(0);
                                                            "}"
                                                        }
                                                        _ => "",
                                                    };
                                                    if closing.is_empty() {
                                                        (new_val, None)
                                                    } else {
                                                        let char_end = byte_i + new_val[byte_i..].chars().next().map(|c| c.len_utf8()).unwrap_or(1);
                                                        let out_str = format!("{}{}{}", &new_val[..char_end], closing, &new_val[char_end..]);
                                                        (out_str, Some(char_end))
                                                    }
                                                } else {
                                                    (new_val, None)
                                                };
                                                skip_condition.set(out.clone());
                                                if let Some(pos) = cursor_after_open {
                                                    skip_condition_cursor.set(Some(pos));
                                                }
                                                skip_condition_error.set(None);
                                                if out.contains('(') {
                                                    skip_docs_visible.set(true);
                                                }
                                                let id = "skip-condition-input-create".to_string();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(0).await;
                                                    #[cfg(target_arch = "wasm32")]
                                                    if let Some(window) = web_sys::window() {
                                                        if let Some(doc) = window.document() {
                                                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                                                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                                    if let Ok(Some(sel)) = input.selection_start() {
                                                                        skip_condition_cursor_pos.set(Some(sel as usize));
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                            onfocus: move |_| {
                                                let id = "skip-condition-input-create".to_string();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(0).await;
                                                    #[cfg(target_arch = "wasm32")]
                                                    if let Some(window) = web_sys::window() {
                                                        if let Some(doc) = window.document() {
                                                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                                                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                                    if let Ok(Some(sel)) = input.selection_start() {
                                                                        skip_condition_cursor_pos.set(Some(sel as usize));
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                            onkeydown: move |ev: Event<KeyboardData>| {
                                                let key = ev.key().to_string();
                                                let n = bracket_options.len();
                                                if show_bracket_ac && n > 0 {
                                                    if key == "ArrowDown" {
                                                        ev.prevent_default();
                                                        skip_bracket_ac_index.set((bracket_ac_idx + 1) % n);
                                                        return;
                                                    }
                                                    if key == "ArrowUp" {
                                                        ev.prevent_default();
                                                        skip_bracket_ac_index.set((bracket_ac_idx + n - 1) % n);
                                                        return;
                                                    }
                                                    if key == "Enter" {
                                                        ev.prevent_default();
                                                        if let Some((opt_insert, _, _)) = bracket_options.get(bracket_ac_idx) {
                                                            let v = skip_condition();
                                                            let cursor_char = skip_condition_cursor_pos().unwrap_or(0);
                                                            let cursor_byte = skip_condition_cursor_byte(&v, cursor_char);
                                                            let inn = skip_condition_innermost_around_cursor(&v, cursor_char);
                                                            if let Some(cs) = inn.and_then(|b| match b {
                                                                InnermostBracket::Square(cs, _) | InnermostBracket::Curly(cs, _) => Some(cs),
                                                                _ => None,
                                                            }) {
                                                                let new_v = format!("{}{}{}", &v[..cs], opt_insert, &v[cursor_byte..]);
                                                                skip_condition.set(new_v);
                                                                let cs_chars = v[..cs].chars().count();
                                                                let new_cursor_char = cs_chars + opt_insert.chars().count();
                                                                skip_condition_cursor.set(Some(new_cursor_char));
                                                                skip_bracket_ac_visible.set(false);
                                                            }
                                                        }
                                                        return;
                                                    }
                                                }
                                                if key == "Enter" && !ev.modifiers().contains(Modifiers::SHIFT) {
                                                    ev.prevent_default();
                                                }
                                            },
                                            onkeyup: move |_| {
                                                let id = "skip-condition-input-create".to_string();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(0).await;
                                                    #[cfg(target_arch = "wasm32")]
                                                    if let Some(window) = web_sys::window() {
                                                        if let Some(doc) = window.document() {
                                                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                                                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                                    if let Ok(Some(sel)) = input.selection_start() {
                                                                        let sel_i = sel as usize;
                                                                        skip_condition_cursor_pos.set(Some(sel_i));
                                                                        let val = input.value();
                                                                        let inside = skip_condition_innermost_around_cursor(&val, sel_i)
                                                                            .map(|b| matches!(b, InnermostBracket::Square(_, _) | InnermostBracket::Curly(_, _)))
                                                                            .unwrap_or(false);
                                                                        if !inside {
                                                                            skip_bracket_ac_visible.set(false);
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                            onblur: move |_| {
                                                skip_docs_visible.set(false);
                                                skip_bracket_ac_visible.set(false);
                                                let expr = skip_condition();
                                                if expr.trim().is_empty() {
                                                    skip_condition_error.set(None);
                                                    return;
                                                }
                                                let url = tournament_url.clone();
                                                spawn(async move {
                                                    match api::validate_dsl(&url, &expr).await {
                                                        Ok(res) => {
                                                            skip_condition_error.set(if res.valid {
                                                                None
                                                            } else {
                                                                res.error.clone()
                                                            });
                                                            skip_condition_simplified.set(if res.valid {
                                                                res.simplified
                                                            } else {
                                                                None
                                                            });
                                                        }
                                                        Err(e) => {
                                                            skip_condition_error.set(Some(e));
                                                            skip_condition_simplified.set(None);
                                                        }
                                                    }
                                                });
                                            },
                                        }
                                        if show_skip_docs && !docs_filtered.is_empty() {
                                            div { class: "position-absolute start-0 mt-1 p-2 bg-light border rounded shadow-sm z-3",
                                                style: "min-width: 280px; max-height: 240px; overflow-y: auto;",
                                                ul { class: "list-unstyled mb-0 small",
                                                    for item in docs_items.iter() {
                                                        {item.clone()}
                                                    }
                                                }
                                            }
                                        }
                                        if show_bracket_ac && !bracket_option_items.is_empty() {
                                            div { class: "position-absolute start-0 mt-1 p-2 bg-light border rounded shadow-sm z-3",
                                                style: "min-width: 200px; max-height: 200px; overflow-y: auto;",
                                                ul { class: "list-unstyled mb-0 small",
                                                    for bracket_item in bracket_option_items.iter() {
                                                        {bracket_item.clone()}
                                                    }
                                                }
                                            }
                                        }
                                        if let Some(err) = skip_condition_error() {
                                            div { class: "form-text text-danger", "✗ {err}" }
                                        } else if let Some(simp) = skip_condition_simplified() {
                                            div { class: "form-text text-success", "✓ Valid (simplified: {simp})" }
                                        } else if !skip_condition().trim().is_empty() {
                                            div { class: "form-text text-success", "✓ Valid" }
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
                }
            }
            if skip_condition_help_open() {
                SkipConditionHelpModal { on_close: move |_| skip_condition_help_open.set(false) }
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
    let mut preview_modal_closed = use_signal(|| None::<std::sync::Arc<std::sync::atomic::AtomicBool>>);
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
                        match api::fetch_preview_frame(&u2, &field_name2, &camera, &cache_bust).await {
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
                        if let Ok(Some(meta)) = api::fetch_preview_metadata(&u2, &field_name2, &camera).await {
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
    tournament_url: String,
    on_edit_match: EventHandler<String>,
) -> Element {
    let tz_offset = schedule_tz_offset_minutes();
    // ... existing filter logic ...
    let matches: Vec<&MatchSetupData> = data.matches.iter().filter(|m| {
        if m.status == "SKIPPED" { return false; }
        if selected_field != "all" {
             if let Some(f_name) = &m.field {
                 let field_id = data.fields.iter().find(|f| &f.name == f_name).map(|f| f.id.to_string());
                 if field_id.as_deref() != Some(selected_field.as_str()) { return false; }
             } else {
                 return false;
             }
        }
        true
    }).collect();

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
                        if edit_mode { th { "Edit" } }
                    }
                }
                tbody {
                    {matches.iter().map(|m| {
                        let match_id = m.uuid.clone();
                        // Team 1 column: only m.team1 / m.team1_initial (first token if comma-separated)
                        let opt1 = m.team1.as_ref().and_then(|id| data.team_options.iter().find(|o| &o.id == id));
                        let t1_raw = opt1.and_then(|o| o.pseudonym.as_deref()).map(String::from)
                            .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string());
                        let photo1 = opt1.and_then(|o| o.profile_photo.clone());
                        // Team 2 column: only m.team2 / m.team2_initial (first token if comma-separated)
                        let opt2 = m.team2.as_ref().and_then(|id| data.team_options.iter().find(|o| &o.id == id));
                        let t2_raw = opt2.and_then(|o| o.pseudonym.as_deref()).map(String::from)
                            .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string());
                        let photo2 = opt2.and_then(|o| o.profile_photo.clone());
                        // Refs column: only m.refs / m.refs_initial (comma-separated list)
                        let refs_list: Vec<(String, Option<String>)> = m.refs.as_deref().or(m.refs_initial.as_deref()).unwrap_or("")
                            .split(',')
                            .map(|s| s.trim())
                            .filter(|s| !s.is_empty())
                            .map(|token| {
                                let opt = data.team_options.iter().find(|o| o.id == token);
                                let display = opt.and_then(|o| o.pseudonym.as_deref()).map(String::from).unwrap_or_else(|| token.to_string());
                                let photo = opt.and_then(|o| o.profile_photo.clone());
                                (display, photo)
                            })
                            .collect();
                        // Same rules as ScheduleTimeline (before t1_raw/t2_raw are moved into display strings)
                        let (highlight_playing, highlight_ref) = if highlight_team.is_empty() {
                            (false, false)
                        } else {
                            let ht = highlight_team.to_lowercase();
                            let playing = t1_raw.to_lowercase().contains(&ht)
                                || t2_raw.to_lowercase().contains(&ht);
                            let refs_joined = refs_list
                                .iter()
                                .map(|(d, _)| d.as_str())
                                .collect::<Vec<_>>()
                                .join(", ");
                            let reffing = !playing && refs_joined.to_lowercase().contains(&ht);
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
                        let t1 = if t1_raw.contains(',') { t1_raw.split(',').next().map(|s| s.trim().to_string()).unwrap_or_default() } else { t1_raw };
                        let t2 = if t2_raw.contains(',') { t2_raw.split(',').next().map(|s| s.trim().to_string()).unwrap_or_default() } else { t2_raw };
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
                        let (status_color, status_label) = if m.status.is_empty() { ("#e9ecef".to_string(), "-".to_string()) } else { status_color_and_label(&m.status) };
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
                                    if let Some(t) = m.confirmed_start_time.as_ref().or(m.nominal_start_time.as_ref()) {
                                        "{format_time_local(t, tz_offset)}"
                                    } else { "-" }
                                }
                                td { "{schedule_type_display}" }
                                td { class: "align-middle",
                                    span {
                                        class: "schedule-timeline-status-tag",
                                        style: "background-color: {status_color};",
                                        "{status_label}"
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
                                if edit_mode {
                                    td {
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
#[derive(Clone, Debug)]
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
    highlight_playing: bool, // team is team1 or team2
    highlight_ref: bool,     // team is one of refs (matched by pseudonym)
    ribbon: bool,
}

#[derive(Clone, Debug)]
struct JoinGroup {
    name: String,
    time: chrono::NaiveDateTime,
    // For each JOIN match: (field_id, match_uuid)
    field_matches: Vec<(u32, String)>,
}


#[component]
fn ScheduleTimeline(
    data: ScheduleSetupResponse,
    selected_field: String,
    highlight_team: String,
    edit_mode: bool,
    tournament_url: String,
    on_edit_match: EventHandler<String>,
    key_nav: Signal<Option<String>>,
    on_key_nav_consumed: EventHandler<()>,
) -> Element {
    use chrono::Timelike;
    use chrono::NaiveDateTime;
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

    // All match dates in local time (unique, sorted) for prev/next navigation
    let dates_with_matches: Vec<chrono::NaiveDate> = {
        let mut dates: Vec<chrono::NaiveDate> = data.matches.iter()
            .filter(|m| m.status != "SKIPPED")
            .filter_map(|m| effective_start_str(m))
            .filter_map(|s| parse_schedule_time_to_local(s, tz_offset_minutes))
            .map(|dt| dt.date())
            .collect();
        dates.sort();
        dates.dedup();
        dates
    };

    // Today in local time
    let today_local = (chrono::Utc::now() + chrono::Duration::minutes(tz_offset_minutes)).date_naive();

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
                    if let Some(idx) = dates.iter().position(|&d| d == current).and_then(|i| i.checked_sub(1)) {
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

    // Filter visible fields
    let visible_fields: Vec<&FieldSetupData> = if selected_field == "all" {
        data.fields.iter().collect()
    } else {
        data.fields.iter()
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

    // Build timeline events (non-join matches)
    // Use confirmed_start_time/completed_time when set; else nominal_start_time and start + nominal_length
    let mut timeline_events: Vec<TimelineEvent> = data.matches.iter()
        .filter(|m| m.status != "SKIPPED")
        .filter(|m| m.schedule_type.as_deref() != Some("JOIN"))
        .filter_map(|m| {
            let start_str = effective_start_str(m)?;
            let start_dt = parse_schedule_time_to_local(start_str, tz_offset_minutes)?;
            let (end_dt, length_min) = if let Some(end_str) = m.completed_time.as_ref() {
                let end_dt = parse_schedule_time_to_local(end_str, tz_offset_minutes)?;
                let len = (end_dt - start_dt).num_minutes().max(0);
                (end_dt, len)
            } else {
                let length_min = m.nominal_length.unwrap_or(30) as i64;
                (start_dt + chrono::Duration::minutes(length_min), length_min)
            };
            let field_name = m.field.as_ref()?;
            let field = data.fields.iter().find(|f| &f.name == field_name)?;
            
            // Check if field is visible
            if selected_field != "all" && field.id.to_string() != selected_field {
                return None;
            }
            
            // Don't filter by date here - we'll filter when rendering based on current_visible_date
            // This allows date navigation to work properly
            
            // Display pseudonyms (from registration): prefer team_options pseudonym when team ID is set
            let t1 = m.team1.as_ref()
                .and_then(|id| data.team_options.iter().find(|o| &o.id == id))
                .and_then(|o| o.pseudonym.as_deref())
                .map(String::from)
                .unwrap_or_else(|| m.team1_initial.as_deref().unwrap_or("").to_string());
            let t2 = m.team2.as_ref()
                .and_then(|id| data.team_options.iter().find(|o| &o.id == id))
                .and_then(|o| o.pseudonym.as_deref())
                .map(String::from)
                .unwrap_or_else(|| m.team2_initial.as_deref().unwrap_or("").to_string());
            
            // Team profile photos
            let team1_photo = m.team1.as_ref()
                .and_then(|id| data.team_options.iter().find(|o| &o.id == id))
                .and_then(|o| o.profile_photo.clone());
            let team2_photo = m.team2.as_ref()
                .and_then(|id| data.team_options.iter().find(|o| &o.id == id))
                .and_then(|o| o.profile_photo.clone());
            // Refs as list of (display_name, profile_photo)
            let refs_list: Vec<(String, Option<String>)> = m.refs.as_deref().or(m.refs_initial.as_deref()).unwrap_or("")
                .split(',')
                .map(|s| s.trim())
                .filter(|s| !s.is_empty())
                .map(|token| {
                    let opt = data.team_options.iter().find(|o| o.id == token);
                    let display = opt.and_then(|o| o.pseudonym.as_deref()).map(String::from).unwrap_or_else(|| token.to_string());
                    let photo = opt.and_then(|o| o.profile_photo.clone());
                    (display, photo)
                })
                .collect();
            let refs_display = refs_list.iter().map(|(d, _)| d.as_str()).collect::<Vec<_>>().join(", ");
            
            // Status tag palette only (never overwritten for highlight; highlight is on the block)
            let (color, _) = status_color_and_label(&m.status);
            
            // Highlight: match against pseudonyms only (t1/t2/refs_display are already pseudonyms)
            let (highlight_playing, highlight_ref) = if highlight_team.is_empty() {
                (false, false)
            } else {
                let ht = highlight_team.to_lowercase();
                let playing = t1.to_lowercase().contains(&ht) || t2.to_lowercase().contains(&ht);
                let reffing = !playing && refs_display.to_lowercase().contains(&ht);
                (playing, reffing)
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
                field_id: field.id,
                field_name: field.name.clone(),
                color: color.to_string(),
                status: m.status.clone(),
                schedule_type: m.schedule_type.clone(),
                lane_index: 0, // Will be computed below
                num_lanes: 1,  // Will be computed below
                highlight_playing,
                highlight_ref,
                ribbon: m.ribbon,
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
        let field_event_indices: Vec<usize> = timeline_events.iter()
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
            let occupied_lanes: std::collections::HashSet<usize> = sorted_indices[..k].iter()
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
            let max_lane = field_event_indices.iter()
                .filter(|&&i| events_overlap(event, &timeline_events[i]))
                .map(|&i| timeline_events[i].lane_index)
                .max()
                .unwrap_or(0);
            timeline_events[idx].num_lanes = (max_lane + 1).max(1);
        }
    }

    // Build join groups
    let join_groups: Vec<JoinGroup> = {
        use std::collections::HashMap;
        let mut groups: HashMap<String, Vec<&MatchSetupData>> = HashMap::new();
        
        for m in &data.matches {
            if m.status == "SKIPPED" {
                continue;
            }
            if m.schedule_type.as_deref() == Some("JOIN") {
                groups.entry(m.name.clone()).or_insert_with(Vec::new).push(m);
            }
        }
        
        groups.into_iter().filter_map(|(name, matches)| {
            if matches.is_empty() {
                return None;
            }
            
            // Get time from first match (effective start in local time)
            let time_str = effective_start_str(matches[0])?;
            let time_dt = parse_schedule_time_to_local(time_str, tz_offset_minutes)?;
            
            // Build per-field join matches (field_id -> match_uuid)
            let field_matches: Vec<(u32, String)> = matches
                .iter()
                .filter_map(|m| {
                    let field_name = m.field.as_ref()?;
                    let field_id = data.fields.iter().find(|f| &f.name == field_name).map(|f| f.id)?;
                    Some((field_id, m.uuid.clone()))
                })
                .filter(|(field_id, _)| selected_field == "all" || field_id.to_string() == selected_field)
                .collect();

            if field_matches.is_empty() {
                return None;
            }
            
            Some(JoinGroup {
                name: name.clone(),
                time: time_dt,
                field_matches,
            })
        }).collect()
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

    let join_lines_data: Vec<JoinLineData> = join_groups.iter()
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
        let event_slots = timeline_events.iter()
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
    use_effect(move || {
        let _ = visible_date_signal(); // re-run effect when date changes
        let slot = target_slot;
        #[cfg(target_arch = "wasm32")]
        {
            let id = format!("schedule-timeline-slot-{}", slot);
            wasm_bindgen_futures::spawn_local(async move {
                gloo_timers::future::TimeoutFuture::new(100).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let (Some(scroll_el), Some(target_el)) = (
                            doc.get_element_by_id("schedule-timeline-scroll"),
                            doc.get_element_by_id(&id),
                        ) {
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
            let _ = slot;
        }
    });

    const TIME_COL_WIDTH_PX: u32 = 80;
    let base_url = api::base_url();
    
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
                    }
                }
            }
            div { class: "schedule-timeline-scroll", id: "schedule-timeline-scroll",
                div {
                    class: "schedule-timeline",
                    // Important: this is the positioning container for join overlays.
                    style: "position: relative; --num-fields: {visible_fields.len()}; --time-col-width: {TIME_COL_WIDTH_PX}px;",
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
                                        // Render events that start in this slot
                                        let events_in_slot: Vec<&TimelineEvent> = timeline_events.iter()
                                            .filter(|e| {
                                                if e.field_id != field.id {
                                                    return false;
                                                }
                                                let date = e.start_time.date();
                                                if date != current_visible_date {
                                                    return false;
                                                }
                                                let hour = e.start_time.hour();
                                                let minute = e.start_time.minute();
                                                if hour < FIRST_HOUR || hour >= LAST_HOUR {
                                                    return false;
                                                }
                                                let total_minutes = (hour - FIRST_HOUR) * 60 + minute;
                                                let event_slot = (total_minutes as i64 / SLOT_MINUTES) as usize;
                                                event_slot == slot
                                            })
                                            .collect();
                                        
                                        // Pre-compute event rendering data: exact-to-the-minute top and height (fraction of slot)
                                        let event_render_data_opt = if !events_in_slot.is_empty() {
                                            let max_lanes = events_in_slot.first().map(|e| e.num_lanes).unwrap_or(1);
                                            Some(events_in_slot.iter().map(|event| {
                                                let start_min = (event.start_time.hour() - FIRST_HOUR) * 60 + event.start_time.minute();
                                                let minutes_within_slot = (start_min as i64) % SLOT_MINUTES;
                                                let top_fraction = (minutes_within_slot as f64) / (SLOT_MINUTES as f64);
                                                let duration_min = (event.end_time - event.start_time).num_minutes().max(1);
                                                let duration_slots_fraction = (duration_min as f64) / (SLOT_MINUTES as f64);
                                                let width_pct = 100.0 / max_lanes as f64;
                                                let left_pct = (event.lane_index as f64) * width_pct;
                                                (event.id.clone(), width_pct, left_pct, top_fraction, duration_slots_fraction)
                                            }).collect::<Vec<_>>())
                                        } else {
                                            None
                                        };
                                        
                                        // Join at this (slot, col_idx): horizontal line in cell; label in edit mode (positioned to-the-minute)
                                        let join_in_cell = join_lines_data.iter().find_map(|jl| {
                                            if jl.slot != slot { return None; }
                                            jl.field_items.iter()
                                                .find(|(c, _)| *c == col_idx)
                                                .map(|(_, mid)| (jl.join.name.clone(), mid.clone(), jl.top_fraction))
                                        });
                                        
                                        rsx! {
                                            if let Some(event_render_data) = event_render_data_opt {
                                                div {
                                                    class: "schedule-timeline-event-container",
                                                    for (idx, event) in events_in_slot.iter().enumerate() {
                                                        {
                                                            let (event_id, width_pct, left_pct, top_fraction, duration_slots_fraction) = &event_render_data[idx];
                                                            let event_id_clone = event_id.clone();
                                                            let (_, status_label) = status_color_and_label(&event.status);

                                                            let is_break = event.schedule_type.as_deref() == Some("BREAK");
                                                            let event_style = format!("background-color: #ffffff; width: {}%; left: {}%; top: calc(var(--slot-height) * {}); height: calc(var(--slot-height) * {}); position: absolute;", width_pct, left_pct, top_fraction, duration_slots_fraction);
                                                            let event_title = if is_break { event.name.clone() } else { format!("{} - {} vs {}", event.name, event.team1, event.team2) };
                                                            let url_clone = tournament_url.clone();
                                                            let nav = navigator.clone();
                                                            let event_class = format!(
                                                                "schedule-timeline-event{}{}",
                                                                if event.highlight_playing { " schedule-timeline-event--highlight-playing" } else { "" },
                                                                if event.highlight_ref { " schedule-timeline-event--highlight-ref" } else { "" }
                                                            );
                                                            let (t1_kind, t1_label) = team_ref_display(&event.team1);
                                                            let (t2_kind, t2_label) = team_ref_display(&event.team2);
                                                            let event_refs: Vec<(String, Option<String>, u8, String)> = event.refs_list
                                                                .iter()
                                                                .map(|(d, p)| {
                                                                    let (k, l) = team_ref_display(d);
                                                                    (d.clone(), p.clone(), k, l)
                                                                })
                                                                .collect();
                                                            rsx! {
                                                                div {
                                                                    class: "{event_class}",
                                                                    style: "{event_style}",
                                                                    title: "{event_title}",
                                                                    cursor: if is_break && !edit_mode { "default" } else { "pointer" },
                                                                    onclick: move |_| {
                                                                        if is_break && !edit_mode {
                                                                            // Break matches don't link anywhere
                                                                        } else if edit_mode {
                                                                            on_edit_match.call(event_id_clone.clone());
                                                                        } else {
                                                                            nav.push(Route::MatchPageById { url: url_clone.clone(), match_id: event_id_clone.clone() });
                                                                        }
                                                                    },
                                                                    span {
                                                                        class: "schedule-timeline-status-tag schedule-timeline-status-tag--corner",
                                                                        style: "background-color: {event.color};",
                                                                        "{status_label}"
                                                                    }
                                                                    div { class: "schedule-timeline-event-header",
                                                                        div { class: "schedule-timeline-event-name", "{event.name}" }
                                                                    }
                                                                    if !is_break {
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
                                                }
                                            }
                                            }
                                            else {
                                                div {}
                                            }
                                            if let Some((join_name, match_id, join_top_fraction)) = join_in_cell {
                                                div {
                                                    class: "schedule-timeline-join-in-cell",
                                                    style: format!("top: calc(var(--slot-height) * {});", join_top_fraction),
                                                    div { class: "schedule-timeline-join-line-in-cell" }
                                                    if edit_mode {
                                                        div {
                                                            class: "schedule-timeline-join-label",
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
                }
            }
        }
    }
}

#[component]
fn EditMatchModal(
    tournament_url: String, 
    match_id: String, 
    data: ScheduleSetupResponse,
    on_close: EventHandler<()>, 
    on_save: EventHandler<()>
) -> Element {
    let match_data = data.matches.iter().find(|m| m.uuid == match_id).cloned();
    
    if match_data.is_none() {
        return rsx! { div { "Match not found" } };
    }
    
    let m = match_data.unwrap();
    
    // Original schedule type for edit: only allow transitions STATIC→SAFE/FAST, SAFE→FAST
    let original_schedule_type = m.schedule_type.as_deref().unwrap_or("STATIC");
    
    let name = use_signal(|| m.name.clone());
    let mut field = use_signal(|| m.field.clone().unwrap_or_default());
    let schedule_type = use_signal(|| m.schedule_type.clone().unwrap_or("STATIC".to_string()));
    let mut length = use_signal(|| m.nominal_length.unwrap_or(60));
    
    let start_time_init = if let Some(t) = &m.nominal_start_time {
        utc_iso_to_local_datetime_input(t).unwrap_or_else(|| t.chars().take(16).collect::<String>())
    } else {
        "".to_string()
    };
    
    let start_time = use_signal(|| start_time_init);
    let mut previous_match_id = use_signal(|| m.previous_match.clone().unwrap_or_default());
    let mut refs = use_signal(|| m.refs_initial.clone().or(m.refs.clone()).unwrap_or_default());
    let mut team1 = use_signal(|| m.team1_initial.clone().or(m.team1.clone()).unwrap_or_default());
    let mut team2 = use_signal(|| m.team2_initial.clone().or(m.team2.clone()).unwrap_or_default());
    let mut set_type = use_signal(|| m.set_type.clone().unwrap_or("SETS".to_string()));
    let mut nsets = use_signal(|| m.nsets.unwrap_or(3));
    let mut stones_per_set = use_signal(|| m.stones_per_set.unwrap_or(100));
    let ribbon = use_signal(|| m.ribbon);
    let mut skip_condition = use_signal(|| m.skip_condition.clone().unwrap_or_default());
    let mut skip_condition_error = use_signal(|| None::<String>);
    let mut skip_condition_simplified = use_signal(|| None::<String>);
    let mut skip_condition_cursor = use_signal(|| None::<usize>);
    let mut skip_condition_cursor_pos = use_signal(|| None::<usize>);
    let mut skip_docs_visible = use_signal(|| false);
    let mut skip_bracket_ac_visible = use_signal(|| false);
    let mut skip_bracket_ac_team = use_signal(|| true);
    let mut skip_bracket_ac_index = use_signal(|| 0usize);
    let mut skip_condition_help_open = use_signal(|| false);

    #[cfg(target_arch = "wasm32")]
    use_effect(move || {
        let pos = skip_condition_cursor();
        if let Some(p) = pos {
            skip_condition_cursor.set(None);
            let id = "skip-condition-input-edit".to_string();
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                            if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                let _ = input.set_selection_range(p as u32, p as u32);
                                let _ = input.focus();
                            }
                        }
                    }
                }
            });
        }
    });

    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);
    let url_sig = use_signal(|| tournament_url.clone());

    // Sync field and previous_match_id from match data when modal opens (fixes initial display)
    let match_id_effect = match_id.clone();
    let data_effect = data.clone();
    use_effect(move || {
        if let Some(m) = data_effect.matches.iter().find(|x| x.uuid == match_id_effect) {
            field.set(m.field.clone().unwrap_or_default());
            previous_match_id.set(m.previous_match.clone().unwrap_or_default());
        }
    });

    let matches_on_field_edit = matches_on_field_sorted(&data.matches, &field(), Some(&match_id));

    // When field changes, clear previous match so user must pick one on the new field
    let data_field_edit = data.clone();
    let match_id_for_field = match_id.clone();
    let mut on_field_change_edit = move |new_field: String| {
        field.set(new_field.clone());
        previous_match_id.set("".to_string());
        if !new_field.is_empty() {
            let list = matches_on_field_sorted(&data_field_edit.matches, &new_field, Some(&match_id_for_field));
            if let Some(prev) = list.first() {
                length.set(prev.nominal_length.unwrap_or(60));
                set_type.set(prev.set_type.clone().unwrap_or_else(|| "SETS".to_string()));
                nsets.set(prev.nsets.unwrap_or(3));
                stones_per_set.set(prev.stones_per_set.unwrap_or(100));
            }
        }
    };

    let u_save = tournament_url.clone();
    let m_id_save = match_id.clone();
    let data_save = data.clone();
    let do_save_rc: Rc<RefCell<Box<dyn FnMut()>>> = Rc::new(RefCell::new(Box::new(move || {
        // Validation: BREAK, JOIN, FAST, SAFE require previous match and same field
        let st = schedule_type();
        if st == "BREAK" || st == "JOIN" || st == "FAST" || st == "SAFE" {
            let prev_id = previous_match_id().trim().to_string();
            if prev_id.is_empty() {
                error.set(Some("Previous match is required for Break, Join, Fast, and Safe matches.".to_string()));
                return;
            }
            let current_field = field();
            if let Some(prev_m) = data_save.matches.iter().find(|x| x.uuid == prev_id) {
                if prev_m.field.as_deref() != Some(current_field.as_str()) {
                    error.set(Some("Previous match must be on the same field.".to_string()));
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
            if (schedule_type() == "SAFE" || schedule_type() == "FAST") && !skip_condition().trim().is_empty() {
                match api::validate_dsl(&tournament_url, &skip_condition()).await {
                    Ok(res) => {
                        if !res.valid {
                            skip_condition_error.set(res.error);
                            skip_condition_simplified.set(None);
                            saving.set(false);
                            return;
                        }
                        skip_condition_simplified.set(res.simplified);
                    }
                    Err(e) => {
                        skip_condition_error.set(Some(e));
                        skip_condition_simplified.set(None);
                        saving.set(false);
                        return;
                    }
                }
            }
            let refs_vec: Vec<String> = refs().split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
            let len = if schedule_type() == "JOIN" {
                Some(0u32)
            } else {
                Some(length())
            };
            let req = UpdateMatchRequest {
                name: Some(name()),
                field: Some(field()),
                field_id: None,
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
                Ok(_) => { saving.set(false); on_save.call(()); }
                Err(e) => { error.set(Some(e)); saving.set(false); }
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

    let sc_val_edit = skip_condition();
    let cursor_char_edit = skip_condition_cursor_pos();
    let innermost_edit = cursor_char_edit.and_then(|c| skip_condition_innermost_around_cursor(&sc_val_edit, c));
    let cursor_byte_edit = cursor_char_edit.map(|c| skip_condition_cursor_byte(&sc_val_edit, c)).unwrap_or(0);
    let show_skip_docs_edit = matches!(innermost_edit, Some(InnermostBracket::Paren(_, _)));
    let docs_prefix_edit = match &innermost_edit {
        Some(InnermostBracket::Paren(cs, ce)) => {
            sc_val_edit[*cs..*ce].trim().split_whitespace().next().unwrap_or("").to_lowercase()
        }
        _ => String::new(),
    };
    let docs_filtered_edit: Vec<_> = if show_skip_docs_edit {
        DSL_FUNCTIONS
            .iter()
            .filter(|(n, _)| n.to_lowercase().starts_with(docs_prefix_edit.as_str()))
            .take(12)
            .collect()
    } else {
        vec![]
    };
    let (bracket_is_team_edit, bracket_query_edit) = match &innermost_edit {
        Some(InnermostBracket::Square(cs, ce)) => {
            let end = (*ce).min(cursor_byte_edit).max(*cs);
            (true, sc_val_edit[*cs..end].trim().to_lowercase())
        }
        Some(InnermostBracket::Curly(cs, ce)) => {
            let end = (*ce).min(cursor_byte_edit).max(*cs);
            (false, sc_val_edit[*cs..end].trim().to_lowercase())
        }
        _ => (true, String::new()),
    };
    let show_bracket_ac_edit = matches!(innermost_edit, Some(InnermostBracket::Square(_, _)) | Some(InnermostBracket::Curly(_, _)));
    let bracket_ac_idx_edit_raw = skip_bracket_ac_index();
    let bracket_options_edit: Vec<(String, String, Option<String>)> = if show_bracket_ac_edit && bracket_is_team_edit {
        let team_opts_edit: Vec<_> = data
            .team_options
            .iter()
            .filter(|t| {
                let disp = t.pseudonym.as_deref().unwrap_or(t.id.as_str());
                disp.to_lowercase().contains(bracket_query_edit.as_str())
                    || t.id.to_lowercase().contains(bracket_query_edit.as_str())
            })
            .map(|t| {
                (
                    t.id.clone(),
                    t.pseudonym.clone().unwrap_or_else(|| t.id.clone()),
                    t.profile_photo.clone(),
                )
            })
            .take(15)
            .collect();
        let match_qual_opts_edit: Vec<_> = data
            .matches
            .iter()
            .flat_map(|m| {
                let w = (format!("{}::winner", m.name), format!("{}::winner", m.name), None);
                let l = (format!("{}::loser", m.name), format!("{}::loser", m.name), None);
                [w, l]
            })
            .filter(|(s, _, _)| s.to_lowercase().contains(bracket_query_edit.as_str()))
            .take(15)
            .collect();
        let tag_opts_edit: Vec<_> = data
            .tags
            .iter()
            .filter(|t| t.name.to_lowercase().contains(bracket_query_edit.as_str()))
            .map(|t| (format!("tag::{}", t.name), t.name.clone(), None))
            .take(15)
            .collect();
        team_opts_edit
            .into_iter()
            .chain(match_qual_opts_edit)
            .chain(tag_opts_edit)
            .take(20)
            .collect()
    } else if show_bracket_ac_edit {
        data.matches
            .iter()
            .filter(|m| m.name.to_lowercase().contains(bracket_query_edit.as_str()))
            .map(|m| (m.name.clone(), m.name.clone(), None))
            .take(15)
            .collect()
    } else {
        vec![]
    };
    let bracket_ac_idx_edit = bracket_ac_idx_edit_raw.min(bracket_options_edit.len().saturating_sub(1));
    let docs_items_edit: Vec<_> = if show_skip_docs_edit && !docs_filtered_edit.is_empty() {
        docs_filtered_edit
            .iter()
            .map(|(dn, ds)| {
                let fname = dn.to_string();
                rsx! {
                    li {
                        class: "py-1 px-2 rounded",
                        onclick: move |_| {
                            let v = skip_condition();
                            if let Some(i) = v.rfind('(') {
                                skip_condition.set(format!("{}{}", &v[..=i], fname));
                            }
                            skip_docs_visible.set(false);
                        },
                        span { class: "fw-medium text-primary", "{dn}" }
                        span { class: "text-muted ms-1", " {ds}" }
                    }
                }
            })
            .collect()
    } else {
        vec![]
    };
    let base_url_edit = api::base_url();
    let bracket_option_items_edit: Vec<_> = if show_bracket_ac_edit && !bracket_options_edit.is_empty() {
        bracket_options_edit
            .iter()
            .enumerate()
            .map(|(idx, (insert_val, display_val, photo))| {
                let opt_insert = insert_val.clone();
                let opt_display = display_val.clone();
                let opt_photo = photo.clone();
                let is_team = bracket_is_team_edit;
                let is_cur = bracket_ac_idx_edit == idx;
                let li_class = if is_cur {
                    "py-1 px-2 rounded bg-primary text-white"
                } else {
                    "py-1 px-2 rounded"
                };
                let avatar_node_edit = if is_team {
                    if opt_insert.ends_with("::winner") || opt_insert.ends_with("::loser") {
                        rsx! {
                            img { class: "icon-primary-svg me-1", src: "{base_url_edit}/static/reference.svg", alt: "Reference", style: "width: 1.25em; height: 1.25em;" }
                        }
                    } else if opt_insert.starts_with("tag::") {
                        rsx! {
                            img { class: "icon-primary-svg me-1", src: "{base_url_edit}/static/tag.svg", alt: "Tag", style: "width: 1.25em; height: 1.25em;" }
                        }
                    } else if let Some(photo) = &opt_photo {
                        rsx! {
                            img {
                                src: "{base_url_edit}/static/{photo}",
                                alt: "{opt_display}",
                                class: "team-token-avatar small me-1 rounded-circle",
                                style: "width: 1.5em; height: 1.5em; object-fit: cover;"
                            }
                        }
                    } else {
                        rsx! {
                            span { class: "team-token-avatar small me-1", "{opt_display.chars().next().unwrap_or('?')}" }
                        }
                    }
                } else {
                    rsx! { }
                };
                rsx! {
                    li {
                        class: "{li_class}",
                        onclick: move |_| {
                            let v = skip_condition();
                            let cursor_char = skip_condition_cursor_pos().unwrap_or(0);
                            let cursor_byte = skip_condition_cursor_byte(&v, cursor_char);
                            let inn = skip_condition_innermost_around_cursor(&v, cursor_char);
                            let Some(cs) = inn.and_then(|b| match b {
                                InnermostBracket::Square(cs, _) | InnermostBracket::Curly(cs, _) => Some(cs),
                                _ => None,
                            }) else {
                                skip_bracket_ac_visible.set(false);
                                return;
                            };
                            let new_v = format!("{}{}{}", &v[..cs], opt_insert, &v[cursor_byte..]);
                            skip_condition.set(new_v.clone());
                            let cs_chars = v[..cs].chars().count();
                            let new_cursor_char = cs_chars + opt_insert.chars().count();
                            skip_condition_cursor.set(Some(new_cursor_char));
                            skip_bracket_ac_visible.set(false);
                        },
                        {avatar_node_edit}
                        span { "{display_val}" }
                    }
                }
            })
            .collect()
    } else {
        vec![]
    };

    let tournament_url_val_edit = tournament_url.clone();
    use_effect(move || {
        let expr = skip_condition();
        let _ = expr.clone();
        if expr.trim().is_empty() {
            return;
        }
        let expr_captured = expr.clone();
        let url = tournament_url_val_edit.clone();
        #[cfg(target_arch = "wasm32")]
        spawn(async move {
            gloo_timers::future::TimeoutFuture::new(3000).await;
            let current = skip_condition();
            if current == expr_captured {
                match api::validate_dsl(&url, &expr_captured).await {
                    Ok(res) => {
                        skip_condition_error.set(if res.valid { None } else { res.error.clone() });
                        skip_condition_simplified.set(if res.valid { res.simplified } else { None });
                    }
                    Err(e) => {
                        skip_condition_error.set(Some(e));
                        skip_condition_simplified.set(None);
                    }
                }
            }
        });
        #[cfg(not(target_arch = "wasm32"))]
        let _ = (expr_captured, url);
    });

    rsx! {
        div {
            div {
                class: "modal d-block",
                tabindex: -1,
                style: "background: rgba(0,0,0,0.5)",
                onkeydown: modal_keydown,
                div { class: "modal-dialog modal-lg",
                    div { class: "modal-content",
                        div { class: "modal-header",
                            h5 { class: "modal-title", "Edit Match: {name}" }
                        }
                        div { class: "modal-body",
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
                                        input { class: "form-control", "type": "text", value: "{name}", oninput: move |e| { let mut name = name; name.set(e.value()); }, required: true }
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
                                    div { class: "col-md-6",
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
                                    div { class: "mb-3 position-relative",
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
                                        input {
                                            id: "skip-condition-input-edit",
                                            class: "form-control font-monospace",
                                            "type": "text",
                                            placeholder: "e.g. (== 0 (losses [Team]))",
                                            value: "{skip_condition}",
                                            oninput: move |e| {
                                                let new_val = e.value();
                                                let old = skip_condition();
                                                let (out, cursor_after_open) = if let Some(byte_i) = skip_condition_new_char_index(&old, &new_val) {
                                                    let open_c = new_val[byte_i..].chars().next().unwrap_or('\0');
                                                    let closing = match open_c {
                                                        '(' => ")",
                                                        '[' => {
                                                            skip_bracket_ac_visible.set(true);
                                                            skip_bracket_ac_team.set(true);
                                                            skip_bracket_ac_index.set(0);
                                                            "]"
                                                        }
                                                        '{' => {
                                                            skip_bracket_ac_visible.set(true);
                                                            skip_bracket_ac_team.set(false);
                                                            skip_bracket_ac_index.set(0);
                                                            "}"
                                                        }
                                                        _ => "",
                                                    };
                                                    if closing.is_empty() {
                                                        (new_val, None)
                                                    } else {
                                                        let char_end = byte_i + new_val[byte_i..].chars().next().map(|c| c.len_utf8()).unwrap_or(1);
                                                        let out_str = format!("{}{}{}", &new_val[..char_end], closing, &new_val[char_end..]);
                                                        (out_str, Some(char_end))
                                                    }
                                                } else {
                                                    (new_val, None)
                                                };
                                                skip_condition.set(out.clone());
                                                if let Some(pos) = cursor_after_open {
                                                    skip_condition_cursor.set(Some(pos));
                                                }
                                                skip_condition_error.set(None);
                                                if out.contains('(') {
                                                    skip_docs_visible.set(true);
                                                }
                                                let id = "skip-condition-input-edit".to_string();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(0).await;
                                                    #[cfg(target_arch = "wasm32")]
                                                    if let Some(window) = web_sys::window() {
                                                        if let Some(doc) = window.document() {
                                                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                                                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                                    if let Ok(Some(sel)) = input.selection_start() {
                                                                        skip_condition_cursor_pos.set(Some(sel as usize));
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                            onfocus: move |_| {
                                                let id = "skip-condition-input-edit".to_string();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(0).await;
                                                    #[cfg(target_arch = "wasm32")]
                                                    if let Some(window) = web_sys::window() {
                                                        if let Some(doc) = window.document() {
                                                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                                                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                                    if let Ok(Some(sel)) = input.selection_start() {
                                                                        skip_condition_cursor_pos.set(Some(sel as usize));
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                            onkeydown: move |ev: Event<KeyboardData>| {
                                                let key = ev.key().to_string();
                                                let n_edit = bracket_options_edit.len();
                                                if show_bracket_ac_edit && n_edit > 0 {
                                                    if key == "ArrowDown" {
                                                        ev.prevent_default();
                                                        skip_bracket_ac_index.set((bracket_ac_idx_edit + 1) % n_edit);
                                                        return;
                                                    }
                                                    if key == "ArrowUp" {
                                                        ev.prevent_default();
                                                        skip_bracket_ac_index.set((bracket_ac_idx_edit + n_edit - 1) % n_edit);
                                                        return;
                                                    }
                                                    if key == "Enter" {
                                                        ev.prevent_default();
                                                        if let Some((opt_insert, _, _)) = bracket_options_edit.get(bracket_ac_idx_edit) {
                                                            let v = skip_condition();
                                                            let cursor_char = skip_condition_cursor_pos().unwrap_or(0);
                                                            let cursor_byte = skip_condition_cursor_byte(&v, cursor_char);
                                                            let inn = skip_condition_innermost_around_cursor(&v, cursor_char);
                                                            if let Some(cs) = inn.and_then(|b| match b {
                                                                InnermostBracket::Square(cs, _) | InnermostBracket::Curly(cs, _) => Some(cs),
                                                                _ => None,
                                                            }) {
                                                                let new_v = format!("{}{}{}", &v[..cs], opt_insert, &v[cursor_byte..]);
                                                                skip_condition.set(new_v);
                                                                let cs_chars = v[..cs].chars().count();
                                                                let new_cursor_char = cs_chars + opt_insert.chars().count();
                                                                skip_condition_cursor.set(Some(new_cursor_char));
                                                                skip_bracket_ac_visible.set(false);
                                                            }
                                                        }
                                                        return;
                                                    }
                                                }
                                                if key == "Enter" && !ev.modifiers().contains(Modifiers::SHIFT) {
                                                    ev.prevent_default();
                                                }
                                            },
                                            onkeyup: move |_| {
                                                let id = "skip-condition-input-edit".to_string();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(0).await;
                                                    #[cfg(target_arch = "wasm32")]
                                                    if let Some(window) = web_sys::window() {
                                                        if let Some(doc) = window.document() {
                                                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                                                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                                    if let Ok(Some(sel)) = input.selection_start() {
                                                                        let sel_i = sel as usize;
                                                                        skip_condition_cursor_pos.set(Some(sel_i));
                                                                        let val = input.value();
                                                                        let inside = skip_condition_innermost_around_cursor(&val, sel_i)
                                                                            .map(|b| matches!(b, InnermostBracket::Square(_, _) | InnermostBracket::Curly(_, _)))
                                                                            .unwrap_or(false);
                                                                        if !inside {
                                                                            skip_bracket_ac_visible.set(false);
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                            onblur: move |_| {
                                                skip_docs_visible.set(false);
                                                skip_bracket_ac_visible.set(false);
                                                let expr = skip_condition();
                                                if expr.trim().is_empty() {
                                                    skip_condition_error.set(None);
                                                    return;
                                                }
                                                let url = tournament_url.clone();
                                                spawn(async move {
                                                    match api::validate_dsl(&url, &expr).await {
                                                        Ok(res) => {
                                                            skip_condition_error.set(if res.valid {
                                                                None
                                                            } else {
                                                                res.error
                                                            });
                                                            skip_condition_simplified.set(if res.valid {
                                                                res.simplified
                                                            } else {
                                                                None
                                                            });
                                                        }
                                                        Err(e) => {
                                                            skip_condition_error.set(Some(e));
                                                            skip_condition_simplified.set(None);
                                                        }
                                                    }
                                                });
                                            },
                                        }
                                        if show_skip_docs_edit && !docs_filtered_edit.is_empty() {
                                            div { class: "position-absolute start-0 mt-1 p-2 bg-light border rounded shadow-sm z-3",
                                                style: "min-width: 280px; max-height: 240px; overflow-y: auto;",
                                                ul { class: "list-unstyled mb-0 small",
                                                    for item in docs_items_edit.iter() {
                                                        {item.clone()}
                                                    }
                                                }
                                            }
                                        }
                                        if show_bracket_ac_edit && !bracket_options_edit.is_empty() {
                                            div { class: "position-absolute start-0 mt-1 p-2 bg-light border rounded shadow-sm z-3",
                                                style: "min-width: 200px; max-height: 240px; overflow-y: auto;",
                                                ul { class: "list-unstyled mb-0 small",
                                                    for item in bracket_option_items_edit.iter() {
                                                        {item.clone()}
                                                    }
                                                }
                                            }
                                        }
                                        if let Some(err) = skip_condition_error() {
                                            div { class: "form-text text-danger", "✗ {err}" }
                                        } else if let Some(simp) = skip_condition_simplified() {
                                            div { class: "form-text text-success", "✓ Valid (simplified: {simp})" }
                                        } else if !skip_condition().trim().is_empty() {
                                            div { class: "form-text text-success", "✓ Valid" }
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
    } else if raw.len() >= 5 && raw.get(..5).map(|s| s.eq_ignore_ascii_case("tag::")).unwrap_or(false) {
        (1, raw.get(5..).unwrap_or("").trim().to_string())
    } else {
        (0, raw.to_string())
    }
}

