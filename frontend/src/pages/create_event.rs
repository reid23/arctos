use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn CreateEvent() -> Element {
    rsx! {
        div { class: "arctos-create-event",
            header { class: "arctos-page-intro arctos-centered-intro",
                div { class: "arctos-brand-mark", aria_hidden: "true", i { class: "fas fa-plus" } }
                h1 { "Create an Event" }
                p { "Choose a tournament or a league." }
            }
            div { class: "arctos-choice-grid",
                article { class: "card arctos-choice-card",
                    div { class: "arctos-choice-icon", aria_hidden: "true", i { class: "fas fa-trophy" } }
                    h2 { "Tournament" }
                    p { "A standalone competition with its own registration, schedule, bracket, and results." }
                    ul {
                        li { "One tournament" }
                        li { "Independent team and player registration" }
                        li { "Separate fees, waivers, and roster limits" }
                    }
                    Link { to: Route::NewTournament {}, class: "btn btn-primary", "Create an Event" i { class: "fas fa-arrow-right", aria_hidden: "true" } }
                }
                article { class: "card arctos-choice-card",
                    div { class: "arctos-choice-icon", aria_hidden: "true", i { class: "fas fa-award" } }
                    h2 { "League" }
                    p { "A group of tournaments with shared registration and standings." }
                    ul {
                        li { "Multiple events throughout a season" }
                        li { "One registration and roster across league events" }
                        li { "Shared fees, waivers, and organizers" }
                    }
                    Link { to: Route::NewLeague {}, class: "btn btn-primary", "Create league" i { class: "fas fa-arrow-right", aria_hidden: "true" } }
                }
            }
            aside { class: "arctos-inline-help",
                i { class: "fas fa-info-circle", aria_hidden: "true" }
                p { "Adding a tournament to an existing league? Open that league and choose “Add event” from its organizer tools." }
            }
        }
    }
}
