//! Registered teams with on-demand player rosters in the same table.

use crate::api;
use crate::types::TeamWithCount;
use crate::Route;
use dioxus::prelude::*;

#[derive(Clone, PartialEq, Props)]
pub struct EventTeamsListProps {
    pub teams: Vec<TeamWithCount>,
    pub card_title: String,
    /// Tournament URL, or `league:<slug>` for leagues and their events.
    pub event_scope: String,
    /// Refresh cached rosters after registration changes on this page.
    pub revision: u32,
    #[props(default = true)]
    pub show_registered_at: bool,
    #[props(default)]
    pub max_team_size_roster: Option<u32>,
}

#[component]
pub fn EventTeamsList(props: EventTeamsListProps) -> Element {
    let EventTeamsListProps {
        teams,
        card_title,
        event_scope,
        revision,
        show_registered_at,
        max_team_size_roster,
    } = props;
    if teams.is_empty() {
        return rsx! {};
    }
    let count = teams.len();
    rsx! {
        div { class: "row mt-4",
            div { class: "col-12",
                div { class: "card data-section event-roster",
                    div { class: "card-header", h2 { class: "h5 mb-0", "{card_title} ({count})" } }
                    div { class: "card-body",
                        div { class: "table-responsive",
                            table { class: "table roster-table event-teams-table",
                                thead {
                                    tr {
                                        th { scope: "col", "Team" }
                                        th { scope: "col", class: "table-number", "Players" }
                                        if show_registered_at {
                                            th { scope: "col", class: "table-nowrap", "Registered" }
                                        }
                                        th { scope: "col", span { class: "visually-hidden", "Player roster" } }
                                    }
                                }
                                tbody {
                                    for team in teams {
                                        EventTeamRow {
                                            key: "{event_scope}/{team.team_id}",
                                            team,
                                            event_scope: event_scope.clone(),
                                            revision,
                                            show_registered_at,
                                            max_team_size_roster,
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
fn EventTeamRow(
    team: TeamWithCount,
    event_scope: String,
    revision: u32,
    show_registered_at: bool,
    max_team_size_roster: Option<u32>,
) -> Element {
    let mut expanded = use_signal(|| false);
    let mut requested = use_signal(|| false);
    let team_id = team.team_id.clone();
    // The parent keys this row by event and team. Closing it preserves the result;
    // moving to another registration scope creates a fresh resource.
    let mut roster = use_resource(use_reactive(&revision, move |_| {
        let should_load = requested();
        let team_id = team_id.clone();
        let event_scope = event_scope.clone();
        async move {
            if should_load {
                Some(api::team_registration_players(&team_id, &event_scope).await)
            } else {
                None
            }
        }
    }));
    let pending = roster.state() == UseResourceState::Pending;
    let roster_value = roster.read();
    let players = roster_value.as_ref().and_then(Option::as_ref);
    let backend = api::base_url();
    let team_name = team.pseudonym.as_deref().unwrap_or(&team.team_name);
    let panel_id = format!("event-team-players-{}", team.team_id);
    let toggle_id = format!("event-team-toggle-{}", team.team_id);
    let retry_focus_id = toggle_id.clone();
    let toggle_label = format!(
        "{} players for {team_name}",
        if expanded() { "Hide" } else { "View" }
    );
    let columns = if show_registered_at { 4 } else { 3 };

    rsx! {
        tr { class: "event-team-summary",
            td { class: "table-copy",
                div { class: "roster-identity",
                    if let Some(photo) = &team.profile_photo {
                        img { src: "{backend}/static/{photo}", alt: "", class: "event-roster-avatar" }
                    }
                    Link { to: Route::TeamProfilePage { id: team.team_id.clone() }, class: "roster-name",
                        strong { "{team_name}" }
                    }
                }
            }
            td { class: "table-number event-team-count", "data-label": "Players",
                span { class: "table-nowrap",
                    span { class: "roster-count", "{team.player_count}" }
                    if let Some(max) = max_team_size_roster { span { " / {max}" } }
                }
            }
            if show_registered_at {
                td { class: "table-nowrap roster-wide event-team-date", "data-label": "Registered",
                    {crate::time_format::format_registration_timestamp(team.registered_at.as_deref().unwrap_or("—"))}
                }
            }
            td { class: "event-team-action",
                button {
                    r#type: "button", id: toggle_id.clone(), class: "event-roster-toggle", aria_label: toggle_label,
                    aria_expanded: expanded(), aria_controls: panel_id.clone(),
                    onclick: move |_| {
                        expanded.toggle();
                        if expanded() && !requested() { requested.set(true); }
                    },
                    if expanded() { "Hide players" } else { "View players" }
                    i { class: if expanded() { "fas fa-chevron-up" } else { "fas fa-chevron-down" }, aria_hidden: "true" }
                }
            }
        }
        tr { class: "event-team-players-row", hidden: !expanded(),
            td { colspan: columns,
                div { id: panel_id, class: "event-team-players", role: "group", aria_label: "{team_name} players", aria_busy: pending,
                    match players {
                        Some(Ok(players)) => rsx! {
                            if players.is_empty() {
                                p { class: "event-roster-message", "No players registered yet." }
                            } else {
                                ul { class: "event-roster-players",
                                    for item in players {
                                        li { key: "{item.registration.player}",
                                            Link {
                                                to: Route::PlayerProfilePage { id: item.registration.player.clone() },
                                                class: "event-roster-player-link",
                                                if let Some(photo) = item.player.as_ref().and_then(|p| p.profile_photo.as_ref()) {
                                                    img { src: "{backend}/static/{photo}", alt: "", class: "event-roster-avatar" }
                                                } else {
                                                    span { class: "event-roster-avatar event-roster-avatar-placeholder", aria_hidden: "true",
                                                        i { class: "fas fa-user" }
                                                    }
                                                }
                                                span { class: "event-roster-player-name",
                                                    "{item.player.as_ref().map(|p| p.name.as_str()).unwrap_or(&item.registration.player)}"
                                                }
                                                if item.registration.jersey_name.as_deref().is_some_and(|name| !name.trim().is_empty())
                                                    || item.registration.jersey_number.as_deref().is_some_and(|number| !number.trim().is_empty()) {
                                                    span { class: "event-roster-jersey",
                                                        span { class: "visually-hidden", "Jersey: " }
                                                        if let Some(name) = item.registration.jersey_name.as_deref().filter(|name| !name.trim().is_empty()) {
                                                            span { "{name}" }
                                                        }
                                                        if let Some(number) = item.registration.jersey_number.as_deref().filter(|number| !number.trim().is_empty()) {
                                                            span { class: "event-roster-jersey-number", "#{number}" }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        Some(Err(_)) => rsx! {
                            div { class: "event-roster-retry",
                                p { class: "event-roster-message", role: "status",
                                    if pending { "Loading players…" } else { "Players could not be loaded." }
                                }
                                button { r#type: "button", class: "btn btn-outline-secondary", disabled: pending,
                                    aria_label: "Retry loading players for {team_name}",
                                    onclick: move |_| {
                                        roster.restart();
                                        // Retry disappears on success; keep focus on a stable control.
                                        crate::appearance::focus_control(&retry_focus_id);
                                    }, "Retry"
                                }
                            }
                        },
                        None => rsx! { p { class: "event-roster-message", role: "status", "Loading players…" } },
                    }
                }
            }
        }
    }
}
