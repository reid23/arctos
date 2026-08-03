use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn Privacy() -> Element {
    rsx! {
        Markdown { page: "privacy-policy" }
    }
}
