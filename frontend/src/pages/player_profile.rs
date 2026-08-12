use crate::api;
use crate::components::PenaltyDisplay;
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
    date_display: String,
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
    let id = match &route {
        Route::PlayerProfilePage { id } => id.clone(),
        _ => return rsx! { div { class: "alert alert-danger", "Invalid route" } },
    };
    let mut id_signal = use_signal(|| id.clone());
    id_signal.set(id);
    rsx! {
        PlayerProfile { id: id_signal }
    }
}

#[component]
pub fn PlayerProfile(id: Signal<String>) -> Element {
    let data = use_resource(use_reactive(&id, move |sid| {
        let i = sid().clone();
        async move { api::player_profile(&i).await.map_err(|e| e.to_string()) }
    }));
    let mut injuries_state = use_signal(|| Vec::<EditableInjury>::new());
    let mut injuries_initialized_for = use_signal(|| None::<String>);
    let me = use_resource(move || async move { api::me().await });
    let val = data.value();
    let backend = api::base_url();
    let mut injury_error = use_signal(|| None::<String>);
    let mut injury_saving = use_signal(|| false);
    let mut bio_markdown = use_signal(|| Option::<String>::None);
    let mut penalty_desc_modal = use_signal(|| None::<String>);
    use_effect(move || {
        let v = val.read();
        if let Some(Ok(d)) = v.as_ref() {
            bio_markdown.set(d.player.bio.clone());
        } else {
            bio_markdown.set(None);
        }
    });
    let bio_html = use_resource(use_reactive(&bio_markdown, move |md| {
        let md = md().clone();
        async move {
            match md.as_deref() {
                Some(m) if !m.is_empty() => api::render_markdown(m).await,
                _ => Ok(String::new()),
            }
        }
    }));

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
        let penalty_rows: Vec<PenaltyRow> = d.player_notes.iter().map(|note| {
            let date_str = stamp_to_date(&note.created_at);
            let date_display = if date_str.is_empty() { "-".to_string() } else { date_str };
            let (border_color, display_text) = penalty_display_parts(note);
            PenaltyRow {
                key: format!("{}-{}", date_display, note.point_index),
                date_display: date_display.clone(),
                border_color,
                display_text,
                display_desc: note.penalty_type_desc.clone().filter(|s| !s.is_empty()),
                point_index: note.point_index.clone(),
                match_info: note.match_info.clone(),
            }
        }).collect();
        let penalty_rows_empty = penalty_rows.is_empty();
        let penalty_row_elements: Vec<_> = penalty_rows
            .into_iter()
            .map(|row| {
                let key = row.key.clone();
                let date_display = row.date_display.clone();
                let border_color = row.border_color.clone();
                let display_text = row.display_text.clone();
                let display_desc = row.display_desc.clone();
                let point_index = row.point_index.clone();
                let match_info = row.match_info.clone();
                rsx! {
                    tr { key: "{key}",
                        td { "{date_display}" }
                        td {
                            PenaltyDisplay {
                                border_color,
                                display_text,
                                description: display_desc,
                                target_display: None,
                                target_profile_id: None,
                                on_description_click: move |desc: Option<String>| penalty_desc_modal.set(desc),
                            }
                        }
                        td { "{point_index}" }
                        td {
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
            div { class: "row",
                div { class: "col-12",
                    h1 { "{d.player.name}" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::PlayersList {}, "Players" } }
                            li { class: "breadcrumb-item active", "{d.player.name}" }
                        }
                    }
                }
            }

            div { class: "row",
                div { class: "col-md-8",
                    div { class: "card",
                        div { class: "card-header d-flex justify-content-between align-items-center",
                            h5 { class: "mb-0", "Player Information" }
                            if can_edit_profile {
                                Link {
                                    to: Route::EditPlayerProfile { player_id: d.player.id.clone() },
                                    class: "btn btn-outline-secondary btn-sm",
                                    "✎"
                                }
                            }
                        }
                        div { class: "card-body",
                            if let Some(photo) = &d.player.profile_photo {
                                div { class: "text-center mb-3",
                                    img { src: "{backend}/static/{photo}", alt: "Profile Photo", class: "rounded-circle", style: "width: 100px; height: 100px; object-fit: cover;" }
                                }
                            }
                            p { strong { "Username: " } "@{d.player.id}" }
                            p { strong { "Display Name: " } "{d.player.name}" }
                            if let Some(loc) = &d.player.location {
                                p { strong { "Location: " } "{loc}" }
                            }
                            if let Some(phone) = &d.player.phone {
                                p { strong { "Phone: " } "{phone}" }
                            }
                            if let Some(bio) = &d.player.bio {
                                if !bio.is_empty() {
                                    div { class: "mt-3",
                                        h6 { strong { "Bio:" } }
                                        if let Some(Ok(html)) = bio_html.value().read().as_ref() {
                                            if html.is_empty() {
                                                div { class: "markdown-content", style: "white-space: pre-wrap;", "{bio}" }
                                            } else {
                                                div { dangerous_inner_html: "{html}" }
                                            }
                                        } else {
                                            div { class: "markdown-content", style: "white-space: pre-wrap;", "{bio}" }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    div { class: "card mt-3",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Tournament History" }
                        }
                        div { class: "card-body",
                            if d.registrations.is_empty() {
                                p { class: "text-muted", "No tournament registrations yet." }
                            } else {
                                div { class: "table-responsive",
                                    table { class: "table table-striped",
                                        thead {
                                            tr {
                                                th { "Tournament" }
                                                th { "Team" }
                                                th { "Jersey" }
                                                th { "Status" }
                                                th { "Payment" }
                                                th { "Waiver" }
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
                                                    td {
                                                        if is_league {
                                                            Link { to: Route::LeagueHome { league_url: league_url.clone() }, "League: {league_url}" }
                                                        } else {
                                                            Link { to: Route::TournamentHome { url: ev.clone() }, "{ev}" }
                                                        }
                                                    }
                                                    td {
                                                        if let (Some(team), Some(id)) = (&r.team_pseudonym, &r.team) {
                                                            Link { to: Route::TeamProfilePage { id: id.clone() }, "{team}" }
                                                        } else {
                                                            "Unattached"
                                                        }
                                                    }
                                                    td {
                                                        Link {
                                                            to: Route::PlayerProfilePage { id: d.player.id.clone() },
                                                            class: "text-decoration-none",
                                                            if let Some(jersey_name) = &r.jersey_name {
                                                                "{jersey_name}"
                                                                if let Some(jersey_number) = &r.jersey_number {
                                                                    span { class: "text-muted", " #{jersey_number}" }
                                                                }
                                                            } else {
                                                                "-"
                                                            }
                                                        }
                                                    }
                                                    td {
                                                        span { class: if r.status == "CONFIRMED" { "badge bg-success" } else { "badge bg-warning" },
                                                            "{r.status}"
                                                        }
                                                    }
                                                    td {
                                                        span { class: if r.paid { "badge bg-success" } else { "badge bg-warning text-dark" },
                                                            if r.paid { "Paid" } else { "Unpaid" }
                                                        }
                                                    }
                                                    td {
                                                        if r.waiver_required {
                                                            {
                                                                let ws = r.waiver_status.as_deref().unwrap_or("NOT_SIGNED");
                                                                let (cls, label) = match ws {
                                                                    "VALID" => ("bg-success", "Waiver valid"),
                                                                    "OUT_OF_DATE" => ("bg-warning text-dark", "Waiver out of date"),
                                                                    "NOT_SIGNED" => ("bg-danger", "Waiver not signed"),
                                                                    _ => ("bg-secondary", "Waiver status unknown"),
                                                                };
                                                                rsx! { span { class: "badge {cls}", "{label}" } }
                                                            }
                                                        } else {
                                                            span { class: "text-muted", "-" }
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

                    if can_edit_injuries || !d.injuries.is_empty() {
                        div { class: "card mt-3",
                            div { class: "card-header",
                                h5 { class: "mb-0", "Injury History" }
                            }
                            div { class: "card-body",
                                if let Some(err) = injury_error() {
                                    div { class: "alert alert-danger", "{err}" }
                                }
                                div { class: "table-responsive",
                                    table { class: "table table-striped",
                                        thead {
                                            tr {
                                                th { "Date" }
                                                th { "Description" }
                                                th { "Status" }
                                                th { "Public" }
                                                if can_edit_injuries {
                                                    th { "Actions" }
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
                                                        td {
                                                            if inj.editing {
                                                                input {
                                                                    class: "form-control form-control-sm",
                                                                    "type": "date",
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
                                                                "{inj.date}"
                                                            }
                                                        }
                                                        td {
                                                            if inj.editing {
                                                                input {
                                                                    class: "form-control form-control-sm",
                                                                    "type": "text",
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
                                                        td {
                                                            if inj.editing {
                                                                div { class: "form-check",
                                                                    input {
                                                                        class: "form-check-input",
                                                                        "type": "checkbox",
                                                                        checked: inj.active,
                                                                        onchange: move |e| {
                                                                            let mut rows = injuries_state.write();
                                                                            if let Some(row) = rows.get_mut(idx) {
                                                                                row.active = e.checked();
                                                                            }
                                                                        }
                                                                    }
                                                                    label { class: "form-check-label", "Active" }
                                                                }
                                                            } else if inj.active {
                                                                span { class: "badge bg-warning", "Active" }
                                                            } else {
                                                                span { class: "badge bg-success", "Healed" }
                                                            }
                                                        }
                                                        td {
                                                            if inj.editing {
                                                                div { class: "form-check",
                                                                    input {
                                                                        class: "form-check-input",
                                                                        "type": "checkbox",
                                                                        checked: inj.show,
                                                                        onchange: move |e| {
                                                                            let mut rows = injuries_state.write();
                                                                            if let Some(row) = rows.get_mut(idx) {
                                                                                row.show = e.checked();
                                                                            }
                                                                        }
                                                                    }
                                                                    label { class: "form-check-label", "Public" }
                                                                }
                                                            } else if inj.show {
                                                                span { class: "badge bg-info", "Public" }
                                                            } else {
                                                                span { class: "badge bg-secondary", "Private" }
                                                            }
                                                        }
                                                        if can_edit_injuries {
                                                            td {
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
                                                        td { "{inj.stamp.as_deref().unwrap_or(\"-\")}" }
                                                        td { "{inj.message}" }
                                                        td {
                                                            if inj.active {
                                                                span { class: "badge bg-warning", "Active" }
                                                            } else {
                                                                span { class: "badge bg-success", "Healed" }
                                                            }
                                                        }
                                                        td {
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
                                                    show: true,
                                                    original_message: "".to_string(),
                                                    original_date: "".to_string(),
                                                    original_active: true,
                                                    original_show: true,
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

                    if !penalty_rows_empty {
                        div { class: "card mt-3",
                            div { class: "card-header",
                                h5 { class: "mb-0", "Penalties" }
                            }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table table-striped table-sm",
                                        thead {
                                            tr {
                                                th { "Date" }
                                                th { "Note" }
                                                th { "Point #" }
                                                th { "Match" }
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
            }
        };
    }
    if let Some(Err(e)) = val.read().as_ref() {
        return rsx! { p { class: "error", "{e}" } };
    }
    rsx! { p { "Loading…" } }
}
