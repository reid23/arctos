//! Consent page for "Sign in with Arctos" (the OAuth authorization endpoint).

use crate::api;
use crate::types::OAuthAuthorizationRequest;
use dioxus::prelude::*;

fn query_param(query: &str, name: &str) -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        web_sys::UrlSearchParams::new_with_str(query).ok()?.get(name)
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (query, name);
        None
    }
}

/// Consent must not be clickjackable, so refuse to show it inside a frame.
fn is_framed() -> bool {
    #[cfg(target_arch = "wasm32")]
    {
        web_sys::window()
            .map(|window| window.top().ok().flatten().map_or(true, |top| top != window))
            .unwrap_or(false)
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        false
    }
}

/// Leaves the SPA, e.g. for an app's loopback redirect URI.
fn navigate_away(url: &str) {
    #[cfg(target_arch = "wasm32")]
    if let Some(window) = web_sys::window() {
        let _ = window.location().assign(url);
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = url;
}

#[component]
pub fn OAuthAuthorize() -> Element {
    let query = use_signal(|| crate::initial_query("/oauth/authorize").unwrap_or_default());
    let navigator = use_navigator();
    let mut restore_query = use_signal(|| true);
    use_effect(move || {
        if !*restore_query.peek() { return; }
        restore_query.set(false);
        let original = query.peek().clone();
        let router = dioxus::router::router();
        let current_url = router.full_route_string();
        let current_query = current_url.split('#').next().unwrap_or(&current_url)
            .split_once('?').map(|(_, query)| query).unwrap_or("");
        // The router strips this route's undeclared query during startup.
        // Keep the exact authorization request in history so reload can resume it.
        if !original.is_empty() && current_query.is_empty()
            && matches!(router.current::<crate::Route>(), crate::Route::OAuthAuthorize {}) {
            navigator.replace(format!("/oauth/authorize?{original}"));
        }
    });
    let mut error = use_signal(|| None::<String>);
    let mut deciding = use_signal(|| false);

    let account = crate::auth::use_account();
    let mut request = use_resource(move || async move {
        let state = account.read().clone();
        match state {
            Some(Err(api::AccountError::Unauthenticated)) => {
                let next = format!("/oauth/authorize?{}", query());
                navigate_away(&format!("/login?next={}", urlencoding::encode(&next)));
                return None;
            }
            Some(Ok(_)) => {},
            _ => return None,
        }
        Some(api::oauth_authorization_request(&query()).await)
    });

    let mut decide = move |approve: bool| {
        deciding.set(true);
        spawn(async move {
            match api::oauth_decide(&query(), approve).await {
                Ok(redirect) => navigate_away(&redirect),
                Err(message) => {
                    error.set(Some(message));
                    deciding.set(false);
                }
            }
        });
    };

    let redirect_uri = query_param(&query(), "redirect_uri");
    let checking_request = *request.state().read() == UseResourceState::Pending;

    let body = match &*request.read() {
        _ if is_framed() => rsx! {
            div { class: "alert alert-danger mb-0", "For your security, sign-in requests can't be shown inside another page." }
        },
        None | Some(None) => rsx! {
            if !matches!(account.read().as_ref(), Some(Err(api::AccountError::Unavailable))) {
                p { role: "status", "Checking sign-in request…" }
            }
        },
        Some(Some(Err(message))) => rsx! {
            div { class: "alert alert-danger mb-3", role: "alert",
                strong { "This sign-in request can't be completed. " }
                "{message}"
            }
            button { r#type: "button", class: "btn btn-outline-secondary", disabled: checking_request,
                onclick: move |_| { if request.finished() { request.restart(); } }, "Retry sign-in request" }
        },
        Some(Some(Ok(OAuthAuthorizationRequest::Redirect { redirect }))) => {
            navigate_away(redirect);
            rsx! { p { "Returning to the app…" } }
        }
        Some(Some(Ok(OAuthAuthorizationRequest::Consent(consent)))) => rsx! {
            p {
                strong { "{consent.client.name}" }
                " wants to sign in with your Arctos account "
                strong { "{consent.user.name}" }
                " (@{consent.user.id})."
            }
            p { class: "mb-1", "It will be able to:" }
            ul {
                li { "See your name and username" }
                li { "Read public Arctos data" }
                for scope in consent.scopes.iter() {
                    li { key: "{scope.name}", "{scope.description}" }
                }
            }
            p { class: "text-muted small mb-1",
                "Registered by @{consent.client.owner_id}. "
                if let Some(homepage) = consent.client.homepage_url.as_ref() {
                    a { href: "{homepage}", target: "_blank", rel: "noopener noreferrer", "{homepage}" }
                }
            }
            if let Some(uri) = redirect_uri.as_ref() {
                p { class: "text-muted small", "You'll be sent back to " code { "{uri}" } "." }
            }
            p { class: "text-muted small",
                "The app never sees your password. You can disconnect it at any time from your profile."
            }
            if let Some(message) = error() {
                div { class: "alert alert-danger", "{message}" }
            }
            div { class: "d-flex gap-2",
                button {
                    class: "btn btn-primary",
                    disabled: deciding(),
                    onclick: move |_| decide(true),
                    "Allow"
                }
                button {
                    class: "btn btn-outline-secondary",
                    disabled: deciding(),
                    onclick: move |_| decide(false),
                    "Cancel"
                }
            }
        },
    };

    rsx! {
        div { class: "row justify-content-center",
            div { class: "col-md-6",
                div { class: "card",
                    div { class: "card-header",
                        h3 { class: "mb-0", "Sign in with Arctos" }
                    }
                    div { class: "card-body", {body} }
                }
            }
        }
    }
}
