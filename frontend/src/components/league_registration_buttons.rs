//! Shared registration buttons for league context (league home or league-event tournament).
//! Shows roster/edit actions when registered, or "Register" when not.

use crate::types::User;
use crate::Route;
use dioxus::prelude::*;

const REGISTRATION_ACTIONS_CSS: &str = include_str!("registration_actions.css");

#[component]
pub fn LeagueRegistrationButtons(
    league_url: String,
    /// Aggregate flag kept for compatibility; per-role flags below take precedence.
    registration_open: bool,
    current_user: Option<Result<User, crate::api::AccountError>>,
    is_team_registered: bool,
    is_player_registered: bool,
    /// When true, Edit Registration uses a button that calls on_edit_registration; otherwise links to the registration page.
    use_edit_modal: bool,
    /// Callback to open the Edit Registration modal. Used when use_edit_modal is true.
    on_edit_registration: EventHandler<()>,
    #[props(default = false)] show_edit_warning: bool,
    /// Per-role toggles; when provided, override the aggregate flag.
    #[props(default = None)]
    team_registration_open: Option<bool>,
    #[props(default = None)] player_registration_open: Option<bool>,
) -> Element {
    let show_register_only = current_user
        .as_ref()
        .and_then(|user| user.as_ref().ok())
        .is_none();
    let (is_registered, user_type) = current_user
        .as_ref()
        .and_then(|r| r.as_ref().ok())
        .map(|u| {
            let reg = (u.user_type == "team" && is_team_registered)
                || (u.user_type == "player" && is_player_registered);
            (reg, u.user_type.as_str())
        })
        .unwrap_or((false, ""));

    let team_open = team_registration_open.unwrap_or(registration_open);
    let player_open = player_registration_open.unwrap_or(registration_open);

    rsx! {
        style { {REGISTRATION_ACTIONS_CSS} }
        if current_user.is_none() {
            span { class: "text-muted", role: "status", "Loading registration…" }
        } else if matches!(current_user, Some(Err(crate::api::AccountError::Unavailable))) {
            // The shared account notice owns retry; do not imply this visitor is signed out.
        } else if show_register_only && (team_open || player_open) {
            a {
                href: format!("/login?next={}", urlencoding::encode(&format!("/leagues/{league_url}/register"))),
                class: "btn btn-primary event-registration-action",
                "Log in to register"
            }
        } else if show_register_only {
            button { r#type: "button", class: "btn btn-secondary", disabled: true, "Registration closed" }
        } else {
            if user_type == "team" {
                if is_team_registered {
                    Link {
                        to: Route::LeagueInvitations { league_url: league_url.clone() },
                        class: "btn btn-outline-secondary",
                        "Manage roster"
                    }
                    if use_edit_modal {
                        button {
                            r#type: "button",
                            class: "btn btn-outline-secondary",
                            aria_haspopup: "dialog",
                            aria_describedby: show_edit_warning.then_some("registration-waiver-warning"),
                            onclick: move |_| on_edit_registration.call(()),
                            "Edit registration"
                        }
                    } else {
                        Link {
                            to: Route::LeagueRegister { league_url: league_url.clone() },
                            class: "btn btn-outline-secondary",
                            aria_describedby: show_edit_warning.then_some("registration-waiver-warning"),
                            "Edit registration"
                        }
                    }
                } else if team_open {
                    Link {
                        to: Route::LeagueRegister { league_url: league_url.clone() },
                        class: "btn btn-primary event-registration-action",
                        "Register"
                    }
                } else {
                    button {
                        r#type: "button",
                        class: "btn btn-secondary disabled",
                        disabled: true,
                        "Team registration closed"
                    }
                }
            } else if user_type == "player" {
                if is_player_registered {
                    if use_edit_modal {
                        button {
                            r#type: "button",
                            class: "btn btn-outline-secondary",
                            aria_haspopup: "dialog",
                            aria_describedby: show_edit_warning.then_some("registration-waiver-warning"),
                            onclick: move |_| on_edit_registration.call(()),
                            "Edit registration"
                        }
                    } else {
                        Link {
                            to: Route::LeagueRegister { league_url: league_url.clone() },
                            class: "btn btn-outline-secondary",
                            aria_describedby: show_edit_warning.then_some("registration-waiver-warning"),
                            "Edit registration"
                        }
                    }
                } else if player_open {
                    Link {
                        to: Route::LeagueRegister { league_url: league_url.clone() },
                        class: "btn btn-primary event-registration-action",
                        "Register"
                    }
                } else {
                    button {
                        r#type: "button",
                        class: "btn btn-secondary disabled",
                        disabled: true,
                        "Player registration closed"
                    }
                }
            } else {
                if team_open || player_open {
                    Link {
                        to: Route::LeagueRegister { league_url: league_url.clone() },
                        class: "btn btn-primary event-registration-action",
                        "Register"
                    }
                } else {
                    button {
                        r#type: "button",
                        class: "btn btn-secondary disabled",
                        disabled: true,
                        "Registration closed"
                    }
                }
            }
            if is_registered && show_edit_warning {
                p { id: "registration-waiver-warning", class: "registration-waiver-warning",
                    i { class: "fas fa-file-signature", aria_hidden: "true" }
                    "Your waiver needs signing."
                }
            }
        }
    }
}
