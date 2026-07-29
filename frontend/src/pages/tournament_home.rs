use crate::api;
use crate::components::{
    EditRegistrationContext, EditRegistrationModal, EventHeader, LeagueRegistrationButtons,
};
use crate::types::{ToEntry, User, UserUploadPlanningMatch, UserUploadPlanningResponse};
use crate::Route;
use chrono::{DateTime, SecondsFormat, Utc};
use dioxus::prelude::*;
use uuid::Uuid;

#[derive(Clone)]
struct PendingUpload {
    filename: String,
    file: dioxus::html::FileData,
    start_world_suggested: Option<String>,
    start_world_value: String,
    start_world_error: Option<String>,
    duration_sec: Option<f64>,
    metadata_error: Option<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum UploadMode {
    RawClips,
    EditedMatch,
}

impl UploadMode {
    fn as_api_str(self) -> &'static str {
        match self {
            Self::RawClips => "raw_clips",
            Self::EditedMatch => "edited_match",
        }
    }
}

#[derive(Clone, PartialEq)]
struct UploadPreviewClip {
    label: String,
    clip_start_file_sec: f64,
    clip_duration_sec: f64,
}

fn infer_start_world_from_file(file: &dioxus::html::FileData) -> Option<String> {
    let ms = file.last_modified() as i64;
    if ms <= 0 {
        return None;
    }
    DateTime::<Utc>::from_timestamp_millis(ms)
        .map(|dt| dt.to_rfc3339_opts(SecondsFormat::Secs, true))
}

fn validate_upload_start_world(raw: &str) -> Result<(), String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Ok(());
    }
    chrono::DateTime::parse_from_rfc3339(trimmed).map_err(|_| {
        "Start timestamp must include timezone, e.g. 2026-03-18T01:23:45Z or 2026-03-17T18:23:45-07:00.".to_string()
    })?;
    Ok(())
}

#[cfg(target_arch = "wasm32")]
async fn load_video_duration_sec(file: &dioxus::html::FileData) -> Result<f64, String> {
    use gloo_timers::future::TimeoutFuture;
    use wasm_bindgen::JsCast;

    let web_file = file
        .inner()
        .downcast_ref::<web_sys::File>()
        .cloned()
        .ok_or_else(|| "Could not access browser file handle".to_string())?;
    let blob: web_sys::Blob = web_file.unchecked_into();
    let object_url = web_sys::Url::create_object_url_with_blob(&blob)
        .map_err(|_| "Could not create object URL for video".to_string())?;
    let document = web_sys::window()
        .and_then(|w| w.document())
        .ok_or_else(|| "Could not access browser document".to_string())?;
    let video: web_sys::HtmlVideoElement = document
        .create_element("video")
        .map_err(|_| "Could not create temporary video element".to_string())?
        .dyn_into()
        .map_err(|_| "Could not cast temporary video element".to_string())?;
    video.set_preload("metadata");
    video.set_src(&object_url);
    video.load();

    for _ in 0..50 {
        let duration = video.duration();
        if duration.is_finite() && duration > 0.0 {
            let _ = web_sys::Url::revoke_object_url(&object_url);
            return Ok(duration);
        }
        TimeoutFuture::new(100).await;
    }

    let _ = web_sys::Url::revoke_object_url(&object_url);
    Err("Could not read video duration from browser metadata.".to_string())
}

fn default_camera_name_from_filename(name: &str) -> String {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return "upload".to_string();
    }
    if let Some((stem, _)) = trimmed.rsplit_once('.') {
        if !stem.trim().is_empty() {
            return stem.trim().to_string();
        }
    }
    trimmed.to_string()
}

fn is_current_user_to(me: Option<&Result<User, String>>, to_entries: &[ToEntry]) -> bool {
    me.and_then(|r| r.as_ref().ok()).map_or(false, |u| {
        to_entries
            .iter()
            .any(|e| e.user_id == u.id && e.user_type == u.user_type)
    })
}

fn format_date(iso: &str) -> String {
    iso.split('T').next().unwrap_or(iso).to_string()
}

fn format_date_display(start: &str, end: Option<&String>) -> String {
    let start_fmt = format_date(start);
    match end {
        None => start_fmt,
        Some(e) if e.as_str() == start => start_fmt,
        Some(e) => format!("{} - {}", start_fmt, format_date(e)),
    }
}

fn parse_iso_utc(raw: &str) -> Option<DateTime<Utc>> {
    chrono::DateTime::parse_from_rfc3339(raw)
        .ok()
        .map(|dt| dt.with_timezone(&Utc))
}

fn format_duration_compact(seconds: f64) -> String {
    let total_ms = (seconds.max(0.0) * 1000.0).round() as i64;
    let mins = total_ms / 60_000;
    let secs = (total_ms % 60_000) as f64 / 1000.0;
    if mins > 0 {
        format!("{mins}m {secs:04.1}s")
    } else {
        format!("{secs:.1}s")
    }
}

fn preview_clips_for_upload(
    upload: &PendingUpload,
    matches: &[UserUploadPlanningMatch],
) -> Result<Vec<UploadPreviewClip>, String> {
    let start_world_raw = upload.start_world_value.trim().to_string();
    let start_world_raw = if start_world_raw.is_empty() {
        upload
            .start_world_suggested
            .clone()
            .ok_or_else(|| "Enter a start timestamp to preview clips.".to_string())?
    } else {
        start_world_raw
    };
    let video_start_world = parse_iso_utc(&start_world_raw)
        .ok_or_else(|| "Start timestamp is not valid RFC3339.".to_string())?;
    let duration_sec = upload
        .duration_sec
        .ok_or_else(|| "Video duration metadata is not available yet.".to_string())?;
    let video_end_world =
        video_start_world + chrono::TimeDelta::milliseconds((duration_sec * 1000.0) as i64);
    let padding = chrono::TimeDelta::seconds(3);

    let mut previews = Vec::new();
    for match_row in matches {
        for point in &match_row.points {
            let Some(point_start_world) = point.stamp.as_deref().and_then(parse_iso_utc) else {
                continue;
            };
            let point_end_world = point
                .end_stamp
                .as_deref()
                .and_then(parse_iso_utc)
                .unwrap_or(point_start_world);

            if point_start_world > video_end_world || point_end_world < video_start_world {
                continue;
            }

            let clip_start_world = std::cmp::max(point_start_world - padding, video_start_world);
            let clip_end_world = std::cmp::min(point_end_world + padding, video_end_world);
            let clip_start_file_sec =
                (clip_start_world - video_start_world).num_milliseconds() as f64 / 1000.0;
            let clip_duration_sec =
                (clip_end_world - clip_start_world).num_milliseconds() as f64 / 1000.0;
            if clip_duration_sec <= 0.0 {
                continue;
            }

            previews.push(UploadPreviewClip {
                label: format!("[{}] point {}", match_row.name, point.index),
                clip_start_file_sec,
                clip_duration_sec,
            });
        }
    }

    previews.sort_by(|a, b| {
        a.clip_start_file_sec
            .partial_cmp(&b.clip_start_file_sec)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    Ok(previews)
}

#[component]
pub fn TournamentHome(url: String) -> Element {
    rsx! {
        TournamentHomeContent { url, initial_tab: None::<String> }
    }
}

#[component]
pub fn TournamentHomeWithTab(url: String, tab: String) -> Element {
    rsx! {
        TournamentHomeContent { url, initial_tab: Some(tab) }
    }
}

#[component]
fn TournamentHomeContent(url: String, initial_tab: Option<String>) -> Element {
    let url_for_data = url.clone();
    let navigator = use_navigator();
    let mut refresh = use_signal(|| 0u32);
    let data = use_resource(move || {
        let _ = refresh();
        let u = url_for_data.clone();
        async move { api::tournament_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let me_res = use_resource(move || async move { api::me().await });
    let val = data.value();
    let backend = api::base_url();
    let mut delete_modal_open = use_signal(|| false);
    let mut delete_confirm_url = use_signal(|| String::new());
    let mut delete_error = use_signal(|| None::<String>);
    let mut show_edit_player_modal = use_signal(|| false);
    let mut show_edit_team_modal = use_signal(|| false);
    let mut show_league_edit_modal = use_signal(|| false);
    let url_for_delete_confirm = url.clone();
    let url_for_user_upload = url.clone();
    let mut about_markdown = use_signal(|| Option::<String>::None);
    let mut delete_redirect_league = use_signal(|| None as Option<String>);
    use_effect(move || {
        let v = val.read();
        if let Some(Ok(d)) = v.as_ref() {
            about_markdown.set(d.tournament.about.clone());
            delete_redirect_league.set(d.tournament.league.as_ref().map(|l| l.league_url.clone()));
        } else {
            about_markdown.set(None);
            delete_redirect_league.set(None);
        }
    });
    let about_html = use_resource(use_reactive(&about_markdown, move |md| {
        let md = md().clone();
        async move {
            match md.as_deref() {
                Some(m) if !m.is_empty() => api::render_markdown(m).await,
                _ => Ok(String::new()),
            }
        }
    }));

    // Upload footage UI state (hidden when not logged in).
    let mut pending_uploads = use_signal(|| Vec::<PendingUpload>::new());
    let mut upload_error = use_signal(|| None::<String>);
    let mut uploading = use_signal(|| false);
    let mut upload_metadata_loading = use_signal(|| false);
    // Per-row upload percent (0–100) while uploading; empty when idle.
    let mut upload_row_progress = use_signal(|| Vec::<Option<u32>>::new());
    // Shared display name for all files in this upload batch (one logical camera).
    let mut upload_batch_camera_name = use_signal(|| String::new());
    // Field for the whole batch (one camera sits on one field).
    let mut upload_batch_field_id = use_signal(|| None::<u32>);
    let mut upload_mode = use_signal(|| UploadMode::RawClips);
    let mut upload_match_uuid = use_signal(|| None::<String>);
    let mut upload_planning = use_signal(|| None::<UserUploadPlanningResponse>);
    let mut upload_planning_error = use_signal(|| None::<String>);
    let mut upload_planning_loading = use_signal(|| false);
    let mut upload_modal_open = use_signal(|| false);

    let url_for_fields = url.clone();
    let fields_res = use_resource(move || {
        let value = url_for_fields.clone();
        async move {
            api::tournament_fields(&value)
                .await
                .map_err(|e| e.to_string())
        }
    });

    let url_for_sidecomps = url.clone();
    let sidecomps = use_resource(move || {
        let u = url_for_sidecomps.clone();
        async move { api::sidecomps_list(&u).await }
    });

    let mut info_tab = use_signal(|| None::<String>);

    {
        let url_for_upload_planning = url.clone();
        let mut upload_planning = upload_planning;
        let mut upload_planning_error = upload_planning_error;
        let mut upload_planning_loading = upload_planning_loading;
        let mut upload_match_uuid = upload_match_uuid;
        use_effect(move || {
            let open = upload_modal_open();
            let field_id = upload_batch_field_id();
            if !open {
                upload_planning.set(None);
                upload_planning_error.set(None);
                upload_planning_loading.set(false);
                upload_match_uuid.set(None);
                return;
            }
            let Some(field_id) = field_id else {
                upload_planning.set(None);
                upload_planning_error.set(None);
                upload_planning_loading.set(false);
                upload_match_uuid.set(None);
                return;
            };

            upload_planning_loading.set(true);
            upload_planning_error.set(None);
            let planning_url = url_for_upload_planning.clone();
            spawn(async move {
                match api::user_upload_planning(&planning_url, field_id).await {
                    Ok(resp) => {
                        let matches = resp.matches.clone();
                        let keep_current = upload_match_uuid()
                            .as_ref()
                            .map(|selected| matches.iter().any(|m| m.uuid == *selected))
                            .unwrap_or(false);
                        if !keep_current {
                            upload_match_uuid.set(matches.first().map(|m| m.uuid.clone()));
                        }
                        upload_planning.set(Some(resp));
                        upload_planning_error.set(None);
                    }
                    Err(err) => {
                        upload_planning.set(None);
                        upload_planning_error.set(Some(err));
                        upload_match_uuid.set(None);
                    }
                }
                upload_planning_loading.set(false);
            });
        });
    }

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            {{
                let teams_count = d.teams_with_counts.len();
                let unattached_count = d.unattached_players.len();
                rsx! {
            EventHeader {
                title: d.tournament.name.clone(),
                subtitle: format!("{} • {}", d.tournament.location.as_deref().unwrap_or("Location TBA"), format_date_display(&d.tournament.start_date, d.tournament.end_date.as_ref())),
                badge_league_url: d.tournament.league.as_ref().map(|l| l.league_url.clone()),
                badge_season: None,
                badge_name: d.tournament.league.as_ref().map(|l| l.name.clone()),
            }

            div { class: "row mb-3",
                div { class: "col-12 d-flex flex-wrap gap-2",
                    if d.tournament.schedule_published {
                        Link { to: Route::Schedule { url: url.clone() }, class: "btn btn-primary", "Schedule" }
                    } else {
                        Link { to: Route::Schedule { url: url.clone() }, class: "btn btn-outline-secondary", "Schedule (Not Published)" }
                    }
                    Link { to: Route::Results { url: url.clone() }, class: "btn btn-outline-primary", "Results" }
                    if d.tournament.schedule_published || is_current_user_to(me_res.read().as_ref(), &d.to_entries) {
                        Link { to: Route::Bracket { url: url.clone() }, class: "btn btn-outline-primary", "Bracket" }
                    }
                    if d.manual_footage_uploads_enabled {
                        if let Some(current_user) = me_res.read().as_ref().and_then(|r| r.as_ref().ok()) {
                            if current_user.user_type == "player" && d.is_current_player_registered {
                                button {
                                    class: "btn btn-outline-secondary",
                                    onclick: move |_| {
                                        upload_modal_open.set(true);
                                        upload_error.set(None);
                                    },
                                    "Upload Footage"
                                }
                            } else {
                                button {
                                    class: "btn btn-outline-secondary disabled",
                                    disabled: true,
                                    title: "Only players registered for this tournament can upload footage.",
                                    "Upload Footage (registered players only)"
                                }
                            }
                        }
                    }
                    if let Some(ref l) = d.tournament.league {
                        LeagueRegistrationButtons {
                            league_url: l.league_url.clone(),
                            registration_open: l.registration_open,
                            team_registration_open: Some(l.team_registration_open),
                            player_registration_open: Some(l.player_registration_open),
                            current_user: me_res.read().as_ref().cloned(),
                            is_team_registered: d.is_current_team_registered,
                            is_player_registered: d.is_current_player_registered,
                            use_edit_modal: true,
                            on_edit_registration: move |_| show_league_edit_modal.set(true),
                            register_label: String::from("Register (league)"),
                        }
                    } else {
                        if let Some(current_user) = me_res.read().as_ref().and_then(|r| r.as_ref().ok()) {
                            if current_user.user_type == "team" {
                                if d.is_current_team_registered {
                                    a { href: "{backend}/{url}/invitations", class: "btn btn-outline-secondary", "Manage Roster" }
                                    button {
                                        class: "btn btn-outline-secondary",
                                        onclick: move |_| show_edit_team_modal.set(true),
                                        "Edit Registration"
                                    }
                                } else if d.tournament.team_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-success", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Team registration closed"
                                    }
                                }
                            } else if current_user.user_type == "player" {
                                if d.is_current_player_registered {
                                    button {
                                        class: "btn btn-outline-secondary",
                                        onclick: move |_| show_edit_player_modal.set(true),
                                        "Edit Registration"
                                    }
                                } else if d.tournament.player_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-success", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Player registration closed"
                                    }
                                }
                            } else {
                                if d.tournament.team_registration_open || d.tournament.player_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-success", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Registration closed"
                                    }
                                }
                            }
                        } else {
                            button {
                                r#type: "button",
                                class: "btn btn-secondary disabled",
                                disabled: true,
                                "Sign in to register"
                            }
                        }
                    }
                }
            }

            {
                let viewer_is_to = is_current_user_to(me_res.read().as_ref(), &d.to_entries);
                let active_info_tab = info_tab().unwrap_or_else(|| {
                    if initial_tab.as_deref() == Some("sidecomps") {
                        "sidecomps".to_string()
                    } else {
                        "info".to_string()
                    }
                });
                rsx! {
            div { class: "row",
                div { class: "col-md-8",
                    div { class: "card",
                        div { class: "card-header",
                            ul { class: "nav nav-tabs card-header-tabs",
                                li { class: "nav-item",
                                    a {
                                        class: if active_info_tab == "info" { "nav-link active" } else { "nav-link" },
                                        href: "#",
                                        onclick: move |evt| { evt.prevent_default(); info_tab.set(Some("info".to_string())); },
                                        "Tournament Information"
                                    }
                                }
                                li { class: "nav-item",
                                    a {
                                        class: if active_info_tab == "sidecomps" { "nav-link active" } else { "nav-link" },
                                        href: "#",
                                        onclick: move |evt| { evt.prevent_default(); info_tab.set(Some("sidecomps".to_string())); },
                                        "Side Competitions"
                                    }
                                }
                            }
                        }
                        div { class: "card-body",
                            if active_info_tab == "info" {
                                div { class: "row mb-3",
                                    div { class: "col-md-6",
                                        p { strong { "Start Date: " } "{format_date(&d.tournament.start_date)}" }
                                        p { strong { "End Date: " } "{d.tournament.end_date.as_ref().map(|e| format_date(e)).unwrap_or_else(|| \"TBA\".into())}" }
                                    }
                                    div { class: "col-md-6",
                                        if let Some(max) = d.tournament.n_max_teams {
                                            p { strong { "Max Teams: " } "{max}" }
                                        }
                                        if let Some(roster) = d.tournament.max_team_size_roster {
                                            p { strong { "Max Team Size (Roster): " } "{roster}" }
                                        }
                                        if let Some(field) = d.tournament.max_team_size_field {
                                            p { strong { "Max Team Size (Field): " } "{field}" }
                                        }
                                    }
                                }
                                if d.tournament.league.is_none() && {
                                    let ro = d.tournament.team_registration_open || d.tournament.player_registration_open;
                                    let tf = d.tournament.team_reg_fee.unwrap_or(0.0);
                                    let pf = d.tournament.player_reg_fee.unwrap_or(0.0);
                                    ro && (tf > 0.0 || pf > 0.0)
                                } {
                                    div { class: "alert alert-info mb-3",
                                        h6 { class: "mb-2", "Registration Fees" }
                                        {
                                            let tf = d.tournament.team_reg_fee.unwrap_or(0.0);
                                            let pf = d.tournament.player_reg_fee.unwrap_or(0.0);
                                            let tf_str = format!("${:.2}", tf);
                                            let pf_str = format!("${:.2}", pf);
                                            rsx! {
                                                if tf > 0.0 {
                                                    p { class: "mb-1", strong { "Team Registration: " } "{tf_str}" }
                                                }
                                                if pf > 0.0 {
                                                    p { class: "mb-0", strong { "Player Registration: " } "{pf_str}" }
                                                }
                                            }
                                        }
                                    }
                                }
                                if let Some(about) = &d.tournament.about {
                                    if !about.is_empty() {
                                        hr {}
                                        if let Some(Ok(html)) = about_html.value().read().as_ref() {
                                            if html.is_empty() {
                                                div { class: "markdown-content", style: "white-space: pre-wrap;", "{about}" }
                                            } else {
                                                div { dangerous_inner_html: "{html}" }
                                            }
                                        } else {
                                            div { class: "markdown-content", style: "white-space: pre-wrap;", "{about}" }
                                        }
                                    }
                                } else {
                                    p { class: "text-muted", "Tournament details coming soon!" }
                                }
                            } else {
                                if viewer_is_to {
                                    div { class: "mb-3 d-flex justify-content-end",
                                        Link {
                                            to: Route::SideCompNew { url: url.clone() },
                                            class: "btn btn-sm btn-outline-success",
                                            title: "Add side competition",
                                            i { class: "fas fa-plus" }
                                        }
                                    }
                                }
                                match sidecomps.read().as_ref() {
                                    Some(Ok(rows)) => {
                                        if rows.is_empty() {
                                            rsx! { p { class: "text-muted mb-0", "No side competitions for this tournament." } }
                                        } else {
                                            rsx! {
                                                ul { class: "list-group",
                                                    for row in rows.iter() {
                                                        {
                                                            let comp_id = row.id;
                                                            let row_name = row.name.clone();
                                                            let mut sidecomps_for_row = sidecomps;
                                                            rsx! {
                                                                li {
                                                                    key: "{comp_id}",
                                                                    class: "list-group-item d-flex justify-content-between align-items-center p-0",
                                                                    Link {
                                                                        to: Route::SideCompDetail { url: url.clone(), comp_id },
                                                                        class: "text-decoration-none text-reset p-3 flex-grow-1",
                                                                        div {
                                                                            strong { "{row.name}" }
                                                                            span { class: "badge bg-secondary ms-2", "{row.type_}" }
                                                                            if row.registration_open {
                                                                                span { class: "badge bg-success ms-2", "Open" }
                                                                            } else {
                                                                                span { class: "badge bg-secondary ms-2", "Closed" }
                                                                            }
                                                                            span { class: "text-muted ms-2", "({row.registrant_count} registered)" }
                                                                        }
                                                                    }
                                                                    if viewer_is_to {
                                                                        div { class: "d-flex gap-1 px-3",
                                                                            Link {
                                                                                to: Route::SideCompEdit { url: url.clone(), comp_id },
                                                                                class: "btn btn-sm btn-outline-secondary",
                                                                                title: "Edit",
                                                                                i { class: "fas fa-pen" }
                                                                            }
                                                                            button {
                                                                                class: "btn btn-sm btn-outline-danger",
                                                                                title: "Delete",
                                                                                onclick: move |_| {
                                                                                    let confirmed = web_sys::window()
                                                                                        .and_then(|w| w
                                                                                            .confirm_with_message(&format!(
                                                                                                "Delete '{}'? This cannot be undone.",
                                                                                                row_name
                                                                                            ))
                                                                                            .ok())
                                                                                        .unwrap_or(false);
                                                                                    if !confirmed {
                                                                                        return;
                                                                                    }
                                                                                    spawn(async move {
                                                                                        match api::sidecomp_delete(comp_id).await {
                                                                                            Ok(_) => {
                                                                                                sidecomps_for_row.restart();
                                                                                            }
                                                                                            Err(e) => {
                                                                                                web_sys::console::error_1(
                                                                                                    &format!("sidecomp_delete failed: {}", e).into(),
                                                                                                );
                                                                                            }
                                                                                        }
                                                                                    });
                                                                                },
                                                                                i { class: "fas fa-trash" }
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
                                    _ => rsx! {},
                                }
                            }
                        }
                    }
                    // Upload Footage is now a modal triggered from the button row.
                }
                                if is_current_user_to(me_res.read().as_ref(), &d.to_entries) {
                                    div { class: "col-md-4",
                                        div { class: "card",
                                            div { class: "card-header", h5 { class: "mb-0", "Admin" } }
                                            div { class: "card-body",
                                                div { class: "d-grid gap-2",
                                                    Link { to: Route::TournamentSettings { url: url.clone() }, class: "btn btn-outline-secondary", "Settings" }
                                                    Link { to: Route::Bracket { url: url.clone() }, class: "btn btn-outline-secondary", "Bracket Builder" }
                                                    if let Some(ref l) = d.tournament.league {
                                                        Link { to: Route::LeagueManage { league_url: l.league_url.clone() }, class: "btn btn-outline-warning", "Registration Management" }
                                                    } else {
                                                        Link { to: Route::Manage { url: url.clone() }, class: "btn btn-outline-warning", "Registration Management" }
                                                    }
                                                    Link {
                                                        to: Route::ManageUserUploads { url: url.clone() },
                                                        class: "btn btn-outline-secondary",
                                                        "Manage User Uploaded Videos"
                                                    }
                                    button {
                                        class: "btn btn-outline-danger",
                                        onclick: move |_| {
                                            delete_modal_open.set(true);
                                            delete_confirm_url.set(String::new());
                                            delete_error.set(None);
                                        },
                                        "Delete Tournament"
                                    }
                                }
                            }
                        }
                    }
                }
            }
                }
            }

            if !d.teams_with_counts.is_empty() {
                div { class: "row mt-4",
                    div { class: "col-12",
                        div { class: "card",
                            div { class: "card-header", h5 { class: "mb-0", "Registered Teams ({teams_count})" } }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table table-striped",
                                        thead {
                                            tr {
                                                th { "Team Name" }
                                                th { "Players" }
                                                th { "Registration Date" }
                                            }
                                        }
                                        tbody {
                                            for team in d.teams_with_counts.iter() {
                                                tr { key: "{team.team_id}",
                                                    td {
                                                        div { class: "d-flex align-items-center",
                                                            div { class: "flex-shrink-0 me-2",
                                                                if let Some(photo) = &team.profile_photo {
                                                                    img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle", style: "width: 40px; height: 40px; object-fit: cover;" }
                                                                } else {
                                                                    div { class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle", style: "width: 40px; height: 40px;",
                                                                        span { class: "text-white", "👥" }
                                                                    }
                                                                }
                                                            }
                                                            div {
                                                                Link { to: Route::TeamProfilePage { id: team.team_id.clone() }, class: "text-decoration-none",
                                                                    strong { "{team.pseudonym.as_deref().unwrap_or(&team.team_name)}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    td {
                                                        span { class: "badge bg-primary", "{team.player_count}" }
                                                        if let Some(max) = d.tournament.max_team_size_roster {
                                                            span { " / {max}" }
                                                        }
                                                    }
                                                    td { "{team.registered_at.as_deref().unwrap_or(\"-\")}" }
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

            if !d.unattached_players.is_empty() {
                div { class: "row mt-4",
                    div { class: "col-12",
                        div { class: "card",
                            div { class: "card-header", h5 { class: "mb-0", "Unattached Players ({unattached_count})" } }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table table-striped",
                                        thead {
                                            tr {
                                                th { "Player Name" }
                                                th { "Jersey" }
                                                th { "Registration Date" }
                                            }
                                        }
                                        tbody {
                                            for p in d.unattached_players.iter() {
                                                tr { key: "{p.player_id}",
                                                    td {
                                                        div { class: "d-flex align-items-center",
                                                            div { class: "flex-shrink-0 me-2",
                                                                if let Some(photo) = &p.profile_photo {
                                                                    img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle", style: "width: 40px; height: 40px; object-fit: cover;" }
                                                                } else {
                                                                    div { class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle", style: "width: 40px; height: 40px;",
                                                                        span { class: "text-white", "👤" }
                                                                    }
                                                                }
                                                            }
                                                            div {
                                                                Link { to: Route::PlayerProfilePage { id: p.player_id.clone() }, class: "text-decoration-none",
                                                                    strong { "{p.player_name}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    td {
                                                        if let (Some(num), Some(name)) = (&p.jersey_number, &p.jersey_name) {
                                                            "#{num} {name}"
                                                        } else if let Some(name) = &p.jersey_name {
                                                            "{name}"
                                                        } else if let Some(num) = &p.jersey_number {
                                                            "#{num}"
                                                        } else {
                                                            span { class: "text-muted", "No jersey info" }
                                                        }
                                                    }
                                                    td { "{p.registered_at.as_deref().unwrap_or(\"-\")}" }
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


            if show_edit_player_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::Tournament { tournament_url: url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_player_modal.set(false),
                        on_success: move |_| {
                            show_edit_player_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }

            if show_edit_team_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::Tournament { tournament_url: url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_team_modal.set(false),
                        on_success: move |_| {
                            show_edit_team_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }

            if show_league_edit_modal() {
                if let Some(ref l) = d.tournament.league {
                    if let Some(Ok(me)) = me_res.read().as_ref() {
                        EditRegistrationModal {
                            context: EditRegistrationContext::League { league_url: l.league_url.clone() },
                            user_type: me.user_type.clone(),
                            on_close: move |_| show_league_edit_modal.set(false),
                            on_success: move |_| {
                                show_league_edit_modal.set(false);
                                refresh.set(refresh() + 1);
                            },
                        }
                    }
                }
            }

            if d.manual_footage_uploads_enabled && upload_modal_open() {
                div {
                    class: "modal show d-block",
                    style: "background: rgba(0,0,0,0.5);",
                    tabindex: "-1",
                    role: "dialog",
                    onclick: move |_| upload_modal_open.set(false),
                    div {
                        class: "modal-dialog modal-dialog-centered modal-lg",
                        onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Upload Footage" }
                                button {
                                    r#type: "button",
                                    class: "btn-close",
                                    aria_label: "Close",
                                    onclick: move |_| upload_modal_open.set(false),
                                }
                            }
                            div { class: "modal-body",
                                if let Some(err) = upload_error() {
                                    div { class: "alert alert-danger mb-3", "{err}" }
                                }
                                if let Some(Ok(fields)) = fields_res.read().as_ref() {
                                    if fields.is_empty() {
                                        p { class: "text-muted", "No fields configured for this tournament." }
                                    } else {
                                        {{
                                            let default_field_id = fields[0].id;
                                            rsx! {
                                                div { class: "row g-2 mb-3",
                                                    div { class: "col-md-6",
                                                        label { class: "form-label small mb-1", "Upload mode" }
                                                        div { class: "d-flex flex-wrap gap-3",
                                                            label { class: "form-check-label d-flex align-items-center gap-2",
                                                                input {
                                                                    class: "form-check-input",
                                                                    r#type: "radio",
                                                                    name: "upload_mode",
                                                                    checked: upload_mode() == UploadMode::RawClips,
                                                                    disabled: uploading() || upload_metadata_loading(),
                                                                    onchange: move |_| {
                                                                        upload_mode.set(UploadMode::RawClips);
                                                                        upload_error.set(None);
                                                                    }
                                                                }
                                                                span { "Generate point clips" }
                                                            }
                                                            label { class: "form-check-label d-flex align-items-center gap-2",
                                                                input {
                                                                    class: "form-check-input",
                                                                    r#type: "radio",
                                                                    name: "upload_mode",
                                                                    checked: upload_mode() == UploadMode::EditedMatch,
                                                                    disabled: uploading() || upload_metadata_loading(),
                                                                    onchange: move |_| {
                                                                        upload_mode.set(UploadMode::EditedMatch);
                                                                        upload_error.set(None);
                                                                    }
                                                                }
                                                                span { "Upload edited match video" }
                                                            }
                                                        }
                                                    }
                                                }
                                                if upload_mode() == UploadMode::RawClips {
                                                    p { class: "text-muted mb-2",
                                                        "All videos are treated as one camera on the field you choose below. They may not overlap and may not have pauses. Their start timestamp is used to detect which points are recorded, and Arctos will clip the video to these points and upload them to YouTube. If the detected timestamp is wrong, you may manually override it."
                                                        br {}
                                                        "If you have footage of multiple fields, upload each field separately (ie, upload all the footage from one field first, submit it, and only then upload the next field)"
                                                    }
                                                    p { class: "text-muted mb-2 small",
                                                        "Your upload will need some processing before it is ready and visible. If you want a status update, check in with a TO; they can see the status of your upload."
                                                    }
                                                } else {
                                                    p { class: "text-muted mb-2",
                                                        "Edited uploads go straight to one selected match page and YouTube. No point timestamps or point seek data are generated for these videos."
                                                    }
                                                }
                                                input {
                                                    class: "form-control",
                                                    r#type: "file",
                                                    accept: "video/*",
                                                    multiple: true,
                                                    disabled: uploading() || upload_metadata_loading(),
                                                    onchange: move |evt| {
                                                        #[cfg(target_arch = "wasm32")]
                                                        {
                                                            let files = evt.files();
                                                            if files.is_empty() {
                                                                return;
                                                            }
                                                            upload_metadata_loading.set(true);
                                                            let mut pending_uploads = pending_uploads;
                                                            let mut upload_batch_camera_name = upload_batch_camera_name;
                                                            let mut upload_batch_field_id = upload_batch_field_id;
                                                            let mut upload_error = upload_error;
                                                            let mut upload_metadata_loading = upload_metadata_loading;
                                                            spawn(async move {
                                                                let mut items: Vec<PendingUpload> = Vec::new();
                                                                let mut first_filename: Option<String> = None;
                                                                for f in files {
                                                                    let guessed_start = infer_start_world_from_file(&f);
                                                                    let filename = f.name();
                                                                    let duration_res = load_video_duration_sec(&f).await;
                                                                    if first_filename.is_none() {
                                                                        first_filename = Some(filename.clone());
                                                                    }
                                                                    let (duration_sec, metadata_error) = match duration_res {
                                                                        Ok(duration) => (Some(duration), None),
                                                                        Err(err) => (None, Some(err)),
                                                                    };
                                                                    items.push(PendingUpload {
                                                                        filename,
                                                                        file: f,
                                                                        start_world_suggested: guessed_start.clone(),
                                                                        start_world_value: String::new(),
                                                                        start_world_error: None,
                                                                        duration_sec,
                                                                        metadata_error,
                                                                    });
                                                                }
                                                                if upload_batch_field_id().is_none() {
                                                                    upload_batch_field_id.set(Some(default_field_id));
                                                                }
                                                                if upload_batch_camera_name().trim().is_empty() {
                                                                    if let Some(ref fnm) = first_filename {
                                                                        upload_batch_camera_name.set(default_camera_name_from_filename(fnm));
                                                                    }
                                                                }
                                                                let mut existing = pending_uploads();
                                                                existing.extend(items);
                                                                pending_uploads.set(existing);
                                                                upload_error.set(None);
                                                                upload_metadata_loading.set(false);
                                                            });
                                                        }
                                                    }
                                                }
                                                if upload_metadata_loading() {
                                                    p { class: "text-muted small mt-2 mb-0", "Reading video metadata..." }
                                                }

                                                if !pending_uploads().is_empty() {
                                                    div { class: "row g-2 mt-3 mb-2",
                                                        div { class: "col-md-6",
                                                            label { class: "form-label small mb-0", "Camera name" }
                                                            input {
                                                                class: "form-control form-control-sm",
                                                                r#type: "text",
                                                                placeholder: "e.g. house camera",
                                                                value: "{upload_batch_camera_name()}",
                                                                disabled: uploading(),
                                                                oninput: move |e| {
                                                                    upload_batch_camera_name.set(e.value());
                                                                }
                                                            }
                                                            p { class: "text-muted small mt-1 mb-0",
                                                                "Used for every match highlight from this upload."
                                                                if upload_mode() == UploadMode::EditedMatch {
                                                                    " Used as the camera name on the selected match page."
                                                                }
                                                            }
                                                        }
                                                        div { class: "col-md-6",
                                                            label { class: "form-label small mb-0", "Field" }
                                                            select {
                                                                class: "form-select form-select-sm",
                                                                value: "{upload_batch_field_id().unwrap_or(default_field_id)}",
                                                                disabled: uploading(),
                                                                onchange: move |ev| {
                                                                    let v = ev.value().parse::<u32>().unwrap_or(default_field_id);
                                                                    upload_batch_field_id.set(Some(v));
                                                                },
                                                                for f in fields.iter() {
                                                                    option { value: "{f.id}", "{f.name}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    if upload_mode() == UploadMode::EditedMatch {
                                                        div { class: "mb-3",
                                                            label { class: "form-label small mb-0", "Target match" }
                                                            select {
                                                                class: "form-select form-select-sm",
                                                                disabled: uploading() || upload_planning_loading(),
                                                                value: "{upload_match_uuid().unwrap_or_default()}",
                                                                onchange: move |ev| {
                                                                    let value = ev.value();
                                                                    upload_match_uuid.set(if value.trim().is_empty() {
                                                                        None
                                                                    } else {
                                                                        Some(value)
                                                                    });
                                                                },
                                                                if let Some(plan) = upload_planning() {
                                                                    for match_row in plan.matches.iter() {
                                                                        option { value: "{match_row.uuid}", "{match_row.name}" }
                                                                    }
                                                                } else {
                                                                    option { value: "", "Select a field first" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    if upload_planning_loading() {
                                                        div { class: "text-muted small mb-2", "Loading field matches and points..." }
                                                    } else if let Some(plan_err) = upload_planning_error() {
                                                        div { class: "alert alert-warning py-2 mb-2", "{plan_err}" }
                                                    }
                                                    div { class: "mt-1 table-responsive",
                                                        table { class: "table table-sm align-middle",
                                                            thead {
                                                                tr {
                                                                    th { "File" }
                                                                    th { "Progress" }
                                                                    th { "Detected metadata" }
                                                                    th {
                                                                        if upload_mode() == UploadMode::RawClips {
                                                                            "Start timestamp override (ISO with timezone)"
                                                                        } else {
                                                                            "Upload target"
                                                                        }
                                                                    }
                                                                    th { "Clip preview" }
                                                                    th { "" }
                                                                }
                                                            }
                                                            tbody {
                                                                for (idx, item) in pending_uploads().iter().enumerate() {
                                                                    tr { key: "{item.filename}-{idx}",
                                                                        td { "{item.filename}" }
                                                                        td {
                                                                            if uploading() {
                                                                                if let Some(p) = upload_row_progress().get(idx).copied().flatten() {
                                                                                    div { class: "progress",
                                                                                        style: "min-width: 6rem; height: 1.1rem;",
                                                                                        div {
                                                                                            class: "progress-bar",
                                                                                            role: "progressbar",
                                                                                            style: "width: {p}%",
                                                                                            "{p}%"
                                                                                        }
                                                                                    }
                                                                                } else {
                                                                                    span { class: "text-muted small", "—" }
                                                                                }
                                                                            } else {
                                                                                span { class: "text-muted small", "—" }
                                                                            }
                                                                        }
                                                                        td {
                                                                            if let Some(duration_sec) = item.duration_sec {
                                                                                div { class: "small", "Duration: {format_duration_compact(duration_sec)}" }
                                                                            } else {
                                                                                div { class: "text-muted small", "Duration unavailable" }
                                                                            }
                                                                            if let Some(s) = &item.start_world_suggested {
                                                                                div { class: "text-muted small mt-1", "Detected file timestamp: {s}" }
                                                                            } else {
                                                                                div { class: "text-muted small mt-1", "No browser timestamp was available for this file." }
                                                                            }
                                                                            if let Some(err) = &item.metadata_error {
                                                                                div { class: "text-danger small mt-1", "{err}" }
                                                                            }
                                                                        }
                                                                        td {
                                                                            if upload_mode() == UploadMode::RawClips {
                                                                                div { class: "d-flex gap-2 align-items-center",
                                                                                    input {
                                                                                        class: "form-control form-control-sm",
                                                                                        r#type: "text",
                                                                                        placeholder: "2026-03-18T01:23:45Z",
                                                                                        value: "{item.start_world_value}",
                                                                                        disabled: uploading(),
                                                                                        oninput: move |e| {
                                                                                            let mut list = pending_uploads();
                                                                                            if let Some(t) = list.get_mut(idx) {
                                                                                                t.start_world_value = e.value();
                                                                                                t.start_world_error = None;
                                                                                            }
                                                                                            pending_uploads.set(list);
                                                                                        }
                                                                                    }
                                                                                    button {
                                                                                        class: "btn btn-sm btn-outline-secondary",
                                                                                        disabled: uploading(),
                                                                                        onclick: move |_| {
                                                                                            let mut list = pending_uploads();
                                                                                            if let Some(t) = list.get_mut(idx) {
                                                                                                t.start_world_value = t
                                                                                                    .start_world_suggested
                                                                                                    .clone()
                                                                                                    .unwrap_or_default();
                                                                                                t.start_world_error = None;
                                                                                            }
                                                                                            pending_uploads.set(list);
                                                                                        },
                                                                                        "Reset"
                                                                                    }
                                                                                }
                                                                                if let Some(err) = &item.start_world_error {
                                                                                    div { class: "text-danger small mt-1", "{err}" }
                                                                                } else {
                                                                                    div { class: "text-muted small mt-1", "Leave blank to keep the detected file timestamp. Enter an override with timezone if needed." }
                                                                                }
                                                                            } else if let Some(plan) = upload_planning() {
                                                                                if let Some(selected_match_uuid) = upload_match_uuid() {
                                                                                    if let Some(match_row) = plan.matches.iter().find(|m| m.uuid == selected_match_uuid) {
                                                                                        div { class: "small", "{match_row.name}" }
                                                                                        div { class: "text-muted small mt-1", "Direct match upload with no point timestamps." }
                                                                                    } else {
                                                                                        div { class: "text-muted small", "Select a match." }
                                                                                    }
                                                                                } else {
                                                                                    div { class: "text-muted small", "Select a match." }
                                                                                }
                                                                            } else {
                                                                                div { class: "text-muted small", "Select a field to load matches." }
                                                                            }
                                                                        }
                                                                        td {
                                                                            if upload_mode() == UploadMode::RawClips {
                                                                                if let Some(plan) = upload_planning() {
                                                                                    {{
                                                                                        let preview_result = preview_clips_for_upload(item, &plan.matches);
                                                                                        rsx! {
                                                                                            match preview_result {
                                                                                                Ok(previews) if previews.is_empty() => rsx! {
                                                                                                    div { class: "text-muted small", "No points overlap this file." }
                                                                                                },
                                                                                                Ok(previews) => rsx! {
                                                                                                    ul { class: "small mb-0 ps-3",
                                                                                                        for preview in previews.iter() {
                                                                                                            li {
                                                                                                                "{preview.label} "
                                                                                                                span {
                                                                                                                    class: "text-muted",
                                                                                                                    {format!(
                                                                                                                        "({} in, {} long)",
                                                                                                                        format_duration_compact(preview.clip_start_file_sec),
                                                                                                                        format_duration_compact(preview.clip_duration_sec),
                                                                                                                    )}
                                                                                                                }
                                                                                                            }
                                                                                                        }
                                                                                                    }
                                                                                                },
                                                                                                Err(err) => rsx! {
                                                                                                    div { class: "text-muted small", "{err}" }
                                                                                                },
                                                                                            }
                                                                                        }
                                                                                    }}
                                                                                } else if upload_planning_loading() {
                                                                                    div { class: "text-muted small", "Loading field points..." }
                                                                                } else {
                                                                                    div { class: "text-muted small", "Select a field to preview clips." }
                                                                                }
                                                                            } else {
                                                                                div { class: "text-muted small", "This video uploads as-is with no point timing metadata." }
                                                                            }
                                                                        }
                                                                        td {
                                                                            div { class: "d-flex gap-2 align-items-center",
                                                                                button {
                                                                                    class: "btn btn-sm btn-outline-danger",
                                                                                    disabled: uploading(),
                                                                                    onclick: move |_| {
                                                                                        let mut list = pending_uploads();
                                                                                        if idx < list.len() {
                                                                                            list.remove(idx);
                                                                                        }
                                                                                        pending_uploads.set(list);
                                                                                    },
                                                                                    "Remove"
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
                                        }}
                                    }
                                } else {
                                    p { class: "text-muted", "Loading fields..." }
                                }
                            }
                            div { class: "modal-footer",
                                button {
                                    class: "btn btn-secondary",
                                    onclick: move |_| upload_modal_open.set(false),
                                    "Close"
                                }
                                {{
                                    let upload_url_submit = url_for_user_upload.clone();
                                    rsx! {
                                        button {
                                            class: "btn btn-primary",
                                            disabled: uploading()
                                                || upload_metadata_loading()
                                                || pending_uploads().is_empty()
                                                || upload_batch_camera_name().trim().is_empty()
                                                || upload_batch_field_id().is_none()
                                                || (upload_mode() == UploadMode::EditedMatch
                                                    && upload_match_uuid().is_none()),
                                            onclick: move |_| {
                                                #[cfg(target_arch = "wasm32")]
                                                {
                                                    let url = upload_url_submit.clone();
                                                    let uploads = pending_uploads();
                                                    if uploads.is_empty() {
                                                        return;
                                                    }
                                                    let camera = upload_batch_camera_name().trim().to_string();
                                                    if camera.is_empty() {
                                                        upload_error.set(Some(
                                                            "Enter a camera name.".into(),
                                                        ));
                                                        return;
                                                    }
                                                    let mode = upload_mode();
                                                    let Some(field_id) = upload_batch_field_id() else {
                                                        upload_error.set(Some("Select a field.".into()));
                                                        return;
                                                    };
                                                    let selected_match_uuid = upload_match_uuid();
                                                    if mode == UploadMode::EditedMatch && selected_match_uuid.is_none() {
                                                        upload_error.set(Some("Select a target match.".into()));
                                                        return;
                                                    }
                                                    let mut validated_uploads = uploads.clone();
                                                    if mode == UploadMode::RawClips {
                                                        let mut has_invalid_start_world = false;
                                                        for item in validated_uploads.iter_mut() {
                                                            match validate_upload_start_world(&item.start_world_value) {
                                                                Ok(()) => item.start_world_error = None,
                                                                Err(err) => {
                                                                    item.start_world_error = Some(err);
                                                                    has_invalid_start_world = true;
                                                                }
                                                            }
                                                        }
                                                        if has_invalid_start_world {
                                                            pending_uploads.set(validated_uploads);
                                                            upload_error.set(Some(
                                                                "Fix the highlighted start timestamps before uploading.".into(),
                                                            ));
                                                            return;
                                                        }
                                                    }
                                                    let n = uploads.len();
                                                    let batch_id = format!(
                                                        "b{}",
                                                        Uuid::new_v4().to_string().replace('-', "")
                                                    );
                                                    let progress_sig = upload_row_progress.clone();
                                                    uploading.set(true);
                                                    upload_error.set(None);
                                                    upload_row_progress.set(vec![Some(0); n]);
                                                    spawn(async move {
                                                        let mut first_err: Option<String> = None;
                                                        for (file_idx, u) in uploads.into_iter().enumerate() {
                                                            let start_world = if mode == UploadMode::RawClips {
                                                                let candidate = if !u.start_world_value.trim().is_empty() {
                                                                    Some(u.start_world_value.clone())
                                                                } else {
                                                                    u.start_world_suggested.clone()
                                                                };
                                                                candidate.filter(|s| !s.trim().is_empty())
                                                            } else {
                                                                None
                                                            };
                                                            let mut progress_sig = progress_sig.clone();
                                                            if let Err(e) = api::user_upload_video_footage_with_progress(
                                                                &url,
                                                                mode.as_api_str(),
                                                                if mode == UploadMode::RawClips {
                                                                    Some(field_id)
                                                                } else {
                                                                    None
                                                                },
                                                                selected_match_uuid.as_deref(),
                                                                u.file,
                                                                start_world,
                                                                Some(camera.clone()),
                                                                batch_id.as_str(),
                                                                file_idx as u32,
                                                                n as u32,
                                                                move |sent, total| {
                                                                    let pct = if total > 0 {
                                                                        ((sent as u128 * 100) / total as u128) as u32
                                                                    } else {
                                                                        0
                                                                    };
                                                                    let mut v = progress_sig();
                                                                    if file_idx < v.len() {
                                                                        v[file_idx] = Some(pct.min(100));
                                                                        progress_sig.set(v);
                                                                    }
                                                                },
                                                            )
                                                            .await
                                                            {
                                                                if first_err.is_none() {
                                                                    first_err = Some(e);
                                                                }
                                                            }
                                                        }
                                                        uploading.set(false);
                                                        if let Some(e) = first_err {
                                                            upload_error.set(Some(e));
                                                        } else {
                                                            upload_row_progress.set(Vec::new());
                                                            pending_uploads.set(Vec::new());
                                                            upload_batch_camera_name.set(String::new());
                                                            upload_batch_field_id.set(None);
                                                            upload_match_uuid.set(None);
                                                            upload_mode.set(UploadMode::RawClips);
                                                            upload_modal_open.set(false);
                                                        }
                                                    });
                                                }
                                            },
                                            if uploading() { "Uploading..." } else { "Upload" }
                                        }
                                    }
                                }}
                            }
                        }
                    }
                }
            }

            if is_current_user_to(me_res.read().as_ref(), &d.to_entries) && delete_modal_open() {
                div { class: "modal d-block", tabindex: -1, style: "background: rgba(0,0,0,0.5)",
                    div { class: "modal-dialog modal-dialog-centered",
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Delete Tournament" }
                                button { class: "btn-close", onclick: move |_| delete_modal_open.set(false) }
                            }
                            div { class: "modal-body",
                                if let Some(ref err) = delete_error() {
                                    div { class: "alert alert-danger mb-3", "{err}" }
                                }
                                div { class: "alert alert-danger",
                                    strong { "Warning: " }
                                    "This action cannot be undone. All matches, registrations, and data will be permanently removed."
                                }
                                p { "To confirm, type the tournament URL exactly:" }
                                p { class: "text-center mb-2", strong { "{url}" } }
                                form {
                                    id: "delete-tournament-form",
                                    onsubmit: move |ev| {
                                        ev.prevent_default();
                                        if delete_confirm_url() != url_for_delete_confirm {
                                            return;
                                        }
                                        delete_error.set(None);
                                        let nav = navigator.clone();
                                        let url_submit = url_for_delete_confirm.clone();
                                        let confirm = delete_confirm_url();
                                        let redirect_league = delete_redirect_league();
                                        spawn(async move {
                                            match api::delete_tournament(&url_submit, &confirm).await {
                                                Ok(res) if res.success => {
                                                    if let Some(lu) = redirect_league {
                                                        let _ = nav.push(Route::LeagueHome { league_url: lu });
                                                    } else {
                                                        let _ = nav.push(Route::Index {});
                                                    }
                                                }
                                                Ok(res) => {
                                                    delete_error.set(Some(res.error.unwrap_or_else(|| "Delete failed.".to_string())));
                                                }
                                                Err(e) => {
                                                    delete_error.set(Some(e));
                                                }
                                            }
                                        });
                                    },
                                    div { class: "mb-3",
                                        label { class: "form-label", "Tournament URL:" }
                                        input {
                                            class: "form-control",
                                            name: "confirm_url",
                                            "type": "text",
                                            placeholder: "{url}",
                                            value: "{delete_confirm_url()}",
                                            oninput: move |e| delete_confirm_url.set(e.value()),
                                        }
                                    }
                                }
                            }
                            div { class: "modal-footer",
                                button { class: "btn btn-secondary", onclick: move |_| delete_modal_open.set(false), "Cancel" }
                                button {
                                    class: "btn btn-danger",
                                    "type": "submit",
                                    form: "delete-tournament-form",
                                    disabled: delete_confirm_url() != url,
                                    "Delete Tournament"
                                }
                            }
                        }
                    }
                }
            }
            }
            }}
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { class: "text-muted", "Loading…" }
        }
    }
}
