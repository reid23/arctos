use super::directory_state::{use_directory, use_directory_scroll, Directory};
use super::index::{format_date_display, listing_date, UserRegBadges};
use crate::api;
use crate::components::{OrganizeContext, OrganizeMenu, PageIntro};
use crate::types::{LeagueEventDates, LeagueInfo, UserRegStatus};
use crate::Route;
use chrono::Local;
use dioxus::prelude::*;

#[component]
pub fn LeaguesIndexWithQuery(q: String, view: String, page: String) -> Element {
    rsx! { LeaguesIndex {} }
}

#[component]
pub fn LeaguesIndex() -> Element {
    let account = crate::auth::use_account();
    let mut data = use_resource(move || {
        // A Resource's Future waits for its next completion; awaiting an
        // already-completed account would wait forever. Subscribe to its value
        // and state instead, and restart this resource when the account settles.
        let pending = *account.state().read() == UseResourceState::Pending;
        let identity = account_identity(account.read().as_ref());
        async move {
            if pending {
                return std::future::pending().await;
            }
            let result =
                crate::league_cache::load(Local::now().date_naive(), identity.clone()).await;
            (identity, result)
        }
    });
    let controls = use_directory(Directory::Leagues, true);
    let mut search = controls.search;
    let show_past = move || (controls.state)().past;
    // A cached revisit renders its cards on the first frame, before the async
    // resource is polled. The current account must be settled before reuse.
    let account_pending = *account.state().read() == UseResourceState::Pending;
    let identity = account_identity(account.read().as_ref());
    let cached = if !account_pending {
        identity
            .as_ref()
            .and_then(|identity| crate::league_cache::peek(Local::now().date_naive(), &identity))
    } else {
        None
    };
    let data_pending = *data.state().read() == UseResourceState::Pending;
    let resolved = data
        .read()
        .as_ref()
        // A retry may retain the previous resource value. Only the explicit
        // account/day cache is safe to show before the new request settles.
        .filter(|(owner, _)| !data_pending && !account_pending && owner == &identity)
        .map(|(_, result)| result.clone())
        .or_else(|| cached.map(Ok));
    use_directory_scroll(controls, resolved.as_ref().is_some_and(Result::is_ok));
    let counts = resolved
        .as_ref()
        .and_then(|result| result.as_ref().ok())
        .map(|response| {
            let query = search()
                .trim()
                .chars()
                .take(200)
                .collect::<String>()
                .to_lowercase();
            let matching: Vec<_> = response
                .leagues
                .iter()
                .filter(|league| league.name.to_lowercase().contains(&query))
                .collect();
            let past = matching
                .iter()
                .filter(|league| response.past_leagues.contains(&league.league_url))
                .count();
            (matching.len() - past, past)
        });
    rsx! {
        PageIntro { page_key: "leagues", title: "Leagues", focus_target: "page-content",
            "A league is a long-running series of events with shared registration."
        }
        div { class: "arctos-discovery-toolbar",
            div { class: "arctos-discovery-actions",
                div { class: "arctos-segmented", role: "group", aria_label: "League dates",
                    button {
                        r#type: "button",
                        class: if !show_past() { "arctos-segment is-active" } else { "arctos-segment" },
                        aria_pressed: "{!show_past()}",
                        onclick: move |_| controls.filter(false),
                        "Active "
                        if let Some((active, _)) = counts { span { class: "arctos-count", "{active}" } }
                    }
                    button {
                        r#type: "button",
                        class: if show_past() { "arctos-segment is-active" } else { "arctos-segment" },
                        aria_pressed: "{show_past()}",
                        onclick: move |_| controls.filter(true),
                        "Past "
                        if let Some((_, past)) = counts { span { class: "arctos-count", "{past}" } }
                    }
                }
                OrganizeMenu { context: OrganizeContext::Leagues }
            }
            div { class: "arctos-event-search",
                label { class: "visually-hidden", r#for: "league-search", "Search league names" }
                i { class: "fas fa-search", aria_hidden: "true" }
                input {
                    id: "league-search", r#type: "search", class: "form-control", maxlength: "200",
                    placeholder: "Find a league…", value: "{search()}",
                    oninput: move |event| search.set(event.value()),
                        onblur: move |_| controls.flush(),
                        onkeydown: move |event| { if event.key() == Key::Enter { controls.flush(); } },
                }
            }
        }
        if let Some(Ok(response)) = resolved.as_ref() {
            {
                let query = search().trim().chars().take(200).collect::<String>().to_lowercase();
                let mut filtered: Vec<_> = response.leagues.iter().filter(|league| {
                    response.past_leagues.contains(&league.league_url) == show_past()
                        && league.name.to_lowercase().contains(&query)
                }).collect();
                if show_past() {
                    filtered.sort_by_key(|league| {
                        let end = response.event_dates.get(&league.league_url).and_then(|dates| {
                            dates.end_date.as_deref().or(dates.start_date.as_deref()).and_then(listing_date)
                        });
                        (std::cmp::Reverse(end), league.name.to_lowercase(), league.league_url.clone())
                    });
                } else {
                    filtered.sort_by_key(|league| {
                        let start = response.event_dates.get(&league.league_url)
                            .and_then(|dates| dates.start_date.as_deref()).and_then(listing_date);
                        (start.is_none(), start, league.name.to_lowercase(), league.league_url.clone())
                    });
                }
                // Match the public document's default page size when the
                // initialized application takes over a directory URL.
                let total_pages = filtered.len().div_ceil(24).max(1) as u32;
                let page = (controls.state)().page.min(total_pages);
                let offset = (page as usize - 1) * 24;
                rsx! {
                    h2 { class: "visually-hidden", if show_past() { "Past leagues" } else { "Active leagues" } }
                    if filtered.is_empty() {
                        div { class: "arctos-empty-state", role: "status",
                            i { class: "fas fa-trophy", aria_hidden: "true" }
                            h3 { if !query.is_empty() { "No matching leagues" } else if show_past() { "No past leagues yet" } else { "No active leagues" } }
                            p { if !query.is_empty() { "Try another league name." } else if show_past() { "Leagues appear here after their final event has ended." } else { "No leagues with current or upcoming events are listed yet." } }
                            if !query.is_empty() {
                                button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| controls.clear(), "Clear search" }
                            }
                        }
                    } else {
                        div { class: "arctos-card-grid arctos-league-grid",
                            for league in filtered.into_iter().skip(offset).take(24) {
                                LeagueCard {
                                    key: "{league.league_url}",
                                    league: league.clone(),
                                    team_count: response.team_counts.get(&league.league_url).copied().unwrap_or(0),
                                    user_reg_status: response.user_reg_status.get(&league.league_url).cloned(),
                                    event_dates: response.event_dates.get(&league.league_url).cloned(),
                                    past: show_past(),
                                }
                            }
                        }
                    }
                    if total_pages > 1 {
                        nav { class: "arctos-pagination", aria_label: "League pages",
                            if page > 1 {
                                button { class: "btn btn-outline-secondary", r#type: "button",
                                    onclick: move |_| controls.page(page - 1, true), "Previous" }
                            }
                            span { class: "arctos-page-position", aria_live: "polite", "Page {page}" }
                            if page < total_pages {
                                button { class: "btn btn-outline-secondary", r#type: "button",
                                    onclick: move |_| controls.page(page + 1, true), "Next" }
                            }
                        }
                    }
                }
            }
        } else if let Some(Err(error)) = resolved.as_ref() {
            div { class: "arctos-empty-state", role: "alert",
                h2 { "Leagues could not be loaded" }
                p { "{error}" }
                button { class: "btn btn-primary", r#type: "button", onclick: move |_| {
                    crate::league_cache::invalidate();
                    data.restart();
                }, "Try again" }
            }
        } else {
            div { class: "arctos-empty-state", role: "status", "Loading leagues…" }
        }
    }
}

fn account_identity(
    account: Option<&Result<crate::types::User, api::AccountError>>,
) -> Option<String> {
    match account {
        Some(Ok(user)) => Some(format!("{}:{}", user.user_type, user.id)),
        Some(Err(api::AccountError::Unauthenticated)) => Some("anonymous".into()),
        _ => None,
    }
}

#[cfg(test)]
mod cache_identity_tests {
    use super::*;

    #[test]
    fn unresolved_or_failed_account_checks_do_not_get_a_cache_partition() {
        assert_eq!(account_identity(None), None);
        assert_eq!(
            account_identity(Some(&Err(api::AccountError::Unavailable))),
            None
        );
        assert_eq!(
            account_identity(Some(&Err(api::AccountError::Unauthenticated))),
            Some("anonymous".into())
        );
    }
}

#[component]
fn LeagueCard(
    league: LeagueInfo,
    team_count: u32,
    user_reg_status: Option<UserRegStatus>,
    event_dates: Option<LeagueEventDates>,
    past: bool,
) -> Element {
    let registration_label = match (
        league.team_registration_open,
        league.player_registration_open,
    ) {
        (true, true) => "Registration open",
        (true, false) => "Team registration open",
        (false, true) => "Player registration open",
        _ => "Registration closed",
    };
    rsx! {
        article { class: "card arctos-event-card",
            div { class: "card-body",
                div { class: "arctos-card-statuses",
                    if !league.published { span { class: "badge arctos-badge-warning", "Unpublished" } }
                    if past {
                        span { class: "badge arctos-badge-neutral", "Past league" }
                    } else {
                        span { class: if league.team_registration_open || league.player_registration_open { "badge arctos-badge-success" } else { "badge arctos-badge-neutral" }, "{registration_label}" }
                    }
                }
                div { class: "arctos-league-identity",
                    div { class: "arctos-brand-mark", aria_hidden: "true", i { class: "fas fa-trophy" } }
                    div {
                        h2 { class: "arctos-card-title", Link { to: Route::LeagueHome { league_url: league.league_url.clone() }, "{league.name}" } }
                        p { class: "arctos-league-caption", "Shared registration" }
                    }
                }
                ul { class: "arctos-meta-list",
                    if let Some(dates) = event_dates.as_ref() {
                        if let Some(start) = dates.start_date.as_deref().or(dates.end_date.as_deref()) {
                            li { i { class: "far fa-calendar-alt", aria_hidden: "true" } "{format_date_display(start, dates.end_date.as_ref())}" }
                        } else {
                            li { i { class: "far fa-calendar-alt", aria_hidden: "true" } "Dates to be announced" }
                        }
                        li { i { class: "far fa-calendar-check", aria_hidden: "true" }
                            "{dates.event_count} " if dates.event_count == 1 { "event" } else { "events" }
                        }
                    } else {
                        li { i { class: "far fa-calendar-alt", aria_hidden: "true" } "Dates to be announced" }
                    }
                    li { i { class: "fas fa-users", aria_hidden: "true" }
                        if let Some(max) = league.n_max_teams { "{team_count} / {max} teams registered" } else { "{team_count} teams registered" }
                    }
                }
                if let Some(urs) = user_reg_status {
                    div { class: "arctos-registration-summary", aria_label: "Your registration", UserRegBadges { urs } }
                }
            }
            div { class: "arctos-card-footer",
                Link { to: Route::LeagueHome { league_url: league.league_url }, class: "btn btn-outline-secondary w-100", "View league" i { class: "fas fa-arrow-right", aria_hidden: "true" } }
            }
        }
    }
}
