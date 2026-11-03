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
pub fn LeagueNewTournament(league_url: String) -> Element {
    let navigator = use_navigator();
    let mut error = use_signal(|| None::<String>);
    let mut field_error = use_signal(|| None::<FieldError>);
    use_effect(move || { if error().is_some() { focus_after_render("create-error"); } });
    let mut busy = use_signal(|| false);
    let lu_for_resource = league_url.clone();
    let mut league_data = use_resource(move || {
        let lu = lu_for_resource.clone();
        async move { api::league_detail(&lu).await.map_err(|e| e.to_string()) }
    });
    let lu_for_cancel = league_url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12 ordinary-form-rail",
                if let Some(Ok(d)) = league_data.value().read().as_ref() {
                    div { class: "edit-section",
                        ToolPageHeader {
                            title: "Add an event",
                            back: Some((Route::LeagueHome { league_url: lu_for_cancel.clone() }, "Back to league".into())),
                            description: format!("Part of {}", d.league.name),
                        }
                        div { class: "edit-section-body",
                            if let Some(ref err) = error() {
                                div { id: "create-error", tabindex: "-1", class: "alert alert-danger mb-3", role: "alert", "{err}" }
                            }
                            p { class: "form-text mb-4", "This event shares the league’s registration, roster, fees, and waiver. Set event dates and location in Settings after creating it." }
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
                                    if url_slug.is_empty() || !is_valid_url_slug(&url_slug) {
                                        field_error.set(Some(FieldError::new("url", if url_slug.is_empty() { "Enter a URL slug." } else { URL_SLUG_ALLOWED_HINT })));
                                        focus_after_render("url");
                                        return;
                                    }
                                    busy.set(true);
                                let nav = navigator.clone();
                                    let lu = league_url.clone();
                                    spawn(async move {
                                        match api::create_tournament(&name, &url_slug, Some(&lu)).await {
                                            Ok(res) if res.success => {
                                                if let Some(url) = res.url {
                                                    nav.push(Route::TournamentHome { url });
                                                } else {
                                                    error.set(Some("Tournament created but no URL returned.".to_string()));
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
                                        id: "url", aria_describedby: described_by(&field_error(), "url", "url-help"),
                                        aria_invalid: field_message(&field_error(), "url").is_some(),
                                        name: "url",
                                    autocomplete: "off",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                        required: true,
                                        pattern: r"[A-Za-z0-9._~\-]+",
                                        title: URL_SLUG_ALLOWED_HINT,
                                    }
                                    if let Some(message) = field_message(&field_error(), "url") { p { id: "url-error", class: "text-danger small mt-1", "{message}" } }
                                    div { id: "url-help", class: "form-text", "This will be used in the URL (e.g., /my-event). {URL_SLUG_ALLOWED_HINT}" }
                                }
                                div { class: "form-actions",
                                    button { r#type: "submit", class: "btn btn-primary", disabled: busy(), if busy() { "Adding event…" } else { "Add event" } }
                                    Link { to: Route::LeagueHome { league_url: lu_for_cancel.clone() }, class: "btn btn-outline-secondary", "Cancel" }
                                }
                            }
                        }
                    }
                } else if let Some(Err(e)) = league_data.value().read().as_ref() {
                    div { class: "alert alert-danger", role: "alert", "Could not load the league: {e}" }
                    button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| league_data.restart(), "Retry loading league" }
                } else {
                    p { class: "text-muted", role: "status", "Loading…" }
                }
            }
        }
    }
}
