//! Change password form for the account owner's profile editor.

use crate::api;
use dioxus::prelude::*;

#[component]
pub fn ChangePasswordCard() -> Element {
    let mut current_password = use_signal(|| String::new());
    let mut new_password = use_signal(|| String::new());
    let mut confirm_password = use_signal(|| String::new());
    let mut error = use_signal(|| None::<String>);
    let mut success = use_signal(|| false);
    let mut loading = use_signal(|| false);

    rsx! {
        div { class: "edit-section mt-3",
            div { class: "edit-section-heading",
                h5 { class: "mb-0", "Change password" }
            }
            div { class: "edit-section-body",
                if let Some(ref e) = error() {
                    div { class: "alert alert-danger py-2", role: "alert", "{e}" }
                }
                form {
                    onsubmit: move |ev| {
                        ev.prevent_default();
                        if loading() { return; }
                        success.set(false);
                        let cur = current_password();
                        let new_pw = new_password();
                        let conf = confirm_password();
                        if cur.is_empty() {
                            error.set(Some("Current password is required.".into()));
                            return;
                        }
                        if new_pw.is_empty() {
                            error.set(Some("New password is required.".into()));
                            return;
                        }
                        if new_pw != conf {
                            error.set(Some("New password and confirmation do not match.".into()));
                            return;
                        }
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
                            autocomplete: "current-password",
                            required: true,
                            value: "{current_password()}",
                            oninput: move |ev| current_password.set(ev.value().clone()),
                            disabled: loading(),
                        }
                    }
                    div { class: "mb-2",
                        label { class: "form-label", r#for: "new-password", "New password" }
                        input {
                            class: "form-control",
                            r#type: "password",
                            id: "new-password",
                            autocomplete: "new-password",
                            required: true,
                            value: "{new_password()}",
                            oninput: move |ev| new_password.set(ev.value().clone()),
                            disabled: loading(),
                        }
                    }
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "confirm-password", "Confirm new password" }
                        input {
                            class: "form-control",
                            r#type: "password",
                            id: "confirm-password",
                            autocomplete: "new-password",
                            required: true,
                            value: "{confirm_password()}",
                            oninput: move |ev| confirm_password.set(ev.value().clone()),
                            disabled: loading(),
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
