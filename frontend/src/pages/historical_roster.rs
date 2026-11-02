use crate::api;
use crate::components::ToolPageHeader;
use crate::types::HistoricalRosterResponse;
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn HistoricalRoster(url: String, team_id: String) -> Element {
    let mut roster = use_signal(|| None::<HistoricalRosterResponse>);
    let mut load_error = use_signal(|| None::<String>);
    let mut action_error = use_signal(|| None::<String>);
    let mut query = use_signal(String::new);
    let mut jersey_name = use_signal(String::new);
    let mut jersey_number = use_signal(String::new);
    let backend = api::base_url();

    let url_for_load = url.clone();
    let team_id_for_load = team_id.clone();
    use_future(move || {
        let url = url_for_load.clone();
        let team_id = team_id_for_load.clone();
        async move {
            match api::historical_roster(&url, &team_id).await {
                Ok(response) => roster.set(Some(response)),
                Err(error) => load_error.set(Some(error)),
            }
        }
    });

    let results = use_resource(move || {
        let search = query().trim().to_string();
        async move {
            if search.chars().count() < 2 {
                return Ok(Vec::new());
            }
            api::players_list(&search, 1).await.map(|response| response.players)
        }
    });

    let url_for_invite = url.clone();
    let team_id_for_invite = team_id.clone();
    let invite = use_callback(move |player_id: String| {
        let url = url_for_invite.clone();
        let team_id = team_id_for_invite.clone();
        let name = jersey_name().trim().to_string();
        let number = jersey_number().trim().to_string();
        spawn(async move {
            match api::historical_invite_player(&url, &team_id, &player_id, &name, &number).await {
                Ok(response) => {
                    roster.set(Some(response));
                    action_error.set(None);
                    query.set(String::new());
                    jersey_name.set(String::new());
                    jersey_number.set(String::new());
                }
                Err(error) => action_error.set(Some(error)),
            }
        });
    });

    if let Some(error) = load_error() {
        return rsx! {
            div { class: "alert alert-danger", "{error}" }
        };
    }
    let Some(data) = roster() else {
        return rsx! {
            p { class: "text-muted", "Loading…" }
        };
    };
    let on_roster: Vec<String> = data.players.iter().map(|player| player.player_id.clone()).collect();
    let candidates: Vec<_> = match results.read().as_ref() {
        Some(Ok(players)) => players
            .iter()
            .filter(|player| !on_roster.contains(&player.id))
            .take(8)
            .cloned()
            .collect(),
        _ => Vec::new(),
    };
    let first_candidate = candidates.first().map(|player| player.id.clone());
    let confirmed_count = data.players.iter().filter(|player| player.status == "CONFIRMED").count();
    let invited_count = data.players.len() - confirmed_count;

    rsx! {
        ToolPageHeader {
            title: format!("{} roster", data.team_name),
            description: Some(format!("{confirmed_count} confirmed{}", if invited_count > 0 { format!(", {invited_count} invited") } else { String::new() })),
        }
        div { class: "row g-4",
            div { class: "col-lg-5",
                div { class: "edit-section",
                    div { class: "edit-section-heading", h2 { class: "h5 mb-0", "Invite a player" } }
                    div { class: "edit-section-body",
                        p { class: "small text-muted",
                            "Players appear on the roster once they accept the invitation from their Arctos home page."
                        }
                        if let Some(error) = action_error() {
                            div { class: "alert alert-danger py-2", "{error}" }
                        }
                        div { class: "row g-2 mb-2",
                            div { class: "col-8",
                                input {
                                    class: "form-control form-control-sm",
                                    placeholder: "Jersey name (optional)",
                                    value: "{jersey_name}",
                                    oninput: move |ev| jersey_name.set(ev.value()),
                                }
                            }
                            div { class: "col-4",
                                input {
                                    class: "form-control form-control-sm",
                                    placeholder: "Number",
                                    value: "{jersey_number}",
                                    oninput: move |ev| jersey_number.set(ev.value()),
                                }
                            }
                        }
                        input {
                            class: "form-control",
                            placeholder: "Search players by name",
                            autofocus: true,
                            value: "{query}",
                            oninput: move |ev| query.set(ev.value()),
                            onkeydown: move |ev: Event<KeyboardData>| {
                                if ev.key().to_string() == "Enter" {
                                    ev.prevent_default();
                                    if let Some(player_id) = first_candidate.clone() {
                                        invite.call(player_id);
                                    }
                                }
                            },
                        }
                        if !candidates.is_empty() {
                            div { class: "list-group mt-2",
                                for (position, candidate) in candidates.into_iter().enumerate() {
                                    {
                                        let player_id = candidate.id.clone();
                                        rsx! {
                                            button {
                                                key: "{candidate.id}",
                                                class: "list-group-item list-group-item-action d-flex justify-content-between align-items-center",
                                                onclick: move |_| invite.call(player_id.clone()),
                                                span {
                                                    "{candidate.name}"
                                                    if let Some(location) = candidate.location.as_ref() {
                                                        span { class: "text-muted small ms-2", "{location}" }
                                                    }
                                                }
                                                span { class: "text-primary small",
                                                    if position == 0 { "Invite (Enter)" } else { "Invite" }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        } else if query().trim().chars().count() >= 2 && results.read().is_some() {
                            div { class: "small text-muted mt-2", "No matching players who aren't already on the roster." }
                        }
                    }
                }
            }
            div { class: "col-lg-7",
                div { class: "edit-section",
                    div { class: "edit-section-heading d-flex justify-content-between align-items-center",
                        h2 { class: "h5 mb-0", "Roster ({confirmed_count})" }
                        if invited_count > 0 {
                            span { class: "small text-muted", "{invited_count} invited" }
                        }
                    }
                    if data.players.is_empty() {
                        div { class: "edit-section-body text-muted", "No players yet." }
                    } else {
                        div { class: "table-responsive",
                            table { class: "table roster-table align-middle mb-0",
                                thead {
                                    tr {
                                        th { "Player" }
                                        th { "Jersey" }
                                        th { "Status" }
                                        th {}
                                    }
                                }
                                tbody {
                                    for player in data.players.iter() {
                                        {
                                            let url = url.clone();
                                            let team_id = team_id.clone();
                                            let url_for_approve = url.clone();
                                            let url_for_decline = url.clone();
                                            let team_for_approve = team_id.clone();
                                            let team_for_decline = team_id.clone();
                                            let player_id = player.player_id.clone();
                                            let player_name = player.player_name.clone();
                                            let registration_id = player.id;
                                            let jersey = match (&player.jersey_number, &player.jersey_name) {
                                                (Some(number), Some(name)) => format!("#{number} {name}"),
                                                (None, Some(name)) => name.clone(),
                                                (Some(number), None) => format!("#{number}"),
                                                (None, None) => String::new(),
                                            };
                                            rsx! {
                                                tr { key: "{player.player_id}",
                                                    td { class: "table-copy", "data-label": "Player",
                                                        div { class: "roster-identity",
                                                            if let Some(photo) = &player.profile_photo {
                                                                img { src: "{backend}/static/{photo}", alt: "", class: "event-roster-avatar" }
                                                            } else {
                                                                span { class: "event-roster-avatar event-roster-avatar-placeholder", aria_hidden: "true",
                                                                    i { class: "fas fa-user" }
                                                                }
                                                            }
                                                            Link { to: Route::PlayerProfilePage { id: player.player_id.clone() }, class: "roster-name", "{player.player_name}" }
                                                        }
                                                    }
                                                    td { class: "table-nowrap", "data-label": "Jersey", "{jersey}" }
                                                    td { class: "table-nowrap", "data-label": "Status",
                                                        if player.status == "CONFIRMED" {
                                                            span { class: "badge arctos-badge-success", "Confirmed" }
                                                        } else if player.status == "REQUESTED" {
                                                            span { class: "badge arctos-badge-warning", "Requested" }
                                                        } else {
                                                            span { class: "badge arctos-badge-neutral", "Invited" }
                                                        }
                                                    }
                                                    td { class: "text-end table-nowrap", "data-label": "Actions",
                                                        if player.status == "REQUESTED" {
                                                            button {
                                                                class: "btn btn-sm btn-success me-1",
                                                                onclick: move |_| {
                                                                    let url = url_for_approve.clone();
                                                                    let team_id = team_for_approve.clone();
                                                                    spawn(async move {
                                                                        match api::historical_decide_roster_request(&url, &team_id, registration_id, true).await {
                                                                            Ok(response) => {
                                                                                roster.set(Some(response));
                                                                                action_error.set(None);
                                                                            }
                                                                            Err(error) => action_error.set(Some(error)),
                                                                        }
                                                                    });
                                                                },
                                                                "Approve"
                                                            }
                                                            button {
                                                                class: "btn btn-sm btn-outline-secondary",
                                                                onclick: move |_| {
                                                                    let url = url_for_decline.clone();
                                                                    let team_id = team_for_decline.clone();
                                                                    spawn(async move {
                                                                        match api::historical_decide_roster_request(&url, &team_id, registration_id, false).await {
                                                                            Ok(response) => {
                                                                                roster.set(Some(response));
                                                                                action_error.set(None);
                                                                            }
                                                                            Err(error) => action_error.set(Some(error)),
                                                                        }
                                                                    });
                                                                },
                                                                "Decline"
                                                            }
                                                        } else {
                                                        button {
                                                            class: "btn btn-sm btn-outline-danger",
                                                            title: if player.status == "CONFIRMED" { "Remove from roster" } else { "Withdraw invitation" },
                                                            onclick: move |_| {
                                                                let confirmed = web_sys::window()
                                                                    .and_then(|window| window.confirm_with_message(&format!("Remove {player_name} from this roster?")).ok())
                                                                    .unwrap_or(false);
                                                                if !confirmed {
                                                                    return;
                                                                }
                                                                let url = url.clone();
                                                                let team_id = team_id.clone();
                                                                let player_id = player_id.clone();
                                                                spawn(async move {
                                                                    match api::historical_remove_player(&url, &team_id, &player_id).await {
                                                                        Ok(response) => {
                                                                            roster.set(Some(response));
                                                                            action_error.set(None);
                                                                        }
                                                                        Err(error) => action_error.set(Some(error)),
                                                                    }
                                                                });
                                                            },
                                                            i { class: "fas fa-user-minus" }
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
