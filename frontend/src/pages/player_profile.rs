use crate::api;
use crate::components::{EventTab, EventTabs, Modal, PenaltyDisplay, ProfileHeader, ProfileBio};
use crate::components::{PaymentStatusBadge, RecordTimestamp, RegistrationStatusBadge, WaiverStatusBadge};
use crate::types::{NoteMatchInfo, PlayerInjury, PlayerNoteItem};
use crate::Route;
use dioxus::prelude::*;
use serde_json::json;

#[derive(Clone, Debug, PartialEq)]
struct EditableInjury {
    id: Option<u32>,
    message: String,
    date: String,
    active: bool,
    show: bool,
    original_message: String,
    original_date: String,
    original_active: bool,
    original_show: bool,
    editing: bool,
    is_new: bool,
}

fn stamp_to_date(stamp: &Option<String>) -> String {
    stamp
        .as_deref()
        .map(|s| s.chars().take(10).collect())
        .unwrap_or_default()
}

/// Format penalty row like run match: colored bar, type name as text when present else note text or "Other".
fn penalty_display_parts(note: &PlayerNoteItem) -> (String, String) {
    let border_color = note
        .penalty_type_color
        .as_deref()
        .map(|c| c.trim_start_matches('#').to_string())
        .unwrap_or_else(|| "808080".to_string());
    let display_text = note
        .penalty_type_name
        .clone()
        .unwrap_or_else(|| {
            if note.text.is_empty() {
                "Other".to_string()
            } else {
                note.text.clone()
            }
        });
    (border_color, display_text)
}

struct PenaltyRow {
    created_at: Option<String>,
    border_color: String,
    display_text: String,
    display_desc: Option<String>,
    point_index: String,
    match_info: Option<NoteMatchInfo>,
    key: String,
}

fn editable_injury_from_api(inj: &PlayerInjury) -> EditableInjury {
    let date = stamp_to_date(&inj.stamp);
    EditableInjury {
        id: Some(inj.id),
        message: inj.message.clone(),
        date: date.clone(),
        active: inj.active,
        show: inj.show,
        original_message: inj.message.clone(),
        original_date: date,
        original_active: inj.active,
        original_show: inj.show,
        editing: false,
        is_new: false,
    }
}

/// Page component for the router. Reads id from use_route() so navigation
/// between profiles updates the view (router reuses the same component and
/// does not pass new props).
#[component]
pub fn PlayerProfilePage(id: String) -> Element {
    let route = use_route::<Route>();
    let (id, tab) = match &route {
        Route::PlayerProfilePage { id } => (id.clone(), "info".to_string()),
        Route::PlayerProfilePageWithTab { id, tab } => (id.clone(), tab.clone()),
        _ => return rsx! { div { class: "alert alert-danger", "Invalid route" } },
    };
    let mut id_signal = use_signal(|| id.clone());
    if *id_signal.peek() != id { id_signal.set(id.clone()); }
    rsx! {
        PlayerProfile { key: "{id}", id: id_signal, tab }
    }
}

#[component]
pub fn PlayerProfilePageWithTab(id: String, tab: String) -> Element {
    rsx! { PlayerProfilePage { id } }
}

#[component]
pub fn PlayerProfile(id: Signal<String>, tab: String) -> Element {
    let data = use_resource(use_reactive(&id, move |sid| {
        let i = sid().clone();
        async move { api::player_profile(&i).await.map_err(|e| e.to_string()) }
    }));
    let mut injuries_state = use_signal(|| Vec::<EditableInjury>::new());
    let mut injuries_initialized_for = use_signal(|| None::<String>);
    let me = crate::auth::use_account();
    let val = data.value();
    let mut injury_error = use_signal(|| None::<String>);
    let mut injury_saving = use_signal(|| false);
    let mut penalty_desc_modal = use_signal(|| None::<String>);

    use_effect(move || {
        if let Some(Ok(d)) = val.read().as_ref() {
            let player_id = d.player.id.clone();
            if injuries_initialized_for().as_deref() != Some(player_id.as_str()) {
                let list = d
                    .injuries
                    .iter()
                    .map(editable_injury_from_api)
                    .collect::<Vec<_>>();
                injuries_state.set(list);
                injuries_initialized_for.set(Some(player_id));
            }
        }
    });
    if let Some(Ok(d)) = val.read().as_ref() {
        let can_edit_injuries = if let Some(Ok(u)) = me.read().as_ref() {
            u.user_type == "player" && u.id == d.player.id
        } else {
            false
        };
        let can_edit_profile = can_edit_injuries;
        let player_id = d.player.id.clone();
        let (injury_total, active_injuries) = if can_edit_injuries
            && injuries_initialized_for().as_deref() == Some(player_id.as_str())
        {
            let injuries = injuries_state.read();
            (injuries.len(), injuries.iter().filter(|injury| injury.active).count())
        } else {
            (d.injuries.len(), d.injuries.iter().filter(|injury| injury.active).count())
        };
        let penalty_rows: Vec<PenaltyRow> = d.player_notes.iter().map(|note| {
            let (border_color, display_text) = penalty_display_parts(note);
            PenaltyRow {
                key: format!("{}-{}", note.created_at.as_deref().unwrap_or("-"), note.point_index),
                created_at: note.created_at.clone(),
                border_color,
                display_text,
                display_desc: note.penalty_type_desc.clone().filter(|s| !s.is_empty()),
                point_index: note.point_index.clone(),
                match_info: note.match_info.clone(),
            }
        }).collect();
        let penalty_rows_empty = penalty_rows.is_empty();
        let show_injuries = can_edit_injuries || !d.injuries.is_empty();
        let show_infractions = can_edit_profile || !penalty_rows_empty;
        let active_tab = match tab.as_str() {
            "history" => "history",
            "injuries" if show_injuries => "injuries",
            "infractions" if show_infractions => "infractions",
            _ => "info",
        };
        let mut tabs = vec![
            EventTab { id: "info".into(), label: "Information".into(), icon: "fas fa-info-circle".into(), href: Some(format!("/players/{player_id}?tab=info")) },
            EventTab { id: "history".into(), label: format!("History ({})", d.registrations.len()), icon: "fas fa-history".into(), href: Some(format!("/players/{player_id}?tab=history")) },
        ];
        if show_infractions {
            tabs.push(EventTab { id: "infractions".into(), label: format!("Infractions ({})", penalty_rows.len()), icon: "fas fa-flag".into(), href: Some(format!("/players/{player_id}?tab=infractions")) });
        }
        if show_injuries {
            tabs.push(EventTab { id: "injuries".into(), label: format!("Injuries ({injury_total})"), icon: "fas fa-notes-medical".into(), href: Some(format!("/players/{player_id}?tab=injuries")) });
        }
        let penalty_row_elements: Vec<_> = penalty_rows
            .into_iter()
            .map(|row| {
                let key = row.key.clone();
                let created_at = row.created_at.clone();
                let border_color = row.border_color.clone();
                let display_text = row.display_text.clone();
                let display_desc = row.display_desc.clone();
                let point_index = row.point_index.clone();
                let match_info = row.match_info.clone();
                rsx! {
                    tr { key: "{key}",
                        td { class: "table-nowrap", RecordTimestamp { value: created_at } }
                        td { class: "table-copy",
                            PenaltyDisplay {
                                border_color,
                                display_text,
                                description: display_desc,
                                target_display: None,
                                target_profile_id: None,
                                on_description_click: move |desc: Option<String>| penalty_desc_modal.set(desc),
                            }
                        }
                        td { class: "table-number", "{point_index}" }
                        td { class: "table-copy",
                            if let Some(ref match_info) = match_info {
                                a { href: "/{match_info.event}/match/{match_info.uuid}", "{match_info.name}" }
                            } else {
                                "-"
                            }
                        }
                    }
                }
            })
            .collect();
        return rsx! {
            div { class: "profile-page",
            ProfileHeader {
                account_id: d.player.id.clone(), kind: "player".to_string(), name: d.player.name.clone(),
                avatar: d.player.profile_photo.clone(), banner: d.player.banner_photo.clone(), location: d.player.location.clone(),
                pronouns: d.player.pronouns.clone(),
                pompfen: d.player.pompfen.clone(),
                past_count: d.past_event_count,
                edit_route: can_edit_profile.then(|| Route::EditPlayerProfile { player_id: d.player.id.clone() }),
            }
            EventTabs { prefix: "player".to_string(), label: "Player sections".to_string(), active: active_tab.to_string(), tabs }
            div { class: "profile-detail-content",
                    section { id: "player-section-info", class: "profile-section", hidden: active_tab != "info", aria_labelledby: "player-tab-info",
                        div { class: "profile-section-body",
                            if let Some(phone) = d.player.phone.as_ref().filter(|value| !value.trim().is_empty()) {
                                p { strong { "Phone: " } "{phone}" }
                            }
                            if let Some(source) = d.player.bio.as_ref().filter(|value| !value.trim().is_empty()) {
                                ProfileBio { source: source.clone() }
                            }
                            if d.player.phone.as_ref().is_none_or(|value| value.trim().is_empty())
                                && d.player.bio.as_ref().is_none_or(|value| value.trim().is_empty()) {
                                p { class: "text-muted mb-0", "No profile details added yet." }
                            }
                        }
                    }

                    section {
                        id: "player-tournament-history", key: "{d.player.id}-history",
                        class: "profile-section", hidden: active_tab != "history", aria_labelledby: "player-tab-history",
                        div { class: "profile-section-heading",
                            h2 { class: "h5 mb-0", "Tournament History ({d.registrations.len()})" }
                        }
                        div { class: "profile-section-body",
                            if d.registrations.is_empty() {
                                p { class: "text-muted", "No tournament registrations yet." }
                            } else {
                                div { class: "profile-table-scroll", role: "region", tabindex: "0",
                                    aria_label: "Tournament history, scroll to see all columns",
                                    table { class: "table profile-history-table", aria_label: "Tournament history",
                                        thead {
                                            tr {
                                                th { scope: "col", "Tournament" }
                                                th { scope: "col", "Team" }
                                                th { scope: "col", "Jersey" }
                                                th { scope: "col", class: "table-nowrap", "Status" }
                                                th { scope: "col", class: "table-nowrap", "Payment" }
                                                th { scope: "col", class: "table-nowrap", "Waiver" }
                                            }
                                        }
                                        tbody {
                                            for r in d.registrations.iter() {
                                                {
                                                    let ev = r.event.clone();
                                                    let is_league = ev.starts_with("league:");
                                                    let league_url = ev.strip_prefix("league:").unwrap_or(&ev).to_string();
                                                    rsx! {
                                                tr { key: "{ev}-{r.team.as_deref().unwrap_or(\"\")}",
                                                    td { class: "profile-record-name table-copy", "data-label": "Tournament",
                                                        if is_league {
                                                            Link { to: Route::LeagueHome { league_url: league_url.clone() }, "League: {league_url}" }
                                                        } else {
                                                            Link { to: Route::TournamentHome { url: ev.clone() }, "{ev}" }
                                                        }
                                                    }
                                                    td { class: "table-copy", "data-label": "Team",
                                                        if let (Some(team), Some(id)) = (&r.team_pseudonym, &r.team) {
                                                            Link { to: Route::TeamProfilePage { id: id.clone() }, "{team}" }
                                                        } else {
                                                            "Unattached"
                                                        }
                                                    }
                                                    td { "data-label": "Jersey",
                                                        Link {
                                                            to: Route::PlayerProfilePage { id: d.player.id.clone() },
                                                            class: "table-jersey text-decoration-none",
                                                            if r.jersey_name.is_some() || r.jersey_number.is_some() {
                                                                if let Some(jersey_name) = &r.jersey_name { span { class: "table-copy", "{jersey_name}" } }
                                                                if let Some(jersey_number) = &r.jersey_number { span { class: "table-nowrap text-muted", "#{jersey_number}" } }
                                                            } else {
                                                                "-"
                                                            }
                                                        }
                                                    }
                                                    td { class: "table-nowrap", "data-label": "Status",
                                                        RegistrationStatusBadge { status: r.status.clone() }
                                                    }
                                                    td { class: "table-nowrap", "data-label": "Payment",
                                                        PaymentStatusBadge { paid: r.paid }
                                                    }
                                                    td { class: "table-nowrap", "data-label": "Waiver",
                                                        WaiverStatusBadge { required: r.waiver_required, status: r.waiver_status.clone(), show_not_required: true }
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

                    if show_injuries {
                        section {
                            id: "player-injury-history", key: "{d.player.id}-injuries",
                            class: "profile-section", hidden: active_tab != "injuries", aria_labelledby: "player-tab-injuries",
                            div { class: "profile-section-heading",
                                h2 { class: "h5 mb-0", "Injury History ({injury_total})" }
                                if active_injuries > 0 {
                                    span { class: "badge bg-warning profile-disclosure-status", "{active_injuries} active" }
                                }
                            }
                            div { class: "profile-section-body",
                                if can_edit_injuries {
                                    p { id: "injury-visibility-help", class: "form-text",
                                        "Public injuries are visible to anyone. Private injuries are hidden from your public profile. Active injuries are still shown to authorized match referees during match setup at events you are registered for."
                                    }
                                }
                                if let Some(err) = injury_error() {
                                    div { class: "alert alert-danger", "{err}" }
                                }
                                div { class: "profile-table-scroll", role: "region", tabindex: "0",
                                    aria_label: "Injury history, scroll to see all columns",
                                    table { class: "table profile-injury-table", aria_label: "Injury history",
                                        thead {
                                            tr {
                                                th { scope: "col", class: "table-nowrap", "Date" }
                                                th { scope: "col", class: "table-copy", "Description" }
                                                th { scope: "col", class: "table-nowrap", "Status" }
                                                th { scope: "col", class: "table-nowrap", "Public" }
                                                if can_edit_injuries {
                                                    th { scope: "col", class: "table-nowrap", "Actions" }
                                                }
                                            }
                                        }
                                        tbody {
                                            if can_edit_injuries {
                                                for (idx, inj) in injuries_state().iter().enumerate() {
                                                    {
                                                        let row_key = if let Some(id) = inj.id {
                                                            format!("injury-{}", id)
                                                        } else {
                                                            format!("injury-new-{}", idx)
                                                        };
                                                        let player_id_for_save = player_id.clone();
                                                        let player_id_for_delete = player_id.clone();
                                                        rsx! { tr { key: "{row_key}",
                                                        td { "data-label": "Date", class: "profile-record-wide table-nowrap",
                                                            if inj.editing {
                                                                input {
                                                                    class: "form-control form-control-sm",
                                                                    "type": "date", aria_label: "Injury date",
                                                                    value: "{inj.date}",
                                                                    oninput: move |e| {
                                                                        let mut rows = injuries_state.write();
                                                                        if let Some(row) = rows.get_mut(idx) {
                                                                            row.date = e.value();
                                                                        }
                                                                    }
                                                                }
                                                            } else if inj.date.is_empty() {
                                                                "-"
                                                            } else {
                                                                {crate::time_format::format_calendar_date(&inj.date)}
                                                            }
                                                        }
                                                        td { "data-label": "Description", class: "profile-record-wide table-copy",
                                                            if inj.editing {
                                                                input {
                                                                    class: "form-control form-control-sm",
                                                                    "type": "text", aria_label: "Injury description",
                                                                    value: "{inj.message}",
                                                                    oninput: move |e| {
                                                                        let mut rows = injuries_state.write();
                                                                        if let Some(row) = rows.get_mut(idx) {
                                                                            row.message = e.value();
                                                                        }
                                                                    }
                                                                }
                                                            } else {
                                                                "{inj.message}"
                                                            }
                                                        }
                                                        td { class: "table-nowrap", "data-label": "Status",
                                                            if inj.editing {
                                                                div { class: "form-check",
                                                                    input {
                                                                        class: "form-check-input",
                                                                        "type": "checkbox", id: "{row_key}-active",
                                                                        checked: inj.active,
                                                                        onchange: move |e| {
                                                                            let mut rows = injuries_state.write();
                                                                            if let Some(row) = rows.get_mut(idx) {
                                                                                row.active = e.checked();
                                                                            }
                                                                        }
                                                                    }
                                                                    label { r#for: "{row_key}-active", class: "form-check-label", "Active" }
                                                                }
                                                            } else if inj.active {
                                                                span { class: "badge bg-warning", "Active" }
                                                            } else {
                                                                span { class: "badge bg-success", "Healed" }
                                                            }
                                                        }
                                                        td { class: "table-nowrap", "data-label": "Visibility",
                                                            if inj.editing {
                                                                div { class: "form-check",
                                                                    input {
                                                                        class: "form-check-input",
                                                                        "type": "checkbox", id: "{row_key}-public",
                                                                        aria_describedby: "injury-visibility-help",
                                                                        checked: inj.show,
                                                                        onchange: move |e| {
                                                                            let mut rows = injuries_state.write();
                                                                            if let Some(row) = rows.get_mut(idx) {
                                                                                row.show = e.checked();
                                                                            }
                                                                        }
                                                                    }
                                                                    label { r#for: "{row_key}-public", class: "form-check-label", "Public" }
                                                                }
                                                            } else if inj.show {
                                                                span { class: "badge bg-info", "Public" }
                                                            } else {
                                                                span { class: "badge bg-secondary", "Private" }
                                                            }
                                                        }
                                                        if can_edit_injuries {
                                                            td { "data-label": "Actions", class: "profile-record-actions table-nowrap",
                                                                div { class: "btn-group btn-group-sm",
                                                                    if inj.editing {
                                                                        button {
                                                                            class: "btn btn-outline-success btn-sm",
                                                                            "type": "button",
                                                                            disabled: injury_saving(),
                                                                            onclick: move |_| {
                                                                                let player_id = player_id_for_save.clone();
                                                                                async move {
                                                                                    injury_saving.set(true);
                                                                                    injury_error.set(None);
                                                                                    let snap = injuries_state.read().get(idx).cloned();
                                                                                    if let Some(row) = snap {
                                                                                        let req = json!({
                                                                                            "message": row.message,
                                                                                            "custom_date": if row.date.is_empty() { None } else { Some(row.date) },
                                                                                            "active": row.active,
                                                                                            "show": row.show
                                                                                        });
                                                                                        let saved = if let Some(id) = row.id {
                                                                                            api::update_injury(&player_id, id, &req).await
                                                                                        } else {
                                                                                            api::create_injury(&player_id, &req).await
                                                                                        };
                                                                                        match saved {
                                                                                            Ok(inj) => {
                                                                                                let date = stamp_to_date(&inj.stamp);
                                                                                                let mut rows = injuries_state.write();
                                                                                                if let Some(mut_row) = rows.get_mut(idx) {
                                                                                                    mut_row.id = Some(inj.id);
                                                                                                    mut_row.message = inj.message.clone();
                                                                                                    mut_row.date = date.clone();
                                                                                                    mut_row.active = inj.active;
                                                                                                    mut_row.show = inj.show;
                                                                                                    mut_row.original_message = inj.message;
                                                                                                    mut_row.original_date = date;
                                                                                                    mut_row.original_active = inj.active;
                                                                                                    mut_row.original_show = inj.show;
                                                                                                    mut_row.editing = false;
                                                                                                    mut_row.is_new = false;
                                                                                                }
                                                                                            }
                                                                                            Err(e) => injury_error.set(Some(e)),
                                                                                        }
                                                                                    }
                                                                                    injury_saving.set(false);
                                                                                }
                                                                            },
                                                                            "Save"
                                                                        }
                                                                        button {
                                                                            class: "btn btn-outline-secondary btn-sm",
                                                                            "type": "button",
                                                                            onclick: move |_| {
                                                                                let mut rows = injuries_state.write();
                                                                                if let Some(row) = rows.get_mut(idx) {
                                                                                    if row.is_new {
                                                                                        rows.remove(idx);
                                                                                    } else {
                                                                                        row.message = row.original_message.clone();
                                                                                        row.date = row.original_date.clone();
                                                                                        row.active = row.original_active;
                                                                                        row.show = row.original_show;
                                                                                        row.editing = false;
                                                                                    }
                                                                                }
                                                                            },
                                                                            "Cancel"
                                                                        }
                                                                    } else {
                                                                        button {
                                                                            class: "btn btn-outline-primary btn-sm",
                                                                            "type": "button",
                                                                            onclick: move |_| {
                                                                                let mut rows = injuries_state.write();
                                                                                if let Some(row) = rows.get_mut(idx) {
                                                                                    row.editing = true;
                                                                                }
                                                                            },
                                                                            "Edit"
                                                                        }
                                                                    }
                                                                    if inj.id.is_some() {
                                                                        button {
                                                                            class: "btn btn-outline-danger btn-sm",
                                                                            "type": "button",
                                                                            onclick: move |_| {
                                                                                let player_id = player_id_for_delete.clone();
                                                                                async move {
                                                                                    if !web_sys::window()
                                                                                        .unwrap()
                                                                                        .confirm_with_message("Delete this injury?")
                                                                                        .unwrap_or(false)
                                                                                    {
                                                                                        return;
                                                                                    }
                                                                                    injury_saving.set(true);
                                                                                    injury_error.set(None);
                                                                                    let injury_id = injuries_state.read().get(idx).and_then(|row| row.id);
                                                                                    if let Some(id) = injury_id {
                                                                                        match api::delete_injury(&player_id, id).await {
                                                                                            Ok(_) => {
                                                                                                injuries_state.write().remove(idx);
                                                                                            }
                                                                                            Err(e) => injury_error.set(Some(e)),
                                                                                        }
                                                                                    }
                                                                                    injury_saving.set(false);
                                                                                }
                                                                            },
                                                                            "Delete"
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                        } }
                                                }
                                            } else {
                                                for inj in d.injuries.iter() {
                                                    tr { key: "{inj.id}",
                                                        td { class: "profile-record-wide table-nowrap", "data-label": "Date", {crate::time_format::format_calendar_date(inj.stamp.as_deref().unwrap_or("—"))} }
                                                        td { class: "profile-record-wide table-copy", "data-label": "Description", "{inj.message}" }
                                                        td { class: "table-nowrap", "data-label": "Status",
                                                            if inj.active {
                                                                span { class: "badge bg-warning", "Active" }
                                                            } else {
                                                                span { class: "badge bg-success", "Healed" }
                                                            }
                                                        }
                                                        td { class: "table-nowrap", "data-label": "Visibility",
                                                            if inj.show {
                                                                span { class: "badge bg-info", "Public" }
                                                            } else {
                                                                span { class: "badge bg-secondary", "Private" }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                if can_edit_injuries {
                                    div { class: "d-grid mt-2",
                                        button {
                                            class: "btn btn-outline-primary btn-sm",
                                            "type": "button",
                                            onclick: move |_| {
                                                let mut rows = injuries_state.write();
                                                rows.push(EditableInjury {
                                                    id: None,
                                                    message: "".to_string(),
                                                    date: "".to_string(),
                                                    active: true,
                                                    show: false,
                                                    original_message: "".to_string(),
                                                    original_date: "".to_string(),
                                                    original_active: true,
                                                    original_show: false,
                                                    editing: true,
                                                    is_new: true,
                                                });
                                            },
                                            "New Injury"
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if show_infractions {
                        section {
                            id: "player-penalties", key: "{d.player.id}-penalties",
                            class: "profile-section", hidden: active_tab != "infractions", aria_labelledby: "player-tab-infractions",
                            div { class: "profile-section-heading",
                                h2 { class: "h5 mb-0", "Infractions ({penalty_row_elements.len()})" }
                            }
                            div { class: "profile-section-body",
                                if penalty_rows_empty { p { class: "text-muted", "No infractions recorded." } }
                                if !penalty_rows_empty { p { class: "small text-muted", "Times shown in your local timezone." } }
                                div { class: "profile-table-scroll", role: "region", tabindex: "0",
                                    aria_label: "Infractions, scroll to see all columns",
                                    table { class: "table table-sm", aria_label: "Infractions",
                                        thead {
                                            tr {
                                                th { scope: "col", class: "table-nowrap", "Date" }
                                                th { scope: "col", class: "table-copy", "Note" }
                                                th { scope: "col", class: "table-number", "Point #" }
                                                th { scope: "col", class: "table-copy", "Match" }
                                            }
                                        }
                                        tbody {
                                            for elem in penalty_row_elements.iter() {
                                                {elem}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if let Some(description) = penalty_desc_modal() {
                        Modal {
                            id: "player-penalty-description-dialog",
                            labelled_by: "player-penalty-description-title",
                            on_close: move |_| penalty_desc_modal.set(None),
                            div { class: "modal-header",
                                h2 { id: "player-penalty-description-title", class: "modal-title h5", tabindex: "-1", "Penalty description" }
                                button { r#type: "button", class: "btn-close", aria_label: "Close penalty description", onclick: move |_| penalty_desc_modal.set(None) }
                            }
                            div { class: "modal-body", "{description}" }
                            div { class: "modal-footer",
                                button { r#type: "button", class: "btn btn-secondary", onclick: move |_| penalty_desc_modal.set(None), "Close" }
                            }
                        }
                    }
            }
            }
        };
    }
    if let Some(Err(e)) = val.read().as_ref() {
        return rsx! { p { class: "error", "{e}" } };
    }
    rsx! { p { "Loading…" } }
}
