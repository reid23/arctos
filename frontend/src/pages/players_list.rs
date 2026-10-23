use crate::api;
use crate::components::PageIntro;
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn PlayersList() -> Element {
    let mut search = use_signal(String::new);
    let mut page = use_signal(|| 1u32);
    let mut submitted_search = use_signal(String::new);
    let mut data = use_resource(move || {
        let query = submitted_search();
        let current_page = page();
        async move {
            api::players_list(&query, current_page)
                .await
                .map_err(|e| e.to_string())
        }
    });
    let val = data.value();
    let backend = api::base_url();
    rsx! {
        PageIntro { page_key: "players", title: "Players", focus_target: "page-content",
            "Find players, learn about them, and see their tournament history."
        }
        form {
            class: "arctos-directory-search", role: "search",
            onsubmit: move |event| { event.prevent_default(); submitted_search.set(search().trim().to_string()); page.set(1); },
            label { class: "visually-hidden", r#for: "player-search", "Search players" }
            input { id: "player-search", r#type: "search", class: "form-control", placeholder: "Search players…", value: "{search()}", oninput: move |event| search.set(event.value()) }
            button { class: "btn btn-primary", r#type: "submit", "Search" }
            if !submitted_search().is_empty() {
                button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| { search.set(String::new()); submitted_search.set(String::new()); page.set(1); }, "Clear" }
            }
        }
        if let Some(Ok(d)) = val.read().as_ref() {
            if d.players.is_empty() {
                div { class: "arctos-empty-state", role: "status",
                    i { class: "fas fa-user", aria_hidden: "true" }
                    h2 { "No players found" }
                    p { if submitted_search().is_empty() { "Player profiles will appear here when players join Arctos." } else { "Try a different name, or clear your search to browse all players." } }
                }
            } else {
                p { class: "arctos-results-count", role: "status", "Showing {((d.page.saturating_sub(1)) * 50) + 1}–{std::cmp::min(d.page * 50, d.total)} of {d.total} players" }
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
                                    span { "Past events" }
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
                        onclick: move |_| page.set(page().saturating_sub(1).max(1)), "Previous" }
                    span { class: "arctos-page-position", aria_live: "polite", "Page {d.page} of {d.total_pages}" }
                    button { class: "btn btn-outline-secondary", r#type: "button", disabled: d.page >= d.total_pages,
                        onclick: move |_| page.set(page() + 1), "Next" }
                    label { class: "arctos-page-jump",
                        "Go to page"
                        select { class: "form-select", value: "{d.page}",
                            onchange: move |event| { if let Ok(number) = event.value().parse::<u32>() { page.set(number); } },
                            for number in 1..=d.total_pages { option { value: "{number}", "{number}" } }
                        }
                    }
                }
            }
        } else if let Some(Err(error)) = val.read().as_ref() {
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
