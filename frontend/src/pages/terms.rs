use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn Terms() -> Element {
    rsx! {
        Markdown { page: "terms" }
    }
}
