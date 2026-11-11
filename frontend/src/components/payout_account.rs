//! Stripe payout accounts: a player's own setup card, and an organizer's online payment settings.
//! Both stay hidden when the server has no Stripe configuration.

use crate::api;
use crate::types::{OnlinePaymentSettings, PayoutAccount, PayoutAccountStatus};
use dioxus::prelude::*;

const STRIPE_DASHBOARD_URL: &str = "https://dashboard.stripe.com";

/// Leaves the SPA for a Stripe-hosted page.
pub(super) fn open_stripe(url: &str) {
    #[cfg(target_arch = "wasm32")]
    if let Some(window) = web_sys::window() {
        let _ = window.location().assign(url);
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = url;
}

fn own_status_text(account: &PayoutAccount) -> &'static str {
    match account.status() {
        PayoutAccountStatus::Incomplete => "Payout setup isn't finished.",
        PayoutAccountStatus::InReview => "Stripe is reviewing your details. You can't receive online payments until it finishes.",
        PayoutAccountStatus::Ready if !account.payouts_enabled => {
            "You can receive payments, but Stripe has paused payouts to your bank. Open the Stripe Dashboard to see why."
        }
        PayoutAccountStatus::Ready => "Ready to receive online payments.",
    }
}

#[component]
pub fn PayoutAccountCard() -> Element {
    let response = use_resource(move || async move {
        let mut response = api::payout_account().await?;
        // Webhooks can lag behind a return from onboarding.
        if response.account.as_ref().is_some_and(|account| account.status() != PayoutAccountStatus::Ready) {
            if let Ok(fresh) = api::refresh_payout_account().await {
                response.account = Some(fresh);
            }
        }
        Ok::<_, String>(response)
    });
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut start_onboarding = move || {
        if busy() { return; }
        busy.set(true);
        error.set(None);
        spawn(async move {
            match api::payout_onboarding_url().await {
                Ok(url) => open_stripe(&url),
                Err(message) => {
                    error.set(Some(message));
                    busy.set(false);
                }
            }
        });
    };

    let body = match &*response.read() {
        None => rsx! { p { class: "mb-0", "Loading…" } },
        Some(Err(message)) => rsx! { p { class: "text-danger mb-0", "{message}" } },
        Some(Ok(response)) if !response.available => return rsx! {},
        Some(Ok(response)) => match response.account.clone() {
            None => rsx! {
                p { "Receive registration fees online for events you organize. Stripe collects your details and pays out to your bank." }
                button { r#type: "button", class: "btn btn-primary btn-sm", disabled: busy(),
                    onclick: move |_| start_onboarding(),
                    if busy() { "Opening Stripe…" } else { "Set up payouts" } }
            },
            Some(account) => rsx! {
                p { role: "status", "{own_status_text(&account)}" }
                if account.status() == PayoutAccountStatus::Ready {
                    p { class: "small text-muted", "Manage payouts, refunds, and your bank details in the Stripe Dashboard. Sign in with the Stripe account you created during setup." }
                    a { class: "btn btn-outline-secondary btn-sm", href: STRIPE_DASHBOARD_URL, target: "_blank", rel: "noopener noreferrer",
                        "Open Stripe Dashboard" }
                } else {
                    button { r#type: "button", class: "btn btn-primary btn-sm", disabled: busy(),
                        onclick: move |_| start_onboarding(),
                        if busy() { "Opening Stripe…" } else if account.status() == PayoutAccountStatus::InReview { "Update details" } else { "Continue setup" } }
                }
            },
        },
    };
    rsx! {
        div { id: "payouts", class: "edit-section data-section mt-3",
            div { class: "edit-section-heading", h5 { class: "mb-0", "Payouts" } }
            div { class: "edit-section-body",
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", role: "alert", "{message}" }
                }
                {body}
            }
        }
    }
}

fn attached_account_text(settings: &OnlinePaymentSettings, account: &PayoutAccount) -> String {
    let owner = &account.owner.name;
    match account.status() {
        _ if settings.enabled && !settings.accepting => format!(
            "Online payments are on, but {owner}'s payout account can't accept payments right now. Paid registration is paused until Stripe re-enables it."
        ),
        PayoutAccountStatus::Ready => format!("Payments go to {owner}'s payout account."),
        _ => format!("Payments will go to {owner}'s payout account once its Stripe setup is finished."),
    }
}

/// `scope_kind` is `events` (standalone tournaments) or `leagues`.
#[component]
pub fn OnlinePaymentsCard(scope_kind: String, slug: String) -> Element {
    let mut settings = use_resource(use_reactive((&scope_kind, &slug), |(scope_kind, slug)| async move {
        api::online_payment_settings(&scope_kind, &slug).await
    }));
    let mut saving = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let save = use_callback(move |(enabled, payout_account_id): (bool, Option<u32>)| {
        if saving() { return; }
        saving.set(true);
        error.set(None);
        let scope_kind = scope_kind.clone();
        let slug = slug.clone();
        spawn(async move {
            match api::update_online_payment_settings(&scope_kind, &slug, enabled, payout_account_id).await {
                Ok(_) => settings.restart(),
                Err(message) => error.set(Some(message)),
            }
            saving.set(false);
        });
    });

    let current = settings.read().clone();
    let body = match current {
        None => rsx! { p { class: "mb-0", "Loading…" } },
        Some(Err(message)) => rsx! { p { class: "text-danger mb-0", "{message}" } },
        Some(Ok(current)) if !current.available => return rsx! {},
        Some(Ok(current)) => {
            let attached_id = current.account.as_ref().map(|account| account.id);
            let can_enable = current.account.as_ref().is_some_and(|account| account.status() == PayoutAccountStatus::Ready);
            let enabled = current.enabled;
            rsx! {
                match current.account.clone() {
                    Some(account) => rsx! {
                        p { role: "status", "{attached_account_text(&current, &account)}" }
                        button { r#type: "button", class: "btn btn-outline-danger btn-sm mb-3", disabled: saving(),
                            onclick: move |_| save.call((false, None)), "Detach payout account" }
                    },
                    None => match current.own_account.clone() {
                        Some(own) => rsx! {
                            p { "No payout account is attached." }
                            button { r#type: "button", class: "btn btn-primary btn-sm mb-3", disabled: saving(),
                                onclick: move |_| save.call((false, Some(own.id))), "Use my payout account" }
                        },
                        None => rsx! {
                            p { "No payout account is attached. To receive payments yourself, set up payouts under Account settings when you edit your profile." }
                        },
                    },
                }
                div { class: "form-check",
                    input { class: "form-check-input", r#type: "checkbox", id: "online_payments_enabled",
                        checked: enabled, disabled: saving() || (!enabled && !can_enable),
                        onchange: move |event| save.call((event.checked(), attached_id)) }
                    label { class: "form-check-label", r#for: "online_payments_enabled", "Registrants pay fees online by card" }
                }
                if !enabled && !can_enable {
                    p { class: "small text-muted mb-0", "Attach a payout account that has finished Stripe setup to turn this on." }
                }
            }
        }
    };
    rsx! {
        div { class: "edit-section mb-3",
            div { class: "edit-section-heading", h3 { class: "h5 mb-0", "Online payments" } }
            div { class: "edit-section-body",
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", role: "alert", "{message}" }
                }
                {body}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::PaidEntryAccount;

    fn account(charges_enabled: bool, payouts_enabled: bool, details_submitted: bool) -> PayoutAccount {
        PayoutAccount {
            id: 1,
            owner: PaidEntryAccount { id: "organizer".into(), name: "Organizer".into() },
            charges_enabled,
            payouts_enabled,
            details_submitted,
        }
    }

    fn settings(enabled: bool, accepting: bool, attached: &PayoutAccount) -> OnlinePaymentSettings {
        OnlinePaymentSettings { available: true, enabled, accepting, account: Some(attached.clone()), own_account: None }
    }

    #[test]
    fn status_follows_stripe_flags() {
        assert_eq!(account(false, false, false).status(), PayoutAccountStatus::Incomplete);
        assert_eq!(account(false, false, true).status(), PayoutAccountStatus::InReview);
        assert_eq!(account(true, false, true).status(), PayoutAccountStatus::Ready);
    }

    #[test]
    fn own_status_warns_about_paused_payouts() {
        assert!(own_status_text(&account(true, false, true)).contains("paused payouts"));
        assert_eq!(own_status_text(&account(true, true, true)), "Ready to receive online payments.");
    }

    #[test]
    fn attached_account_text_warns_when_enabled_payments_cannot_be_taken() {
        let restricted = account(false, true, true);
        assert!(attached_account_text(&settings(true, false, &restricted), &restricted).contains("Paid registration is paused"));
        assert!(attached_account_text(&settings(false, false, &restricted), &restricted).contains("once its Stripe setup is finished"));
        let ready = account(true, true, true);
        assert_eq!(attached_account_text(&settings(true, true, &ready), &ready), "Payments go to Organizer's payout account.");
    }
}
