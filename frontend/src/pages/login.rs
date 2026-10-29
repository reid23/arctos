use crate::api;
use crate::form_validation::{described_by, field_message, focus_after_render, FieldError};
use crate::pages::layout::use_auth_invalidate;
use dioxus::prelude::*;

fn next_url_from_query() -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        let query = crate::initial_query("/login")?;
        let params = web_sys::UrlSearchParams::new_with_str(&query).ok()?;
        // `//host` and `/\host` leave the site.
        params.get("next").filter(|next| is_safe_return_path(next))
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        None
    }
}

/// A full page load, since navigating within the app would drop `next`'s query string.
pub(super) fn go_to_next(next: &str) {
    #[cfg(target_arch = "wasm32")]
    if let Some(window) = web_sys::window() {
        let _ = window.location().assign(next);
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = next;
}

pub(super) fn is_safe_return_path(path: &str) -> bool {
    path.starts_with('/')
        && !path.starts_with("//")
        && !path.contains('\\')
        && !path.chars().any(char::is_control)
}

/// Keep the intended destination through Google account setup as well as login.
pub(super) fn remember_google_return(next: Option<&str>) {
    #[cfg(target_arch = "wasm32")]
    if let Some(storage) = web_sys::window().and_then(|w| w.session_storage().ok().flatten()) {
        let _ = storage.remove_item("arctos.auth.return");
        if let Some(next) = next.filter(|path| is_safe_return_path(path)) {
            let _ = storage.set_item("arctos.auth.return", next);
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = next;
}

pub(super) fn take_google_return() -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        let storage = web_sys::window()?.session_storage().ok().flatten()?;
        let next = storage.get_item("arctos.auth.return").ok().flatten();
        let _ = storage.remove_item("arctos.auth.return");
        next.filter(|path| is_safe_return_path(path))
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        None
    }
}

#[component]
pub fn Login() -> Element {
    let mut username = use_signal(|| String::new());
    let mut password = use_signal(|| String::new());
    let mut err = use_signal(|| None::<String>);
    let mut field_error = use_signal(|| None::<FieldError>);
    use_effect(move || { if err().is_some() { focus_after_render("login-error"); } });
    let mut busy = use_signal(|| false);
    let navigator = use_navigator();
    let auth_invalidate = use_auth_invalidate();
    let google_url = match next_url_from_query() {
        Some(next) => format!(
            "{}/_api/auth/google/login?next={}",
            api::base_url(),
            urlencoding::encode(&next)
        ),
        None => format!("{}/_api/auth/google/login", api::base_url()),
    };

    let registration_url = match next_url_from_query() {
        Some(next) => format!("/register?next={}", urlencoding::encode(&next)),
        None => "/register".to_string(),
    };

    rsx! {
        div { class: "row justify-content-center auth-page",
            div { class: "col-12 auth-column",
                div { class: "card auth-card",
                    div { class: "card-header",
                        h1 { class: "mb-2", "Sign in to Arctos" }
                    }
                    div { class: "card-body",
                        if let Some(e) = err.read().as_ref() {
                            div {
                                id: "login-error", tabindex: "-1",
                                class: "alert alert-danger d-flex align-items-center mb-3",
                                role: "alert",
                                span {
                                    class: "fa-solid fa-circle-exclamation me-2",
                                    style: "font-size: 1.25rem;",
                                }
                                div {
                                    strong { class: "alert-heading", "Login failed" }
                                    p { class: "mb-0 mt-1", "{e}" }
                                }
                            }
                        }
                        form {
                            oninput: move |_| { err.set(None); field_error.set(None); },
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                if busy() {
                                    return;
                                }
                                let u = username().trim().to_string();
                                let p = password().clone();
                                if u.is_empty() || p.is_empty() {
                                    let feedback = if u.is_empty() { FieldError::new("username", "Enter your username.") }
                                        else { FieldError::new("password", "Enter your password.") };
                                    focus_after_render(feedback.field_id.clone());
                                    field_error.set(Some(feedback));
                                    return;
                                }
                                err.set(None);
                                busy.set(true);
                                let nav = navigator.clone();
                                let mut auth_invalidate = auth_invalidate;
                                let next = next_url_from_query();
                                spawn(async move {
                                    match api::login(&u, &p).await {
                                        Ok(_) => {
                                            auth_invalidate.set(auth_invalidate() + 1);
                                            match next.as_deref() {
                                                Some(next) => go_to_next(next),
                                                None => {
                                                    let _ = nav.push("/");
                                                }
                                            }
                                        }
                                        Err(e) => {
                                            err.set(Some(e));
                                            busy.set(false);
                                        }
                                    }
                                });
                            },
                            div { class: "mb-3",
                                label { r#for: "username", class: "form-label", "Username" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "username",
                                    aria_invalid: field_message(&field_error(), "username").is_some(),
                                    aria_describedby: described_by(&field_error(), "username", "username-help"),
                                    name: "username",
                                    autocomplete: "username",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                    disabled: busy(),
                                    value: "{username()}",
                                    oninput: move |ev| username.set(ev.value().clone()),
                                    required: true,
                                }
                                if let Some(message) = field_message(&field_error(), "username") { p { id: "username-error", class: "text-danger small mt-1", "{message}" } }
                                div { id: "username-help", class: "form-text",
                                    "Use your player or team username. Google accounts can sign in below."
                                }
                            }
                            div { class: "mb-3",
                                label { r#for: "password", class: "form-label", "Password" }
                                input {
                                    r#type: "password",
                                    class: "form-control",
                                    id: "password",
                                    aria_invalid: field_message(&field_error(), "password").is_some(),
                                    aria_describedby: described_by(&field_error(), "password", ""),
                                    name: "password",
                                    autocomplete: "current-password",
                                    disabled: busy(),
                                    value: "{password()}",
                                    oninput: move |ev| password.set(ev.value().clone()),
                                    required: true,
                                }
                                if let Some(message) = field_message(&field_error(), "password") { p { id: "password-error", class: "text-danger small mt-1", "{message}" } }
                            }
                            div { class: "d-grid",
                                button {
                                    r#type: "submit",
                                    class: "btn btn-primary",
                                    disabled: busy(),
                                    if busy() {
                                        "Signing in…"
                                    } else {
                                        "Sign in"
                                    }
                                }
                            }
                        }
                        hr {}
                        div { class: "text-center mb-3",
                            p { class: "text-muted", "Or" }
                        }
                        div { class: "d-grid mb-3",
                            a {
                                href: "{google_url}",
                                class: "btn btn-outline-secondary",
                                onclick: move |_| remember_google_return(next_url_from_query().as_deref()),
                                span { style: "margin-right: 8px; vertical-align: middle; display: inline-block; width: 18px; height: 18px;",
                                    svg {
                                        width: "18",
                                        height: "18",
                                        view_box: "0 0 18 18",
                                        path {
                                            fill: "#4285F4",
                                            d: "M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z",
                                        }
                                        path {
                                            fill: "#34A853",
                                            d: "M9 18c2.43 0 4.467-.806 5.96-2.184l-2.908-2.258c-.806.54-1.837.86-3.052.86-2.347 0-4.33-1.585-5.04-3.715H.957v2.332C2.438 15.983 5.482 18 9 18z",
                                        }
                                        path {
                                            fill: "#FBBC05",
                                            d: "M3.96 10.703c-.18-.54-.282-1.117-.282-1.703s.102-1.163.282-1.703V4.965H.957C.348 6.175 0 7.55 0 9s.348 2.825.957 4.035l3.003-2.332z",
                                        }
                                        path {
                                            fill: "#EA4335",
                                            d: "M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.965L3.96 7.297C4.67 5.167 6.653 3.58 9 3.58z",
                                        }
                                    }
                                }
                                "Sign in with Google"
                            }
                        }
                        hr {}
                        div { class: "text-center",
                            p { class: "mb-0", "Don't have an account?" }
                            a { class: "auth-switch-link", href: "{registration_url}", "Create an account" }
                        }

                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::is_safe_return_path;

    #[test]
    fn auth_return_preserves_local_queries_and_rejects_external_destinations() {
        assert!(is_safe_return_path(
            "/oauth/authorize?client_id=example&scope=read"
        ));
        assert!(is_safe_return_path("/event/register"));
        for path in [
            "https://example.com",
            "//example.com",
            "/\\example.com",
            "/\n/example.com",
            "",
        ] {
            assert!(!is_safe_return_path(path), "{path:?}");
        }
    }
}
