use crate::{api, components::{ActionRecovery, RequestEpoch}};
use dioxus::prelude::*;

#[component]
pub fn InvitationActions(
    scope_url: String,
    league: bool,
    registration_id: u32,
    player_name: String,
    at_capacity: bool,
    refresh_epoch: Signal<RequestEpoch>,
    on_refreshed: EventHandler<crate::types::TournamentInvitationsResponse>,
) -> Element {
    let mut state = use_signal(ActionRecovery::default);
    let mut accepted = use_signal(|| false);
    let mut refresh_epoch = refresh_epoch;
    let mut act = {
        let scope_url = scope_url.clone();
        move |decision: Option<bool>| {
            if state().busy() || (decision.is_some() && state().blocks_write()) { return; }
            if decision == Some(true) && at_capacity { return; }
            if let Some(accept) = decision { accepted.set(accept); }
            state.set(if decision.is_some() { ActionRecovery::Pending } else { ActionRecovery::Checking });
            let url = scope_url.clone();
            spawn(async move {
                let write = match decision {
                    Some(true) if league => api::league_accept_invitation(&url, registration_id).await,
                    Some(false) if league => api::league_decline_invitation(&url, registration_id).await,
                    Some(true) => api::accept_invitation(&url, registration_id).await,
                    Some(false) => api::decline_invitation(&url, registration_id).await,
                    None => Ok(()),
                };
                state.set(ActionRecovery::Checking);
                let request = refresh_epoch.write().next();
                let current = if league { api::league_invitations(&url).await } else { api::tournament_invitations(&url).await };
                match current {
                    Ok(roster) => {
                        let still_pending = roster.invitations.iter().any(|item| item.registration.id == registration_id);
                        let latest = refresh_epoch.peek().is_current(request);
                        state.set(if still_pending {
                            ActionRecovery::Retryable(match write {
                                Err(error) => format!("The request is still pending. {error} Review the roster and try again."),
                                Ok(()) => "This request is still pending. Review it and try again.".into(),
                            })
                        } else if latest { ActionRecovery::Checking } else {
                            ActionRecovery::NeedsCheck("This request changed while another roster check was running. Check status to refresh the roster.".into())
                        });
                        if latest { on_refreshed.call(roster); }
                    }
                    Err(error) => state.set(ActionRecovery::NeedsCheck(format!("Could not confirm the request's current state. {error} Check status before trying again."))),
                }
            });
        }
    };
    let mut accept_action = act.clone();
    let mut decline_action = act.clone();
    rsx! {
        div { aria_busy: state().busy(),
            div { class: "btn-group", role: "group", aria_label: "Request from {player_name}",
                button { r#type: "button", class: "btn btn-success btn-sm", disabled: at_capacity || state().blocks_write(),
                    onclick: move |_| accept_action(Some(true)), "Accept" }
                button { r#type: "button", class: "btn btn-outline-danger btn-sm", disabled: state().blocks_write(),
                    onclick: move |_| decline_action(Some(false)), "Decline" }
            }
            if state().busy() {
                p { class: "small text-muted mb-0", role: "status",
                    if state() == ActionRecovery::Checking { "Checking roster…" }
                    else if accepted() { "Accepting request…" } else { "Declining request…" }
                }
            }
            if at_capacity { p { class: "small text-muted mb-0", "Roster is full. Decline is still available." } }
            if let Some(message) = state().message() { p { class: "small text-danger mb-0", role: "alert", "{message}" } }
            if matches!(state(), ActionRecovery::NeedsCheck(_)) {
                button { r#type: "button", class: "btn btn-sm btn-outline-secondary mt-1", onclick: move |_| act(None), "Check status" }
            }
        }
    }
}
