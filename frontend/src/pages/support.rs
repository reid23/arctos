use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn Support() -> Element {
    rsx! {
        Markdown { page: "support" }
    }
}
