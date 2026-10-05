use crate::Route;
use crate::api;
use crate::components::{AllowedWeaponsCheckboxes, default_allowed_weapons};
use dioxus::prelude::*;

#[component]
pub fn SideCompNew(url: String) -> Element {
    let navigator = use_navigator();
    let mut name = use_signal(String::new);
    let mut type_ = use_signal(|| "DUELING".to_string());
    let mut description = use_signal(String::new);
    let mut allowed_weapons = use_signal(default_allowed_weapons);
    let mut top_n_text = use_signal(String::new);
    let mut error = use_signal(|| None::<String>);
    let mut submitting = use_signal(|| false);

    let url_for_back = url.clone();
    let url_for_submit = url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12",
                h1 { "Create side competition" }
                div { class: "mb-3",
                    Link {
                        to: Route::TournamentHomeWithTab {
                            url: url_for_back.clone(),
                            tab: "sidecomps".to_string(),
                        },
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
                        let d_opt = if d.trim().is_empty() { None } else { Some(d) };
                        let weapons = allowed_weapons();
                        let top_n = {
                            let raw = top_n_text().trim().to_string();
                            if raw.is_empty() {
                                None
                            } else {
                                match raw.parse::<i32>() {
                                    Ok(v) if v >= 0 => Some(v),
                                    _ => {
                                        error.set(Some("Top N results must be a non-negative integer or blank".into()));
                                        return;
                                    }
                                }
                            }
                        };
                        submitting.set(true);
                        error.set(None);
                        spawn(async move {
                            match api::sidecomp_create(
                                &url_inner,
                                &n,
                                &t,
                                d_opt.as_deref(),
                                Some(&weapons),
                                top_n,
                            ).await {
                                Ok(_) => {
                                    navigator.push(Route::TournamentHomeWithTab {
                                        url: url_inner,
                                        tab: "sidecomps".to_string(),
                                    });
                                }
                                Err(e) => {
                                    error.set(Some(e));
                                    submitting.set(false);
                                }
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
                            required: true,
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
                        div { class: "form-text", "Optional. Shown on the side competition page." }
                    }
                    AllowedWeaponsCheckboxes { selected: allowed_weapons }
                    div { class: "mb-3",
                        label { class: "form-label", "Only show top N results" }
                        input {
                            class: "form-control",
                            r#type: "number",
                            min: "0",
                            placeholder: "Leave blank to show all",
                            value: "{top_n_text}",
                            oninput: move |evt| top_n_text.set(evt.value()),
                        }
                    }
                    if let Some(err) = error() {
                        div { class: "alert alert-danger", "{err}" }
                    }
                    button {
                        class: "btn btn-primary",
                        r#type: "submit",
                        disabled: submitting(),
                        "Create"
                    }
                }
            }
        }
    }
}
