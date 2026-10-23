//! Event description and compact registration details shared by events and leagues.

use crate::Route;
use dioxus::prelude::*;

#[derive(Clone, PartialEq, Props)]
pub struct EventAboutProps {
    pub card_title: String,
    pub about_html: Option<String>,
    pub about_raw: Option<String>,
    pub empty_message: String,
    pub team_registration_open: bool,
    pub player_registration_open: bool,
    pub team_fee: Option<f64>,
    pub player_fee: Option<f64>,
    pub max_teams: Option<u32>,
    pub max_roster: Option<u32>,
    pub max_field: Option<u32>,
    pub waiver_required: bool,
    pub terms_link: Option<String>,
    pub league_url: Option<String>,
}

#[component]
pub fn EventAbout(props: EventAboutProps) -> Element {
    let fee = |value: Option<f64>| {
        value
            .map(|v| {
                if v == 0.0 {
                    "Free".to_string()
                } else {
                    format!("${v:.2}")
                }
            })
            .unwrap_or_else(|| "Not specified".to_string())
    };
    let limit = |value: Option<u32>| {
        value
            .map(|v| v.to_string())
            .unwrap_or_else(|| "Not specified".to_string())
    };
    rsx! {
        div { class: "event-info-grid",
            section { class: "card event-about-card",
                div { class: "card-header", h2 { "{props.card_title}" } }
                div { class: "card-body",
                    if let Some(about) = props.about_raw.as_ref().filter(|about| !about.trim().is_empty()) {
                        if let Some(html) = props.about_html.as_ref().filter(|html| !html.is_empty()) {
                            div { class: "markdown-content", dangerous_inner_html: "{html}" }
                        } else {
                            div { class: "markdown-content", style: "white-space: pre-wrap;", "{about}" }
                        }
                    } else {
                        p { class: "text-muted mb-0", "{props.empty_message}" }
                    }
                }
            }
            section { class: "card event-registration-card",
                div { class: "card-header", h2 { i { class: "fas fa-clipboard-list", aria_hidden: "true" } "Registration details" } }
                div { class: "card-body",
                    if let Some(league_url) = props.league_url {
                        p { class: "event-scope-note", "Registration and fees cover every event in this " Link { to: Route::LeagueHome { league_url }, "league" } "." }
                    }
                    dl { class: "event-detail-list",
                        div { dt { "Team registration" } dd { span { class: if props.team_registration_open { "badge arctos-badge-success" } else { "badge arctos-badge-neutral" }, if props.team_registration_open { "Open" } else { "Closed" } } } }
                        div { dt { "Player registration" } dd { span { class: if props.player_registration_open { "badge arctos-badge-success" } else { "badge arctos-badge-neutral" }, if props.player_registration_open { "Open" } else { "Closed" } } } }
                        div { dt { "Team fee" } dd { "{fee(props.team_fee)}" } }
                        div { dt { "Player fee" } dd { "{fee(props.player_fee)}" } }
                    }
                    if props.waiver_required {
                        p { class: "event-detail-note", i { class: "far fa-file-alt", aria_hidden: "true" } "A signed waiver is required for each player." }
                    }
                    if let Some(link) = props.terms_link.as_ref().filter(|link| !link.is_empty()) {
                        a { href: "{link}", target: "_blank", rel: "noopener noreferrer", class: "event-terms-link", "Registration terms" i { class: "fas fa-arrow-up-right-from-square", aria_hidden: "true" } }
                    }
                }
            }
            section { class: "card event-limits-card",
                div { class: "card-header", h2 { i { class: "fas fa-users", aria_hidden: "true" } "Team limits" } }
                div { class: "card-body",
                    dl { class: "event-detail-list",
                        div { dt { "Team capacity" } dd { "{limit(props.max_teams)}" } }
                        div { dt { "Players per roster" } dd { "{limit(props.max_roster)}" } }
                        div { dt { "Players on field" } dd { "{limit(props.max_field)}" } }
                    }
                }
            }
        }
    }
}
