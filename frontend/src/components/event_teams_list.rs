//! Shared registered teams section: one surface with compact, labeled rows.

use crate::api;
use crate::types::TeamWithCount;
use crate::Route;
use dioxus::prelude::*;

#[derive(Clone, PartialEq, Props)]
pub struct EventTeamsListProps {
    pub teams: Vec<TeamWithCount>,
    pub card_title: String,
    /// Show a "Registration Date" column (tournament/league home use this).
    #[props(default = true)]
    pub show_registered_at: bool,
    /// If set, show "count / max" in Players column (matches tournament_home).
    #[props(default)]
    pub max_team_size_roster: Option<u32>,
}

#[component]
pub fn EventTeamsList(props: EventTeamsListProps) -> Element {
    let EventTeamsListProps {
        teams,
        card_title,
        show_registered_at,
        max_team_size_roster,
    } = props;
    let backend = api::base_url();
    if teams.is_empty() {
        return rsx! {};
    }
    let count = teams.len();
    let title = format!("{card_title} ({count})");
    rsx! {
        div { class: "row mt-4",
            div { class: "col-12",
                div { class: "card data-section event-roster",
                    div { class: "card-header", h5 { class: "mb-0", "{title}" } }
                    div { class: "card-body",
                        div { class: "table-responsive",
                            table { class: "table roster-table",
                                thead {
                                    tr {
                                        th { scope: "col", "Team Name" }
                                        th { scope: "col", class: "table-number", "Players" }
                                        if show_registered_at {
                                            th { scope: "col", class: "table-nowrap", "Registration Date" }
                                        }
                                    }
                                }
                                tbody {
                                    for team in teams.iter() {
                                        tr { key: "{team.team_id}",
                                            td { class: "table-copy",
                                                div { class: "roster-identity",
                                                    if let Some(photo) = &team.profile_photo {
                                                        img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle me-2", style: "width: 32px; height: 32px; object-fit: cover; flex-shrink: 0;" }
                                                    }
                                                    div {
                                                        Link { to: Route::TeamProfilePage { id: team.team_id.clone() }, class: "roster-name",
                                                            strong { "{team.pseudonym.as_deref().unwrap_or(&team.team_name)}" }
                                                        }
                                                    }
                                                }
                                            }
                                            td { class: "table-number", "data-label": "Players",
                                                span { class: "table-nowrap",
                                                    span { class: "roster-count", "{team.player_count}" }
                                                    if let Some(max) = max_team_size_roster {
                                                        span { " / {max}" }
                                                    }
                                                }
                                            }
                                            if show_registered_at {
                                                td { class: "table-nowrap roster-wide", "data-label": "Registered", {crate::time_format::format_registration_timestamp(team.registered_at.as_deref().unwrap_or("—"))} }
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
