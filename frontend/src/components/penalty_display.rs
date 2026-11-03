//! Single penalty line: colored bar, optional target (link to profile), text, optional description icon.

use crate::api;
use crate::Route;
use dioxus::prelude::*;
/// One penalty display row: left border + background by color, optional "target: " (linked if profile_id given), text, optional ? icon.
/// Layout: block with margin-bottom so multiple penalties stack vertically.
#[component]
pub fn PenaltyDisplay(
    /// Hex color without # (e.g. "808080")
    border_color: String,
    /// Penalty type name or "Other" / note text
    display_text: String,
    /// If present, show ? icon that triggers on_description_click with this string
    description: Option<String>,
    /// Optional label before display_text (e.g. "Point" or player name). If target_profile_id is set, this is a link.
    target_display: Option<String>,
    /// If set with target_display, render target_display as a link to this player's profile
    target_profile_id: Option<String>,
    /// Called when ? icon is clicked, with the description string (or None to close). Pass a no-op if no modal.
    on_description_click: EventHandler<Option<String>>,
) -> Element {
    let show_help = description.as_ref().map(|s| !s.is_empty()).unwrap_or(false);
    let desc = description.clone();
    let style = format!("border-left: 6px solid #{}; background-color: #{}18;", border_color, border_color);
    rsx! {
        div {
            class: "small text-muted ps-2 py-1 mb-1",
            style: "{style}",
            if let Some(ref target) = target_display {
                if let Some(ref pid) = target_profile_id {
                    Link {
                        to: Route::PlayerProfilePage { id: pid.clone() },
                        class: "text-decoration-none fw-bold",
                        "{target}"
                    }
                } else {
                    strong { "{target}" }
                }
                ": "
            }
            "{display_text}"
            if show_help {
                button {
                    r#type: "button",
                    class: "btn btn-link p-0 ms-1 text-decoration-none align-middle",
                    style: "min-width: 44px; min-height: 44px; font-size: inherit;",
                    aria_label: "Read description: {display_text}",
                    aria_haspopup: "dialog",
                    title: "Description: {display_text}",
                    onclick: move |event| {
                        event.stop_propagation();
                        on_description_click.call(desc.clone());
                    },
                    img {
                        src: format!("{}/static/question-mark.svg", api::base_url()),
                        alt: "",
                        aria_hidden: "true",
                        style: "width: 1em; height: 1em; object-fit: contain; vertical-align: 0.15em;",
                    }
                }
            }
        }
    }
}
