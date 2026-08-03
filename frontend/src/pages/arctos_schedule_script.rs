use crate::components::Markdown;
use dioxus::prelude::*;

#[component]
pub fn ArctosScheduleScript() -> Element {
    rsx! {
        Markdown { page: "arctos-schedule-script" }
    }
}
