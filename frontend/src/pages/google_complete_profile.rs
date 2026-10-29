use super::login::{go_to_next, take_google_return};
use crate::api;
use crate::form_validation::{described_by, field_message, focus_after_render, FieldError};
use crate::pages::layout::use_auth_invalidate;
use crate::types::GoogleCompleteProfileRequest;
use crate::Route;
use dioxus::prelude::use_navigator;
use dioxus::prelude::*;

fn validate_username(username: &str) -> Result<(), String> {
    let u = username.trim();
    if u.is_empty() {
        return Err("Username is required".into());
    }
    if u.len() > 50 {
        return Err("Username must be 50 characters or less".into());
    }
    let mut chars = u.chars().peekable();
    let first = chars
        .next()
        .ok_or_else(|| "Username is required".to_string())?;
    let last = u.chars().last().unwrap_or(first);
    let valid_edge = |c: char| c.is_ascii_alphanumeric();
    if !valid_edge(first) || !valid_edge(last) {
        return Err("Username must be URL-safe: only letters, numbers, hyphens, and underscores. Cannot start or end with hyphen or underscore.".into());
    }
    for c in u.chars() {
        if !(c.is_ascii_alphanumeric() || c == '-' || c == '_') {
            return Err("Username must be URL-safe: only letters, numbers, hyphens, and underscores. Cannot start or end with hyphen or underscore.".into());
        }
    }
    Ok(())
}

fn username_check_is_current(request_revision: u64, current_revision: u64, requested: &str, current: &str) -> bool {
    request_revision == current_revision && requested == current
}

#[component]
pub fn GoogleCompleteProfile() -> Element {
    let nav = use_navigator();
    let mut auth_invalidate = use_auth_invalidate();
    let mut email = use_signal(|| "".to_string());
    let mut user_type = use_signal(|| "".to_string());
    let mut username = use_signal(|| "".to_string());
    let mut display_name = use_signal(|| "".to_string());
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);
    let mut load_error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);
    let mut field_error = use_signal(|| None::<FieldError>);
    let mut check_revision = use_signal(|| 0_u64);
    use_effect(move || { if error().is_some() { focus_after_render("google-profile-error"); } });
    let mut username_status = use_signal(|| None::<String>);

    let mut fetch = use_resource(move || async move {
        loading.set(true);
        load_error.set(None);
        match api::google_complete_profile_info().await {
            Ok(res) => {
                email.set(res.email);
                user_type.set(res.user_type);
                display_name.set(res.suggested_name);
            }
            Err(e) => load_error.set(Some(e)),
        }
        loading.set(false);
    });

    let on_check_username = move |_| async move {
        if loading() || saving() || load_error().is_some() { return; }
        let u = username();
        let revision = check_revision().wrapping_add(1);
        check_revision.set(revision);
        if let Err(msg) = validate_username(&u) {
            username_status.set(Some(msg));
            return;
        }
        let result = api::check_username(u.trim()).await;
        // A response belongs to the exact draft/request that produced it, including
        // when the user changes away from a name and then changes back.
        if !username_check_is_current(revision, check_revision(), &u, &username()) { return; }
        username_status.set(Some(match result {
            Ok(res) if res.available => "Username is available".into(),
            Ok(res) => res.message,
            Err(message) => message,
        }));
    };

    let onsubmit = move |evt: Event<FormData>| async move {
        evt.prevent_default();
        if loading() || saving() || load_error().is_some() {
            return;
        }
        error.set(None);

        if let Err(msg) = validate_username(&username()) {
            field_error.set(Some(FieldError::new("google-username", msg)));
            focus_after_render("google-username");
            return;
        }
        if display_name().trim().is_empty() {
            field_error.set(Some(FieldError::new("google-display-name", "Enter a display name.")));
            focus_after_render("google-display-name");
            return;
        }

        saving.set(true);
        let req = GoogleCompleteProfileRequest {
            username: username().trim().to_string(),
            display_name: display_name().trim().to_string(),
        };
        match api::google_complete_profile(&req).await {
            Ok(_) => {
                auth_invalidate.set(auth_invalidate() + 1);
                match take_google_return() {
                    Some(next) => go_to_next(&next),
                    None => {
                        nav.push(Route::Index {});
                    }
                }
            }
            Err(e) => {
                error.set(Some(e));
                saving.set(false);
            }
        }
    };

    rsx! {
        div { class: "row justify-content-center auth-page",
            div { class: "col-12 auth-column",
                div { class: "card auth-card",
                    div { class: "card-header",
                        h1 { class: "mb-2", "Complete your profile" }
                        if !user_type().is_empty() { p { class: "mb-0 text-muted",
                            small { "Creating a {user_type()} account" }
                        } }
                    }
                    div { class: "card-body",
                        if loading() {
                            p { role: "status", "Loading account setup…" }
                        } else if let Some(message) = load_error() {
                            p { class: "text-danger", role: "alert", "Could not load account setup: {message}" }
                            button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| fetch.restart(), "Retry account setup" }
                        } else {
                        p { class: "text-muted",
                            "You're signing in with Google as "
                            strong { "{email}" }
                            ". Please choose your username and display name to complete your account setup."
                        }
                        if user_type() == "team" {
                            p { class: "form-text", "Your Google email won't be published. You can add a public contact email in Edit profile." }
                        }
                        if let Some(err) = error() {
                            div { id: "google-profile-error", tabindex: "-1", class: "alert alert-danger", role: "alert", "{err}" }
                        }
                        form {
                            oninput: move |_| { error.set(None); field_error.set(None); },
                            onsubmit: onsubmit,
                            div { class: "mb-3",
                                label { class: "form-label", r#for: "google-username", "Username" }
                                input {
                                    class: "form-control",
                                    "type": "text",
                                    id: "google-username",
                                    aria_invalid: field_message(&field_error(), "google-username").is_some(),
                                    aria_describedby: described_by(&field_error(), "google-username", "google-username-help google-username-status"),
                                    name: "username",
                                    autocomplete: "username",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                    maxlength: 50,
                                    disabled: saving(),
                                    value: "{username}",
                                    oninput: move |e| { check_revision.set(check_revision().wrapping_add(1)); username.set(e.value()); username_status.set(None); },
                                    onblur: on_check_username,
                                    required: true
                                }
                                if let Some(message) = field_message(&field_error(), "google-username") { p { id: "google-username-error", class: "text-danger small mt-1", "{message}" } }
                                div { id: "google-username-help", class: "form-text",
                                    strong { "This is your permanent URL identifier. " }
                                    "Must be URL-safe: only letters, numbers, hyphens, and underscores. Cannot start or end with hyphen or underscore."
                                }
                                div { id: "google-username-status", class: "form-text", role: "status", if let Some(msg) = username_status() { "{msg}" } }
                            }
                            div { class: "mb-3",
                                label { class: "form-label", r#for: "google-display-name", "Display name" }
                                input {
                                    class: "form-control",
                                    "type": "text",
                                    id: "google-display-name",
                                    aria_invalid: field_message(&field_error(), "google-display-name").is_some(),
                                    aria_describedby: described_by(&field_error(), "google-display-name", "google-display-name-help"),
                                    name: "display_name",
                                    autocomplete: "nickname",
                                    disabled: saving(),
                                    value: "{display_name}",
                                    oninput: move |e| display_name.set(e.value()),
                                    required: true
                                }
                                if let Some(message) = field_message(&field_error(), "google-display-name") { p { id: "google-display-name-error", class: "text-danger small mt-1", "{message}" } }
                                div { id: "google-display-name-help", class: "form-text", "Your public name. You can change it later." }
                            }
                            div { class: "d-grid",
                                button { class: "btn btn-primary", "type": "submit", disabled: saving(), if saving() { "Creating account…" } else { "Create account" } }
                            }
                        }
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod form_tests {
    use super::{username_check_is_current, validate_username};

    #[test]
    fn username_rules_keep_existing_valid_identifiers_and_boundaries() {
        for valid in ["a", "Player_2", "team-name", "  player  "] { assert!(validate_username(valid).is_ok()); }
        for invalid in ["", " ", "-player", "player_", "two words", "é"] { assert!(validate_username(invalid).is_err()); }
        assert!(validate_username(&"a".repeat(50)).is_ok());
        assert!(validate_username(&"a".repeat(51)).is_err());
    }

    #[test]
    fn availability_results_cannot_overwrite_edited_or_rechecked_names() {
        assert!(username_check_is_current(1, 1, "player", "player"));
        assert!(!username_check_is_current(1, 1, "player", "other"));
        assert!(!username_check_is_current(1, 2, "player", "player"));
        // Returning to the same text still must not accept an earlier request.
        assert!(!username_check_is_current(1, 3, "player", "player"));
    }
}
