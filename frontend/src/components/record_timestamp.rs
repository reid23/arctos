use crate::time_format::record_timestamp_parts;
use dioxus::prelude::*;

/// A compact local instant with its exact UTC value available to assistive tools.
#[component]
pub fn RecordTimestamp(value: Option<String>, #[props(default)] include_seconds: bool) -> Element {
    let Some(value) = value.filter(|value| !value.trim().is_empty()) else {
        return rsx! {
            span { aria_hidden: "true", "—" }
            span { class: "visually-hidden", "Date unavailable" }
        };
    };
    match record_timestamp_parts(&value, include_seconds) {
        Some(parts) => rsx! {
            time {
                class: "d-inline-flex flex-column align-items-start",
                datetime: parts.datetime,
                title: value,
                aria_label: parts.accessible_label,
                span { class: "table-nowrap", "{parts.date}" }
                span { class: "table-nowrap text-muted", "{parts.time}" }
            }
        },
        None => rsx! { span { "{value}" } },
    }
}
