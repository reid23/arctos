use super::directory_state::{use_directory, use_directory_scroll, Directory};
use crate::api;
use crate::components::PageIntro;
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn PlayersListWithQuery(q: String, page: String) -> Element {
    rsx! { PlayersList {} }
}

#[component]
pub fn PlayersList() -> Element {
    let controls = use_directory(Directory::Players, false);
    let mut search = controls.search;
    let submitted_search = move || (controls.state)().search;
    let page = move || (controls.state)().page;
    let mut data = use_resource(move || {
        let query = submitted_search();
        let current_page = page();
        async move {
            let result = api::players_list(&query, current_page).await.map_err(|e| e.to_string());
            ((query, current_page), result)
        }
    });
    let val = data.value();
    let matching = val.read().as_ref().filter(|((query, number), _)| *query == submitted_search() && *number == page()).map(|(_, result)| result.clone());
    use_directory_scroll(controls, matching.as_ref().is_some_and(Result::is_ok));
    use_effect(move || {
        if let Some(((query, number), Ok(response))) = val.read().as_ref() {
            if *query == submitted_search() && *number == page() && page() > response.total_pages.max(1) {
                controls.page(response.total_pages.max(1), false);
            }
        }
    });
    let backend = api::base_url();
    rsx! {
        PageIntro { page_key: "players", title: "Players", focus_target: "page-content",
            "Find players and their tournament history."
        }
        form {
            class: "arctos-directory-search", role: "search",
            onsubmit: move |event| { event.prevent_default(); controls.submit(); },
            label { class: "visually-hidden", r#for: "player-search", "Search players" }
            input { id: "player-search", r#type: "search", class: "form-control", placeholder: "Search players…", value: "{search()}", oninput: move |event| search.set(event.value()) }
            button { class: "btn btn-primary", r#type: "submit", "Search" }
            if !submitted_search().is_empty() {
                button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| { controls.clear(); }, "Clear" }
            }
        }
        if let Some(Ok(d)) = matching.as_ref() {
            if d.players.is_empty() {
                div { class: "arctos-empty-state", role: "status",
                    i { class: "fas fa-user", aria_hidden: "true" }
                    h2 { "No players found" }
                    p { if submitted_search().is_empty() { "Player profiles will appear here when players join Arctos." } else { "Try a different name, or clear your search to browse all players." } }
                }
            } else {
                p { class: "arctos-results-count", role: "status", "Showing {d.page.saturating_sub(1).saturating_mul(50).saturating_add(1)}–{std::cmp::min(d.page.saturating_mul(50), d.total)} of {d.total} players" }
                div { class: "arctos-card-grid",
                    for player in d.players.iter() {
                        Link { key: "{player.id}", to: Route::PlayerProfilePage { id: player.id.clone() }, class: "card arctos-directory-card",
                            div { class: "arctos-directory-identity",
                                if let Some(photo) = &player.profile_photo {
                                    img { src: "{backend}/static/{photo}", alt: "", loading: "lazy", class: "arctos-directory-avatar arctos-player-avatar" }
                                } else {
                                    div { class: "arctos-directory-avatar arctos-player-avatar arctos-avatar-placeholder", aria_hidden: "true", i { class: "fas fa-user" } }
                                }
                                div { class: "arctos-directory-name",
                                    h2 { "{player.name}" }
                                    p { "@{player.id}" }
                                }
                            }
                            if let Some(location) = player.location.as_ref().filter(|location| !location.trim().is_empty()) {
                                p { class: "arctos-directory-location", i { class: "fas fa-map-marker-alt", aria_hidden: "true" } "{location}" }
                            }
                            div { class: "arctos-directory-footer",
                                span { class: "arctos-directory-event-count", title: "Confirmed past registrations",
                                    strong { "{player.past_event_count}" }
                                    span { if player.past_event_count == 1 { "Past event" } else { "Past events" } }
                                }
                                span { class: "arctos-directory-action", "View player" i { class: "fas fa-arrow-right", aria_hidden: "true" } }
                            }
                        }
                    }
                }
            }
            if d.total_pages > 1 {
                nav { class: "arctos-pagination", aria_label: "Players pagination",
                    button { class: "btn btn-outline-secondary", r#type: "button", disabled: d.page <= 1,
                        onclick: move |_| controls.page(page().saturating_sub(1).max(1), true), "Previous" }
                    span { class: "arctos-page-position", aria_live: "polite", "Page {d.page} of {d.total_pages}" }
                    button { class: "btn btn-outline-secondary", r#type: "button", disabled: d.page >= d.total_pages,
                        onclick: move |_| controls.page(page().saturating_add(1), true), "Next" }
                    label { class: "arctos-page-jump",
                        "Go to page"
                        select { class: "form-select", value: "{d.page}",
                            onchange: move |event| { if let Ok(number) = event.value().parse::<u32>() { controls.page(number, true); } },
                            for number in 1..=d.total_pages { option { value: "{number}", "{number}" } }
                        }
                    }
                }
            }
        } else if let Some(Err(error)) = matching.as_ref() {
            div { class: "arctos-empty-state", role: "alert",
                h2 { "Players could not be loaded" }
                p { "{error}" }
                button { class: "btn btn-primary", r#type: "button", onclick: move |_| data.restart(), "Try again" }
            }
        } else {
            div { class: "arctos-empty-state", role: "status", "Loading players…" }
        }
    }
}
