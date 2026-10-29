use crate::api;
use crate::form_validation::focus_after_render;
use crate::Route;
use dioxus::prelude::use_navigator;
use dioxus::prelude::*;

#[component]
pub fn EditInjury(player_id: String, injury_id: u32) -> Element {
    let nav = use_navigator();
    let mut message = use_signal(|| "".to_string());
    let mut date = use_signal(|| "".to_string());
    let mut active = use_signal(|| false);
    let mut show = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);
    let mut saving = use_signal(|| false);
    let mut load_error = use_signal(|| None::<String>);
    use_effect(move || {
        if error().is_some() {
            focus_after_render("injury-error");
        }
    });

    let mut fetch = use_resource(use_reactive(
        (&player_id, &injury_id),
        move |(pid, iid)| async move {
            loading.set(true);
            load_error.set(None);
            error.set(None);
            match api::get_injury(&pid, iid).await {
                Ok(inj) => {
                    message.set(inj.message);
                    date.set(
                        inj.stamp
                            .map(|stamp| stamp.chars().take(10).collect())
                            .unwrap_or_default(),
                    );
                    active.set(inj.active);
                    show.set(inj.show);
                }
                Err(e) => load_error.set(Some(e)),
            }
            loading.set(false);
        },
    ));

    let player_id_for_submit = player_id.clone();
    let onsubmit = move |evt: Event<FormData>| {
        evt.prevent_default();
        let player_id = player_id_for_submit.clone();
        async move {
            if saving() || loading() || load_error().is_some() {
                return;
            }
            saving.set(true);
            error.set(None);

            let req = serde_json::json!({
                "message": message(),
                "custom_date": if date().is_empty() { None } else { Some(date()) },
                "active": active(),
                "show": show()
            });

            match api::update_injury(&player_id, injury_id, &req).await {
                Ok(_) => {
                    nav.push(Route::PlayerProfilePage {
                        id: player_id.clone(),
                    });
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        }
    };

    let player_id_for_delete = player_id.clone();
    let ondelete = move |_| {
        let player_id = player_id_for_delete.clone();
        async move {
            if saving() || loading() || load_error().is_some() {
                return;
            }
            if !web_sys::window()
                .and_then(|window| {
                    window
                        .confirm_with_message("Are you sure you want to delete this injury?")
                        .ok()
                })
                .unwrap_or(false)
            {
                return;
            }
            saving.set(true);
            error.set(None);
            match api::delete_injury(&player_id, injury_id).await {
                Ok(_) => {
                    nav.push(Route::PlayerProfilePage {
                        id: player_id.clone(),
                    });
                }
                Err(e) => {
                    error.set(Some(e));
                    saving.set(false);
                }
            }
        }
    };

    rsx! {
        div { class: "row",
            div { class: "col-12",
                h1 { "Edit Injury" }
                nav { "aria-label": "breadcrumb",
                    ol { class: "breadcrumb",
                        li { class: "breadcrumb-item",
                            Link { to: Route::PlayerProfilePage { id: player_id.clone() }, "Profile" }
                        }
                        li { class: "breadcrumb-item active", "Edit Injury" }
                    }
                }
            }
        }

        if loading() {
            div { class: "d-flex justify-content-center",
                div { class: "spinner-border", role: "status",
                    span { class: "visually-hidden", "Loading..." }
                }
            }
        } else if let Some(message) = load_error() {
            div { class: "alert alert-danger", role: "alert", "Could not load this injury: {message}" }
            button { class: "btn btn-outline-secondary", r#type: "button", onclick: move |_| fetch.restart(), "Retry loading injury" }
        } else {
            div { class: "row justify-content-center",
                div { class: "col-md-6",
                    div { class: "edit-section",
                        div { class: "edit-section-heading", h2 { class: "h5 mb-0", "Injury details" } }
                        div { class: "edit-section-body",
                            if let Some(err) = error() {
                                div { id: "injury-error", tabindex: "-1", class: "alert alert-danger", role: "alert", "{err}" }
                            }
                            form {
                                onsubmit: onsubmit,
                            oninput: move |_| { error.set(None); },
                            onchange: move |_| { error.set(None); },
                            fieldset { class: "border-0 p-0 m-0", disabled: saving(),
                                div { class: "mb-3",
                                    label { class: "form-label", r#for: "injury-message", "Injury Message" }
                                    input {
                                        id: "injury-message",
                                        class: "form-control",
                                        "type": "text",
                                        value: "{message}",
                                        oninput: move |e| message.set(e.value()),
                                        required: true
                                    }
                                }
                                div { class: "mb-3",
                                    label { class: "form-label", r#for: "injury-date", "Date (Optional)" }
                                    input {
                                        id: "injury-date",
                                        class: "form-control",
                                        "type": "date",
                                        value: "{date}",
                                        oninput: move |e| date.set(e.value())
                                    }
                                }
                                div { class: "mb-3 form-check",
                                    input {
                                        class: "form-check-input",
                                        id: "injury-active",
                                        "type": "checkbox",
                                        checked: active(),
                                        onchange: move |e| active.set(e.checked())
                                    }
                                    label { class: "form-check-label", r#for: "injury-active", "Active (currently injured)" }
                                }
                                div { class: "mb-3 form-check",
                                    input {
                                        class: "form-check-input",
                                        id: "injury-public", aria_describedby: "injury-visibility-help",
                                        "type": "checkbox",
                                        checked: show(),
                                        onchange: move |e| show.set(e.checked())
                                    }
                                    label { class: "form-check-label", r#for: "injury-public", "Show publicly" }
                                }
                                p { id: "injury-visibility-help", class: "form-text", "Public injuries are visible to anyone. Private injuries are hidden from your public profile. Active injuries are still shown to authorized match referees during match setup at events you are registered for." }
                                div { class: "d-grid gap-2",
                                    button { class: "btn btn-primary", "type": "submit", disabled: saving(), if saving() { "Saving…" } else { "Update Injury" } }
                                    button { class: "btn btn-danger", "type": "button", disabled: saving(), onclick: ondelete, "Delete Injury" }
                                    if saving() {
                                    span { class: "btn btn-outline-secondary disabled", aria_disabled: "true", "Cancel" }
                                } else {
                                    Link { class: "btn btn-outline-secondary", to: Route::PlayerProfilePage { id: player_id.clone() }, "Cancel" }
                                }
                                }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
