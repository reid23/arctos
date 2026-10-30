use crate::Route;
use dioxus::prelude::*;

/// Task context below an entity cover, or the main title of a standalone form.
#[component]
pub fn ToolPageHeader(
    title: String,
    #[props(default)] standalone: bool,
    #[props(default)] back: Option<(Route, String)>,
    #[props(default)] description: Option<String>,
    #[props(default)] actions: Option<Element>,
) -> Element {
    rsx! {
        header { class: "tool-page-header",
            if let Some((route, label)) = back {
                Link { to: route, class: "tool-page-back",
                    i { class: "fa-solid fa-arrow-left", aria_hidden: "true" }
                    "{label}"
                }
            }
            div { class: "tool-page-heading",
                div { class: "tool-page-context",
                    if standalone {
                        h1 { class: "tool-page-title", "{title}" }
                    } else {
                        h2 { class: "tool-page-title", "{title}" }
                    }
                    if let Some(text) = description {
                        p { class: "tool-page-description", "{text}" }
                    }
                }
                if let Some(actions) = actions {
                    div { class: "tool-page-actions", {actions} }
                }
            }
        }
    }
}
