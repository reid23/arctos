use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn Agents() -> Element {
    rsx! {
        Markdown { page: "agents" }
    }
}
