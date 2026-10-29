use crate::api;
use crate::components::{Modal, PenaltyDisplay};
use crate::types::StartMatchPlayer;
use crate::Route;
use dioxus::prelude::*;
use serde_json::Value;
use std::collections::HashSet;

// The API treats both an absent limit and zero as an uncapped roster.
fn roster_limit(configured: Option<u32>) -> Option<usize> {
    configured.filter(|limit| *limit > 0).map(|limit| limit as usize)
}

fn exceeds_roster_limit(selected: usize, limit: Option<usize>) -> bool {
    limit.is_some_and(|maximum| selected > maximum)
}

fn selected_roster_label(selected: usize, limit: Option<usize>) -> String {
    match limit {
        Some(maximum) => format!("{selected}/{maximum} players selected"),
        None => format!("{selected} players selected"),
    }
}

fn start_blocking_reasons(
    teams: [(&str, usize, bool); 2],
    limit: Option<usize>,
) -> Vec<String> {
    let mut reasons = Vec::new();
    for (name, selected, has_unpaid) in teams {
        if let Some(maximum) = limit.filter(|maximum| selected > *maximum) {
            reasons.push(format!("{name}: select no more than {maximum} players ({selected} selected)."));
        }
        if has_unpaid {
            reasons.push(format!("{name}: resolve payment for selected unpaid players."));
        }
    }
    reasons
}

#[component]
pub fn StartMatch(url: String, match_id: String) -> Element {
    let match_id_for_data = match_id.clone();
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        let id = match_id_for_data.clone();
        async move {
            api::start_match_data(&u, &id)
                .await
                .map_err(|e| e.to_string())
        }
    });
    let val = data.value();
    let mut team1_all = use_signal(Vec::<String>::new);
    let mut team2_all = use_signal(Vec::<String>::new);
    let mut team1_selected = use_signal(HashSet::<String>::new);
    let mut team2_selected = use_signal(HashSet::<String>::new);
    let mut team1_search = use_signal(String::new);
    let mut team2_search = use_signal(String::new);
    let mut notes_modal_show = use_signal(|| false);
    let mut notes_modal_content = use_signal(|| None as Option<Result<Vec<Value>, String>>);
    let mut penalty_desc_modal = use_signal(|| None::<String>);
    let mut match_notes = use_signal(String::new);
    let mut submit_error = use_signal(|| None::<String>);
    let mut submitting = use_signal(|| false);
    let navigator = use_navigator();
    let submit_url = url.clone();
    let notes_url_team1 = url.clone();
    let notes_url_team2 = url.clone();
    let schedule_url = url.clone();
    let home_url = url.clone();
    let data_snapshot = val.read().clone();

    // One-time init when data loads; avoid depending on team1_all/team2_all so setting them doesn't re-trigger the effect (prevents render loop / browser slowdown).
    let mut has_initialized_teams = use_signal(|| false);
    use_effect(move || {
        let binding = val.read();
        let data_ready = binding.as_ref().and_then(|r| r.as_ref().ok());
        if let Some(d) = data_ready {
            if !has_initialized_teams() {
                team1_all.set(d.team1_players.iter().map(|p| p.id.clone()).collect());
                team1_selected.set(
                    d.team1_players
                        .iter()
                        .map(|p| p.id.clone())
                        .collect::<HashSet<_>>(),
                );
                team2_all.set(d.team2_players.iter().map(|p| p.id.clone()).collect());
                team2_selected.set(
                    d.team2_players
                        .iter()
                        .map(|p| p.id.clone())
                        .collect::<HashSet<_>>(),
                );
                has_initialized_teams.set(true);
            }
        }
    });

    match data_snapshot {
        Some(Ok(d)) => {
            let max_roster = roster_limit(d.tournament.max_team_size_field);
            let match_uuid = d.match_info.uuid.clone();
            let match_uuid_notes1 = match_uuid.clone();
            let match_uuid_notes2 = match_uuid.clone();
            let team1_name = d.match_info.team1_name.clone();
            let team2_name = d.match_info.team2_name.clone();
            let team1_has_unpaid = team1_selected().iter().any(|id| {
                d.all_players
                    .iter()
                    .find(|p| &p.id == id)
                    .map(|p| !p.paid)
                    .unwrap_or(false)
            });
            let team2_has_unpaid = team2_selected().iter().any(|id| {
                d.all_players
                    .iter()
                    .find(|p| &p.id == id)
                    .map(|p| !p.paid)
                    .unwrap_or(false)
            });
            // Use route param for POST so backend receives the same id we navigated to
            let route_match_id_for_submit = match_id.clone();
            let all_players_team1 = d.all_players.clone();
            let all_players_team2 = d.all_players.clone();
            let start_reasons = start_blocking_reasons([
                (&d.match_info.team1_name, team1_selected().len(), team1_has_unpaid),
                (&d.match_info.team2_name, team2_selected().len(), team2_has_unpaid),
            ], max_roster);
            rsx! {
                style { {include_str!("live_match_actions.css")} }
                div { class: "row",
                    div { class: "col-12",
                        h1 { "Start match" }
                        p { class: "text-muted", "Review both rosters before starting the match." }
                        nav { aria_label: "breadcrumb",
                            ol { class: "breadcrumb",
                                li { class: "breadcrumb-item",
                                    Link {
                                        to: Route::TournamentHome {
                                            url: home_url.clone(),
                                        },
                                        "{d.tournament.name}"
                                    }
                                }
                                li { class: "breadcrumb-item",
                                    if d.can_view_schedule {
                                        Link {
                                            to: Route::Schedule {
                                                url: schedule_url.clone(),
                                                view: String::new(),
                                                team: String::new(),
                                                field: String::new(),
                                            },
                                            if d.tournament.schedule_published { "Schedule" } else { "Preview schedule" }
                                        }
                                    } else {
                                        span { class: "text-muted",
                                            if d.tournament.schedule_published { "Schedule unavailable" } else { "Schedule not posted" }
                                        }
                                    }
                                }
                                li { class: "breadcrumb-item active", "Start Match" }
                            }
                        }
                    }
                }

                div { class: "row",
                    div { class: "col-md-8 operator-setup",
                        div { class: "edit-section",
                            div { class: "edit-section-heading",
                                h2 { class: "h5 mb-0", "Match Setup" }
                            }
                            div { class: "edit-section-body",
                                form {
                                    onsubmit: move |ev| {
                                        ev.prevent_default();
                                        if submitting() { return; }
                                        submit_error.set(None);
                                        let team1_count = team1_selected().len();
                                        let team2_count = team2_selected().len();
                                        if exceeds_roster_limit(team1_count, max_roster)
                                            || exceeds_roster_limit(team2_count, max_roster)
                                        {
                                            submit_error.set(Some("Reduce the selected roster to the event's player limit before starting.".to_string()));
                                            return;
                                        }
                                        if team1_has_unpaid || team2_has_unpaid {
                                            submit_error.set(Some("Resolve payment for the selected players before starting.".to_string()));
                                            return;
                                        }
                                        #[cfg(target_arch = "wasm32")]
                                        {
                                            if team1_count == 0 || team2_count == 0 {
                                                let mut message = "".to_string();
                                                if team1_count == 0 && team2_count == 0 {
                                                    message.push_str("Both teams have zero players. ");
                                                } else if team1_count == 0 {
                                                    message.push_str("Team 1 has zero players. ");
                                                } else {
                                                    message.push_str("Team 2 has zero players. ");
                                                }
                                                message
                                                    .push_str(
                                                        "This typically only happens if a team doesn't show up. Are you sure you want to start the match?",
                                                    );
                                                if let Some(window) = web_sys::window() {
                                                    let ok = window.confirm_with_message(&message).unwrap_or(false);
                                                    if !ok {
                                                        return;
                                                    }
                                                }
                                            }
                                        }
                                        submitting.set(true);
                                        let nav = navigator.clone();
                                        let team1_players: Vec<String> = team1_selected().iter().cloned().collect();
                                        let team2_players: Vec<String> = team2_selected().iter().cloned().collect();
                                        let match_notes = match_notes().clone();
                                        let u = submit_url.clone();
                                        let match_id_for_req = route_match_id_for_submit.clone();
                                        spawn(async move {
                                            let req = crate::types::StartMatchRequest {
                                                match_id: match_id_for_req,
                                                team1_players,
                                                team2_players,
                                                match_notes,
                                                stones_per_set: None,
                                            };
                                            match api::start_match(&u, &req).await {
                                                Ok(resp) => {
                                                    nav.push(Route::RunMatch {
                                                        url: u.clone(),
                                                        match_id: resp.match_id.clone(),
                                                    });
                                                }
                                                Err(error) => submit_error.set(Some(format!("The match could not start. {error}"))),
                                            }
                                            submitting.set(false);
                                        });
                                    },
                                    fieldset { class: "live-match-fields", disabled: submitting(),
                                    input {
                                        r#type: "hidden",
                                        name: "match_id",
                                        value: "{match_uuid}",
                                    }
                                    div { class: "row mb-4",
                                        div { class: "col-md-6",
                                            div { class: "d-flex justify-content-between align-items-center",
                                                h6 { class: "mb-0",
                                                    "Team 1: {d.match_info.team1_name}"
                                                }
                                                button {
                                                    r#type: "button",
                                                    class: "btn btn-sm btn-outline-info",
                                                    aria_label: "View notes for {d.match_info.team1_name}",
                                                    onclick: move |_| {
                                                        notes_modal_show.set(true);
                                                        notes_modal_content.set(None);
                                                        let u = notes_url_team1.clone();
                                                        let match_id = match_uuid_notes1.clone();
                                                        let ids = team1_selected().iter().cloned().collect::<Vec<_>>().join(",");
                                                        let mut notes_modal_content = notes_modal_content;
                                                        spawn(async move {
                                                            match api::get_selection_notes(&u, &match_id, "team1", &ids).await {
                                                                Ok(val) => {
                                                                    if let Some(false) = val.get("success").and_then(|v| v.as_bool()) {
                                                                        let err = val.get("error").and_then(|e| e.as_str()).unwrap_or("Failed to load notes").to_string();
                                                                        notes_modal_content.set(Some(Err(err)));
                                                                    } else {
                                                                        let notes = val.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                                        notes_modal_content.set(Some(Ok(notes)));
                                                                    }
                                                                }
                                                                Err(e) => notes_modal_content.set(Some(Err(e))),
                                                            }
                                                        });
                                                    },
                                                    "View Notes"
                                                }
                                            }
                                            fieldset { class: "mb-3",
                                                legend { class: "form-label fs-6",
                                                    span { class: "visually-hidden", "{d.match_info.team1_name}: " }
                                                    if let Some(maximum) = max_roster { "Select players · maximum {maximum}" } else { "Select players" }
                                                }
                                                div {
                                                    class: "border p-3",
                                                    style: "max-height: 200px; overflow-y: auto;",
                                                    StartMatchPlayerList {
                                                        team_key: "team1".to_string(),
                                                        players: d.all_players.clone(),
                                                        team_ids: team1_all(),
                                                        selected_ids: team1_selected(),
                                                        other_selected_ids: team2_selected(),
                                                        on_toggle: move |(id, is_selected): (String, bool)| {
                                                            let mut selected = team1_selected();
                                                            if is_selected {
                                                                selected.insert(id.clone());
                                                                // Player can only play for one team: remove from team2 if now selected for team1.
                                                                let mut other = team2_selected();
                                                                other.remove(&id);
                                                                team2_selected.set(other);
                                                            } else {
                                                                selected.remove(&id);
                                                            }
                                                            team1_selected.set(selected);
                                                        },
                                                    }
                                                }
                                                p {
                                                    class: if exceeds_roster_limit(team1_selected().len(), max_roster) {
                                                        "small text-danger mb-0 mt-1"
                                                    } else {
                                                        "small text-muted mb-0 mt-1"
                                                    },
                                                    "{selected_roster_label(team1_selected().len(), max_roster)}"
                                                }
                                                if team1_has_unpaid {
                                                    p { class: "small text-danger mb-0 mt-1", "Unpaid players selected" }
                                                }
                                            }
                                            div { class: "mb-3",
                                                label {
                                                    r#for: "team1_search",
                                                    class: "form-label",
                                                    "Add Player:"
                                                }
                                                div { class: "input-group",
                                                    input {
                                                        r#type: "text",
                                                        class: "form-control",
                                                        id: "team1_search",
                                                        aria_label: "Search players to add to this roster",
                                                        placeholder: "Search players...",
                                                        value: "{team1_search()}",
                                                        oninput: move |ev| team1_search.set(ev.value().clone()),
                                                    }
                                                    button {
                                                        class: "btn btn-outline-secondary",
                                                        r#type: "button",
                                                        onclick: move |_| {
                                                            if let Some(p) = find_player(&all_players_team1, &team1_search()) {
                                                                if !team1_all().contains(&p.id) {
                                                                    let mut ids = team1_all();
                                                                    ids.push(p.id);
                                                                    team1_all.set(ids);
                                                                }
                                                                team1_search.set(String::new());
                                                            }
                                                        },
                                                        "Search"
                                                    }
                                                }
                                                div { class: "mt-2",
                                                    StartMatchSearchResults {
                                                        players: all_players_team1.clone(),
                                                        query: team1_search(),
                                                        on_add: move |id| {
                                                            let mut ids = team1_all();
                                                            if !ids.contains(&id) {
                                                                ids.push(id);
                                                                team1_all.set(ids);
                                                            }
                                                        },
                                                    }
                                                }
                                            }
                                        }

                                        div { class: "col-md-6",
                                            div { class: "d-flex justify-content-between align-items-center",
                                                h6 { class: "mb-0",
                                                    "Team 2: {d.match_info.team2_name}"
                                                }
                                                button {
                                                    r#type: "button",
                                                    class: "btn btn-sm btn-outline-info",
                                                    aria_label: "View notes for {d.match_info.team2_name}",
                                                    onclick: move |_| {
                                                        notes_modal_show.set(true);
                                                        notes_modal_content.set(None);
                                                        let u = notes_url_team2.clone();
                                                        let match_id = match_uuid_notes2.clone();
                                                        let ids = team2_selected().iter().cloned().collect::<Vec<_>>().join(",");
                                                        let mut notes_modal_content = notes_modal_content;
                                                        spawn(async move {
                                                            match api::get_selection_notes(&u, &match_id, "team2", &ids).await {
                                                                Ok(val) => {
                                                                    if let Some(false) = val.get("success").and_then(|v| v.as_bool()) {
                                                                        let err = val.get("error").and_then(|e| e.as_str()).unwrap_or("Failed to load notes").to_string();
                                                                        notes_modal_content.set(Some(Err(err)));
                                                                    } else {
                                                                        let notes = val.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                                        notes_modal_content.set(Some(Ok(notes)));
                                                                    }
                                                                }
                                                                Err(e) => notes_modal_content.set(Some(Err(e))),
                                                            }
                                                        });
                                                    },
                                                    "View Notes"
                                                }
                                            }
                                            fieldset { class: "mb-3",
                                                legend { class: "form-label fs-6",
                                                    span { class: "visually-hidden", "{d.match_info.team2_name}: " }
                                                    if let Some(maximum) = max_roster { "Select players · maximum {maximum}" } else { "Select players" }
                                                }
                                                div {
                                                    class: "border p-3",
                                                    style: "max-height: 200px; overflow-y: auto;",
                                                    StartMatchPlayerList {
                                                        team_key: "team2".to_string(),
                                                        players: d.all_players.clone(),
                                                        team_ids: team2_all(),
                                                        selected_ids: team2_selected(),
                                                        other_selected_ids: team1_selected(),
                                                        on_toggle: move |(id, is_selected): (String, bool)| {
                                                            let mut selected = team2_selected();
                                                            if is_selected {
                                                                selected.insert(id.clone());
                                                                // Player can only play for one team: remove from team1 if now selected for team2.
                                                                let mut other = team1_selected();
                                                                other.remove(&id);
                                                                team1_selected.set(other);
                                                            } else {
                                                                selected.remove(&id);
                                                            }
                                                            team2_selected.set(selected);
                                                        },
                                                    }
                                                }
                                                p {
                                                    class: if exceeds_roster_limit(team2_selected().len(), max_roster) {
                                                        "small text-danger mb-0 mt-1"
                                                    } else {
                                                        "small text-muted mb-0 mt-1"
                                                    },
                                                    "{selected_roster_label(team2_selected().len(), max_roster)}"
                                                }
                                                if team2_has_unpaid {
                                                    p { class: "small text-danger mb-0 mt-1", "Unpaid players selected" }
                                                }
                                            }
                                            div { class: "mb-3",
                                                label {
                                                    r#for: "team2_search",
                                                    class: "form-label",
                                                    "Add Player:"
                                                }
                                                div { class: "input-group",
                                                    input {
                                                        r#type: "text",
                                                        class: "form-control",
                                                        id: "team2_search",
                                                        aria_label: "Search players to add to this roster",
                                                        placeholder: "Search players...",
                                                        value: "{team2_search()}",
                                                        oninput: move |ev| team2_search.set(ev.value().clone()),
                                                    }
                                                    button {
                                                        class: "btn btn-outline-secondary",
                                                        r#type: "button",
                                                        onclick: move |_| {
                                                            if let Some(p) = find_player(&all_players_team2, &team2_search()) {
                                                                if !team2_all().contains(&p.id) {
                                                                    let mut ids = team2_all();
                                                                    ids.push(p.id);
                                                                    team2_all.set(ids);
                                                                }
                                                                team2_search.set(String::new());
                                                            }
                                                        },
                                                        "Search"
                                                    }
                                                }
                                                div { class: "mt-2",
                                                    StartMatchSearchResults {
                                                        players: all_players_team2.clone(),
                                                        query: team2_search(),
                                                        on_add: move |id| {
                                                            let mut ids = team2_all();
                                                            if !ids.contains(&id) {
                                                                ids.push(id);
                                                                team2_all.set(ids);
                                                            }
                                                        },
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    div { class: "mb-4",
                                        label {
                                            r#for: "match_notes",
                                            class: "form-label",
                                            "Match Notes"
                                        }
                                        textarea {
                                            class: "form-control",
                                            id: "match_notes",
                                            name: "match_notes",
                                            rows: "3",
                                            placeholder: "Any special rules or notes for this match...",
                                            value: "{match_notes()}",
                                            oninput: move |ev| match_notes.set(ev.value().clone()),
                                        }
                                    }
                                    if let Some(error) = submit_error() {
                                        div { class: "alert alert-danger", role: "alert", "{error}" }
                                    }
                                    p { class: "form-text", "Starting locks in the match roster. Check the selected players before continuing." }
                                    if !start_reasons.is_empty() {
                                        ul { id: "start-match-prerequisites", class: "live-match-prerequisites", role: "status",
                                            for reason in start_reasons { li { "{reason}" } }
                                        }
                                    } else {
                                        p { id: "start-match-prerequisites", class: "small text-muted", "Review both selected rosters, then start the match." }
                                    }
                                    div { class: "live-match-actions",
                                        button {
                                            r#type: "submit",
                                            class: "btn btn-primary btn-lg",
                                            aria_describedby: "start-match-prerequisites",
                                            disabled: submitting() || exceeds_roster_limit(team1_selected().len(), max_roster)
                                                || exceeds_roster_limit(team2_selected().len(), max_roster)
                                                || team1_has_unpaid
                                                || team2_has_unpaid,
                                            if submitting() { "Starting match…" } else { "Start match" }
                                        }
                                        if !submitting() {
                                            Link { to: Route::MatchPageById { url: url.clone(), match_id: match_id.clone() }, class: "btn btn-outline-secondary", "Back to match" }
                                        }
                                    }
                                    }
                                }
                            }
                        }
                    }
                    div { class: "col-md-4",
                        div { class: "card",
                            div { class: "card-header",
                                h2 { class: "h5 mb-0", "Match Info" }
                            }
                            div { class: "card-body",
                                p {
                                    strong { "Match: " }
                                    "{d.match_info.name}"
                                }
                                p {
                                    strong { "Field: " }
                                    "{d.match_info.field.as_deref().unwrap_or(\"TBA\")}"
                                }
                                p {
                                    strong { "Set Type: " }
                                    "{d.match_info.set_type.as_deref().unwrap_or(\"Standard\")}"
                                }
                                p {
                                    strong { "Refs: " }
                                    "{d.match_info.refs.as_deref().unwrap_or(\"TBA\")}"
                                }
                            }
                        }
                    }
                }

            if notes_modal_show() {
                Modal {
                    id: "start-notes-dialog", labelled_by: "start-notes-title",
                    on_close: move |_| notes_modal_show.set(false),
                            div { class: "modal-header",
                                h2 { id: "start-notes-title", class: "modal-title h5", tabindex: "-1", "Relevant Notes" }
                                button {
                                    r#type: "button",
                                    class: "btn-close",
                                    aria_label: "Close",
                                    onclick: move |_| notes_modal_show.set(false),
                                }
                            }
                            div { class: "modal-body",
                                match notes_modal_content().as_ref() {
                                    None => rsx! { div { class: "text-muted", role: "status", "Loading…" } },
                                    Some(Err(e)) => rsx! { div { class: "text-danger", role: "alert", "{e}" } },
                                    Some(Ok(notes)) => {
                                        if notes.is_empty() {
                                            rsx! {
                                                div { class: "text-muted", "No notes for this selection." }
                                            }
                                        } else {
                                            let note_elems: Vec<_> = notes.iter().map(|n| {
                                                let text = n.get("text").and_then(|t| t.as_str()).unwrap_or("").trim().to_string();
                                                let type_name = n.get("penalty_type_name").and_then(|t| t.as_str()).map(|s| s.to_string());
                                                let color = n.get("penalty_type_color").and_then(|c| c.as_str()).map(|s| s.trim_start_matches('#').to_string());
                                                let target_display = n.get("player_display").and_then(|p| p.as_str())
                                                    .or_else(|| n.get("player_name").and_then(|p| p.as_str()))
                                                    .or_else(|| n.get("target").and_then(|t| t.as_str()))
                                                    .map(|t| {
                                                        if t == "team1" { team1_name.clone() } else if t == "team2" { team2_name.clone() } else { t.to_string() }
                                                    })
                                                    .unwrap_or_else(|| "Match".to_string());
                                                let target_profile_id = n.get("player_id").and_then(|v| v.as_str()).map(String::from);
                                                let (border_color, display_text) = if let Some(ref tn) = type_name {
                                                    (color.unwrap_or_else(|| "808080".to_string()), tn.clone())
                                                } else {
                                                    ("808080".to_string(), if text.is_empty() { "Other".to_string() } else { text })
                                                };
                                                let description = n.get("penalty_type_desc").and_then(|v| v.as_str()).map(|s| s.to_string()).filter(|s| !s.is_empty());
                                                rsx! {
                                                    PenaltyDisplay {
                                                        border_color,
                                                        display_text,
                                                        description,
                                                        target_display: Some(target_display),
                                                        target_profile_id,
                                                        on_description_click: move |d: Option<String>| penalty_desc_modal.set(d),
                                                    }
                                                }
                                            }).collect();
                                            rsx! {
                                                div {
                                                    for elem in note_elems.iter() {
                                                        {elem}
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            div { class: "modal-footer",
                                button {
                                    r#type: "button",
                                    class: "btn btn-secondary",
                                    onclick: move |_| notes_modal_show.set(false),
                                    "Close"
                                }
                            }
                }
                    if penalty_desc_modal().is_some() {
                        Modal {
                            id: "start-penalty-description-dialog", labelled_by: "start-penalty-description-title",
                            on_close: move |_| penalty_desc_modal.set(None),
                                    div { class: "modal-header",
                                        h2 { id: "start-penalty-description-title", class: "modal-title h5", tabindex: "-1", "Penalty description" }
                                        button { r#type: "button", class: "btn-close", aria_label: "Close penalty description", onclick: move |_| penalty_desc_modal.set(None) }
                                    }
                                    div { class: "modal-body", "{penalty_desc_modal().as_ref().unwrap_or(&String::new())}" }
                                    div { class: "modal-footer",
                                        button { r#type: "button", class: "btn btn-secondary", onclick: move |_| penalty_desc_modal.set(None), "Close" }
                                    }
                        }
                    }
            } else {
                div { }
            }
            }
        }
        Some(Err(e)) => rsx! {
            p { class: "text-danger", "{e}" }
        },
        None => rsx! {
            p { "Loading…" }
        },
    }
}

#[component]
fn StartMatchSearchResults(
    players: Vec<StartMatchPlayer>,
    query: String,
    on_add: EventHandler<String>,
) -> Element {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return rsx! {};
    }
    rsx! {
        for p in players
            .iter()
            .filter(|p| {
                p.name.to_lowercase().contains(&q) || p.id.to_lowercase().contains(&q)
            })
            .take(5)
        {
            {
                let id = p.id.clone();
                rsx! {
                    div { class: "d-flex justify-content-between align-items-center border rounded p-2 mb-1",
                        div {
                            {
                                let display = match (&p.jersey_name, &p.jersey_number) {
                                    (Some(jn), Some(num)) => format!("{} #{}", jn, num),
                                    (Some(jn), None) => jn.clone(),
                                    (None, Some(num)) => format!("{} #{}", p.name, num),
                                    (None, None) => p.name.clone(),
                                };
                                rsx! {
                                    strong { "{display}" }
                                    span { class: "text-muted ms-2", "@{p.id}" }
                                }
                            }
                        }
                        button {
                            r#type: "button",
                            class: "btn btn-sm btn-outline-secondary",
                            onclick: move |_| on_add.call(id.clone()),
                            "Add"
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn StartMatchPlayerList(
    team_key: String,
    players: Vec<StartMatchPlayer>,
    team_ids: Vec<String>,
    selected_ids: HashSet<String>,
    other_selected_ids: HashSet<String>,
    on_toggle: EventHandler<(String, bool)>,
) -> Element {
    rsx! {
        if team_ids.is_empty() {
            p { class: "text-muted", "No players added yet. Use the search below to add players." }
        } else {
            for player_id in team_ids.iter() {
                if let Some(p) = players.iter().find(|p| &p.id == player_id) {
                    {
                        let is_selected = selected_ids.contains(&p.id);
                        let is_on_other = other_selected_ids.contains(&p.id);
                        let disabled = is_on_other;
                        let injuries = if p.injuries.is_empty() {
                            None
                        } else {
                            Some(p.injuries.join(" • "))
                        };
                        let id = p.id.clone();
                        let id_for_toggle = id.clone();
                        let input_id = format!("{}-{}", team_key, id);
                        rsx! {
                            div { class: "form-check",
                                input {
                                    class: "form-check-input",
                                    r#type: "checkbox",
                                    id: "{input_id}",
                                    checked: is_selected,
                                    disabled,
                                    onchange: move |_| on_toggle.call((id_for_toggle.clone(), !is_selected)),
                                }
                                label { class: "form-check-label", r#for: "{input_id}",
                                    {
                                        let mut label = p.jersey_name.clone().unwrap_or_else(|| p.name.clone());
                                        if let Some(num) = &p.jersey_number {
                                            label = format!("{} #{}", label, num);
                                        }
                                        label
                                    }
                                    if !p.paid {
                                        span { class: "badge bg-secondary ms-2", "Unpaid" }
                                    }
                                }
                                if let Some(inj) = injuries {
                                    div { class: "small text-danger mt-1", "{inj}" }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

fn find_player(players: &[StartMatchPlayer], query: &str) -> Option<StartMatchPlayer> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return None;
    }
    players
        .iter()
        .find(|p| p.name.to_lowercase().contains(&q) || p.id.to_lowercase().contains(&q))
        .cloned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn absent_and_zero_limits_allow_populated_rosters() {
        for configured in [None, Some(0)] {
            let limit = roster_limit(configured);
            assert!(!exceeds_roster_limit(8, limit));
            assert_eq!(selected_roster_label(8, limit), "8 players selected");
        }
    }

    #[test]
    fn positive_limit_allows_boundary_and_blocks_excess() {
        for maximum in [1, 5, 8] {
            let limit = roster_limit(Some(maximum));
            assert!(!exceeds_roster_limit(maximum as usize, limit));
            assert!(exceeds_roster_limit(maximum as usize + 1, limit));
            assert_eq!(selected_roster_label(maximum as usize, limit), format!("{maximum}/{maximum} players selected"));
        }
    }

    #[test]
    fn start_prerequisites_identify_affected_team_without_blocking_empty_rosters() {
        let reasons = start_blocking_reasons([("Cedar", 6, false), ("River", 5, true)], Some(5));
        assert_eq!(reasons, [
            "Cedar: select no more than 5 players (6 selected).",
            "River: resolve payment for selected unpaid players.",
        ]);
        assert!(start_blocking_reasons([("Cedar", 0, false), ("River", 8, false)], None).is_empty());
        assert!(start_blocking_reasons([("Cedar", 5, false), ("River", 5, false)], Some(5)).is_empty());
    }
}
