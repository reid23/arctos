//! Change password form for the account owner's profile editor.

use crate::api;
use crate::form_validation::{described_by, field_message, focus_after_render, FieldError};
use dioxus::prelude::*;

fn password_error(current: &str, new: &str, confirmation: &str) -> Option<FieldError> {
    if current.is_empty() {
        Some(FieldError::new("current-password", "Enter your current password."))
    } else if new.is_empty() {
        Some(FieldError::new("new-password", "Enter a new password."))
    } else if confirmation.is_empty() {
        Some(FieldError::new("confirm-password", "Confirm your new password."))
    } else if new != confirmation {
        Some(FieldError::new("confirm-password", "New password and confirmation do not match."))
    } else { None }
}

#[component]
pub fn ChangePasswordCard() -> Element {
    let mut current_password = use_signal(|| String::new());
    let mut new_password = use_signal(|| String::new());
    let mut confirm_password = use_signal(|| String::new());
    let mut error = use_signal(|| None::<String>);
    let mut field_error = use_signal(|| None::<FieldError>);
    use_effect(move || { if error().is_some() { focus_after_render("password-error"); } });
    let mut success = use_signal(|| false);
    let mut loading = use_signal(|| false);

    rsx! {
        div { class: "edit-section mt-3",
            div { class: "edit-section-heading",
                h3 { class: "h5 mb-0", "Change password" }
            }
            div { class: "edit-section-body",
                if let Some(ref e) = error() {
                    div { id: "password-error", tabindex: "-1", class: "alert alert-danger py-2", role: "alert", "{e}" }
                }
                form {
                    oninput: move |_| { field_error.set(None); error.set(None); success.set(false); },
                    onsubmit: move |ev| {
                        ev.prevent_default();
                        if loading() { return; }
                        success.set(false);
                        let cur = current_password();
                        let new_pw = new_password();
                        let conf = confirm_password();
                        if let Some(invalid) = password_error(&cur, &new_pw, &conf) {
                            focus_after_render(invalid.field_id.clone());
                            field_error.set(Some(invalid));
                            return;
                        }
                        field_error.set(None);
                        error.set(None);
                        loading.set(true);
                        let mut loading_sig = loading;
                        let mut error_sig = error;
                        let mut success_sig = success;
                        let mut cur_sig = current_password;
                        let mut new_sig = new_password;
                        let mut conf_sig = confirm_password;
                        spawn(async move {
                            match api::change_password(&cur, &new_pw).await {
                                Ok(()) => {
                                    success_sig.set(true);
                                    cur_sig.set(String::new());
                                    new_sig.set(String::new());
                                    conf_sig.set(String::new());
                                }
                                Err(e) => error_sig.set(Some(e)),
                            }
                            loading_sig.set(false);
                        });
                    },
                    div { class: "mb-2",
                        label { class: "form-label", r#for: "current-password", "Current password" }
                        input {
                            class: "form-control",
                            r#type: "password",
                            id: "current-password",
                            aria_invalid: field_message(&field_error(), "current-password").is_some(),
                            aria_describedby: described_by(&field_error(), "current-password", ""),
                            autocomplete: "current-password",
                            required: true,
                            value: "{current_password()}",
                            oninput: move |ev| current_password.set(ev.value().clone()),
                            disabled: loading(),
                        }
                        if let Some(message) = field_message(&field_error(), "current-password") {
                            p { id: "current-password-error", class: "text-danger small mt-1", "{message}" }
                        }
                    }
                    div { class: "mb-2",
                        label { class: "form-label", r#for: "new-password", "New password" }
                        input {
                            class: "form-control",
                            r#type: "password",
                            id: "new-password",
                            aria_invalid: field_message(&field_error(), "new-password").is_some(),
                            aria_describedby: described_by(&field_error(), "new-password", ""),
                            autocomplete: "new-password",
                            required: true,
                            value: "{new_password()}",
                            oninput: move |ev| new_password.set(ev.value().clone()),
                            disabled: loading(),
                        }
                        if let Some(message) = field_message(&field_error(), "new-password") {
                            p { id: "new-password-error", class: "text-danger small mt-1", "{message}" }
                        }
                    }
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "confirm-password", "Confirm new password" }
                        input {
                            class: "form-control",
                            r#type: "password",
                            id: "confirm-password",
                            aria_invalid: field_message(&field_error(), "confirm-password").is_some(),
                            aria_describedby: described_by(&field_error(), "confirm-password", ""),
                            autocomplete: "new-password",
                            required: true,
                            value: "{confirm_password()}",
                            oninput: move |ev| confirm_password.set(ev.value().clone()),
                            disabled: loading(),
                        }
                        if let Some(message) = field_message(&field_error(), "confirm-password") {
                            p { id: "confirm-password-error", class: "text-danger small mt-1", "{message}" }
                        }
                    }
                    button {
                        class: "btn btn-primary",
                        r#type: "submit",
                        disabled: loading(),
                        if loading() { "Changing…" } else { "Change password" }
                    }
                }
                if success() {
                    p { class: "text-success mb-0 mt-3", role: "status", "Password changed successfully." }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn password_validation_identifies_first_field_without_altering_passwords() {
        assert_eq!(password_error("", "", "").unwrap().field_id, "current-password");
        assert_eq!(password_error("old", "", "").unwrap().field_id, "new-password");
        assert_eq!(password_error("old", "new", "").unwrap().field_id, "confirm-password");
        assert_eq!(password_error("old", "new", "new ").unwrap().field_id, "confirm-password");
        assert!(password_error("old", " new ", " new ").is_none());
    }
}
