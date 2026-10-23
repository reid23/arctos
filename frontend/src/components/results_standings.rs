use dioxus::prelude::*;

#[component]
pub fn ResultsRank(rank: Option<usize>) -> Element {
    let medal = match rank {
        Some(1) => Some(("gold", "Gold")),
        Some(2) => Some(("silver", "Silver")),
        Some(3) => Some(("bronze", "Bronze")),
        _ => None,
    };
    rsx! {
        span { class: "results-rank",
            if let Some(rank) = rank {
                if let Some((class, label)) = medal {
                    i { class: "fa-solid fa-trophy results-rank-{class}", "aria-hidden": "true" }
                    span { class: "visually-hidden", "{label}, rank " }
                } else {
                    span { class: "visually-hidden", "Rank " }
                }
                "{rank}"
            } else {
                span { "aria-hidden": "true", "—" }
                span { class: "visually-hidden", "Unranked" }
            }
        }
    }
}

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
