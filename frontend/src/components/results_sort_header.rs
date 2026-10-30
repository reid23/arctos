use dioxus::prelude::*;

#[component]
pub fn ResultsSortHeader(label: String, active: bool, ascending: bool, onsort: EventHandler<()>, #[props(default = false)] numeric: bool) -> Element {
    rsx! {
        th {
            scope: "col",
            class: if numeric { "table-number" } else { "" },
            "aria-sort": if !active { "none" } else if ascending { "ascending" } else { "descending" },
            button {
                r#type: "button",
                class: "results-sort-button",
                onclick: move |_| onsort.call(()),
                "{label}"
                if active {
                    span { "aria-hidden": "true", if ascending { "↑" } else { "↓" } }
                }
            }
        }
    }
}
