use super::index::{final_day_has_passed, format_date_display, listing_date, UserRegBadges};
use crate::api;
use crate::components::{OrganizeContext, OrganizeMenu, PageIntro};
use crate::types::{LeagueEventDates, LeagueInfo, UserRegStatus};
use crate::Route;
use chrono::{Local, NaiveDate};
use dioxus::prelude::*;

fn league_is_past(dates: Option<&LeagueEventDates>, today: NaiveDate) -> bool {
    dates.filter(|dates| dates.event_count > 0).is_some_and(|dates| {
        final_day_has_passed(dates.end_date.as_deref().or(dates.start_date.as_deref()), today)
    })
}

#[component]
pub fn LeaguesIndex() -> Element {
    let mut data = use_resource(move || async move {
        api::leagues_list().await.map_err(|error| error.to_string())
    });
    let mut show_past = use_signal(|| false);
    let mut search = use_signal(String::new);
    let val = data.value();
    let today = Local::now().date_naive();
    let counts = val.read().as_ref().and_then(|result| result.as_ref().ok()).map(|response| {
        let past = response.leagues.iter().filter(|league| {
            league_is_past(response.event_dates.get(&league.league_url), today)
        }).count();
        (response.leagues.len() - past, past)
    });
    rsx! {
        PageIntro { page_key: "leagues", title: "Leagues", focus_target: "page-content",
            "A league connects tournaments across a season, with shared registration and standings."
        }
        div { class: "arctos-discovery-toolbar",
            div { class: "arctos-discovery-actions",
                div { class: "arctos-segmented", role: "group", aria_label: "League dates",
                    button {
                        r#type: "button",
                        class: if !show_past() { "arctos-segment is-active" } else { "arctos-segment" },
                        aria_pressed: "{!show_past()}",
                        onclick: move |_| show_past.set(false),
                        "Active "
                        if let Some((active, _)) = counts { span { class: "arctos-count", "{active}" } }
                    }
                    button {
                        r#type: "button",
                        class: if show_past() { "arctos-segment is-active" } else { "arctos-segment" },
                        aria_pressed: "{show_past()}",
                        onclick: move |_| show_past.set(true),
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
                    id: "league-search", r#type: "search", class: "form-control",
                    placeholder: "Find a league…", value: "{search()}",
                    oninput: move |event| search.set(event.value()),
                }
            }
        }
        if let Some(Ok(response)) = val.read().as_ref() {
            {
                let query = search().trim().to_lowercase();
                let mut filtered: Vec<_> = response.leagues.iter().filter(|league| {
                    league_is_past(response.event_dates.get(&league.league_url), today) == show_past()
                        && league.name.to_lowercase().contains(&query)
                }).collect();
                if show_past() {
                    filtered.sort_by_key(|league| {
                        let end = response.event_dates.get(&league.league_url).and_then(|dates| {
                            dates.end_date.as_deref().or(dates.start_date.as_deref()).and_then(listing_date)
                        });
                        (std::cmp::Reverse(end), league.name.to_lowercase())
                    });
                } else {
                    filtered.sort_by_key(|league| {
                        let start = response.event_dates.get(&league.league_url)
                            .and_then(|dates| dates.start_date.as_deref()).and_then(listing_date);
                        (start.is_none(), start, league.name.to_lowercase())
                    });
                }
                rsx! {
                    h2 { class: "visually-hidden", if show_past() { "Past leagues" } else { "Active leagues" } }
                    if filtered.is_empty() {
                        div { class: "arctos-empty-state", role: "status",
                            i { class: "fas fa-trophy", aria_hidden: "true" }
                            h3 { if !query.is_empty() { "No matching leagues" } else if show_past() { "No past leagues yet" } else { "No active leagues" } }
                            p { if !query.is_empty() { "Try another league name." } else if show_past() { "Leagues appear here after their final event has ended." } else { "No leagues with current or upcoming events are listed yet." } }
                            if !query.is_empty() {
                                button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| search.set(String::new()), "Clear search" }
                            }
                        }
                    } else {
                        div { class: "arctos-card-grid arctos-league-grid",
                            for league in filtered {
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
                }
            }
        } else if let Some(Err(error)) = val.read().as_ref() {
            div { class: "arctos-empty-state", role: "alert",
                h2 { "Leagues could not be loaded" }
                p { "{error}" }
                button { class: "btn btn-primary", r#type: "button", onclick: move |_| data.restart(), "Try again" }
            }
        } else {
            div { class: "arctos-empty-state", role: "status", "Loading leagues…" }
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn dates(start: Option<&str>, end: Option<&str>, event_count: u32) -> LeagueEventDates {
        LeagueEventDates {
            start_date: start.map(str::to_owned),
            end_date: end.map(str::to_owned),
            event_count,
        }
    }

    #[test]
    fn final_event_day_remains_active_in_the_viewers_calendar() {
        let dates = dates(Some("2026-04-01T00:00:00"), Some("2026-10-22T00:00:00"), 3);
        assert!(!league_is_past(Some(&dates), NaiveDate::from_ymd_opt(2026, 10, 22).unwrap()));
        assert!(league_is_past(Some(&dates), NaiveDate::from_ymd_opt(2026, 10, 23).unwrap()));
    }

    #[test]
    fn a_future_round_keeps_an_already_started_league_active() {
        let dates = dates(Some("2026-04-01T00:00:00"), Some("2026-12-01T00:00:00"), 4);
        assert!(!league_is_past(Some(&dates), NaiveDate::from_ymd_opt(2026, 10, 22).unwrap()));
    }

    #[test]
    fn a_round_without_an_end_date_uses_its_start_date() {
        let dates = dates(Some("2026-10-21T00:00:00"), None, 1);
        assert!(league_is_past(Some(&dates), NaiveDate::from_ymd_opt(2026, 10, 22).unwrap()));
    }

    #[test]
    fn missing_or_invalid_dates_and_empty_leagues_remain_active() {
        let today = NaiveDate::from_ymd_opt(2026, 10, 22).unwrap();
        assert!(!league_is_past(None, today));
        assert!(!league_is_past(Some(&dates(None, None, 0)), today));
        assert!(!league_is_past(Some(&dates(Some("2020-01-01"), Some("invalid"), 1)), today));
        assert!(!league_is_past(Some(&dates(Some("2020-01-01"), Some("2020-01-01"), 0)), today));
    }
}
