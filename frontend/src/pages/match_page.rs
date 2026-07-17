use crate::Route;
use crate::api;
use crate::components::{PenaltyDisplay, all_tokens_known, resolve_value_to_team_ids};
use crate::display::short_or_truncate;
use crate::pages::TeamSelectionField;
use crate::time_format::format_match_display_local;
use crate::types::{
    ConflictingMatchInfo, ForceStartMatchRequest, MatchDetailData, PointData, PointTimestamp,
};
#[cfg(target_arch = "wasm32")]
use chrono;
use dioxus::prelude::*;
#[cfg(target_arch = "wasm32")]
use gloo_timers::callback::Interval;
use serde_json::Value;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast;

/// Resolve in-video start time for a point: by uuid if present, else by index (legacy).
fn in_video_start_for_point(
    ts: &[PointTimestamp],
    points: &[PointData],
    point_index: usize,
) -> Option<f64> {
    let uuid = points.get(point_index).map(|p| &p.uuid)?;
    for t in ts {
        if t.point_uuid.as_ref() == Some(uuid) {
            return Some(t.in_video_start);
        }
    }
    // Legacy: no uuids, use index
    ts.get(point_index).map(|t| t.in_video_start)
}

/// Compute seek time in the new camera to match "same time in real life" from current camera.
/// Returns None if we can't resolve (e.g. legacy data without uuids, or point not in new camera).
fn same_time_seek_target(
    current_ts: &[PointTimestamp],
    new_ts: Option<&[PointTimestamp]>,
    current_video_time_secs: f64,
) -> Option<f64> {
    let new_ts = new_ts?;
    // Latest point (by in_video_start) that we're at or past
    let entry = current_ts
        .iter()
        .filter(|t| t.in_video_start <= current_video_time_secs)
        .last()?;
    let point_uuid = entry.point_uuid.as_ref()?;
    let offset = current_video_time_secs - entry.in_video_start;
    let new_entry = new_ts
        .iter()
        .find(|t| t.point_uuid.as_ref() == Some(point_uuid))?;
    Some(new_entry.in_video_start + offset)
}

/// (target_display, border_color, display_text, description, target_profile_id) for a point note when rendering penalties column.
fn point_note_display(
    note: &crate::types::MatchNoteData,
    penalty_types: &[crate::types::PenaltyType],
) -> (String, String, String, Option<String>, Option<String>) {
    let target_display = note
        .player_display
        .as_deref()
        .or(note.player_name.as_deref())
        .unwrap_or(if note.target == "match" {
            "Point"
        } else {
            note.target.as_str()
        })
        .to_string();
    let (border_color, display_text, desc) = match note
        .penalty_type_id
        .and_then(|id| penalty_types.iter().find(|t| t.id == id))
    {
        Some(pt) => (
            pt.color.clone(),
            pt.name.clone(),
            pt.desc.clone().filter(|s| !s.is_empty()),
        ),
        None => (
            "808080".to_string(),
            if note.text.is_empty() {
                "Other".to_string()
            } else {
                note.text.clone()
            },
            None,
        ),
    };
    let target_profile_id = note.player_id.clone();
    (
        target_display,
        border_color,
        display_text,
        desc,
        target_profile_id,
    )
}

fn get_query_param(name: &str) -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        let window = web_sys::window()?;
        let search = window.location().search().ok()?;
        let params = web_sys::UrlSearchParams::new_with_str(&search).ok()?;
        params.get(name)
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = name;
        None
    }
}

/// Seek the local video player to the given time in seconds (wasm only).
#[cfg(target_arch = "wasm32")]
fn seek_video_to(secs: f64) {
    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
        if let Some(el) = doc.get_element_by_id("local-video-player") {
            if let Ok(media) = el.dyn_into::<web_sys::HtmlMediaElement>() {
                media.set_current_time(secs);
            }
        }
    }
}

/// Step the local video player forward or backward by one frame (~1/30s) (wasm only).
#[cfg(target_arch = "wasm32")]
fn step_video_frame(forward: bool) {
    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
        if let Some(el) = doc.get_element_by_id("local-video-player") {
            if let Ok(media) = el.dyn_into::<web_sys::HtmlMediaElement>() {
                let t: f64 = media.current_time();
                let delta = if forward { 1.0 / 30.0 } else { -1.0 / 30.0 };
                media.set_current_time((t + delta).max(0.0));
            }
        }
    }
}

/// Toggle play/pause on the local video player (wasm only).
#[cfg(target_arch = "wasm32")]
fn toggle_video_play_pause() {
    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
        if let Some(el) = doc.get_element_by_id("local-video-player") {
            if let Ok(media) = el.dyn_into::<web_sys::HtmlMediaElement>() {
                if media.paused() {
                    let _ = media.play();
                } else {
                    let _ = media.pause();
                }
            }
        }
    }
}

/// Set the local video player playback rate (wasm only).
#[cfg(target_arch = "wasm32")]
fn set_video_playback_speed(rate: f64) {
    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
        if let Some(el) = doc.get_element_by_id("local-video-player") {
            if let Ok(media) = el.dyn_into::<web_sys::HtmlMediaElement>() {
                media.set_playback_rate(rate);
            }
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn seek_video_to(_secs: f64) {}

#[cfg(not(target_arch = "wasm32"))]
fn step_video_frame(_forward: bool) {}

#[cfg(not(target_arch = "wasm32"))]
fn toggle_video_play_pause() {}

#[cfg(not(target_arch = "wasm32"))]
fn set_video_playback_speed(_rate: f64) {}

/// Get current time in seconds of the local video player (wasm only).
#[cfg(target_arch = "wasm32")]
fn get_video_current_time() -> Option<f64> {
    let doc = web_sys::window()?.document()?;
    let el = doc.get_element_by_id("local-video-player")?;
    let media = el.dyn_into::<web_sys::HtmlMediaElement>().ok()?;
    Some(media.current_time())
}

#[cfg(not(target_arch = "wasm32"))]
fn get_video_current_time() -> Option<f64> {
    None
}

/// Seek the YouTube iframe player to the given time in seconds (wasm only).
#[cfg(target_arch = "wasm32")]
fn seek_youtube_to(secs: f64) {
    let script = format!(
        r#"if (window.__arctosYtPlayer && window.__arctosYtPlayer.seekTo) {{ window.__arctosYtPlayer.seekTo({}, true); }}"#,
        secs
    );
    spawn(async move {
        let _ = dioxus::prelude::document::eval(&script).await;
    });
}

#[cfg(not(target_arch = "wasm32"))]
fn seek_youtube_to(_secs: f64) {}

/// Compute seek time in seconds for YouTube: (point_stamp - stream_start_time). Returns None if missing/invalid.
fn youtube_seek_seconds(point_stamp: Option<&str>, stream_start_time: Option<&str>) -> Option<f64> {
    let point_str = point_stamp?.trim();
    let stream_str = stream_start_time?.trim();
    if point_str.is_empty() || stream_str.is_empty() {
        return None;
    }
    let ensure_z = |s: &str| -> String {
        if s.ends_with('Z') || s.contains('+') {
            s.to_string()
        } else {
            format!("{}Z", s)
        }
    };
    let point_parsed = chrono::DateTime::parse_from_rfc3339(&ensure_z(point_str))
        .or_else(|_| chrono::DateTime::parse_from_rfc3339(point_str))
        .ok()?;
    let stream_parsed = chrono::DateTime::parse_from_rfc3339(&ensure_z(stream_str))
        .or_else(|_| chrono::DateTime::parse_from_rfc3339(stream_str))
        .ok()?;
    let diff = point_parsed.signed_duration_since(stream_parsed);
    let secs = diff.num_milliseconds() as f64 / 1000.0;
    web_sys::console::log_1(
        &format!(
            "point_parsed: {:?}, stream_parsed: {:?}, diff: {:?}, secs: {:?}",
            point_parsed, stream_parsed, diff, secs
        )
        .into(),
    );
    Some(secs.max(0.0))
}

/// Map an absolute world timestamp (point stamp) to in-video seconds using
/// piecewise linear interpolation over `(time_world, time_video)` session boundaries.
///
/// If interpolation arrays are missing, falls back to YouTube seek logic using `stream_start_time`.
fn in_video_start_for_world_stamp_interpolated(
    point_stamp: Option<&str>,
    time_world: Option<&Vec<String>>,
    time_video: Option<&Vec<f64>>,
    stream_start_time: Option<&str>,
) -> Option<f64> {
    let point_stamp = point_stamp?.trim();
    if point_stamp.is_empty() {
        return None;
    }

    let world_secs = parse_iso_to_secs(point_stamp)?;

    let (tw, tv) = match (time_world, time_video) {
        (Some(tw), Some(tv)) if !tw.is_empty() && tw.len() == tv.len() => (tw, tv),
        _ => {
            return youtube_seek_seconds(Some(point_stamp), stream_start_time);
        }
    };

    if tv.is_empty() {
        return None;
    }

    if tv.len() == 1 {
        // Best-effort: if we only have a single boundary, assume slope 1 in time.
        let t0_world = parse_iso_to_secs(&tw[0])?;
        return Some((world_secs - t0_world + tv[0]).max(0.0));
    }

    // Parse boundaries to seconds since epoch.
    let tw_secs: Option<Vec<f64>> = tw.iter().map(|s| parse_iso_to_secs(s)).collect();
    let tw_secs = tw_secs?;

    // Find segment for interpolation / extrapolation.
    let n = tv.len();
    let (i0, i1) = if world_secs <= tw_secs[0] {
        (0usize, 1usize)
    } else if world_secs >= tw_secs[n - 1] {
        (n - 2, n - 1)
    } else {
        let mut found = None;
        for i in 0..(n - 1) {
            if tw_secs[i] <= world_secs && world_secs <= tw_secs[i + 1] {
                found = Some((i, i + 1));
                break;
            }
        }
        found?
    };

    let denom = tw_secs[i1] - tw_secs[i0];
    if denom.abs() < f64::EPSILON {
        return Some(tv[i0].max(0.0));
    }
    let slope = (tv[i1] - tv[i0]) / denom;
    let secs = tv[i0] + (world_secs - tw_secs[i0]) * slope;
    Some(secs.max(0.0))
}

/// Parse ISO timestamp to epoch seconds. Handles RFC3339 and naive ISO from Python (e.g. "2025-02-21T12:34:56.123456").
fn parse_iso_to_secs(s: &str) -> Option<f64> {
    let s = s.trim();
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.timestamp_millis() as f64 / 1000.0)
        .or_else(|| {
            let with_z = if s.ends_with('Z') || s.contains('+') || (s.contains('-') && s.len() > 10)
            {
                s.to_string()
            } else {
                format!("{}Z", s.trim_end_matches('z').trim_end_matches('Z'))
            };
            chrono::DateTime::parse_from_rfc3339(&with_z)
                .ok()
                .map(|dt| dt.timestamp_millis() as f64 / 1000.0)
        })
        .or_else(|| {
            let without_tz = s.trim_end_matches('z').trim_end_matches('Z').trim();
            let base = if let Some(dot) = without_tz.find('.') {
                &without_tz[..dot]
            } else if let Some(plus) = without_tz.find('+') {
                &without_tz[..plus]
            } else {
                without_tz
            };
            chrono::NaiveDateTime::parse_from_str(base, "%Y-%m-%dT%H:%M:%S")
                .ok()
                .map(|t| t.and_utc().timestamp() as f64)
        })
}

/// Compute stones elapsed = number of global 1.5s beat boundaries crossed (so counters tick in sync with global clock).
#[cfg(target_arch = "wasm32")]
fn compute_stones_elapsed(
    start_stamp: Option<&str>,
    end_stamp: Option<&str>,
    time_sync: &crate::time_sync::TimeSync,
) -> String {
    const BEAT: f64 = 1.5;

    let start = match start_stamp.and_then(|s| parse_iso_to_secs(s)) {
        Some(secs) => secs,
        None => return "0".to_string(),
    };

    let end = match end_stamp.and_then(|s| parse_iso_to_secs(s)) {
        Some(secs) => secs,
        None => time_sync.server_now_secs(),
    };

    let start_beat = (start / BEAT).floor() as i64;
    let end_beat = (end / BEAT).floor() as i64;
    let elapsed = (end_beat - start_beat).max(0);
    elapsed.to_string()
}

/// Compute stones remaining from points list
#[cfg(target_arch = "wasm32")]
fn compute_stones_remaining(
    points: &[&crate::types::PointData],
    time_sync: &crate::time_sync::TimeSync,
) -> String {
    if points.is_empty() {
        return "??".to_string();
    }

    // Find the last point
    let last_point = points[points.len() - 1];

    // If last point is ongoing (no end_stamp), compute from it
    let last_point_is_ongoing = last_point.end_stamp.is_none();
    if last_point_is_ongoing {
        if let Some(stones_at_start) = last_point.stones_at_start {
            if let Some(start_stamp) = &last_point.stamp {
                let elapsed_str = compute_stones_elapsed(Some(start_stamp), None, time_sync);
                if let Ok(elapsed) = elapsed_str.parse::<u32>() {
                    let remaining = stones_at_start.saturating_sub(elapsed);
                    return remaining.to_string();
                }
            }
        }
    }

    // Last point is completed, find the last completed point with stones_at_start
    for pt in points.iter().rev() {
        if pt.end_stamp.is_some() {
            if let Some(stones_at_start) = pt.stones_at_start {
                if let (Some(start_stamp), Some(end_stamp)) = (&pt.stamp, &pt.end_stamp) {
                    let elapsed_str =
                        compute_stones_elapsed(Some(start_stamp), Some(end_stamp), time_sync);
                    if let Ok(elapsed) = elapsed_str.parse::<u32>() {
                        let remaining = stones_at_start.saturating_sub(elapsed);
                        return remaining.to_string();
                    }
                }
            }
        }
    }

    "??".to_string()
}

/// Parse team display name to check if it's a match reference (e.g., "MatchName::winner" or "MatchName winner")
fn parse_team_reference(display: &str) -> Option<(String, String)> {
    // Check for "::winner" or "::loser"
    if let Some(pos) = display.find("::") {
        let match_name = display[..pos].to_string();
        let suffix = display[pos..].to_string();
        if suffix == "::winner" || suffix == "::loser" {
            return Some((match_name, display.replace("::", " ")));
        }
    }
    // Check for " winner" or " loser" at the end
    if display.ends_with(" winner") || display.ends_with(" loser") {
        if let Some(pos) = display.rfind(' ') {
            let match_name = display[..pos].to_string();
            return Some((match_name, display.to_string()));
        }
    }
    None
}

// CSS for TeamTokenInput in Force Start modal (same as schedule page)
const TEAM_TOKEN_MODAL_CSS: &str = include_str!("schedule_timeline.css");

#[component]
fn ForceStartModal(
    tournament_url: String,
    match_id: String,
    match_data: MatchDetailData,
    conflicting_match: Option<ConflictingMatchInfo>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
) -> Element {
    let schedule_data = use_resource({
        let tournament_url_for_resource = tournament_url.clone();
        move || {
            let u = tournament_url_for_resource.clone();
            async move { api::schedule_setup(&u).await.map_err(|e| e.to_string()) }
        }
    });
    let val = schedule_data.value();

    let mut team1 = use_signal(|| {
        match_data
            .team1
            .clone()
            .or(match_data.team1_initial.clone())
            .unwrap_or_default()
    });
    let mut team2 = use_signal(|| {
        match_data
            .team2
            .clone()
            .or(match_data.team2_initial.clone())
            .unwrap_or_default()
    });

    let refs_initial_str = match_data
        .r#refs_initial
        .as_deref()
        .or(match_data.r#refs.as_deref())
        .unwrap_or("");
    let refs_initial_owned = match_data
        .r#refs
        .clone()
        .unwrap_or_else(|| refs_initial_str.to_string());
    let mut refs = use_signal(move || refs_initial_owned.clone());

    let mut conflicting_action = use_signal(|| None::<String>);
    let mut conflicting_winner = use_signal(|| None::<String>);

    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);
    let mut submit_trigger = use_signal(|| 0u32);

    let tournament_url_submit = tournament_url.clone();
    let match_id_submit = match_id.clone();
    let team1_submit = team1.clone();
    let team2_submit = team2.clone();
    let refs_submit = refs.clone();
    let conflicting_action_submit = conflicting_action.clone();
    let conflicting_winner_submit = conflicting_winner.clone();
    let on_success_cb = on_success.clone();
    let has_conflict = conflicting_match.is_some();
    let val_for_submit = val.clone();

    let validation = use_memo(move || {
        let data = match val.read().as_ref() {
            Some(Ok(d)) => d.clone(),
            _ => return (true, None),
        };
        let t1 = team1().trim().to_string();
        let t2 = team2().trim().to_string();
        let r = refs().trim().to_string();

        #[cfg(target_arch = "wasm32")]
        web_sys::console::log_1(
            &format!(
                "[ForceStart] validation memo run t1={:?} t2={:?} refs={:?}",
                t1, t2, r
            )
            .into(),
        );

        if t1.is_empty() || t2.is_empty() {
            return (false, Some("Team 1 and Team 2 are required.".to_string()));
        }
        let all_known = all_tokens_known(&t1, false, &data.team_options, &data.tags, &data.matches)
            && all_tokens_known(&t2, false, &data.team_options, &data.tags, &data.matches)
            && (r.is_empty()
                || all_tokens_known(&r, true, &data.team_options, &data.tags, &data.matches));

        #[cfg(target_arch = "wasm32")]
        web_sys::console::log_1(
            &format!(
                "[ForceStart] all_known={} (t1={:?} t2={:?} refs={:?})",
                all_known, t1, t2, r
            )
            .into(),
        );

        if !all_known {
            return (
                false,
                Some("All participating teams must be known.".to_string()),
            );
        }
        if has_conflict {
            let act = conflicting_action();
            if act.is_none() {
                return (
                    false,
                    Some(
                        "Another match is in progress on this field. Choose SKIP or COMPLETE."
                            .to_string(),
                    ),
                );
            }
            if act.as_deref() == Some("COMPLETE") && conflicting_winner().is_none() {
                return (
                    false,
                    Some("When marking as COMPLETE, choose TEAM1 or TEAM2 as winner.".to_string()),
                );
            }
        }
        (true, None)
    });
    let (can_submit, validation_message) = validation();

    #[cfg(target_arch = "wasm32")]
    web_sys::console::log_1(
        &format!(
            "[ForceStart] render can_submit={} msg={:?}",
            can_submit, validation_message
        )
        .into(),
    );

    use_effect(move || {
        let trigger = submit_trigger();
        if trigger == 0 {
            return;
        }
        let t1 = team1_submit().trim().to_string();
        let t2 = team2_submit().trim().to_string();
        if t1.is_empty() || t2.is_empty() {
            submit_trigger.set(0);
            return;
        }
        if has_conflict {
            let act = conflicting_action_submit();
            if act.is_none() {
                submit_trigger.set(0);
                return;
            }
            if act.as_deref() == Some("COMPLETE") {
                let win = conflicting_winner_submit();
                if win.is_none() {
                    submit_trigger.set(0);
                    return;
                }
            }
        }
        let data = match val_for_submit.read().as_ref() {
            Some(Ok(d)) => d.clone(),
            _ => {
                submit_trigger.set(0);
                return;
            }
        };
        let r = refs_submit().trim().to_string();
        let team1_ids = match resolve_value_to_team_ids(
            &t1,
            false,
            &data.team_options,
            &data.tags,
            &data.matches,
        ) {
            Some(ids) => ids,
            None => {
                error.set(Some("Could not resolve Team 1 to a team ID.".to_string()));
                submit_trigger.set(0);
                return;
            }
        };
        let team2_ids = match resolve_value_to_team_ids(
            &t2,
            false,
            &data.team_options,
            &data.tags,
            &data.matches,
        ) {
            Some(ids) => ids,
            None => {
                error.set(Some("Could not resolve Team 2 to a team ID.".to_string()));
                submit_trigger.set(0);
                return;
            }
        };
        let refs_ids = if r.is_empty() {
            Vec::new()
        } else {
            match resolve_value_to_team_ids(&r, true, &data.team_options, &data.tags, &data.matches)
            {
                Some(ids_str) => ids_str
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect::<Vec<_>>(),
                None => {
                    error.set(Some("Could not resolve Referees to team IDs.".to_string()));
                    submit_trigger.set(0);
                    return;
                }
            }
        };
        error.set(None);
        saving.set(true);
        let req = ForceStartMatchRequest {
            team1: team1_ids,
            team2: team2_ids,
            refs: refs_ids,
            conflicting_match_action: conflicting_action_submit(),
            conflicting_match_winner: conflicting_winner_submit(),
        };
        let u = tournament_url_submit.clone();
        let m = match_id_submit.clone();
        spawn(async move {
            match api::force_start_match(&u, &m, &req).await {
                Ok(()) => {
                    saving.set(false);
                    on_success_cb.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        });
        submit_trigger.set(0);
    });

    let mut on_submit_click = move |_| {
        submit_trigger.set(submit_trigger() + 1);
    };

    rsx! {
        style { {TEAM_TOKEN_MODAL_CSS} }
        div { class: "modal show", style: "display: block;",
            div { class: "modal-dialog modal-dialog-centered modal-lg",
                div { class: "modal-content",
                    div { class: "modal-header",
                        h5 { class: "modal-title", "Force Start Match" }
                        button { r#type: "button", class: "btn-close", onclick: move |_| on_close.call(()) }
                    }
                    div { class: "modal-body",
                        if let Some(Ok(data)) = val.read().as_ref() {
                            form {
                                onsubmit: move |ev| { ev.prevent_default(); on_submit_click(()); },
                                div { class: "mb-3",
                                    TeamSelectionField {
                                        label: "Team 1".to_string(),
                                        team_options: data.team_options.clone(),
                                        tags: data.tags.clone(),
                                        matches: data.matches.clone(),
                                        value: team1(),
                                        on_change: move |s| team1.set(s),
                                        multiple: false,
                                        placeholder: "Team 1".to_string(),
                                    }
                                }
                                div { class: "mb-3",
                                    TeamSelectionField {
                                        label: "Team 2".to_string(),
                                        team_options: data.team_options.clone(),
                                        tags: data.tags.clone(),
                                        matches: data.matches.clone(),
                                        value: team2(),
                                        on_change: move |s| team2.set(s),
                                        multiple: false,
                                        placeholder: "Team 2".to_string(),
                                    }
                                }
                                div { class: "mb-3",
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
                                if let Some(ref cm) = conflicting_match {
                                    div { class: "mb-3 p-3 border rounded",
                                        p { class: "mb-2", "Another match is in progress on this field: {cm.name}." }
                                        div { class: "mb-2",
                                            label { class: "form-label small", "What should happen to that match?" }
                                            div { class: "form-check",
                                                input {
                                                    class: "form-check-input",
                                                    r#type: "radio",
                                                    name: "conflict_action",
                                                    id: "conflict_skip",
                                                    checked: conflicting_action().as_deref() == Some("SKIP"),
                                                    onchange: move |_| {
                                                        conflicting_action.set(Some("SKIP".to_string()));
                                                        conflicting_winner.set(None);
                                                    }
                                                }
                                                label { class: "form-check-label", r#for: "conflict_skip", "Mark as SKIPPED (discard results)" }
                                            }
                                            div { class: "form-check",
                                                input {
                                                    class: "form-check-input",
                                                    r#type: "radio",
                                                    name: "conflict_action",
                                                    id: "conflict_complete",
                                                    checked: conflicting_action().as_deref() == Some("COMPLETE"),
                                                    onchange: move |_| conflicting_action.set(Some("COMPLETE".to_string()))
                                                }
                                                label { class: "form-check-label", r#for: "conflict_complete", "Mark as COMPLETED (choose winner)" }
                                            }
                                        }
                                        if conflicting_action().as_deref() == Some("COMPLETE") {
                                            div { class: "ms-3",
                                                label { class: "form-label small", "Winner" }
                                                div { class: "form-check",
                                                    input {
                                                        class: "form-check-input",
                                                        r#type: "radio",
                                                        name: "conflict_winner",
                                                        id: "winner_team1",
                                                        checked: conflicting_winner().as_deref() == Some("TEAM1"),
                                                        onchange: move |_| conflicting_winner.set(Some("TEAM1".to_string()))
                                                    }
                                                    label { class: "form-check-label", r#for: "winner_team1", "{short_or_truncate(&cm.team1_name, cm.team1_shortname.as_deref())}" }
                                                }
                                                div { class: "form-check",
                                                    input {
                                                        class: "form-check-input",
                                                        r#type: "radio",
                                                        name: "conflict_winner",
                                                        id: "winner_team2",
                                                        checked: conflicting_winner().as_deref() == Some("TEAM2"),
                                                        onchange: move |_| conflicting_winner.set(Some("TEAM2".to_string()))
                                                    }
                                                    label { class: "form-check-label", r#for: "winner_team2", "{short_or_truncate(&cm.team2_name, cm.team2_shortname.as_deref())}" }
                                                }
                                            }
                                        }
                                    }
                                }
                                if let Some(msg) = validation_message {
                                    div { class: "alert alert-danger mb-0 mt-3", "{msg}" }
                                }
                                if let Some(err) = error() {
                                    div { class: "alert alert-danger mb-0 mt-2", "{err}" }
                                }
                                div { class: "modal-footer mt-3",
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary",
                                        onclick: move |_| on_close.call(()),
                                        "Cancel"
                                    }
                                    button {
                                        r#type: "submit",
                                        class: "btn btn-warning",
                                        disabled: saving() || !can_submit,
                                        "Force Start"
                                    }
                                }
                            }
                        } else if let Some(Err(e)) = val.read().as_ref() {
                            p { class: "text-danger", "{e}" }
                        } else {
                            p { "Loading…" }
                        }
                    }
                }
            }
        }
        div { class: "modal-backdrop show" }
    }
}

#[component]
pub fn MatchPage(url: String) -> Element {
    let match_id = get_query_param("id");
    let match_name = get_query_param("name");
    match_page_inner(url, match_id, match_name)
}

#[component]
pub fn MatchPageById(url: String, match_id: String) -> Element {
    match_page_inner(url, Some(match_id), None)
}

fn match_page_inner(url: String, match_id: Option<String>, match_name: Option<String>) -> Element {
    let url_for_resource = url.clone();
    let id_for_resource = match_id.clone();
    let name_for_resource = match_name.clone();

    // Main match data
    let data = use_resource(move || {
        let u = url_for_resource.clone();
        let id = id_for_resource.clone();
        let name = name_for_resource.clone();
        async move {
            if id.is_some() || name.is_some() {
                api::match_detail(&u, id.as_deref(), name.as_deref())
                    .await
                    .map_err(|e| e.to_string())
            } else {
                Err("id or name query param required".to_string())
            }
        }
    });

    // Polling for live match state updates
    let poll_tick = use_signal(|| 0u32);
    let url_for_poll = url.clone();
    let match_id_for_poll = match_id.clone();

    #[cfg(target_arch = "wasm32")]
    let poll_interval = use_signal(|| None as Option<Interval>);
    #[cfg(target_arch = "wasm32")]
    {
        let mut poll_tick = poll_tick;
        let mut poll_interval = poll_interval;
        use_effect(move || {
            if let Some(Ok(d)) = data.value().read().as_ref() {
                if d.match_data.status == "IN_PROGRESS" {
                    if poll_interval.read().is_none() {
                        let handle = Interval::new(1000, move || {
                            poll_tick.set(poll_tick() + 1);
                        });
                        poll_interval.set(Some(handle));
                    }
                } else {
                    poll_interval.set(None);
                }
            } else {
                poll_interval.set(None);
            }
        });
    }

    // Match state from polling
    let state_signal = use_signal(|| None as Option<Result<Value, String>>);
    use_effect(move || {
        let u = url_for_poll.clone();
        let id = match_id_for_poll.clone();
        let _tick = poll_tick();
        let mut state_signal = state_signal;
        if let Some(id) = id {
            if let Some(Ok(d)) = data.value().read().as_ref() {
                if d.match_data.status == "IN_PROGRESS" {
                    spawn(async move {
                        match api::match_state(&u, &id).await {
                            Ok(v) => state_signal.set(Some(Ok(v))),
                            Err(e) => state_signal.set(Some(Err(e))),
                        }
                    });
                }
            }
        }
    });

    // Live points from polled state (so points table and score update every poll)
    let live_points_signal = use_signal(|| None as Option<Vec<PointData>>);
    use_effect(move || {
        let _ = state_signal();
        let mut live_points_signal = live_points_signal;
        if let Some(Ok(state)) = state_signal.read().as_ref() {
            if let Some(points_val) = state.get("points") {
                if let Ok(pts) = serde_json::from_value::<Vec<PointData>>(points_val.clone()) {
                    live_points_signal.set(Some(pts));
                    return;
                }
            }
        }
        live_points_signal.set(None);
    });

    // User info for permissions
    let user_info = use_resource(move || async move { api::me().await.ok() });

    // Bayesian filter for server time sync (for stones elapsed calculation)
    // Shared client-server time sync (converges to the server clock; the probe
    // loop lives in the module).
    #[cfg_attr(not(target_arch = "wasm32"), allow(unused_variables))]
    let time_sync = crate::time_sync::use_time_sync();

    // Stones elapsed update interval (for ongoing points)
    #[cfg(target_arch = "wasm32")]
    let stones_update_tick = use_signal(|| 0u32);
    #[cfg(target_arch = "wasm32")]
    let stones_update_interval = use_signal(|| None as Option<Interval>);
    #[cfg(target_arch = "wasm32")]
    {
        let mut stones_update_tick = stones_update_tick;
        let mut stones_update_interval = stones_update_interval;
        use_effect(move || {
            if let Some(Ok(d)) = data.value().read().as_ref() {
                if d.match_data.status == "IN_PROGRESS"
                    && d.match_data.set_type.as_deref() == Some("STONES")
                {
                    if stones_update_interval.read().is_none() {
                        let handle = Interval::new(100, move || {
                            stones_update_tick.set(stones_update_tick() + 1);
                        });
                        stones_update_interval.set(Some(handle));
                    }
                } else {
                    stones_update_interval.set(None);
                }
            } else {
                stones_update_interval.set(None);
            }
        });
    }

    let val = data.value();
    let base_url = api::base_url();
    let navigator = use_navigator();
    let mut selected_camera_idx = use_signal(|| 0usize);
    let mut camera_dropdown_open = use_signal(|| false);
    let mut selected_point_index = use_signal(|| 0usize);
    let mut point_timestamps_for_keys = use_signal(|| Vec::<PointTimestamp>::new());
    let mut n_cameras_for_keys = use_signal(|| 0usize);
    let mut n_points_for_keys = use_signal(|| 0usize);
    let mut playback_speed = use_signal(|| "1".to_string());
    // Throttle frame step to 5 fps when holding f/b (min 200ms between steps).
    let mut last_frame_step_time_ms = use_signal(|| 0.0f64);
    // When switching camera, seek to this time (secs) once the new video is ready.
    let mut pending_seek_time = use_signal(|| None::<f64>);
    // Fetched YouTube stream start times per camera index (when API does not provide them).
    let fetched_stream_starts = use_signal(|| Vec::<Option<String>>::new());
    // Match UUID we have already triggered stream-start fetches for (so we only query once per load).
    let stream_starts_fetched_for_match = use_signal(|| None::<String>);
    let mut penalty_desc_modal = use_signal(|| None::<String>);
    let mut why_modal_show = use_signal(|| false);
    let mut force_start_modal_show = use_signal(|| false);
    let retry_finalization_pending = use_signal(|| false);
    let retry_finalization_message = use_signal(|| None::<String>);
    let retry_finalization_error = use_signal(|| None::<String>);

    // Reset per-page player/camera state when navigating to a different match.
    {
        let val_reset = val.clone();
        use_effect(move || {
            let match_uuid = val_reset
                .read()
                .as_ref()
                .and_then(|r| r.as_ref().ok())
                .map(|d| d.match_data.uuid.clone());
            if match_uuid.is_some() {
                selected_camera_idx.set(0);
                selected_point_index.set(0);
                camera_dropdown_open.set(false);
                pending_seek_time.set(None);
            }
        });
    }

    use_effect(move || {
        let _ = (
            val.read().as_ref(),
            selected_camera_idx(),
            live_points_signal(),
        );
        if let Some(Ok(d)) = val.read().as_ref() {
            let idx = selected_camera_idx().min(d.available_cameras.len().saturating_sub(1));
            if let Some(cam) = d.available_cameras.get(idx) {
                point_timestamps_for_keys.set(cam.point_timestamps.clone().unwrap_or_default());
            } else {
                point_timestamps_for_keys.set(Vec::new());
            }
            n_cameras_for_keys.set(d.available_cameras.len());
            let n_pts = live_points_signal()
                .as_ref()
                .map(|p| p.len())
                .unwrap_or(d.points.len());
            n_points_for_keys.set(n_pts);
        } else {
            point_timestamps_for_keys.set(Vec::new());
            n_cameras_for_keys.set(0);
            n_points_for_keys.set(0);
        }
    });

    // Fetch YouTube stream start for each camera that has a URL but no stream_start_time from API.
    // Only runs once when the match page loads (keyed by match uuid).
    #[cfg(target_arch = "wasm32")]
    {
        let val_fetch = val.clone();
        let mut fetched_stream_starts = fetched_stream_starts;
        let mut stream_starts_fetched_for_match = stream_starts_fetched_for_match;
        use_effect(move || {
            let match_uuid = val_fetch
                .read()
                .as_ref()
                .and_then(|r| r.as_ref().ok())
                .map(|d| d.match_data.uuid.clone());
            let Some(match_uuid) = match_uuid else { return };
            if stream_starts_fetched_for_match().as_deref() == Some(match_uuid.as_str()) {
                return;
            }
            stream_starts_fetched_for_match.set(Some(match_uuid.clone()));
            let cameras = val_fetch
                .read()
                .as_ref()
                .and_then(|r| r.as_ref().ok())
                .map(|d| d.available_cameras.clone());
            let Some(cameras) = cameras else { return };
            let n = cameras.len();
            if n == 0 {
                fetched_stream_starts.set(vec![]);
                return;
            }
            let mut current = vec![None::<String>; n];
            fetched_stream_starts.set(current.clone());
            for (idx, cam) in cameras.iter().enumerate() {
                let url = match &cam.url {
                    Some(u) if !u.trim().is_empty() => u.clone(),
                    _ => continue,
                };
                if cam.stream_start_time.is_some() {
                    continue;
                }
                if cam.camera_type == "recorded" {
                    continue;
                }
                let mut set_fetched = fetched_stream_starts;
                spawn(async move {
                    if let Ok(Some(iso)) = api::youtube_stream_start(&url).await {
                        let mut v = set_fetched();
                        if v.len() <= idx {
                            v.resize(idx + 1, None);
                        }
                        v[idx] = Some(iso);
                        set_fetched.set(v);
                    }
                });
            }
        });
    }

    // When switching camera with pending same-time seek, poll video until ready then seek.
    #[cfg(target_arch = "wasm32")]
    {
        let pending_seek_time_eff = pending_seek_time;
        use_effect(move || {
            let pending = pending_seek_time_eff();
            let _ = selected_camera_idx();
            if let Some(secs) = pending {
                let mut set_pending = pending_seek_time_eff;
                spawn(async move {
                    // Local video element may not exist anymore (we now scrub YouTube).
                    // Just issue a seek and clear the pending value.
                    gloo_timers::future::TimeoutFuture::new(300).await;
                    seek_youtube_to(secs);
                    set_pending.set(None);
                });
            }
        });
    }

    // Initialize YouTube iframe player when selected camera is YouTube and has a URL.
    #[cfg(target_arch = "wasm32")]
    {
        let val_yt = val.clone();
        use_effect(move || {
            let _ = selected_camera_idx();
            let binding = val_yt.read();
            let camera_url: Option<String> = binding
                .as_ref()
                .and_then(|r| r.as_ref().ok())
                .and_then(|d| {
                    let idx =
                        selected_camera_idx().min(d.available_cameras.len().saturating_sub(1));
                    let cam = d.available_cameras.get(idx)?;
                    if cam.status.as_deref() == Some("SUCCESS") {
                        cam.url.clone()
                    } else {
                        None
                    }
                });
            if let Some(url) = camera_url {
                let url_escaped = url
                    .replace('\\', "\\\\")
                    .replace('\'', "\\'")
                    .replace('\n', " ");
                let script = format!(
                    r#"
(function() {{
  var url = '{}';
  function ensureApiLoaded() {{
    if (window.YT && window.YT.Player) return;
    if (document.querySelector('script[data-arctos-yt-api="1"]')) return;
    var tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    tag.setAttribute('data-arctos-yt-api', '1');
    document.head.appendChild(tag);
  }}
  function extractVideoId(u) {{
    if (!u) return null;
    var m = u.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/)([^&\n?#]+)/) || u.match(/^([a-zA-Z0-9_-]{{11}})$/);
    return m ? m[1] : null;
  }}
  var videoId = extractVideoId(url);
  if (videoId) {{
    function getMountEl() {{
      return document.getElementById('youtube-player');
    }}
    function resetStalePlayer(mountEl) {{
      if (!window.__arctosYtPlayer) return;
      try {{
        var iframe = window.__arctosYtPlayer.getIframe ? window.__arctosYtPlayer.getIframe() : null;
        if (!iframe || !mountEl || !mountEl.contains(iframe)) {{
          try {{ window.__arctosYtPlayer.destroy(); }} catch (_err) {{}}
          window.__arctosYtPlayer = null;
          if (mountEl) {{
            mountEl.innerHTML = '';
          }}
        }}
      }} catch (_err) {{
        window.__arctosYtPlayer = null;
        if (mountEl) {{
          mountEl.innerHTML = '';
        }}
      }}
    }}
    function createOrLoad() {{
      var el = getMountEl();
      if (!el) return;
      resetStalePlayer(el);
      if (window.__arctosYtPlayer) {{
        try {{
          window.__arctosYtPlayer.loadVideoById(videoId);
          return;
        }} catch (e) {{
          try {{ window.__arctosYtPlayer.destroy(); }} catch (_err) {{}}
          window.__arctosYtPlayer = null;
          el.innerHTML = '';
        }}
      }}
      if (!window.__arctosYtPlayer) {{
        el.innerHTML = '';
        window.__arctosYtPlayer = new YT.Player(el, {{
          videoId: videoId,
          host: 'https://www.youtube-nocookie.com',
          playerVars: {{ autoplay: 1, controls: 1, rel: 0, modestbranding: 1, enablejsapi: 1, origin: window.location.origin, iv_load_policy: 1, playsinline: 1 }},
          events: {{ onReady: function() {{}}, onError: function(e) {{ console.error('YouTube player error', e.data); }} }}
        }});
      }}
    }}
    ensureApiLoaded();
    window.onYouTubeIframeAPIReady = function() {{ createOrLoad(); }};
    if (window.YT && window.YT.Player) {{
      createOrLoad();
    }} else {{
      setTimeout(createOrLoad, 150);
      setTimeout(createOrLoad, 500);
    }}
  }} else {{
    console.warn('Arctos: no YouTube video ID in', url);
  }}
}})();
"#,
                    url_escaped
                );
                spawn(async move {
                    let _ = dioxus::prelude::document::eval(&script).await;
                });
            }
        });
    }

    // When match is not completed and we have a YouTube stream start, seek to live (end of stream) after player loads.
    #[cfg(target_arch = "wasm32")]
    {
        let val_live = val.clone();
        use_effect(move || {
            let _ = selected_camera_idx();
            let _ = fetched_stream_starts();
            if let Some(Ok(d)) = val_live.read().as_ref() {
                if d.match_data.status == "COMPLETED" {
                    return;
                }
                let idx = selected_camera_idx().min(d.available_cameras.len().saturating_sub(1));
                let cam = match d.available_cameras.get(idx) {
                    Some(c) if c.camera_type == "youtube" => c,
                    _ => return,
                };
                let stream_start = cam
                    .stream_start_time
                    .clone()
                    .or_else(|| fetched_stream_starts().get(idx).cloned().flatten());
                let Some(stream_start) = stream_start else {
                    return;
                };
                spawn(async move {
                    gloo_timers::future::TimeoutFuture::new(3000).await;
                    let now_iso = chrono::Utc::now().to_rfc3339();
                    if let Some(secs) = youtube_seek_seconds(Some(&now_iso), Some(&stream_start)) {
                        seek_youtube_to(secs);
                    }
                });
            }
        });
    }

    // Initialize video player JavaScript when cameras are available
    #[cfg(target_arch = "wasm32")]
    {
        let val_for_effect = val.clone();
        let match_id_for_effect = match_id.clone();
        let url_for_effect = url.clone();
        use_effect(move || {
            if let Some(Ok(d)) = val_for_effect.read().as_ref() {
                if !d.available_cameras.is_empty() {
                    let cameras = d.available_cameras.clone();
                    let points = d.points.clone();
                    let match_id_str = match_id_for_effect.clone();
                    let url_str = url_for_effect.clone();
                    spawn(async move {
                        // Initialize video player (placeholder - full implementation requires YouTube IFrame API)
                        let cameras_json =
                            serde_json::to_string(&cameras).unwrap_or_else(|_| "[]".to_string());
                        let points_json = serde_json::to_string(
                            &points
                                .iter()
                                .map(|p| {
                                    serde_json::json!({
                                        "uuid": p.uuid,
                                        "stamp": p.stamp,
                                        "end_stamp": p.end_stamp,
                                        "stones_at_start": p.stones_at_start,
                                    })
                                })
                                .collect::<Vec<_>>(),
                        )
                        .unwrap_or_else(|_| "[]".to_string());
                        let match_id_val = match_id_str.as_ref().map(|s| s.as_str()).unwrap_or("");
                        let url_val = url_str.as_str();

                        let js_init = format!(
                            r#"
                            console.log('Match page video player initialized ' + JSON.stringify({{ matchId: '{}', tournamentUrl: '{}', availableCameras: {}, pointsData: {} }}));
                            "#,
                            match_id_val.replace('\'', "\\'"),
                            url_val.replace('\'', "\\'"),
                            cameras_json,
                            points_json
                        );
                        let _ = dioxus::prelude::document::eval(&js_init).await;
                    });
                }
            }
        });
    }

    let keydown_handler = move |ev: Event<KeyboardData>| {
        #[cfg(target_arch = "wasm32")]
        {
            if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                if let Some(active) = doc.active_element() {
                    if let Ok(html_el) = active.dyn_into::<web_sys::HtmlElement>() {
                        let tag = html_el.tag_name().to_uppercase();
                        if tag == "INPUT" || tag == "TEXTAREA" || tag == "SELECT" {
                            return;
                        }
                    }
                }
            }
        }
        let key = ev.key().to_string();
        let shift = ev.modifiers().contains(Modifiers::SHIFT);
        match key.as_str() {
            "g" | "G" => {
                ev.prevent_default();
                if let Some(Ok(d)) = val.read().as_ref() {
                    let pi = selected_point_index().min(d.points.len().saturating_sub(1));
                    let idx =
                        selected_camera_idx().min(d.available_cameras.len().saturating_sub(1));
                    let cam = d.available_cameras.get(idx);
                    if let Some(cam) = cam {
                        if cam.status.as_deref() != Some("SUCCESS") {
                            return;
                        }
                        let stamp = d.points.get(pi).and_then(|p| p.stamp.as_deref());
                        let stream_start_time: Option<String> = cam
                            .stream_start_time
                            .clone()
                            .or_else(|| fetched_stream_starts().get(idx).cloned().flatten());
                        if let Some(secs) = in_video_start_for_world_stamp_interpolated(
                            stamp,
                            cam.time_world.as_ref(),
                            cam.time_video.as_ref(),
                            stream_start_time.as_deref(),
                        ) {
                            seek_youtube_to(secs);
                        }
                    }
                }
            }
            "p" | "P" => {
                ev.prevent_default();
                let new_idx = selected_point_index().saturating_sub(1);
                selected_point_index.set(new_idx);
                if let Some(Ok(d)) = val.read().as_ref() {
                    let idx =
                        selected_camera_idx().min(d.available_cameras.len().saturating_sub(1));
                    let cam = d.available_cameras.get(idx);
                    if let Some(cam) = cam {
                        if cam.status.as_deref() != Some("SUCCESS") {
                            return;
                        }
                        let stamp = d.points.get(new_idx).and_then(|p| p.stamp.as_deref());
                        let stream_start_time: Option<String> = cam
                            .stream_start_time
                            .clone()
                            .or_else(|| fetched_stream_starts().get(idx).cloned().flatten());
                        if let Some(secs) = in_video_start_for_world_stamp_interpolated(
                            stamp,
                            cam.time_world.as_ref(),
                            cam.time_video.as_ref(),
                            stream_start_time.as_deref(),
                        ) {
                            seek_youtube_to(secs);
                        }
                    }
                }
            }
            "n" | "N" => {
                ev.prevent_default();
                let n = n_points_for_keys();
                let new_idx = (selected_point_index() + 1).min(n.saturating_sub(1));
                selected_point_index.set(new_idx);
                if let Some(Ok(d)) = val.read().as_ref() {
                    let idx =
                        selected_camera_idx().min(d.available_cameras.len().saturating_sub(1));
                    let cam = d.available_cameras.get(idx);
                    if let Some(cam) = cam {
                        if cam.status.as_deref() != Some("SUCCESS") {
                            return;
                        }
                        let stamp = d.points.get(new_idx).and_then(|p| p.stamp.as_deref());
                        let stream_start_time: Option<String> = cam
                            .stream_start_time
                            .clone()
                            .or_else(|| fetched_stream_starts().get(idx).cloned().flatten());
                        if let Some(secs) = in_video_start_for_world_stamp_interpolated(
                            stamp,
                            cam.time_world.as_ref(),
                            cam.time_video.as_ref(),
                            stream_start_time.as_deref(),
                        ) {
                            seek_youtube_to(secs);
                        }
                    }
                }
            }
            " " => {
                ev.prevent_default();
                toggle_video_play_pause();
            }
            "f" | "F" => {
                ev.prevent_default();
                #[cfg(target_arch = "wasm32")]
                {
                    let now = js_sys::Date::now();
                    if now - last_frame_step_time_ms() >= 100.0 {
                        last_frame_step_time_ms.set(now);
                        step_video_frame(true);
                    }
                }
                #[cfg(not(target_arch = "wasm32"))]
                step_video_frame(true);
            }
            "b" | "B" => {
                ev.prevent_default();
                #[cfg(target_arch = "wasm32")]
                {
                    let now = js_sys::Date::now();
                    if now - last_frame_step_time_ms() >= 100.0 {
                        last_frame_step_time_ms.set(now);
                        step_video_frame(false);
                    }
                }
                #[cfg(not(target_arch = "wasm32"))]
                step_video_frame(false);
            }
            "c" | "C" => {
                ev.prevent_default();
                let n = n_cameras_for_keys();
                if n > 1 {
                    let current_idx = selected_camera_idx();
                    let new_idx = if shift {
                        (current_idx + n - 1) % n
                    } else {
                        (current_idx + 1) % n
                    };
                    if new_idx != current_idx {
                        #[cfg(target_arch = "wasm32")]
                        if let Some(Ok(d)) = val.read().as_ref() {
                            let current_ts = point_timestamps_for_keys();
                            let new_cam = d.available_cameras.get(new_idx);
                            let new_ts = new_cam.and_then(|c| c.point_timestamps.as_deref());
                            if let Some(t) = get_video_current_time()
                                .and_then(|now| same_time_seek_target(&current_ts, new_ts, now))
                            {
                                pending_seek_time.set(Some(t));
                            }
                        }
                        selected_camera_idx.set(new_idx);
                    }
                }
            }
            _ => {}
        }
    };

    rsx! {
        style { "#match-page-keyboard:focus {{ outline: none; }}" }
        div {
            id: "match-page-keyboard",
            tabindex: "-1",
            onkeydown: keydown_handler,
            onmounted: move |ev: Event<MountedData>| {
                spawn(async move {
                    let _ = ev.data().set_focus(true).await;
                });
            },
        if let Some(Ok(d)) = val.read().as_ref() {
            {
                let modal_url = url.clone();
                let modal_match_id = d.match_data.uuid.clone();
                let has_cameras = d.available_cameras.len() > 0 || d.camera_url.is_some();
                let cameras = d.available_cameras.clone();
                let points_for_footage: Vec<PointData> = live_points_signal().clone().unwrap_or_else(|| d.points.clone());
                let base_url_footage = base_url.clone();
                let footage_section = has_cameras.then(move || {
                    let points = points_for_footage.clone();
                    let points_go = points.clone();
                    let points_prev = points.clone();
                    let points_next = points.clone();
                    let idx = selected_camera_idx().min(cameras.len().saturating_sub(1));
                    let current = cameras.get(idx);
                    let status = current
                        .and_then(|c| c.status.clone())
                        .unwrap_or_else(|| "SUCCESS".to_string());
                    let stream_start_time = current
                        .and_then(|c| c.stream_start_time.clone())
                        .or_else(|| fetched_stream_starts().get(idx).cloned().flatten());
                    let stream_start_go = stream_start_time.clone();
                    let stream_start_prev = stream_start_time.clone();
                    let stream_start_next = stream_start_time.clone();
                    let time_world_go = current.and_then(|c| c.time_world.clone());
                    let time_video_go = current.and_then(|c| c.time_video.clone());
                    let time_world_prev = time_world_go.clone();
                    let time_video_prev = time_video_go.clone();
                    let time_world_next = time_world_go.clone();
                    let time_video_next = time_video_go.clone();

                    let failed_download_url = current
                        .and_then(|c| c.video_path.clone())
                        .and_then(|p| {
                            let p = p.as_str().to_string();
                            if p.starts_with("http://") || p.starts_with("https://") {
                                Some(p)
                            } else {
                                let base = base_url_footage.trim_end_matches('/');
                                let p = p.trim_start_matches('/');
                                Some(format!("{}/{}", base, p))
                            }
                        });
                    rsx! {
                        div { class: "card mt-3",
                        div { class: "card-header d-flex justify-content-between align-items-center",
                            h5 { class: "mb-0", "Match Footage" }
                            if cameras.len() > 1 {
                                div { class: "dropdown",
                                    button {
                                        class: "btn btn-sm btn-outline-secondary dropdown-toggle",
                                        "type": "button",
                                        id: "camera-selector-dropdown",
                                        "aria-expanded": "{camera_dropdown_open()}",
                                        onclick: move |_| camera_dropdown_open.toggle(),
                                        {
                                            if let Some(cam) = current {
                                                if cam.camera_type == "recorded" {
                                                    { format!("📹 Camera: {}", cam.camera_id.as_deref().unwrap_or("unknown")) }
                                                } else {
                                                    { format!("📹 Camera: {}", cam.index + 1) }
                                                }
                                            } else {
                                                "📹 Camera".to_string()
                                            }
                                        }
                                    }
                                    if camera_dropdown_open() {
                                        ul {
                                            class: "dropdown-menu dropdown-menu-end show",
                                            style: "z-index: 9999;",
                                            "aria-labelledby": "camera-selector-dropdown",
                                            {
                                                cameras
                                                    .iter()
                                                    .enumerate()
                                                    .map(|(idx, cam)| {
                                                        let idx = idx;
                                                        let new_ts = cam.point_timestamps.clone();
                                                        rsx! {
                                                            li { key: "{cam.index}",
                                                                a {
                                                                    class: "dropdown-item",
                                                                    href: "#",
                                                                    onclick: move |ev| {
                                                                        ev.prevent_default();
                                                                        let current_idx = selected_camera_idx();
                                                                        if idx != current_idx {
                                                                            selected_camera_idx.set(idx);
                                                                        }
                                                                        camera_dropdown_open.set(false);
                                                                    },
                                                                    {
                                                                        if cam.camera_type == "recorded" {
                                                                            format!(
                                                                                "Camera: {}",
                                                                                cam.camera_id.as_deref().unwrap_or("unknown"),
                                                                            )
                                                                        } else {
                                                                            format!("Camera {}", cam.index + 1)
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    })
                                            }
                                        }
                                    }
                                }
                            } else if let Some(cam) = current {
                                span { class: "text-muted small",
                                    if cam.camera_type == "recorded" {
                                        { format!("📹 Camera: {}", cam.camera_id.as_deref().unwrap_or("unknown")) }
                                    } else {
                                        { format!("📹 Camera {}", cam.index + 1) }
                                    }
                                }
                            }
                        }
                        div { class: "card-body",
                            div { id: "video-stream-container",
                                if status == "UPLOADING" {
                                    p { class: "text-muted", "Video is still processing." }
                                } else if status == "FAILED" {
                                    p { class: "text-danger", "error processing video. click here to download source." }
                                    if let Some(ref href) = failed_download_url {
                                        a { href: "{href}", class: "btn btn-sm btn-outline-danger ms-2", "Download source" }
                                    }
                                } else {
                                    div {
                                        id: "youtube-player-container",
                                        style: "width: 100%; max-width: 100%; aspect-ratio: 16/9;",
                                        div {
                                            id: "youtube-player",
                                            style: "width: 100%; height: 100%;",
                                            "Loading YouTube player…"
                                        }
                                    }
                                    div { class: "mt-3 d-flex align-items-center flex-wrap gap-2",
                                        span { "Seek to point:" }
                                        select {
                                            id: "points-dropdown",
                                            class: "form-select form-select-sm",
                                            style: "width: auto;",
                                            value: "{selected_point_index().min(points.len().saturating_sub(1))}",
                                            onchange: move |ev| {
                                                if let Ok(i) = ev.value().parse::<usize>() {
                                                    selected_point_index.set(i);
                                                }
                                            },
                                            option { value: "", "Select point..." }
                                            {
                                                points
                                                    .iter()
                                                    .enumerate()
                                                    .map(|(idx, pt)| {
                                                        rsx! {
                                                            option { key: "{pt.uuid}", value: "{idx}", "Point {idx + 1}" }
                                                        }
                                                    })
                                            }
                                        }
                                        button {
                                            id: "seek-go-btn",
                                            class: "btn btn-sm btn-primary",
                                            onclick: move |_| {
                                                let pi = selected_point_index()
                                                    .min(points_go.len().saturating_sub(1));
                                                let stamp = points_go
                                                    .get(pi)
                                                    .and_then(|p| p.stamp.as_deref());
                                                if let Some(secs) =
                                                    in_video_start_for_world_stamp_interpolated(
                                                        stamp,
                                                        time_world_go.as_ref(),
                                                        time_video_go.as_ref(),
                                                        stream_start_go.as_deref(),
                                                    )
                                                {
                                                    seek_youtube_to(secs);
                                                }
                                            },
                                            "Go (g)"
                                        }
                                        button {
                                            id: "seek-prev-btn",
                                            class: "btn btn-sm btn-secondary",
                                            onclick: move |_| {
                                                let new_idx =
                                                    selected_point_index().saturating_sub(1);
                                                selected_point_index.set(new_idx);
                                                let stamp = points_prev
                                                    .get(new_idx)
                                                    .and_then(|p| p.stamp.as_deref());
                                                if let Some(secs) =
                                                    in_video_start_for_world_stamp_interpolated(
                                                        stamp,
                                                        time_world_prev.as_ref(),
                                                        time_video_prev.as_ref(),
                                                        stream_start_prev.as_deref(),
                                                    )
                                                {
                                                    seek_youtube_to(secs);
                                                }
                                            },
                                            "Previous Point (p)"
                                        }
                                        button {
                                            id: "seek-next-btn",
                                            class: "btn btn-sm btn-secondary",
                                            onclick: move |_| {
                                                let n = n_points_for_keys();
                                                let new_idx = (selected_point_index() + 1)
                                                    .min(n.saturating_sub(1));
                                                selected_point_index.set(new_idx);
                                                let stamp = points_next
                                                    .get(new_idx)
                                                    .and_then(|p| p.stamp.as_deref());
                                                if let Some(secs) =
                                                    in_video_start_for_world_stamp_interpolated(
                                                        stamp,
                                                        time_world_next.as_ref(),
                                                        time_video_next.as_ref(),
                                                        stream_start_next.as_deref(),
                                                    )
                                                {
                                                    seek_youtube_to(secs);
                                                }
                                            },
                                            "Next Point (n)"
                                        }
                                        span { class: "ms-2", "Speed:" }
                                        select {
                                            id: "playback-speed",
                                            class: "form-select form-select-sm",
                                            style: "width: auto;",
                                            value: "{playback_speed()}",
                                            onchange: move |ev| {
                                                let v = ev.value();
                                                playback_speed.set(v.clone());
                                                if let Ok(rate) = v.parse::<f64>() {
                                                    set_video_playback_speed(rate);
                                                }
                                            },
                                            option { value: "0.25", "0.25x" }
                                            option { value: "0.5", "0.5x" }
                                            option { value: "0.75", "0.75x" }
                                            option { value: "1", "1x" }
                                            option { value: "1.25", "1.25x" }
                                            option { value: "1.5", "1.5x" }
                                            option { value: "1.75", "1.75x" }
                                            option { value: "2", "2x" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            });
                // Refs by pseudonym (refs_display), same as teams; fallback to refs_initial then refs
                let reflist_str = d.match_data.refs_display.as_deref()
                    .or(d.match_data.r#refs_initial.as_deref())
                    .or(d.match_data.r#refs.as_deref());
                let match_title = format!("{} | Arctos", d.match_data.name);
                rsx! {
                    Title { "{match_title}" }
            div { class: "row",
                div { class: "col-12",
                    h1 { "{d.match_data.name}" }
                    nav { "aria-label": "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item",
                                Link {
                                    to: Route::TournamentHome {
                                        url: url.clone(),
                                    },
                                    "{url}"
                                }
                            }
                            li { class: "breadcrumb-item",
                                Link {
                                    to: Route::Schedule {
                                        url: url.clone(),
                                    },
                                    "Schedule"
                                }
                            }
                            li { class: "breadcrumb-item active", "{d.match_data.name}" }
                        }
                    }
                }
            }

            div { class: "row",
                div { class: "col-md-8",
                    // Match Information Card
                    div { class: "card",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Match Information" }
                        }
                        div { class: "card-body",
                            div { class: "row",
                                div { class: "col-md-4",
                                    div { class: "d-flex align-items-center mb-2",
                                        strong { class: "me-2", "Teams:" }
                                        div {
                                            // Team 1
                                            if let Some((match_name, display_text)) = parse_team_reference(
                                                &d.match_data.team1_name,
                                            )
                                            {
                                                Link {
                                                    to: Route::MatchPage {
                                                        url: url.clone(),
                                                    },
                                                    class: "text-decoration-none",
                                                    "{display_text}"
                                                }
                                            } else if let Some(team1_id) = &d.match_data.team1 {
                                                Link {
                                                    to: Route::TeamProfilePage {
                                                        id: team1_id.clone(),
                                                    },
                                                    class: "text-decoration-none",
                                                    "{d.match_data.team1_name}"
                                                }
                                            } else {
                                                span { "{d.match_data.team1_name}" }
                                            }
                                            if d.match_data.status == "COMPLETED"
                                                && d.match_data.match_winner.as_deref() == Some("TEAM1")
                                            {
                                                span { class: "badge bg-success ms-2",
                                                    "Winner"
                                                }
                                            }
                                            span { class: "mx-2", "vs" }
                                            // Team 2
                                            if let Some((match_name, display_text)) = parse_team_reference(
                                                &d.match_data.team2_name,
                                            )
                                            {
                                                Link {
                                                    to: Route::MatchPage {
                                                        url: url.clone(),
                                                    },
                                                    class: "text-decoration-none",
                                                    "{display_text}"
                                                }
                                            } else if let Some(team2_id) = &d.match_data.team2 {
                                                Link {
                                                    to: Route::TeamProfilePage {
                                                        id: team2_id.clone(),
                                                    },
                                                    class: "text-decoration-none",
                                                    "{d.match_data.team2_name}"
                                                }
                                            } else {
                                                span { "{d.match_data.team2_name}" }
                                            }
                                            if d.match_data.status == "COMPLETED"
                                                && d.match_data.match_winner.as_deref() == Some("TEAM2")
                                            {
                                                span { class: "badge bg-success ms-2",
                                                    "Winner"
                                                }
                                            }
                                        }
                                    }
                                    // Refs display (by pseudonym, same as teams)
                                    if let Some(refs_list) = reflist_str {
                                        if !refs_list.is_empty() {
                                            div { class: "d-flex align-items-center mb-2",
                                                strong { class: "me-2", "Refs:" }
                                                span {
                                                    {
                                                        refs_list.split(',')
                                                            .filter_map(|ref_trimmed| {
                                                                let ref_trimmed = ref_trimmed.trim();
                                                                if ref_trimmed.is_empty() {
                                                                    return None;
                                                                }
                                                                Some(ref_trimmed)
                                                            })
                                                            .enumerate()
                                                            .map(|(idx, ref_trimmed)| {
                                                                let refs_vec: Vec<&str> = refs_list
                                                                    .split(',')
                                                                    .filter(|s| !s.trim().is_empty())
                                                                    .collect();
                                                                let is_last = idx == refs_vec.len() - 1;
                                                                rsx! {
                                                                    if let Some((match_name, display_text)) = parse_team_reference(ref_trimmed) {
                                                                        Link {
                                                                            to: Route::MatchPage {
                                                                                url: url.clone(),
                                                                            },
                                                                            class: "text-decoration-none",
                                                                            "{display_text}"
                                                                        }
                                                                        if !is_last {
                                                                            ", "
                                                                        }
                                                                    } else {
                                                                        span { "{ref_trimmed}" }
                                                                        if !is_last {
                                                                            ", "
                                                                        }
                                                                    }
                                                                }
                                                            })
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                div { class: "col-md-4",
                                    div { class: "d-flex align-items-center mb-2",
                                        strong { class: "me-2", "Status:" }
                                        span {
                                            id: "match-status",
                                            class: format!(
                                                "badge {}",
                                                match d.match_data.status.as_str() {
                                                    "COMPLETED" => "bg-success",
                                                    "IN_PROGRESS" => "bg-warning",
                                                    _ => "bg-secondary",
                                                },
                                            ),
                                            {
                                                match d.match_data.status.as_str() {
                                                    "COMPLETED" => "Completed",
                                                    "IN_PROGRESS" => "In Progress",
                                                    _ => "Scheduled",
                                                }
                                            }
                                        }
                                    }
                                    if let Some(field) = &d.match_data.field {
                                        div { class: "d-flex align-items-center mb-2",
                                            strong { class: "me-2", "Field:" }
                                            span { "{field}" }
                                        }
                                    }
                                    div { class: "d-flex align-items-center mb-2",
                                        strong { class: "me-2", "Start:" }
                                        span {
                                            {
                                                match d.match_data
                                                    .confirmed_start_time
                                                    .as_deref()
                                                    .or(d.match_data.nominal_start_time.as_deref())
                                                {
                                                    None => "TBA".to_string(),
                                                    Some(t) => format_match_display_local(t),
                                                }
                                            }
                                        }
                                    }
                                    div { class: "d-flex align-items-center mb-2",
                                        strong { class: "me-2", "End:" }
                                        span {
                                            {
                                                match d.match_data.completed_time.as_deref() {
                                                    None => "TBA".to_string(),
                                                    Some(t) => format_match_display_local(t),
                                                }
                                            }
                                        }
                                    }
                                }
                                div { class: "col-md-4" }
                            }

                            if let Some(set_type) = &d.match_data.set_type {
                                div { class: "row mt-3",
                                    div { class: "col-md-6",
                                        h6 { "Type" }
                                        p { "{set_type}" }
                                    }
                                    if let Some(length) = d.match_data.nominal_length {
                                        div { class: "col-md-6",
                                            h6 { "Length" }
                                            p { "{length} minutes" }
                                        }
                                    }
                                }
                            }

                            // Action buttons: use can_start from API (includes field-busy and deps)
                            if d.is_head_ref && d.can_start {
                                div { class: "row mt-3",
                                    div { class: "col-12",
                                        div { class: "d-flex gap-2 align-items-center",
                                            Link {
                                                to: Route::StartMatch {
                                                    url: url.clone(),
                                                    match_id: d.match_data.uuid.clone(),
                                                },
                                                class: "btn btn-success",
                                                "Start Match"
                                            }
                                        }
                                    }
                                }
                            } else if d.is_head_ref && d.match_data.status == "IN_PROGRESS" {
                                div { class: "row mt-3",
                                    div { class: "col-12",
                                        div { class: "d-flex gap-2 align-items-center",
                                            Link {
                                                to: Route::RunMatch {
                                                    url: url.clone(),
                                                    match_id: d.match_data.uuid.clone(),
                                                },
                                                class: "btn btn-warning",
                                                "Run Match"
                                            }
                                        }
                                    }
                                }
                            } else if d.match_data.status != "COMPLETED" && d.match_data.status != "SKIPPED" && (d.why_sections.is_some() || !d.block_reasons.is_empty()) {
                                div { class: "row mt-3",
                                    div { class: "col-12",
                                        div { class: "d-flex gap-2 align-items-center flex-wrap",
                                            a {
                                                href: "#",
                                                class: "text-muted small text-decoration-none",
                                                onclick: move |ev: Event<MouseData>| { ev.prevent_default(); why_modal_show.set(true); },
                                                "Why can't I start this match?"
                                            }
                                        }
                                    }
                                }
                            }

                            if d.can_retry_finalization {
                                div { class: "row mt-3",
                                    div { class: "col-12",
                                        div { class: "d-flex gap-2 align-items-center flex-wrap",
                                            button {
                                                class: "btn btn-outline-secondary",
                                                disabled: retry_finalization_pending(),
                                                onclick: {
                                                    let url = url.clone();
                                                    let match_id = d.match_data.uuid.clone();
                                                    let mut retry_finalization_pending = retry_finalization_pending;
                                                    let mut retry_finalization_message = retry_finalization_message;
                                                    let mut retry_finalization_error = retry_finalization_error;
                                                    let mut data = data.clone();
                                                    move |_| {
                                                        if retry_finalization_pending() {
                                                            return;
                                                        }
                                                        let url = url.clone();
                                                        let match_id = match_id.clone();
                                                        retry_finalization_pending.set(true);
                                                        retry_finalization_message.set(None);
                                                        retry_finalization_error.set(None);
                                                        spawn(async move {
                                                            match api::retry_match_finalization(&url, &match_id).await {
                                                                Ok(msg) => {
                                                                    retry_finalization_message.set(Some(msg));
                                                                    data.restart();
                                                                }
                                                                Err(err) => retry_finalization_error.set(Some(err)),
                                                            }
                                                            retry_finalization_pending.set(false);
                                                        });
                                                    }
                                                },
                                                if retry_finalization_pending() {
                                                    "Retrying..."
                                                } else {
                                                    "Retry Finalization"
                                                }
                                            }
                                            if let Some(msg) = retry_finalization_message() {
                                                span { class: "text-success small", "{msg}" }
                                            }
                                            if let Some(err) = retry_finalization_error() {
                                                span { class: "text-danger small", "{err}" }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Live Match Status Card
                    div { class: "card mt-3",
                        div { class: "card-header",
                            h5 { class: "mb-0",
                                "Match Status: "
                                span {
                                    id: "live-status-message",
                                    class: match d.match_data.status.as_str() {
                                        "COMPLETED" => "text-success",
                                        "IN_PROGRESS" => "text-success",
                                        _ => "text-muted",
                                    },
                                    {
                                        match d.match_data.status.as_str() {
                                            "COMPLETED" => "✓ completed",
                                            "IN_PROGRESS" => "● live",
                                            _ => "○ not started",
                                        }
                                    }
                                }
                            }
                        }
                        div { class: "card-body",
                            // Stones remaining (for STONES matches): during point = from ongoing point's stamp + stones_at_start; between points = from server
                            if d.match_data.set_type.as_deref() == Some("STONES") {
                                {
                                    #[cfg(target_arch = "wasm32")]
                                    let stones_str: String = {
                                        let _update_tick = stones_update_tick();
                                        let points_for_stones: Vec<PointData> = live_points_signal()
                                            .clone()
                                            .unwrap_or_else(|| d.points.clone());
                                        let points_refs: Vec<&PointData> = points_for_stones.iter().collect();
                                        let last_ongoing = points_refs.last().map(|p| p.end_stamp.is_none()).unwrap_or(false);
                                        let between_points = !points_refs.is_empty() && !last_ongoing;
                                        if between_points {
                                        if let Some(Ok(state)) = state_signal.read().as_ref() {
                                            state.get("stones_remaining").and_then(|v| v.as_u64()).map(|s| s.to_string()).unwrap_or_else(|| compute_stones_remaining(&points_refs, &time_sync))
                                        } else {
                                            compute_stones_remaining(&points_refs, &time_sync)
                                        }
                                        } else {
                                            compute_stones_remaining(&points_refs, &time_sync)
                                        }
                                    };
                                    #[cfg(not(target_arch = "wasm32"))]
                                    let stones_str: String = d.match_data.stones_remaining.map(|s| s.to_string()).unwrap_or_else(|| "??".to_string());
                                    rsx! {
                                        div { class: "row mb-3",
                                            div { class: "col-md-6",
                                                p {
                                                    strong { "Stones Remaining: " }
                                                    span { id: "live-stones-remaining", "{stones_str}" }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // Score by Set - update from state
                            div { class: "mb-3",
                                h6 { "Score by Set" }
                                div { id: "score-by-set",
                                    {
                                        // Get scores from state if available, otherwise from match data
                                        {
                                            let scores_by_set: std::collections::BTreeMap<u32, (u32, u32)> = if let Some(
                                                Ok(state),
                                            ) = state_signal.read().as_ref()
                                            {
                                                if let Some(scores) = state
                                                    .get("scores_by_set")
                                                    .and_then(|v| v.as_object()) // Fallback: compute from points
                                                {
                                                    let mut map = std::collections::BTreeMap::new();
                                                    for (set_str, scores_obj) in scores {
                                                        if let (Ok(set_num), Some(team1), Some(team2)) = (
                                                            set_str.parse::<u32>(),
                                                            scores_obj.get("team1_score").and_then(|v| v.as_u64()),
                                                            scores_obj.get("team2_score").and_then(|v| v.as_u64()),
                                                        ) { // Compute from match data points
                                                            map.insert(set_num, (team1 as u32, team2 as u32));
                                                        }
                                                    }
                                                    map
                                                } else {
                                                    let mut sets: std::collections::BTreeMap<u32, (u32, u32)> = std::collections::BTreeMap::new();
                                                    for pt in &d.points {
                                                        if !pt.rerolled {
                                                            let set_num = pt.set_number.unwrap_or(1);
                                                            let entry = sets.entry(set_num).or_insert((0, 0));
                                                            match pt.winner.as_deref() {
                                                                Some("TEAM1") => entry.0 += 1,
                                                                Some("TEAM2") => entry.1 += 1,
                                                                _ => {}
                                                            }
                                                        }
                                                    }
                                                    sets
                                                }
                                            } else {
                                                let mut sets: std::collections::BTreeMap<u32, (u32, u32)> = std::collections::BTreeMap::new();
                                                for pt in &d.points {
                                                    if !pt.rerolled {
                                                        let set_num = pt.set_number.unwrap_or(1);
                                                        let entry = sets.entry(set_num).or_insert((0, 0));
                                                        match pt.winner.as_deref() {
                                                            Some("TEAM1") => entry.0 += 1,
                                                            Some("TEAM2") => entry.1 += 1,
                                                            _ => {}
                                                        }
                                                    }
                                                }
                                                sets
                                            };
                                            if !scores_by_set.is_empty() {
                                                rsx! {
                                                    // Team names header
                                                    div { class: "row mb-2",
                                                        div { class: "col-5 text-center",
                                                            small { class: "text-muted", "{short_or_truncate(&d.match_data.team1_name, d.match_data.team1_shortname.as_deref())}" }
                                                        }
                                                        div { class: "col-2" }
                                                        div { class: "col-5 text-center",
                                                            small { class: "text-muted", "{short_or_truncate(&d.match_data.team2_name, d.match_data.team2_shortname.as_deref())}" }
                                                        }
                                                    }
                                                    // Scores for each set
                                                    {
                                                        scores_by_set
                                                            .iter()
                                                            .map(|(set_num, (team1_score, team2_score))| {
                                                                rsx! {
                                                                    div { class: "row mb-1", key: "{set_num}",
                                                                        div { class: "col-5 text-center",
                                                                            strong { id: "live-team1-set-{set_num}-score", "{team1_score}" }
                                                                        }
                                                                        div { class: "col-2 text-center",
                                                                            small { class: "text-muted", "Set {set_num}" }
                                                                        }
                                                                        div { class: "col-5 text-center",
                                                                            strong { id: "live-team2-set-{set_num}-score", "{team2_score}" }
                                                                        }
                                                                    }
                                                                }
                                                            })
                                                    }
                                                }
                                            } else {
                                                rsx! {
                                                    div { class: "text-muted text-center", "No points yet" }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Points Table - update from state
                    div { class: "card mt-3",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Points" }
                        }
                        div { class: "card-body",
                            {
                                let points_for_table: Vec<PointData> = live_points_signal()
                                    .clone()
                                    .unwrap_or_else(|| d.points.clone());
                                let points_to_display: Vec<&PointData> = points_for_table.iter().collect();
                                if points_to_display.is_empty() {
                                    rsx! {
                                        p { class: "text-muted", "No points recorded yet." }
                                    }
                                } else {
                                    let points_with_note_displays: Vec<(&PointData, Vec<_>)> = points_to_display
                                        .iter()
                                        .map(|pt| {
                                            let notes = d.point_notes_map.get(&pt.uuid).cloned().unwrap_or_default();
                                            let displays: Vec<_> = notes.iter().map(|n| point_note_display(n, &d.penalty_types)).collect();
                                            let note_elems: Vec<_> = displays
                                                .into_iter()
                                                .map(|(target_display, border_color, display_text, desc, target_profile_id)| {
                                                    rsx! {
                                                        PenaltyDisplay {
                                                            border_color,
                                                            display_text,
                                                            description: desc,
                                                            target_display: Some(target_display),
                                                            target_profile_id,
                                                            on_description_click: move |d: Option<String>| penalty_desc_modal.set(d),
                                                        }
                                                    }
                                                })
                                                .collect();
                                            (*pt, note_elems)
                                        })
                                        .collect();
                                    rsx! {
                                        div { class: "table-responsive",
                                            table { class: "table table-sm",
                                                thead {
                                                    tr {
                                                        th { "#" }
                                                        th { "Set" }
                                                        th { "Stones Elapsed" }
                                                        th { "Winner" }
                                                        th { "Rerun" }
                                                        if d.is_head_ref {
                                                            th { "Penalties" }
                                                        }
                                                    }
                                                }
                                                tbody { id: "live-points-table",
                                                    for (idx, (pt, note_elems)) in points_with_note_displays.iter().enumerate() {
                                                        tr { key: "{pt.uuid}", id: "live-point-row-{pt.uuid}",
                                                            td { "{idx + 1}" }
                                                            td { "{pt.set_number.unwrap_or(1)}" }
                                                            td {
                                                                id: "live-stones-{pt.uuid}",
                                                                "data-stamp": pt.stamp.as_deref().unwrap_or(""),
                                                                "data-end": pt.end_stamp.as_deref().unwrap_or(""),
                                                                "data-stones-at-start": pt.stones_at_start.map(|s: u32| s.to_string()).unwrap_or_default(),
                                                                        {
                                                                            {
                                                                                #[cfg(target_arch = "wasm32")]
                                                                                {
                                                                                    let _update_tick = stones_update_tick();
                                                                                    compute_stones_elapsed(
                                                                                        pt.stamp.as_deref(),
                                                                                        pt.end_stamp.as_deref(),
                                                                                        &time_sync,
                                                                                    )
                                                                                }
                                                                                #[cfg(not(target_arch = "wasm32"))] { "0" }
                                                                            }
                                                                        }
                                                                    }
                                                                    td {
                                                                        {
                                                                            match pt.winner.as_deref() {
                                                                                Some("TEAM1") => d.match_data.team1_name.clone(),
                                                                                Some("TEAM2") => d.match_data.team2_name.clone(),
                                                                                _ => "-".to_string(),
                                                                            }
                                                                        }
                                                                    }
                                                                    td {
                                                                        if pt.rerolled {
                                                                            span { class: "badge bg-warning", "Rerun" }
                                                                        } else {
                                                                            span { class: "badge bg-success", "Normal" }
                                                                        }
                                                                    }
                                                                    if d.is_head_ref {
                                                                        td {
                                                                            div { class: "mt-1",
                                                                                for elem in note_elems.iter() {
                                                                                    {elem}
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

                    // Match Footage Section (rendered via footage_section above)
                    { footage_section }

                    // YouTube IFrame API (script from youtube.com; use host: youtube-nocookie.com when creating player)
                        script { src: "https://www.youtube.com/iframe_api" }
                        script { src: "https://cdnjs.cloudflare.com/polyfill/v3/polyfill.min.js?features=es6" }

                        {
                            {
                                let cameras = d.available_cameras.clone();
                                let points = d.points.clone();
                                let match_id_str = match_id.as_ref().map(|s| s.as_str()).unwrap_or("");
                                let url_str = url.as_str();
                                if !cameras.is_empty() {
                                    let cameras_json = serde_json::to_string(&cameras)
                                        .unwrap_or_else(|_| "[]".to_string());
                                    let points_json = serde_json::to_string(
                                            &points
                                                .iter()
                                                .map(|p| {
                                                    serde_json::json!(
                                                        { "uuid" : p.uuid, "stamp" : p.stamp, "end_stamp" : p
                                                        .end_stamp, "stones_at_start" : p.stones_at_start, }
                                                    )
                                                })
                                                .collect::<Vec<_>>(),
                                        )
                                        .unwrap_or_else(|_| "[]".to_string());
                                    #[cfg(target_arch = "wasm32")]
                                    {
                                        let cameras_json_for_effect = cameras_json.clone();
                                        let points_json_for_effect = points_json.clone();
                                        let match_id_str_for_effect = match_id_str.to_string();
                                        let url_str_for_effect = url_str.to_string();
                                        use_effect(move || {
                                            let cameras_json = cameras_json_for_effect.clone();
                                            let points_json = points_json_for_effect.clone();
                                            let match_id_str = match_id_str_for_effect.clone();
                                            let url_str = url_str_for_effect.clone();
                                            spawn(async move {
                                                let js_init = format!(
                                                    r#"
                                                                                        console.log('Match page video player initialized ' + JSON.stringify({{ matchId: '{}', tournamentUrl: '{}', availableCameras: {}, pointsData: {} }}));
                                                                                        "#,
                                                    match_id_str.replace('\'', "\\'"),
                                                    url_str.replace('\'', "\\'"),
                                                    cameras_json,
                                                    points_json,
                                                );
                                                let _ = dioxus::prelude::document::eval(&js_init).await;
                                            });
                                        });
                                    }
                                }
                                rsx! {}
                            }
                        }
                    }

                // Head Ref Notes Sidebar
                div { class: "col-md-4",
                    div { class: "card",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Head Ref Notes" }
                        }
                        div { class: "card-body",
                            div { class: "mb-3",
                                h6 { class: "mb-1", "Start Notes" }
                                if let Some(notes) = &d.match_data.initial_notes {
                                    if !notes.is_empty() {
                                        div { class: "small text-muted border-start border-3 ps-2",
                                            "{notes}"
                                        }
                                    } else {
                                        div { class: "text-muted small", "None" }
                                    }
                                } else {
                                    div { class: "text-muted small", "None" }
                                }
                            }
                            div { class: "mb-3",
                                h6 { class: "mb-1", "Finalize Notes" }
                                if let Some(notes) = &d.match_data.final_notes {
                                    if !notes.is_empty() {
                                        div { class: "small text-muted border-start border-3 ps-2",
                                            "{notes}"
                                        }
                                    } else {
                                        div { class: "text-muted small", "None" }
                                    }
                                } else {
                                    div { class: "text-muted small", "None" }
                                }
                            }
                            if d.is_head_ref && !d.match_notes.is_empty() {
                                div {
                                    h6 { class: "mb-1", "Match Notes" }
                                    div { style: "max-height: 300px; overflow-y: auto;",
                                        {
                                            d.match_notes
                                                .iter()
                                                .map(|note| {
                                                    let target_display = match note.target.as_str() {
                                                        "team1" => d.match_data.team1_name.clone(),
                                                        "team2" => d.match_data.team2_name.clone(),
                                                        "match" => "".to_string(),
                                                        _ => {
                                                            note.player_display
                                                                .as_ref()
                                                                .or(note.player_name.as_ref())
                                                                .cloned()
                                                                .unwrap_or_else(|| note.target.clone())
                                                        }
                                                    };
                                                    let prefix = if note.target == "match" {
                                                        "".to_string()
                                                    } else {
                                                        format!("{}: ", target_display)
                                                    };
                                                    let pt_id = note.penalty_type_id;
                                                    let penalty_info = if let Some(id) = pt_id {
                                                        d.penalty_types.iter().find(|t| t.id == id)
                                                    } else {
                                                        None
                                                    };
                                                    let penalty_desc_for_modal = penalty_info
                                                        .and_then(|pt| pt.desc.clone())
                                                        .filter(|s| !s.is_empty());
                                                    rsx! {
                                                        div {
                                                            class: "small text-muted border-start border-3 ps-2 mb-2",
                                                            key: "{note.created_at.as_deref().unwrap_or(\"\")}",
                                                            {
                                                                if !target_display.is_empty()
                                                                    && (note.team_id.is_some() || note.player_id.is_some())
                                                                {
                                                                    rsx! {
                                                                        Link {
                                                                            to: if note.team_id.is_some() { Route::TeamProfilePage {
                                                                                id: note.team_id.as_ref().unwrap().clone(),
                                                                            } } else { Route::PlayerProfilePage {
                                                                                id: note.player_id.as_ref().unwrap().clone(),
                                                                            } },
                                                                            class: "text-decoration-none",
                                                                            "{target_display}"
                                                                        }
                                                                        ": "
                                                                    }
                                                                } else {
                                                                    rsx! { "{prefix}" }
                                                                }
                                                            }
                                                            if let Some(pt) = penalty_info {
                                                                PenaltyDisplay {
                                                                    border_color: pt.color.clone(),
                                                                    display_text: pt.name.clone(),
                                                                    description: penalty_desc_for_modal.clone(),
                                                                    target_display: None,
                                                                    target_profile_id: None,
                                                                    on_description_click: move |d: Option<String>| penalty_desc_modal.set(d),
                                                                }
                                                            }
                                                            "{note.text}"
                                                            if let Some(created) = &note.created_at {
                                                                div { class: "text-muted", style: "font-size: 0.75rem;",
                                                                    "{created.chars().take(16).collect::<String>().replace('T', \" \")}"
                                                                }
                                                            }
                                                        }
                                                    }
                                                })
                                        }
                                    }
                                }
                            } else if d.is_head_ref {
                                div {
                                    h6 { class: "mb-1", "Match Notes" }
                                    div { class: "text-muted small", "None" }
                                }
                            }
                        }
                    }
                }
            }
            if penalty_desc_modal().is_some() {
                div { class: "modal show", style: "display: block;",
                    div { class: "modal-dialog modal-dialog-centered",
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Penalty description" }
                                button { r#type: "button", class: "btn-close", onclick: move |_| penalty_desc_modal.set(None) }
                            }
                            div { class: "modal-body", "{penalty_desc_modal().as_ref().unwrap_or(&String::new())}" }
                            div { class: "modal-footer",
                                button { r#type: "button", class: "btn btn-secondary", onclick: move |_| penalty_desc_modal.set(None), "Close" }
                            }
                        }
                    }
                }
                div { class: "modal-backdrop show" }
            }
            if why_modal_show() {
                div { class: "modal show", style: "display: block;",
                    div { class: "modal-dialog modal-dialog-centered modal-lg",
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Why can't I start this match?" }
                                button { r#type: "button", class: "btn-close", onclick: move |_| why_modal_show.set(false) }
                            }
                            div { class: "modal-body",
                                {
                                    match &d.why_sections {
                                        Some(sections) => rsx! {
                                            div { class: "mb-4",
                                                h6 { class: "text-muted mb-2",
                                                    "1. Match Status & Dependencies"
                                                    if !sections.match_ready.blocks_start {
                                                        span { class: "text-success ms-1", "✓" }
                                                    }
                                                }
                                                p { class: "small mb-1", "What is the match status? Does this prevent it from being started?" }
                                                if !sections.match_ready.blocks_start {
                                                    p { class: "text-success small mb-0", "Ready to start." }
                                                } else {
                                                    ul { class: "mb-0 small",
                                                        for reason in sections.match_ready.reasons.iter().filter(|r| !r.starts_with("Match status is")) {
                                                            li { class: "mb-1", "{reason}" }
                                                        }
                                                    }
                                                }
                                            }
                                            div { class: "mb-4",
                                                h6 { class: "text-muted mb-2",
                                                    "2. Conflicts"
                                                    if sections.conflicts.is_empty() {
                                                        span { class: "text-success ms-1", "✓" }
                                                    }
                                                }
                                                p { class: "small mb-2", "Is there another match currently happening on this field?" }
                                                if sections.conflicts.is_empty() {
                                                    p { class: "text-success small mb-0", "No conflicts." }
                                                } else {
                                                    ul { class: "mb-0 small",
                                                        for c in &sections.conflicts {
                                                            li { class: "mb-1", "{c}" }
                                                        }
                                                    }
                                                }
                                            }
                                            div { class: "mb-0",
                                                h6 { class: "text-muted mb-2",
                                                    "3. Ref permissions"
                                                    if sections.ref_permissions.is_ok {
                                                        span { class: "text-success ms-1", "✓" }
                                                    }
                                                }
                                                div { class: "ms-3 mb-3",
                                                    h6 { class: "small mb-1", "a. Who is allowed" }
                                                    p { class: "small text-muted mb-1", "Based on tournament settings, who should be allowed to head ref?" }
                                                    ul { class: "mb-0 small",
                                                        for s in &sections.ref_permissions.who_allowed {
                                                            li { class: "mb-1", "{s}" }
                                                        }
                                                    }
                                                }
                                                div { class: "ms-3 mb-0",
                                                    h6 { class: "small mb-1", "b. Current user" }
                                                    p { class: "small text-muted mb-1", "Your current sign-in and registration." }
                                                    ul { class: "mb-0 small",
                                                        for s in &sections.ref_permissions.current_user {
                                                            li { class: "mb-1", "{s}" }
                                                        }
                                                    }
                                                }
                                            }
                                        },
                                        None => rsx! {
                                            ul { class: "mb-0",
                                                for reason in &d.block_reasons {
                                                    li { class: "mb-1", "{reason}" }
                                                }
                                            }
                                        },
                                    }
                                }
                                if d.is_head_ref && !d.can_start {
                                    div { class: "mt-4 pt-3 border-top",
                                        p { class: "small text-muted mb-2",
                                            "If you need to start this match anyway (e.g. to resolve a conflict or bypass a blocking condition), you can force start it. You will set the teams and referees, and any conflicting match on the same field can be skipped or marked complete."
                                        }
                                        button {
                                            r#type: "button",
                                            class: "btn btn-warning",
                                            onclick: move |_| {
                                                why_modal_show.set(false);
                                                force_start_modal_show.set(true);
                                            },
                                            "Force Start Match"
                                        }
                                    }
                                }
                            }
                            div { class: "modal-footer",
                                button { r#type: "button", class: "btn btn-secondary", onclick: move |_| why_modal_show.set(false), "Close" }
                            }
                        }
                    }
                }
                div { class: "modal-backdrop show" }
            }
            if force_start_modal_show() {
                ForceStartModal {
                    tournament_url: url.clone(),
                    match_id: d.match_data.uuid.clone(),
                    match_data: d.match_data.clone(),
                    conflicting_match: d.conflicting_match.clone(),
                    on_close: move |_| force_start_modal_show.set(false),
                    on_success: move |_| {
                        force_start_modal_show.set(false);
                        navigator.push(Route::StartMatch {
                            url: modal_url.clone(),
                            match_id: modal_match_id.clone(),
                        });
                    },
                }
            }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else if match_id.is_none() && match_name.is_none() {
            p { "Add ?id=... or ?name=... to the URL" }
        } else {
            p { "Loading…" }
        }
        }
    }
}
