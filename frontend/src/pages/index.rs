use super::directory_state::{use_directory, use_directory_scroll, Directory, DirectoryState};
use crate::api;
use crate::components::{
    OrganizeContext, OrganizeMenu, PageIntro, PaymentStatusBadge, RegistrationStatusBadge,
    WaiverStatusBadge,
};
use crate::types::{PublicEventCard, PublicRegistrationSummary, UserRegStatus};
use crate::Route;
use chrono::NaiveDate;
use dioxus::prelude::*;

pub(super) fn format_date_display(start: &str, end: Option<&String>) -> String {
    crate::time_format::format_calendar_range(start, end.map(String::as_str))
}

pub(super) fn listing_date(iso: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(iso.get(..10).unwrap_or(iso), "%Y-%m-%d").ok()
}

#[component]
pub fn IndexWithQuery(q: String, view: String, page: String) -> Element {
    rsx! { Index {} }
}

#[component]
pub fn Index() -> Element {
    let controls = use_directory(Directory::Events, true);
    let auth_version = super::layout::use_auth_invalidate();
    let mut search = controls.search;
    let mut directory = use_resource(move || {
        // A logout while already on Events need not remount this component.
        // Tie personalized cards to the same account generation as Layout,
        // without serializing this request behind the initial account check.
        let requested = ((controls.state)(), auth_version());
        async move {
            let (filters, _) = &requested;
            let response =
                api::public_events::directory(&filters.search, filters.past, filters.page)
                    .await;
            (requested, response)
        }
    });
    let value = directory.value();
    // An older account/query response must never replace the current page.
    // This also hides the prior account's private cards immediately on logout,
    // while the new request is pending. Cancellation alone cannot guarantee it.
    let matching = value
        .read()
        .as_ref()
        .filter(|(requested, _)| *requested == ((controls.state)(), auth_version()))
        .map(|(_, response)| response.clone());
    use_directory_scroll(controls, matching.as_ref().is_some_and(Result::is_ok));
    use_effect(move || {
        let current = ((controls.state)(), auth_version());
        if let Some((requested, Ok(page))) = value.read().as_ref() {
            if *requested == current {
                // Flask clamps invalid or now-empty last pages and limits
                // search length. Reflect that canonical state without adding
                // another Back entry; later controls use the actual page.
                let actual = DirectoryState::new(&page.query, &page.view, &page.page.to_string());
                if actual != current.0 {
                    if search.peek().trim() == current.0.search {
                        search.set(actual.search.clone());
                    }
                    controls.commit(actual, false);
                }
            }
        }
    });
    let show_past = (controls.state)().past;
    let counts = matching
        .as_ref()
        .and_then(|result| result.as_ref().ok())
        .map(|page| (page.upcoming_count, page.past_count));

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
                            r#type: "button", class: if !show_past { "arctos-segment is-active" } else { "arctos-segment" },
                            aria_pressed: "{!show_past}", onclick: move |_| controls.filter(false),
                            "Upcoming "
                            if let Some((upcoming, _)) = counts { span { class: "arctos-count", "{upcoming}" } }
                        }
                        button {
                            r#type: "button", class: if show_past { "arctos-segment is-active" } else { "arctos-segment" },
                            aria_pressed: "{show_past}", onclick: move |_| controls.filter(true),
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
                        id: "event-search", r#type: "search", class: "form-control", maxlength: "200",
                        placeholder: "Find an event or location…", value: "{search()}",
                        oninput: move |event| search.set(event.value()),
                        onblur: move |_| controls.flush(),
                        onkeydown: move |event| { if event.key() == Key::Enter { controls.flush(); } },
                    }
                }
            }
            if let Some(Ok(page)) = matching.as_ref() {
                if !page.query.is_empty() {
                    p { class: "arctos-results-count", role: "status",
                        "{page.total} "
                        if page.total == 1 { "event" } else { "events" }
                        " matching “{page.query}”. "
                        Link { to: app_target(&page.clear_url), "Clear search" }
                    }
                }
                if page.cards.is_empty() {
                    div { class: "arctos-empty-state", role: "status",
                        i { class: "fas fa-calendar-alt", aria_hidden: "true" }
                        h2 { if !page.query.is_empty() { "No matching events" } else if page.view == "past" { "No past events yet" } else { "No upcoming events yet" } }
                        p { if !page.query.is_empty() { "Try another event name, location or league." } else if page.view == "past" { "Completed events will appear here after their final day." } else { "Check back for new events, or explore the leagues." } }
                        if !page.query.is_empty() {
                            Link { to: app_target(&page.clear_url), class: "btn btn-outline-secondary", "Clear search" }
                        } else if page.view != "past" {
                            Link { to: Route::LeaguesIndex {}, class: "btn btn-outline-secondary", "Browse leagues" }
                        }
                    }
                } else {
                    div { class: "arctos-card-grid",
                        for card in page.cards.iter() {
                            PublicTournamentCard { key: "{card.url}", card: card.clone(), past: page.view == "past" }
                        }
                    }
                }
                if page.previous_url.is_some() || page.next_url.is_some() {
                    nav { class: "arctos-pagination", aria_label: "Event pages",
                        if let Some(url) = &page.previous_url {
                            Link { class: "btn btn-outline-secondary", rel: "prev", to: app_target(url), "Previous" }
                        }
                        span { class: "arctos-page-position", aria_live: "polite", "Page {page.page}" }
                        if let Some(url) = &page.next_url {
                            Link { class: "btn btn-outline-secondary", rel: "next", to: app_target(url), "Next" }
                        }
                    }
                }
            } else if let Some(Err(error)) = matching.as_ref() {
                div { class: "arctos-empty-state", role: "alert",
                    h2 { "Events could not be loaded" }
                    p { "{error}" }
                    button { class: "btn btn-primary", r#type: "button", onclick: move |_| directory.restart(), "Try again" }
                }
            } else {
                div { class: "arctos-empty-state", role: "status", "Loading events…" }
            }
        }
    }
}

/// Server links include the deployment prefix. Router links add that prefix
/// themselves; remove it exactly once without decoding query delimiters.
fn app_target(url: &str) -> String {
    let prefix = dioxus::history::history()
        .current_prefix()
        .unwrap_or_default();
    crate::public_navigation::without_prefix(url, &prefix)
        .unwrap_or(url)
        .to_string()
}

#[component]
fn PublicTournamentCard(card: PublicEventCard, past: bool) -> Element {
    let labels: Vec<_> = card.date_label.split(" – ").collect();
    rsx! {
        article { class: "card arctos-event-card event-card",
            div { class: "card-body",
                div { class: "arctos-card-statuses",
                    if !card.published { span { class: "badge bg-warning text-dark", title: "Draft · Only visible to organizers", "Unpublished" } }
                    if past {
                        span { class: "badge arctos-badge-neutral", "Past event" }
                    } else {
                        span { class: if card.registration_open { "badge arctos-badge-success" } else { "badge arctos-badge-neutral" }, "{card.registration_label}" }
                    }
                }
                h2 { class: "arctos-card-title", Link { to: app_target(&card.url), "{card.name}" } }
                if let (Some(name), Some(url)) = (&card.league_name, &card.league_url) {
                    Link { to: app_target(url), class: "arctos-league-link",
                        i { class: "fas fa-trophy", aria_hidden: "true" } "{name}"
                    }
                }
                ul { class: "arctos-meta-list",
                    li { i { class: "fas fa-map-marker-alt", aria_hidden: "true" } span { "{card.location}" } }
                    li { i { class: "fas fa-calendar-alt", aria_hidden: "true" }
                        span {
                            time { datetime: "{card.date_start}", "{labels[0]}" }
                            if let Some(end) = card.date_end.as_ref().filter(|end| *end != &card.date_start) {
                                " – "
                                time { datetime: "{end}", "{labels.last().copied().unwrap_or_default()}" }
                            }
                        }
                    }
                    li { i { class: "fas fa-users", aria_hidden: "true" }
                        span { "{card.team_count}"
                            if let Some(max) = card.max_teams { " / {max}" }
                            " teams registered"
                        }
                    }
                }
                if let Some(status) = &card.account_status { PublicRegistrationBadges { status: status.clone() } }
            }
            div { class: "arctos-card-footer",
                Link { to: app_target(&card.url), class: "btn btn-outline-secondary w-100",
                    if card.published { "View event" } else { "Preview event" }
                    i { class: "fas fa-arrow-right", aria_hidden: "true" }
                }
            }
        }
    }
}

#[component]
fn PublicRegistrationBadges(status: PublicRegistrationSummary) -> Element {
    let (tone, icon) = match status.status.as_str() {
        "CONFIRMED" => ("success", "fas fa-circle-check"),
        "PENDING_TEAM_APPROVAL" => ("warning", "fas fa-clock"),
        "REJECTED" => ("danger", "fas fa-circle-xmark"),
        "CANCELLED" => ("neutral", "fas fa-circle-minus"),
        _ => ("neutral", "fas fa-circle-question"),
    };
    let waiver = status.waiver_status.as_deref().map(|value| match value {
        "Signed" => ("success", "fas fa-check", "Waiver valid"),
        "Needs updating" => (
            "warning",
            "fas fa-file-signature",
            "Waiver needs re-signing",
        ),
        "Not signed" => ("danger", "fas fa-file-signature", "Waiver not signed"),
        _ => ("danger", "fas fa-file-signature", "Waiver status unknown"),
    });
    rsx! {
        div { class: "arctos-registration-summary", aria_label: "Your registration",
            span { class: "badge arctos-registration-badge arctos-badge-{tone}",
                i { class: icon, aria_hidden: "true" } "{status.status_label}"
            }
            PaymentStatusBadge { paid: status.paid, show_icon: true }
            if let Some((tone, icon, label)) = waiver {
                span { class: "badge arctos-registration-badge arctos-badge-{tone}",
                    i { class: icon, aria_hidden: "true" } "{label}"
                }
            }
        }
    }
}

// Leagues still consumes its existing registration response. Keep that shared
// presentation independent of the server's smaller public event projection.
#[component]
pub(super) fn UserRegBadges(urs: UserRegStatus) -> Element {
    rsx! {
        RegistrationStatusBadge { status: urs.status, show_icon: true }
        PaymentStatusBadge { paid: urs.paid, show_icon: true }
        WaiverStatusBadge { required: urs.waiver_required, status: urs.waiver_status, show_icon: true }
    }
}
