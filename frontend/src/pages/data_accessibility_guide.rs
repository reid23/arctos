use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn DataAccessibilityGuide() -> Element {
    rsx! {
        Markdown { page: "data-accessibility-guide" }
    }
}
