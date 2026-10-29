//! A durable URL for editing an existing registration from a public document.
//! Reuse the same editor and mutation rules as the event/league overview.

use super::{EditRegistrationContext, EditRegistrationModal};
use crate::Route;
use dioxus::prelude::*;

pub fn has_existing_registration(user_type: &str, team: bool, player: bool) -> bool {
    match user_type {
        "team" => team,
        "player" => player,
        _ => false,
    }
}

#[component]
pub fn ExistingRegistration(
    context: EditRegistrationContext,
    user_type: String,
    return_to: Route,
    #[props(default)] roster: Option<Route>,
) -> Element {
    let navigator = use_navigator();
    let mut returning = use_signal(|| false);
    let close_destination = return_to.clone();
    let saved_destination = return_to.clone();
    rsx! {
        section { class: "form-page", aria_labelledby: "existing-registration-title",
            h2 { id: "existing-registration-title", "Your registration" }
            p { "You are already registered. Update your details or withdraw using the registration editor." }
            div { class: "d-flex flex-wrap gap-2",
                Link { to: return_to, class: "btn btn-outline-secondary", "Back to details" }
                if let Some(roster) = roster {
                    Link { to: roster, class: "btn btn-outline-secondary", "Manage roster" }
                }
            }
            EditRegistrationModal {
                context,
                user_type,
                // Replace the editing entry after cancel/save/withdraw so Back
                // does not unexpectedly reopen a completed modal workflow.
                // The shared editor calls both callbacks after a successful
                // mutation. Complete the URL transition exactly once.
                on_close: move |_| {
                    if !returning() {
                        returning.set(true);
                        navigator.replace(close_destination.clone());
                    }
                },
                on_success: move |_| {
                    if !returning() {
                        returning.set(true);
                        navigator.replace(saved_destination.clone());
                    }
                },
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::has_existing_registration;

    #[test]
    fn registration_entry_uses_the_signed_in_account_type() {
        assert!(has_existing_registration("team", true, false));
        assert!(has_existing_registration("player", false, true));
        assert!(!has_existing_registration("team", false, true));
        assert!(!has_existing_registration("player", true, false));
        assert!(!has_existing_registration("organizer", true, true));
        assert!(!has_existing_registration("", true, true));
    }
}
