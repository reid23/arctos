//! Where Stripe Checkout returns the payer. Confirms the payment with the
//! backend, which completes the registration if the webhook hasn't yet.

use crate::api;
use crate::types::CheckoutStatus;
use crate::Route;
use dioxus::prelude::*;

/// Delayed payment methods can take a while; stop polling after about a minute.
#[cfg(target_arch = "wasm32")]
const POLL_INTERVAL_MS: u32 = 2_000;
const MAX_POLLS: u32 = 30;

fn registration_route(status: &CheckoutStatus) -> Route {
    let slug = status.scope.slug.clone();
    match (status.scope.kind.as_str(), &status.team_id) {
        (kind, Some(team_id)) => Route::ManageTeamEvent { team_id: team_id.clone(), kind: kind.into(), slug },
        ("leagues", None) => Route::LeagueRegister { league_url: slug },
        (_, None) => Route::TournamentRegister { url: slug },
    }
}

fn event_route(status: &CheckoutStatus) -> Route {
    let slug = status.scope.slug.clone();
    if status.scope.kind == "leagues" {
        Route::LeagueHome { league_url: slug }
    } else {
        Route::TournamentHome { url: slug }
    }
}

#[component]
pub fn CheckoutReturn(session_id: String) -> Element {
    #[cfg_attr(not(target_arch = "wasm32"), allow(unused_mut))]
    let mut polls = use_signal(|| 0_u32);
    let mut checkout = use_resource(use_reactive(&session_id, |session_id| async move {
        api::sync_checkout(&session_id).await
    }));
    use_effect(move || {
        let still_open = matches!(&*checkout.read(), Some(Ok(status)) if status.status == "open");
        if still_open && polls() < MAX_POLLS {
            #[cfg(target_arch = "wasm32")]
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(POLL_INTERVAL_MS).await;
                polls += 1;
                checkout.restart();
            });
        }
    });

    let body = match &*checkout.read() {
        None => rsx! { p { role: "status", "Confirming your payment…" } },
        Some(Err(message)) => rsx! {
            div { class: "alert alert-danger", role: "alert", "Couldn't confirm your payment: {message}" }
            button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| checkout.restart(), "Try again" }
        },
        Some(Ok(status)) => {
            let event_name = status.scope.name.clone();
            match (status.status.as_str(), status.registered, status.failure.clone()) {
                ("completed", true, _) => rsx! {
                    div { class: "alert alert-success", role: "status",
                        h2 { class: "h5", "Payment received" }
                        p { class: "mb-0", "You're registered for {event_name}." }
                    }
                    Link { to: event_route(status), class: "btn btn-primary", "Go to {event_name}" }
                },
                ("completed", false, failure) => rsx! {
                    div { class: "alert alert-warning", role: "alert",
                        h2 { class: "h5", "Payment received" }
                        p { class: "mb-0",
                            "You have a prepaid entry for {event_name}, but registration couldn't complete"
                            if let Some(reason) = failure { ": {reason}" } else { "." }
                        }
                    }
                    Link { to: registration_route(status), class: "btn btn-primary", "Finish registering" }
                },
                ("expired", _, failure) => rsx! {
                    div { class: "alert alert-secondary", role: "status",
                        p { class: "mb-0", {failure.unwrap_or_else(|| "This checkout expired. Nothing was charged.".into())} }
                    }
                    Link { to: registration_route(status), class: "btn btn-primary", "Back to registration" }
                },
                _ if polls() >= MAX_POLLS => rsx! {
                    div { class: "alert alert-info", role: "status",
                        p { class: "mb-0", "Your payment is still processing. You'll be registered once it goes through; check back later." }
                    }
                    Link { to: event_route(status), class: "btn btn-outline-secondary", "Go to {event_name}" }
                },
                _ => rsx! { p { role: "status", "Confirming your payment…" } },
            }
        }
    };
    rsx! {
        section { class: "form-page", aria_labelledby: "checkout-return-title",
            h1 { id: "checkout-return-title", class: "h3", "Registration payment" }
            {body}
        }
    }
}
