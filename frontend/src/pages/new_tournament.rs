use crate::api;
use crate::components::ToolPageHeader;
use crate::form_validation::{described_by, field_message, focus_after_render, FieldError};
use crate::url_slug::{is_valid_url_slug, URL_SLUG_ALLOWED_HINT};
use crate::Route;
use dioxus::prelude::*;
use wasm_bindgen::JsCast;

fn get_form_value(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

#[component]
pub fn NewTournament() -> Element {
    let navigator = use_navigator();
    let mut error = use_signal(|| None::<String>);
    let mut field_error = use_signal(|| None::<FieldError>);
    let mut busy = use_signal(|| false);
    let user = crate::auth::use_account();
    use_effect(move || { if error().is_some() { focus_after_render("create-error"); } });

    if user.read().is_none() {
        return rsx! { div { class: "loading-state", role: "status", "Checking your account…" } };
    }
    if matches!(user.read().as_ref(), Some(Err(api::AccountError::Unauthenticated))) {
        return rsx! {
            div { class: "row justify-content-center form-page",
                div { class: "col-12 ordinary-form-rail",
                    div { class: "edit-section",
                        div { class: "edit-section-body",
                            ToolPageHeader {
                                title: "Create an Event", standalone: true,
                                back: Some((Route::Index {}, "Back to Events".into())),
                            }
                            p { "Log in to create and manage your event. Both player and team accounts can organize events." }
                            a { class: "btn btn-primary", href: "/login?next=/new-tournament", "Log in to continue" }
                        }
                    }
                }
            }
        };
    }

    if matches!(user.read().as_ref(), Some(Err(api::AccountError::Unavailable))) {
        return rsx! {};
    }

    rsx! {
        div { class: "row justify-content-center form-page",
            div { class: "col-12 ordinary-form-rail",
                div { class: "edit-section",
                    ToolPageHeader {
                        title: "Create an Event", standalone: true,
                        back: Some((Route::Index {}, "Back to Events".into())),
                        description: "Start with a name and link. Your event is a private draft until you publish it.",
                    }
                    div { class: "edit-section-body",
                        if let Some(ref err) = error() {
                            div { id: "create-error", tabindex: "-1", class: "alert alert-danger mb-3", role: "alert", "{err}" }
                        }
                        p { class: "form-text mb-4", "Next, you’ll go to Settings to add dates, location, and registration details." }
                        form {
                            oninput: move |_| { field_error.set(None); error.set(None); },
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                if busy() { return; }
                                error.set(None);
                                let name = get_form_value("name").trim().to_string();
                                let url_slug = get_form_value("url").trim().to_string();
                                if name.is_empty() {
                                    field_error.set(Some(FieldError::new("name", "Enter a name.")));
                                    focus_after_render("name");
                                    return;
                                }
                                if url_slug.is_empty() {
                                    field_error.set(Some(FieldError::new("url", "Enter a URL slug.")));
                                    focus_after_render("url");
                                    return;
                                }
                                if !is_valid_url_slug(&url_slug) {
                                    field_error.set(Some(FieldError::new("url", URL_SLUG_ALLOWED_HINT)));
                                    focus_after_render("url");
                                    return;
                                }
                                busy.set(true);
                                let nav = navigator.clone();
                                spawn(async move {
                                    match api::create_tournament(&name, &url_slug, None).await {
                                        Ok(res) if res.success => {
                                            if let Some(url) = res.url {
                                                nav.push(Route::TournamentSettings { url });
                                            } else {
                                                error.set(Some("Event created but no URL returned.".to_string()));
                                            }
                                        }
                                        Ok(res) => {
                                            error.set(Some(res.error.unwrap_or_else(|| "Creation failed.".to_string())));
                                        }
                                        Err(e) => {
                                            error.set(Some(e));
                                        }
                                    }
                                    busy.set(false);
                                });
                            },
                            div { class: "mb-3",
                                label { r#for: "name", class: "form-label", "Event Name" }
                                input { r#type: "text", class: "form-control", id: "name", name: "name", required: true,
                                    aria_invalid: field_message(&field_error(), "name").is_some(),
                                    aria_describedby: described_by(&field_error(), "name", "") }
                                if let Some(message) = field_message(&field_error(), "name") { p { id: "name-error", class: "text-danger small mt-1", "{message}" } }
                            }
                            div { class: "mb-3",
                                label { r#for: "url", class: "form-label", "Event URL" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "url",
                                    aria_invalid: field_message(&field_error(), "url").is_some(),
                                    aria_describedby: described_by(&field_error(), "url", "url-help"),
                                    name: "url",
                                    autocomplete: "off",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                    required: true,
                                    pattern: r"[A-Za-z0-9._~\-]+",
                                    title: URL_SLUG_ALLOWED_HINT,
                                }
                                if let Some(message) = field_message(&field_error(), "url") { p { id: "url-error", class: "text-danger small mt-1", "{message}" } }
                                div { id: "url-help", class: "form-text", "Choose a unique, shareable link, such as /summer-cup. {URL_SLUG_ALLOWED_HINT}" }
                            }
                            div { class: "form-actions",
                                button { r#type: "submit", class: "btn btn-primary", disabled: busy(), if busy() { "Creating event…" } else { "Create an Event" } }
                            }
                        }
                    }
                }
            }
        }
    }
}
