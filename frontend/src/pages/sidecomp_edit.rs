use crate::api;
use crate::components::{default_allowed_weapons, AllowedWeaponsCheckboxes};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn SideCompEdit(url: String, comp_id: i32) -> Element {
    let navigator = use_navigator();
    let detail = use_resource(move || async move { api::sidecomp_detail(comp_id).await });

    let mut name = use_signal(String::new);
    let mut type_ = use_signal(String::new);
    let mut description = use_signal(String::new);
    let mut registration_open = use_signal(|| false);
    let mut allowed_weapons = use_signal(default_allowed_weapons);
    let mut initialised = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);

    if !initialised() {
        if let Some(Ok(d)) = detail.read().as_ref() {
            name.set(d.name.clone());
            type_.set(d.type_.clone());
            description.set(d.description.clone().unwrap_or_default());
            registration_open.set(d.registration_open);
            if d.allowed_weapons.is_empty() {
                allowed_weapons.set(Vec::new());
            } else {
                allowed_weapons.set(d.allowed_weapons.clone());
            }
            initialised.set(true);
        }
    }

    let disabled_warning_count = {
        let selected = allowed_weapons();
        match detail.read().as_ref() {
            Some(Ok(d)) => {
                let n = d
                    .registrants
                    .iter()
                    .filter(|r| match r.weapon.as_deref() {
                        Some(w) => !selected.iter().any(|s| s == w),
                        None => true,
                    })
                    .count() as i32;
                Some(n)
            }
            _ => None,
        }
    };

    let url_for_back = url.clone();
    let url_for_submit = url.clone();
    let url_for_delete = url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12",
                h1 { "Edit side competition" }
                div { class: "mb-3",
                    Link {
                        to: Route::SideCompDetail { url: url_for_back, comp_id },
                        class: "btn btn-link",
                        "<- Back"
                    }
                }
                form {
                    onsubmit: move |evt| {
                        evt.prevent_default();
                        let url_inner = url_for_submit.clone();
                        let n = name();
                        let t = type_();
                        let d = description();
                        let open = registration_open();
                        let weapons = allowed_weapons();
                        error.set(None);
                        spawn(async move {
                            match api::sidecomp_update(
                                comp_id,
                                Some(&n),
                                Some(&t),
                                Some(&d),
                                Some(open),
                                Some(&weapons),
                            ).await {
                                Ok(_) => {
                                    navigator.push(Route::SideCompDetail { url: url_inner, comp_id });
                                }
                                Err(e) => error.set(Some(e)),
                            }
                        });
                    },
                    div { class: "mb-3",
                        label { class: "form-label", "Name" }
                        input {
                            class: "form-control",
                            r#type: "text",
                            value: "{name}",
                            oninput: move |evt| name.set(evt.value()),
                        }
                    }
                    div { class: "mb-3",
                        label { class: "form-label", "Type" }
                        select {
                            class: "form-select",
                            value: "{type_}",
                            onchange: move |evt| type_.set(evt.value()),
                            option { value: "DUELING", "Dueling" }
                            option { value: "CHAIN_BREAKING", "Chain / Breaking" }
                            option { value: "OTHER", "Other" }
                        }
                    }
                    div { class: "mb-3",
                        label { class: "form-label", "Description" }
                        textarea {
                            class: "form-control",
                            rows: "4",
                            value: "{description}",
                            oninput: move |evt| description.set(evt.value()),
                        }
                        div { class: "form-text", "Optional. Leave blank to clear." }
                    }
                    div { class: "mb-3 form-check form-switch",
                        input {
                            class: "form-check-input",
                            r#type: "checkbox",
                            id: "registration-open-toggle",
                            checked: registration_open(),
                            onchange: move |evt| registration_open.set(evt.checked()),
                        }
                        label {
                            class: "form-check-label",
                            r#for: "registration-open-toggle",
                            "Registration open"
                        }
                        div { class: "form-text",
                            "When off, only TO check-in can add players."
                        }
                    }
                    AllowedWeaponsCheckboxes {
                        selected: allowed_weapons,
                        disabled_warning_count,
                    }
                    if let Some(err) = error() {
                        div { class: "alert alert-danger", "{err}" }
                    }
                    button { class: "btn btn-primary", r#type: "submit", "Save" }
                }
                hr {}
                button {
                    class: "btn btn-danger",
                    onclick: move |_| {
                        let url_inner = url_for_delete.clone();
                        spawn(async move {
                            match api::sidecomp_delete(comp_id).await {
                                Ok(_) => {
                                    navigator.push(Route::TournamentHomeWithTab {
                                        url: url_inner,
                                        tab: "sidecomps".to_string(),
                                    });
                                }
                                Err(e) => error.set(Some(e)),
                            }
                        });
                    },
                    "Delete this side competition"
                }
            }
        }
    }
}
