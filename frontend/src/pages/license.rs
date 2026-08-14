use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn License() -> Element {
    rsx! {
        Markdown { page: "license" }
    }
}
