//! Paying registration fees through Stripe Checkout. The backend decides
//! whether a registrant must pay online; these components follow its answer.

use super::payout_account::open_stripe;
use super::{PaidEntryScope, ScopePaidEntries};
use crate::api;
use crate::types::CheckoutOptions;
use dioxus::prelude::*;

/// How the registrant pays, refetched when their prepaid entries change.
/// Resolves to `None` when `registrant` is unknown or the lookup fails;
/// the backend still refuses unpaid registrations.
pub fn use_checkout_options(
    scope: PaidEntryScope,
    team_id: Option<String>,
    registrant: impl Fn() -> Option<&'static str> + 'static,
    entries: Resource<Result<ScopePaidEntries, String>>,
) -> Resource<Option<CheckoutOptions>> {
    use_resource(move || {
        let scope = scope.clone();
        let team_id = team_id.clone();
        let registrant = registrant();
        // Subscribe: declining or claiming an entry changes whether payment is needed.
        let _ = entries.read();
        async move {
            api::checkout_options(scope.kind(), scope.slug(), registrant?, team_id.as_deref()).await.ok()
        }
    })
}

/// The registrant kind for a signed-in account type, if it can register.
pub fn registrant_kind(user_type: &str) -> Option<&'static str> {
    match user_type {
        "team" => Some("team"),
        "player" => Some("player"),
        _ => None,
    }
}

/// Starts a checkout and leaves for Stripe. Cancelling returns to `return_to`.
pub async fn continue_to_checkout(
    scope: &PaidEntryScope,
    registrant: &str,
    team_id: Option<&str>,
    form: serde_json::Value,
    return_to: &str,
) -> Result<(), String> {
    let url = api::start_checkout(scope.kind(), scope.slug(), registrant, team_id, form, return_to).await?;
    open_stripe(&url);
    Ok(())
}

/// Replaces the "pay the organizer separately" note when the fee is paid online.
#[component]
pub fn CheckoutFeeNotice(options: Option<CheckoutOptions>) -> Element {
    match options {
        Some(options) if options.pays_online() => rsx! {
            div { class: "alert alert-info",
                strong { "Registration fee: {options.amount()}" }
                p { class: "small mb-0 mt-1",
                    "You'll pay by card on Stripe's secure checkout. You're registered once the payment goes through."
                }
            }
        },
        Some(options) if options.blocked() => rsx! {
            div { class: "alert alert-warning", role: "status",
                "The organizer's payment setup is incomplete. Contact them to register."
            }
        },
        _ => rsx! {},
    }
}

/// Offers online payment for an existing registration that isn't paid.
#[component]
pub fn PayNowNotice(scope: PaidEntryScope, registrant: String, team_id: Option<String>, return_to: String) -> Element {
    let options = use_resource(use_reactive(
        &(scope.clone(), registrant.clone(), team_id.clone()),
        |(scope, registrant, team_id)| async move {
            api::checkout_options(scope.kind(), scope.slug(), &registrant, team_id.as_deref()).await.ok()
        },
    ));
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let Some(Some(current)) = options.read().clone() else {
        return rsx! {};
    };
    if !current.pays_online() {
        return rsx! {};
    }
    let pay = move |_| {
        if busy() {
            return;
        }
        busy.set(true);
        error.set(None);
        let scope = scope.clone();
        let registrant = registrant.clone();
        let team_id = team_id.clone();
        let return_to = return_to.clone();
        spawn(async move {
            if let Err(message) =
                continue_to_checkout(&scope, &registrant, team_id.as_deref(), serde_json::json!({}), &return_to).await
            {
                error.set(Some(message));
                busy.set(false);
            }
        });
    };
    rsx! {
        div { class: "alert alert-warning", role: "status",
            div { class: "d-flex flex-wrap align-items-center gap-2",
                span { class: "me-auto", "This registration isn't paid yet." }
                button { r#type: "button", class: "btn btn-primary btn-sm", disabled: busy(), onclick: pay,
                    if busy() { "Opening checkout…" } else { "Pay {current.amount()} now" }
                }
            }
            if let Some(message) = error() {
                p { class: "text-danger small mb-0 mt-2", role: "alert", "{message}" }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::registrant_kind;
    use crate::types::CheckoutOptions;

    #[test]
    fn checkout_options_format_whole_and_fractional_amounts() {
        let options = |amount_cents| CheckoutOptions { mode: "checkout".into(), amount_cents };
        assert_eq!(options(1000).amount(), "$10.00");
        assert_eq!(options(1205).amount(), "$12.05");
        assert_eq!(options(99).amount(), "$0.99");
    }

    #[test]
    fn checkout_modes_decide_payment_and_blocking() {
        let options = |mode: &str| CheckoutOptions { mode: mode.into(), amount_cents: 500 };
        assert!(options("checkout").pays_online() && !options("checkout").blocked());
        assert!(options("unavailable").blocked() && !options("unavailable").pays_online());
        assert!(!options("none").pays_online() && !options("none").blocked());
    }

    #[test]
    fn only_teams_and_players_register() {
        assert_eq!(registrant_kind("team"), Some("team"));
        assert_eq!(registrant_kind("player"), Some("player"));
        assert_eq!(registrant_kind("organizer"), None);
    }
}
