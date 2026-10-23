use crate::api;
use crate::components::PageIntro;
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn TeamsList() -> Element {
    let mut search = use_signal(String::new);
    let mut submitted_search = use_signal(String::new);
    let mut data = use_resource(move || {
        let query = submitted_search();
        async move { api::teams_list(&query).await.map_err(|e| e.to_string()) }
    });
    let val = data.value();
    let backend = api::base_url();
    rsx! {
        PageIntro { page_key: "teams", title: "Teams", focus_target: "page-content",
            "Find teams, meet their players, and see where they’ve competed."
        }
        form {
            class: "arctos-directory-search", role: "search",
            onsubmit: move |event| { event.prevent_default(); submitted_search.set(search().trim().to_string()); },
            label { class: "visually-hidden", r#for: "team-search", "Search teams" }
            input { id: "team-search", r#type: "search", class: "form-control", placeholder: "Search teams…", value: "{search()}", oninput: move |event| search.set(event.value()) }
            button { class: "btn btn-primary", r#type: "submit", "Search" }
            if !submitted_search().is_empty() {
                button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| { search.set(String::new()); submitted_search.set(String::new()); }, "Clear" }
            }
        }
        if let Some(Ok(d)) = val.read().as_ref() {
            p { class: "arctos-results-count", role: "status", "{d.teams.len()} teams found" }
            if d.teams.is_empty() {
                div { class: "arctos-empty-state",
                    i { class: "fas fa-users", aria_hidden: "true" }
                    h2 { "No teams found" }
                    p { if submitted_search().is_empty() { "Team profiles will appear here when teams join Arctos." } else { "Try a different name, or clear your search to browse all teams." } }
                }
            } else {
                div { class: "arctos-card-grid",
                    for team in d.teams.iter() {
                        Link { key: "{team.id}", to: Route::TeamProfilePage { id: team.id.clone() }, class: "card arctos-directory-card",
                            div { class: "arctos-directory-identity",
                                if let Some(photo) = &team.profile_photo {
                                    img { src: "{backend}/static/{photo}", alt: "", loading: "lazy", class: "arctos-directory-avatar" }
                                } else {
                                    div { class: "arctos-directory-avatar arctos-avatar-placeholder", aria_hidden: "true", i { class: "fas fa-users" } }
                                }
                                div { class: "arctos-directory-name",
                                    h2 { "{team.name}" }
                                    p { "@{team.id}" }
                                }
                            }
                            if let Some(location) = team.location.as_ref().filter(|location| !location.trim().is_empty()) {
                                p { class: "arctos-directory-location", i { class: "fas fa-map-marker-alt", aria_hidden: "true" } "{location}" }
                            }
                            div { class: "arctos-directory-footer",
                                span { class: "arctos-directory-event-count", title: "Confirmed past registrations",
                                    strong { "{team.past_event_count}" }
                                    span { "Past events" }
                                }
                                span { class: "arctos-directory-action", "View team" i { class: "fas fa-arrow-right", aria_hidden: "true" } }
                            }
                        }
                    }
                }
            }
        } else if let Some(Err(error)) = val.read().as_ref() {
            div { class: "arctos-empty-state", role: "alert",
                h2 { "Teams could not be loaded" }
                p { "{error}" }
                button { class: "btn btn-primary", r#type: "button", onclick: move |_| data.restart(), "Try again" }
            }
        } else {
            div { class: "arctos-empty-state", role: "status", "Loading teams…" }
        }
    }
}
