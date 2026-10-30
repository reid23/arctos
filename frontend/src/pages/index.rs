use super::directory_state::{use_directory, use_directory_scroll, Directory};
use crate::api;
use crate::components::{OrganizeContext, OrganizeMenu, PageIntro, PaymentStatusBadge, RegistrationStatusBadge, WaiverStatusBadge};
use crate::types::{Tournament, UserRegStatus};
use crate::Route;
use chrono::{Local, NaiveDate};
use dioxus::prelude::*;

pub(super) fn format_date_display(start: &str, end: Option<&String>) -> String {
    crate::time_format::format_calendar_range(start, end.map(String::as_str))
}

/// An event stays upcoming until its final day has passed in the user's timezone.
fn is_past_in_local_tz(t: &Tournament) -> bool {
    let end_str = t.end_date.as_deref().unwrap_or(&t.start_date);
    final_day_has_passed(Some(end_str), Local::now().date_naive())
}

pub(super) fn listing_date(iso: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(iso.get(..10).unwrap_or(iso), "%Y-%m-%d").ok()
}

pub(super) fn final_day_has_passed(end: Option<&str>, today: NaiveDate) -> bool {
    end.and_then(listing_date).is_some_and(|end| end < today)
}

fn split_upcoming_past(tournaments: &[Tournament]) -> (Vec<Tournament>, Vec<Tournament>) {
    let (mut past, mut upcoming): (Vec<_>, Vec<_>) =
        tournaments.iter().cloned().partition(is_past_in_local_tz);
    upcoming.sort_by(|a, b| a.start_date.cmp(&b.start_date));
    past.sort_by(|a, b| {
        let ea = a.end_date.as_deref().unwrap_or(&a.start_date);
        let eb = b.end_date.as_deref().unwrap_or(&b.start_date);
        eb.cmp(ea)
    });
    (upcoming, past)
}

#[component]
pub fn IndexWithQuery(q: String, view: String) -> Element {
    rsx! { Index {} }
}

#[component]
pub fn Index() -> Element {
    let mut tournaments =
        use_resource(move || async move { api::tournaments().await.map_err(|e| e.to_string()) });
    let controls = use_directory(Directory::Events, true);
    let mut search = controls.search;
    let show_past = move || (controls.state)().past;
    let val = tournaments.value();
    use_directory_scroll(controls, val.read().as_ref().is_some_and(Result::is_ok));
    let counts = val.read().as_ref().and_then(|result| result.as_ref().ok()).map(|data| {
        let past = data.tournaments.iter().filter(|event| is_past_in_local_tz(event)).count();
        (data.tournaments.len() - past, past)
    });

    rsx! {
        PageIntro { page_key: "events", title: "Events", focus_target: "event-discovery",
            "Find "
            a { href: "https://en.wikipedia.org/wiki/Jugger", "jugger" }
            " tournaments and results."
        }
        section { id: "event-discovery", class: "arctos-discovery", aria_label: "Find an event", tabindex: "-1",
            div { class: "arctos-discovery-toolbar",
                div { class: "arctos-discovery-actions",
                    div { class: "arctos-segmented", role: "group", aria_label: "Event dates",
                        button {
                            r#type: "button",
                            class: if !show_past() { "arctos-segment is-active" } else { "arctos-segment" },
                            aria_pressed: "{!show_past()}",
                            onclick: move |_| controls.filter(false),
                            "Upcoming "
                            if let Some((upcoming, _)) = counts { span { class: "arctos-count", "{upcoming}" } }
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
                    OrganizeMenu { context: OrganizeContext::Events }
                }
                div { class: "arctos-event-search",
                    label { class: "visually-hidden", r#for: "event-search", "Search event names, locations, or leagues" }
                    i { class: "fas fa-search", aria_hidden: "true" }
                    input {
                        id: "event-search", r#type: "search", class: "form-control",
                        placeholder: "Find an event or location…", value: "{search()}",
                        oninput: move |event| search.set(event.value()),
                        onblur: move |_| controls.flush(),
                        onkeydown: move |event| { if event.key() == Key::Enter { controls.flush(); } },
                    }
                }
            }
            if let Some(Ok(data)) = val.read().as_ref() {
                {
                    let (upcoming, past) = split_upcoming_past(&data.tournaments);
                    let active = if show_past() { &past } else { &upcoming };
                    let query = search().trim().to_lowercase();
                    let filtered: Vec<_> = active.iter().filter(|t| {
                        t.name.to_lowercase().contains(&query)
                            || t.location.as_deref().unwrap_or("").to_lowercase().contains(&query)
                            || t.league.as_ref().is_some_and(|league| league.name.to_lowercase().contains(&query))
                    }).collect();
                    rsx! {
                        h2 { class: "visually-hidden", if show_past() { "Past events" } else { "Upcoming events" } }
                        if filtered.is_empty() {
                            div { class: "arctos-empty-state", role: "status",
                                i { class: "fas fa-calendar-alt", aria_hidden: "true" }
                                h3 { if !query.is_empty() { "No matching events" } else if show_past() { "No past events yet" } else { "No upcoming events yet" } }
                                p { if !query.is_empty() { "Try another event name or location." } else if show_past() { "Completed events will appear here after their final day." } else { "Check back for new events, or explore the leagues." } }
                                if !query.is_empty() {
                                    button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| controls.clear(), "Clear search" }
                                } else if !show_past() {
                                    Link { to: Route::LeaguesIndex {}, class: "btn btn-outline-secondary", "Browse leagues" }
                                }
                            }
                        } else {
                            div { class: "arctos-card-grid",
                                for t in filtered {
                                    TournamentCard {
                                        key: "{t.url}",
                                        tournament: t.clone(),
                                        team_count: data.team_counts.get(&t.url).copied().unwrap_or(0),
                                        user_reg_status: data.user_reg_status.get(&t.url).cloned(),
                                        past: show_past(),
                                    }
                                }
                            }
                        }
                    }
                }
            } else if let Some(Err(error)) = val.read().as_ref() {
                div { class: "arctos-empty-state", role: "alert",
                    h2 { "Events could not be loaded" }
                    p { "{error}" }
                    button { class: "btn btn-primary", r#type: "button", onclick: move |_| tournaments.restart(), "Try again" }
                }
            } else {
                div { class: "arctos-empty-state", role: "status", "Loading events…" }
            }
        }
    }
}

#[component]
fn TournamentCard(
    tournament: Tournament,
    team_count: u32,
    user_reg_status: Option<UserRegStatus>,
    past: bool,
) -> Element {
    let (teams_open, players_open) = tournament
        .league
        .as_ref()
        .map(|league| {
            (
                league.team_registration_open,
                league.player_registration_open,
            )
        })
        .unwrap_or((
            tournament.team_registration_open,
            tournament.player_registration_open,
        ));
    let registration_label = match (teams_open, players_open) {
        (true, true) => "Registration open",
        (true, false) => "Team registration open",
        (false, true) => "Player registration open",
        _ => "Registration closed",
    };
    rsx! {
        article { class: "card arctos-event-card",
            div { class: "card-body",
                div { class: "arctos-card-statuses",
                    if !tournament.published { span { class: "badge bg-warning text-dark", "Unpublished" } }
                    if past {
                        span { class: "badge arctos-badge-neutral", "Past event" }
                    } else {
                        span { class: if teams_open || players_open { "badge arctos-badge-success" } else { "badge arctos-badge-neutral" }, "{registration_label}" }
                    }
                }
                h3 { class: "arctos-card-title",
                    Link { to: Route::TournamentHome { url: tournament.url.clone() }, "{tournament.name}" }
                }
                if let Some(ref league) = tournament.league {
                    Link {
                        to: Route::LeagueHome { league_url: league.league_url.clone() },
                        class: "arctos-league-link",
                        i { class: "fas fa-trophy", aria_hidden: "true" }
                        "{league.name}"
                    }
                }
                ul { class: "arctos-meta-list",
                    li { i { class: "fas fa-map-marker-alt", aria_hidden: "true" } "{tournament.location.as_deref().filter(|s| !s.trim().is_empty()).unwrap_or(\"Location to be announced\")}" }
                    li { i { class: "far fa-calendar-alt", aria_hidden: "true" } "{format_date_display(&tournament.start_date, tournament.end_date.as_ref())}" }
                    li {
                        i { class: "fas fa-users", aria_hidden: "true" }
                        if let Some(max) = tournament.n_max_teams {
                            "{team_count} / {max} teams registered"
                        } else { "{team_count} teams registered" }
                    }
                }
                if let Some(urs) = user_reg_status {
                    div { class: "arctos-registration-summary", aria_label: "Your registration", UserRegBadges { urs } }
                }
            }
            div { class: "arctos-card-footer",
                Link { to: Route::TournamentHome { url: tournament.url }, class: "btn btn-outline-secondary w-100", "View event" i { class: "fas fa-arrow-right", aria_hidden: "true" } }
            }
        }
    }
}

#[component]
pub(super) fn UserRegBadges(urs: UserRegStatus) -> Element {
    rsx! {
        RegistrationStatusBadge { status: urs.status, show_icon: true }
        PaymentStatusBadge { paid: urs.paid, show_icon: true }
        WaiverStatusBadge { required: urs.waiver_required, status: urs.waiver_status, show_icon: true }
    }
}
