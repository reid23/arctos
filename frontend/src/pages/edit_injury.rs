use crate::api;
use dioxus::prelude::*;
use dioxus::prelude::use_navigator;
use crate::Route;

#[component]
pub fn EditInjury(player_id: String, injury_id: u32) -> Element {
    let nav = use_navigator();
    let mut message = use_signal(|| "".to_string());
    let mut date = use_signal(|| "".to_string());
    let mut active = use_signal(|| false);
    let mut show = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);

    let _fetch = use_resource(use_reactive((&player_id, &injury_id), move |(pid, iid)| async move {
        loading.set(true);
        match api::get_injury(&pid, iid).await {
            Ok(inj) => {
                message.set(inj.message);
                if let Some(s) = inj.stamp {
                    date.set(s.chars().take(10).collect());
                }
                active.set(inj.active);
                show.set(inj.show);
            }
            Err(e) => error.set(Some(e)),
        }
        loading.set(false);
    }));

    let player_id_for_submit = player_id.clone();
    let onsubmit = move |_evt: Event<FormData>| {
        let player_id = player_id_for_submit.clone();
        async move {
            loading.set(true);
            error.set(None);
            
            let req = serde_json::json!({
                "message": message(),
                "custom_date": if date().is_empty() { None } else { Some(date()) },
                "active": active(),
                "show": show()
            });

            match api::update_injury(&player_id, injury_id, &req).await {
                Ok(_) => {
                    nav.push(Route::PlayerProfilePage { id: player_id.clone() });
                }
                Err(e) => {
                    error.set(Some(e));
                    loading.set(false);
                }
            }
        }
    };
    
    let player_id_for_delete = player_id.clone();
    let ondelete = move |_| {
        let player_id = player_id_for_delete.clone();
        async move {
            if !web_sys::window().unwrap().confirm_with_message("Are you sure you want to delete this injury?").unwrap_or(false) {
                return;
            }
            loading.set(true);
            match api::delete_injury(&player_id, injury_id).await {
                Ok(_) => {
                    nav.push(Route::PlayerProfilePage { id: player_id.clone() });
                }
                Err(e) => {
                    error.set(Some(e));
                    loading.set(false);
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
        } else {
            div { class: "row justify-content-center",
                div { class: "col-md-6",
                    div { class: "edit-section",
                        div { class: "edit-section-heading", h5 { class: "mb-0", "Injury Details" } }
                        div { class: "edit-section-body",
                            if let Some(err) = error() {
                                div { class: "alert alert-danger", "{err}" }
                            }
                            form {
                                onsubmit: onsubmit,
                                div { class: "mb-3",
                                    label { class: "form-label", "Injury Message" }
                                    input {
                                        class: "form-control",
                                        "type": "text",
                                        value: "{message}",
                                        oninput: move |e| message.set(e.value()),
                                        required: true
                                    }
                                }
                                div { class: "mb-3",
                                    label { class: "form-label", "Date (Optional)" }
                                    input {
                                        class: "form-control",
                                        "type": "date",
                                        value: "{date}",
                                        oninput: move |e| date.set(e.value())
                                    }
                                }
                                div { class: "mb-3 form-check",
                                    input {
                                        class: "form-check-input",
                                        "type": "checkbox",
                                        checked: active(),
                                        onchange: move |e| active.set(e.checked())
                                    }
                                    label { class: "form-check-label", "Active (currently injured)" }
                                }
                                div { class: "mb-3 form-check",
                                    input {
                                        class: "form-check-input",
                                        "type": "checkbox",
                                        checked: show(),
                                        onchange: move |e| show.set(e.checked())
                                    }
                                    label { class: "form-check-label", "Show publicly" }
                                }
                                div { class: "d-grid gap-2",
                                    button { class: "btn btn-primary", "type": "submit", "Update Injury" }
                                    button { class: "btn btn-danger", "type": "button", onclick: ondelete, "Delete Injury" }
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
