//! Help modal for team selection in player registration.
//! Explains the registration flow and what to do if team isn't listed.

use dioxus::prelude::*;
use crate::components::Modal;

#[component]
pub fn TeamRegistrationHelpModal(
    /// Context for the message: "tournament" or "league". Affects "Check the X homepage" text.
    context: String,
    #[props(default)] return_focus_id: Option<String>,
    on_close: EventHandler<()>,
) -> Element {
    let homepage_text = if context == "league" {
        "league homepage"
    } else {
        "tournament homepage"
    };

    rsx! {
        Modal {
            id: "registration-help-dialog",
            labelled_by: "teamRegistrationHelpModalLabel",
            return_focus_id,
            on_close,
                    div { class: "modal-header",
                        h2 { class: "modal-title h5", id: "teamRegistrationHelpModalLabel", tabindex: "-1", "Registration Help" }
                        button {
                            r#type: "button",
                            class: "btn-close",
                            aria_label: "Close",
                            onclick: move |_| on_close.call(()),
                        }
                    }
                    div { class: "modal-body",
                        p { "The registration process works in three steps:" }
                        ol {
                            li { strong { "Teams register first:" } " A team account must register before players can join that team." }
                            li { strong { "Players register under the team:" } " Once a team is registered, players can select that team from the dropdown and register to join them." }
                            li { strong { "Team accepts the player:" } " After a player requests to join a team, the team must approve the player's request before they are officially on the roster." }
                        }
                        p { class: "mb-0",
                            strong { "Don't see your team in the dropdown?" }
                            " They may not have registered yet. Check the {homepage_text} to see all registered teams. Ask your team to register first, then come back to complete your player registration."
                        }
                    }
                    div { class: "modal-footer",
                        button {
                            r#type: "button",
                            class: "btn btn-secondary",
                            onclick: move |_| on_close.call(()),
                            "Close"
                        }
                    }
        }
    }
}
