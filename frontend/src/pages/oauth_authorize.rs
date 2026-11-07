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

fn prompt_contains(query: &str, value: &str) -> bool {
    query_param(query, "prompt")
        .is_some_and(|prompt| prompt.split_whitespace().any(|part| part == value))
}

fn needs_fresh_login(query: &str) -> bool {
    prompt_contains(query, "login") || query_param(query, "max_age").as_deref() == Some("0")
}

fn take_reauthentication(query: &str) -> bool {
    #[cfg(target_arch = "wasm32")]
    {
        let Some(storage) = web_sys::window().and_then(|window| window.session_storage().ok().flatten()) else {
            return false;
        };
        let marked = storage.get_item("arctos.oauth.reauth").ok().flatten().as_deref() == Some(query);
        if marked {
            let _ = storage.remove_item("arctos.oauth.reauth");
        }
        return marked;
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = query;
        false
    }
}

fn mark_reauthentication(query: &str) {
    #[cfg(target_arch = "wasm32")]
    if let Some(storage) = web_sys::window().and_then(|window| window.session_storage().ok().flatten()) {
        let _ = storage.set_item("arctos.oauth.reauth", query);
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = query;
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
    let mut reauth_checked = use_signal(|| false);
    let mut request = use_resource(move || async move {
        let state = account.read().clone();
        let current_query = query();
        match state {
            Some(Err(api::AccountError::Unauthenticated)) => {
                if !prompt_contains(&current_query, "none") {
                    let next = format!("/oauth/authorize?{current_query}");
                    navigate_away(&format!("/login?next={}", urlencoding::encode(&next)));
                    return None;
                }
            }
            Some(Ok(_)) => {
                if needs_fresh_login(&current_query) && !*reauth_checked.peek() {
                    if take_reauthentication(&current_query) {
                        reauth_checked.set(true);
                    } else {
                        mark_reauthentication(&current_query);
                        let next = format!("/oauth/authorize?{current_query}");
                        let _ = api::logout().await;
                        navigate_away(&format!("/login?next={}", urlencoding::encode(&next)));
                        return None;
                    }
                }
            }
            _ => return None,
        }
        Some(api::oauth_authorization_request(&current_query).await)
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
    let mut switch_account = move |_| {
        deciding.set(true);
        let next = format!("/oauth/authorize?{}", query());
        spawn(async move {
            let _ = api::logout().await;
            navigate_away(&format!("/login?next={}", urlencoding::encode(&next)));
        });
    };

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
            if consent.already_allowed {
                p { class: "mb-0",
                    "Sign in to "
                    strong { "{consent.client.name}" }
                    " as "
                    strong { "{consent.user.name}" }
                    " (@{consent.user.id})?"
                }
            } else {
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
                    for scope in consent.scopes.iter().filter(|scope| scope.name != "openid" && scope.name != "email") {
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
            }
            if let Some(message) = error() {
                div { class: "alert alert-danger", "{message}" }
            }
            div { class: "d-flex flex-wrap gap-2 mt-3",
                button {
                    class: "btn btn-primary",
                    disabled: deciding(),
                    onclick: move |_| decide(true),
                    if consent.already_allowed { "Sign in" } else { "Allow" }
                }
                button {
                    class: "btn btn-outline-secondary",
                    disabled: deciding(),
                    onclick: move |_| decide(false),
                    "Cancel"
                }
                button {
                    class: "btn btn-link",
                    disabled: deciding(),
                    onclick: move |_| switch_account(()),
                    "Use a different account"
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
