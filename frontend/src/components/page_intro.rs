use crate::appearance::focus_control;
use crate::pages::layout::{set_page_banner_hidden, use_page_banner_preferences};
use dioxus::prelude::*;

#[component]
pub fn PageIntro(
    page_key: String,
    title: String,
    focus_target: String,
    children: Element,
) -> Element {
    let preferences = use_page_banner_preferences();
    let hidden = preferences.read().contains(&page_key);
    rsx! {
        header {
            id: "page-banner", class: "arctos-page-banner", hidden,
            div { id: "page-banner-content",
                h1 { id: "page-banner-heading", tabindex: "-1", "{title}" }
                p { {children} }
            }
            button {
                id: "page-banner-dismiss", class: "arctos-intro-dismiss", r#type: "button",
                aria_label: "Collapse {title} introduction", title: "Collapse introduction",
                aria_expanded: !hidden, aria_controls: "page-banner-content",
                onclick: move |_| {
                    set_page_banner_hidden(preferences, &page_key, true);
                    focus_control(&focus_target);
                },
                i { class: "fa-solid fa-chevron-up", aria_hidden: "true" }
            }
        }
        if hidden {
            h1 { class: "visually-hidden", "{title}" }
        }
    }
}
