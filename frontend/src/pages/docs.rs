use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn Docs() -> Element {
    rsx! {
        Markdown { page: "docs" }
    }
}
