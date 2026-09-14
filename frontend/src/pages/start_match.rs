use crate::api;
use crate::components::PenaltyDisplay;
use crate::types::StartMatchPlayer;
use crate::Route;
use dioxus::prelude::*;
use std::collections::HashSet;
use serde_json::Value;

#[component]
pub fn StartMatch(url: String, match_id: String) -> Element {
    let match_id_for_data = match_id.clone();
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        let id = match_id_for_data.clone();
        async move {
            api::start_match_data(&u, &id).await.map_err(|e| e.to_string())
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
                team1_selected.set(d.team1_players.iter().map(|p| p.id.clone()).collect::<HashSet<_>>());
                team2_all.set(d.team2_players.iter().map(|p| p.id.clone()).collect());
                team2_selected.set(d.team2_players.iter().map(|p| p.id.clone()).collect::<HashSet<_>>());
                has_initialized_teams.set(true);
            }
        }
    });

    match data_snapshot {
        Some(Ok(d)) => {
            let match_uuid = d.match_info.uuid.clone();
            let match_uuid_notes1 = match_uuid.clone();
            let match_uuid_notes2 = match_uuid.clone();
            let team1_name = d.match_info.team1_name.clone();
            let team2_name = d.match_info.team2_name.clone();
            let team1_has_unpaid = team1_selected().iter().any(|id| {
                d.all_players.iter().find(|p| &p.id == id).map(|p| !p.paid).unwrap_or(false)
            });
            let team2_has_unpaid = team2_selected().iter().any(|id| {
                d.all_players.iter().find(|p| &p.id == id).map(|p| !p.paid).unwrap_or(false)
            });
            // Use route param for POST so backend receives the same id we navigated to
            let route_match_id_for_submit = match_id.clone();
            let all_players_team1 = d.all_players.clone();
            let all_players_team2 = d.all_players.clone();
            rsx! {
                div { class: "row",
                    div { class: "col-12",
                        h1 { "Start Match" }
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
                                    Link {
                                        to: Route::Schedule {
                                            url: schedule_url.clone(),
                                            view: String::new(),
                                            team: String::new(),
                                            field: String::new(),
                                        },
                                        "Schedule"
                                    }
                                }
                                li { class: "breadcrumb-item active", "Start Match" }
                            }
                        }
                    }
                }

                div { class: "row",
                    div { class: "col-md-8",
                        div { class: "card",
                            div { class: "card-header",
                                h5 { class: "mb-0", "Match Setup" }
                            }
                            div { class: "card-body",
                                form {
                                    onsubmit: move |ev| {
                                        ev.prevent_default();
                                        let team1_count = team1_selected().len();
                                        let team2_count = team2_selected().len();
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
                                            if let Ok(resp) = api::start_match(&u, &req).await {
                                                nav.push(Route::RunMatch {
                                                    url: u.clone(),
                                                    match_id: resp.match_id.clone(),
                                                });
                                            }
                                        });
                                    },
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
                                            div { class: "mb-3",
                                                label { class: "form-label",
                                                    "Select Players (max {d.tournament.max_team_size_field.unwrap_or(0)}):"
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
                                                    class: if team1_selected().len() > d.tournament.max_team_size_field.unwrap_or(0) as usize {
                                                        "small text-danger mb-0 mt-1"
                                                    } else {
                                                        "small text-muted mb-0 mt-1"
                                                    },
                                                    "{team1_selected().len()}/{d.tournament.max_team_size_field.unwrap_or(0)} players selected"
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
                                            div { class: "mb-3",
                                                label { class: "form-label",
                                                    "Select Players (max {d.tournament.max_team_size_field.unwrap_or(0)}):"
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
                                                    class: if team2_selected().len() > d.tournament.max_team_size_field.unwrap_or(0) as usize {
                                                        "small text-danger mb-0 mt-1"
                                                    } else {
                                                        "small text-muted mb-0 mt-1"
                                                    },
                                                    "{team2_selected().len()}/{d.tournament.max_team_size_field.unwrap_or(0)} players selected"
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
                                    div { class: "d-grid",
                                        button {
                                            r#type: "submit",
                                            class: "btn btn-success btn-lg",
                                            disabled: team1_selected().len() > d.tournament.max_team_size_field.unwrap_or(0) as usize
                                                || team2_selected().len() > d.tournament.max_team_size_field.unwrap_or(0) as usize
                                                || team1_has_unpaid
                                                || team2_has_unpaid,
                                            "Start Match"
                                        }
                                    }
                                }
                            }
                        }
                    }
                    div { class: "col-md-4",
                        div { class: "card",
                            div { class: "card-header",
                                h5 { class: "mb-0", "Match Info" }
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
                div {
                    class: "modal show",
                    style: "display: block; background: rgba(0,0,0,0.5);",
                    role: "dialog",
                    tabindex: "-1",
                    onclick: move |_| notes_modal_show.set(false),
                    div {
                        class: "modal-dialog",
                        onclick: move |ev| { ev.stop_propagation(); },
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Relevant Notes" }
                                button {
                                    r#type: "button",
                                    class: "btn-close",
                                    aria_label: "Close",
                                    onclick: move |_| notes_modal_show.set(false),
                                }
                            }
                            div { class: "modal-body",
                                match notes_modal_content().as_ref() {
                                    None => rsx! { div { class: "text-muted", "Loading…" } },
                                    Some(Err(e)) => rsx! { div { class: "text-danger", "{e}" } },
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
