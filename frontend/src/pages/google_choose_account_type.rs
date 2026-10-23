use crate::api;
use crate::types::GoogleChooseAccountTypeRequest;
use crate::Route;
use dioxus::prelude::use_navigator;
use dioxus::prelude::*;

#[component]
pub fn GoogleChooseAccountType() -> Element {
    let nav = use_navigator();
    let mut email = use_signal(|| "".to_string());
    let mut user_type = use_signal(|| "player".to_string());
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);

    let _fetch = use_resource(move || async move {
        loading.set(true);
        match api::google_choose_account_type_info().await {
            Ok(res) => {
                email.set(res.email);
            }
            Err(e) => error.set(Some(e)),
        }
        loading.set(false);
    });

    let onsubmit = move |evt: Event<FormData>| async move {
        evt.prevent_default();
        if loading() {
            return;
        }
        loading.set(true);
        error.set(None);
        let req = GoogleChooseAccountTypeRequest {
            user_type: user_type(),
        };
        match api::google_choose_account_type(&req).await {
            Ok(_) => {
                nav.push(Route::GoogleCompleteProfile {});
            }
            Err(e) => {
                error.set(Some(e));
                loading.set(false);
            }
        }
    };

    rsx! {
        div { class: "row justify-content-center auth-page",
            div { class: "col-12 auth-column",
                div { class: "card auth-card",
                    div { class: "card-header",
                        h1 { class: "mb-0", "Choose an account type" }
                    }
                    div { class: "card-body",
                        p { class: "text-muted",
                            "You're signing in with Google as "
                            strong { "{email}" }
                            ". Please choose whether you'd like to create a Player or Team account."
                        }
                        if let Some(err) = error() {
                            div { class: "alert alert-danger", role: "alert", "{err}" }
                        }
                        form {
                            onsubmit: onsubmit,
                            div { class: "mb-3",
                                div { class: "account-type-picker", role: "group", aria_label: "Account type",
                                    input {
                                        class: "btn-check",
                                        "type": "radio",
                                        name: "user_type",
                                        id: "user_type_player",
                                        value: "player",
                                        checked: user_type() == "player",
                                        disabled: loading(),
                                        onchange: move |_| user_type.set("player".to_string())
                                    }
                                    label { class: if user_type() == "player" { "account-type-option is-selected" } else { "account-type-option" }, r#for: "user_type_player",
                                        strong { "Player" }
                                        small { "Join events, play, and referee." }
                                    }

                                    input {
                                        class: "btn-check",
                                        "type": "radio",
                                        name: "user_type",
                                        id: "user_type_team",
                                        value: "team",
                                        checked: user_type() == "team",
                                        disabled: loading(),
                                        onchange: move |_| user_type.set("team".to_string())
                                    }
                                    label { class: if user_type() == "team" { "account-type-option is-selected" } else { "account-type-option" }, r#for: "user_type_team",
                                        strong { "Team" }
                                        small { "Register one team and manage its roster." }
                                    }
                                }
                            }
                            p { class: "form-text", "Both types can organize events. Only player accounts can referee matches." }
                            div { class: "d-grid",
                                button { class: "btn btn-primary btn-lg", "type": "submit", disabled: loading(), if loading() { "Please wait…" } else { "Continue" } }
                            }
                        }
                    }
                }
            }
        }
    }
}
