use super::login::{go_to_next, is_safe_return_path, remember_google_return};
use crate::api;
use crate::pages::layout::use_auth_invalidate;
use dioxus::prelude::*;

fn get_query_param(name: &str) -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        let window = web_sys::window()?;
        let search = ["/register", "/register/player", "/register/team"]
            .iter()
            .find_map(|path| crate::initial_query(path))
            .unwrap_or_else(|| window.location().search().unwrap_or_default());
        let params = web_sys::UrlSearchParams::new_with_str(&search).ok()?;
        params.get(name)
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = name;
        None
    }
}

fn initial_user_type_from_query() -> String {
    match get_query_param("type").as_deref() {
        Some("team") => "team".to_string(),
        _ => "player".to_string(),
    }
}

/// Initial account type from route: None = use query param (default player).
fn resolve_initial_type(account_type: Option<String>) -> String {
    match account_type.as_deref() {
        Some("team") => "team".to_string(),
        Some("player") => "player".to_string(),
        _ => initial_user_type_from_query(),
    }
}

#[component]
pub fn Register() -> Element {
    rsx! { RegisterPage { account_type: None } }
}

#[component]
pub fn RegisterPlayer() -> Element {
    rsx! { RegisterPage { account_type: Some("player".to_string()) } }
}

#[component]
pub fn RegisterTeam() -> Element {
    rsx! { RegisterPage { account_type: Some("team".to_string()) } }
}

#[component]
fn RegisterPage(account_type: Option<String>) -> Element {
    let initial = resolve_initial_type(account_type.clone());
    let mut username = use_signal(|| String::new());
    let mut password = use_signal(|| String::new());
    let mut confirm_password = use_signal(|| String::new());
    let mut name = use_signal(|| String::new());
    let mut user_type = use_signal(move || initial);
    let mut err = use_signal(|| None::<String>);
    let mut busy = use_signal(|| false);
    let navigator = use_navigator();
    let auth_invalidate = use_auth_invalidate();
    let next = get_query_param("next").filter(|path| is_safe_return_path(path));
    let next_suffix = next
        .as_ref()
        .map(|path| format!("&next={}", urlencoding::encode(path)))
        .unwrap_or_default();
    let google_url = format!(
        "{}/_api/auth/google/login?type={}{}",
        api::base_url(),
        user_type(),
        next_suffix
    );
    let login_url = next
        .as_ref()
        .map(|path| format!("/login?next={}", urlencoding::encode(path)))
        .unwrap_or_else(|| "/login".into());

    rsx! {
        div { class: "row justify-content-center auth-page",
            div { class: "col-12 auth-column",
                div { class: "card auth-card",
                    div { class: "card-header",
                        h1 { class: "mb-2", "Create your account" }
                        p { class: "text-muted mb-0", "Choose a player or team account." }
                    }
                    div { class: "card-body",
                        if let Some(e) = err.read().as_ref() {
                            div { class: "alert alert-danger mb-3", role: "alert", "{e}" }
                        }
                        div { class: "account-type-picker mb-4", role: "group", aria_label: "Account type",
                            button { r#type: "button", class: if user_type() == "player" { "account-type-option is-selected" } else { "account-type-option" }, aria_pressed: user_type() == "player", disabled: busy(), onclick: move |_| user_type.set("player".into()),
                                strong { "Player" } small { "Join events, play, and referee." }
                            }
                            button { r#type: "button", class: if user_type() == "team" { "account-type-option is-selected" } else { "account-type-option" }, aria_pressed: user_type() == "team", disabled: busy(), onclick: move |_| user_type.set("team".into()),
                                strong { "Team" } small { "Register one team and manage its roster." }
                            }
                        }
                        p { class: "form-text mb-3", "Both account types can organize events. Use a player account to referee matches." }
                        form {
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                if busy() { return; }
                                let u = username().trim().to_string();
                                let p = password().clone();
                                let cp = confirm_password().clone();
                                let n = name().trim().to_string();
                                let t = user_type().clone();
                                if u.is_empty() || p.is_empty() || n.is_empty() {
                                    err.set(Some("Username, password, and display name required".into()));
                                    return;
                                }
                                if p != cp {
                                    err.set(Some("Passwords do not match".into()));
                                    return;
                                }
                                err.set(None);
                                busy.set(true);
                                let nav = navigator.clone();
                                let mut auth_invalidate = auth_invalidate;
                                let next = get_query_param("next").filter(|path| is_safe_return_path(path));
                                spawn(async move {
                                    match api::register(&u, &p, &n, &t).await {
                                        Ok(_) => {
                                            auth_invalidate.set(auth_invalidate() + 1);
                                            match next.as_deref() {
                                                Some(next) => go_to_next(next),
                                                None => { let _ = nav.push("/"); }
                                            }
                                        }
                                        Err(e) => { err.set(Some(e)); busy.set(false); },
                                    }
                                });
                            },
                            div { class: "mb-3",
                                label { r#for: "username", class: "form-label", "Username" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "username",
                                    name: "username",
                                    autocomplete: "username",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                    maxlength: 50,
                                    disabled: busy(),
                                    value: "{username()}",
                                    oninput: move |ev| username.set(ev.value().clone()),
                                    required: true,
                                    pattern: r"[a-zA-Z0-9][a-zA-Z0-9_\-]*[a-zA-Z0-9]|[a-zA-Z0-9]",
                                }
                                div { class: "form-text",
                                    "This will be your unique identifier. "
                                    strong { "It is permanent." }
                                    " Must be URL-safe: only letters, numbers, hyphens, and underscores. Cannot start or end with hyphen or underscore."
                                }
                            }
                            div { class: "mb-3",
                                label { r#for: "name", class: "form-label", "Display Name" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "name",
                                    name: "name",
                                    autocomplete: if user_type() == "team" { "organization" } else { "nickname" },
                                    disabled: busy(),
                                    value: "{name()}",
                                    oninput: move |ev| name.set(ev.value().clone()),
                                    required: true,
                                }
                                div { class: "form-text", "Your public name. You can change it later." }
                            }
                            div { class: "mb-3",
                                label { r#for: "password", class: "form-label", "Password" }
                                input {
                                    r#type: "password",
                                    class: "form-control",
                                    id: "password",
                                    name: "password",
                                    autocomplete: "new-password",
                                    disabled: busy(),
                                    value: "{password()}",
                                    oninput: move |ev| password.set(ev.value().clone()),
                                    required: true,
                                }
                            }
                            div { class: "mb-3",
                                label { r#for: "confirm_password", class: "form-label", "Confirm Password" }
                                input {
                                    r#type: "password",
                                    class: "form-control",
                                    id: "confirm_password",
                                    name: "confirm_password",
                                    autocomplete: "new-password",
                                    disabled: busy(),
                                    value: "{confirm_password()}",
                                    oninput: move |ev| confirm_password.set(ev.value().clone()),
                                    required: true,
                                }
                            }
                            div { class: "d-grid",
                                button { r#type: "submit", class: "btn btn-primary", id: "submitBtn", disabled: busy(), if busy() { "Creating account…" } else { "Create account" } }
                            }
                        }
                        hr {}
                        div { class: "text-center mb-3",
                            p { class: "text-muted", "Or" }
                        }
                        div { class: "d-grid mb-3",
                            a { href: "{google_url}", class: "btn btn-outline-secondary", onclick: move |_| remember_google_return(get_query_param("next").as_deref()),
                                span { style: "margin-right: 8px; vertical-align: middle; display: inline-block; width: 18px; height: 18px;",
                                    svg { width: "18", height: "18", view_box: "0 0 18 18",
                                        path { fill: "#4285F4", d: "M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" }
                                        path { fill: "#34A853", d: "M9 18c2.43 0 4.467-.806 5.96-2.184l-2.908-2.258c-.806.54-1.837.86-3.052.86-2.347 0-4.33-1.585-5.04-3.715H.957v2.332C2.438 15.983 5.482 18 9 18z" }
                                        path { fill: "#FBBC05", d: "M3.96 10.703c-.18-.54-.282-1.117-.282-1.703s.102-1.163.282-1.703V4.965H.957C.348 6.175 0 7.55 0 9s.348 2.825.957 4.035l3.003-2.332z" }
                                        path { fill: "#EA4335", d: "M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.965L3.96 7.297C4.67 5.167 6.653 3.58 9 3.58z" }
                                    }
                                }
                                "Sign in with Google"
                            }
                        }
                        hr {}
                        div { class: "text-center",
                            p { "Already have an account? " a { href: "{login_url}", "Sign in" } }
                        }

                    }
                }
            }
        }
    }
}
