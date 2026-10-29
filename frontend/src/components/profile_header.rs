use super::PlayerPompfen;
use crate::Route;
use crate::api;
use dioxus::prelude::*;

#[component]
pub fn ProfileHeader(
    account_id: String,
    kind: String,
    name: String,
    avatar: Option<String>,
    banner: Option<String>,
    location: Option<String>,
    #[props(default)] pronouns: Option<String>,
    #[props(default)] pompfen: Vec<String>,
    past_count: u32,
    edit_route: Option<Route>,
) -> Element {
    let backend = api::base_url();
    rsx! {
        style { {include_str!("event_details.css")} }
        style { {include_str!("profile_details.css")} }
        header { id: "page-banner", class: "profile-detail-header",
            div { class: "profile-cover",
                if let Some(photo) = banner {
                    img { class: "profile-cover-image", src: "{backend}/static/{photo}", alt: "" }
                }
            }
            div { class: "profile-header-body",
                div { class: "profile-avatar-row",
                    if let Some(photo) = avatar {
                        img { class: "profile-avatar", src: "{backend}/static/{photo}", alt: "" }
                    } else {
                        span { class: "profile-avatar profile-avatar-placeholder", aria_hidden: "true",
                            i { class: if kind == "team" { "fas fa-users" } else { "fas fa-user" } }
                        }
                    }
                    if let Some(route) = edit_route {
                        Link { to: route, class: "btn btn-outline-secondary profile-edit-link", "Edit profile" }
                    }
                }
                div { class: "profile-identity",
                    h1 { id: "page-banner-heading", tabindex: "-1", "{name}" }
                    p { class: "profile-account-name", "@{account_id}"
                        if let Some(pronouns) = pronouns.as_ref().filter(|value| !value.trim().is_empty()) {
                            span { class: "profile-pronouns", " · {pronouns}" }
                        }
                    }
                }
                div { class: "profile-meta",
                    if let Some(location) = location.as_ref().filter(|value| !value.trim().is_empty()) {
                        p { class: "profile-location", i { class: "fas fa-map-marker-alt", aria_hidden: "true" } "{location}" }
                    }
                    dl { class: "profile-event-stat", title: "Confirmed past registrations",
                        dt { "Past events" }
                        dd { "{past_count}" }
                    }
                    PlayerPompfen { selected: pompfen }
                }
            }
        }
    }
}
