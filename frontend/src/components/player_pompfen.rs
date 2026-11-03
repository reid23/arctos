use super::{ALL_POMPFEN, PompfenIcon};
use dioxus::prelude::*;

#[component]
pub fn PlayerPompfen(
    selected: Vec<String>,
    #[props(default)] onchange: Option<EventHandler<Vec<String>>>,
    #[props(default)] disabled: bool,
) -> Element {
    if onchange.is_none() && selected.is_empty() {
        return rsx! {};
    }
    rsx! {
        style { {include_str!("player_pompfen.css")} }
        if let Some(onchange) = onchange {
            fieldset { class: "player-pompfen-editor", disabled,
                legend { "Pompfen" }
                p { id: "player-pompfen-help", class: "form-text", "Choose the pompfen and roles you play. Shown on your profile." }
                div { class: "player-pompfen-options",
                    for option in ALL_POMPFEN {
                        {
                            let id = option.id.to_string();
                            let current = selected.clone();
                            let checked = selected.iter().any(|value| value == option.id);
                            rsx! {
                                label { class: if checked { "player-pompfen-choice is-selected" } else { "player-pompfen-choice" },
                                    r#for: "profile-pompfen-{option.id}",
                                    input { id: "profile-pompfen-{option.id}", r#type: "checkbox", class: "form-check-input",
                                        checked, aria_describedby: "player-pompfen-help",
                                        onchange: move |event| {
                                            let mut next = current.clone();
                                            if event.checked() { if !next.contains(&id) { next.push(id.clone()); } }
                                            else { next.retain(|value| value != &id); }
                                            next.sort_by_key(|value| ALL_POMPFEN.iter().position(|option| option.id == value).unwrap_or(usize::MAX));
                                            onchange.call(next);
                                        }
                                    }
                                    span { class: "player-pompfen-symbol", aria_hidden: "true", PompfenIcon { weapon: option.id.to_string(), size: "1.5rem".to_string() } }
                                    span { "{option.label}" }
                                }
                            }
                        }
                    }
                }
            }
        } else {
            ul { class: "player-pompfen player-pompfen-list", aria_label: "Pompfen",
                    for option in ALL_POMPFEN {
                        {
                            let chosen = selected.iter().any(|value| value == option.id);
                            rsx! {
                                li { class: if chosen { "player-pompfen-item is-selected" } else { "player-pompfen-item" },
                                    "data-pompfen": option.id,
                                    title: format!("{} ({})", option.label, if chosen { "selected" } else { "not selected" }),
                                    span { class: "visually-hidden", if chosen { "Selected: " } else { "Not selected: " } "{option.label}" }
                                    span { class: "player-pompfen-symbol", aria_hidden: "true", PompfenIcon { weapon: option.id.to_string(), size: "1.5rem".to_string() } }
                                }
                            }
                        }
                    }
            }
        }
    }
}
