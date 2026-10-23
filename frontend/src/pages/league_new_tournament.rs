use crate::api;
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
    let mut busy = use_signal(|| false);
    let lu_for_resource = league_url.clone();
    let league_data = use_resource(move || {
        let lu = lu_for_resource.clone();
        async move { api::league_detail(&lu).await.map_err(|e| e.to_string()) }
    });
    let lu_for_cancel = league_url.clone();

    rsx! {
        div { class: "row justify-content-center form-page",
            div { class: "col-12 form-column",
                if let Some(Ok(d)) = league_data.value().read().as_ref() {
                    div { class: "edit-section",
                        div { class: "edit-section-heading",
                            h2 { class: "mb-2", "Add an event" }
                            p { class: "text-muted mb-0", "Part of {d.league.name}" }
                        }
                        div { class: "edit-section-body",
                            if let Some(ref err) = error() {
                                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
                            }
                            p { class: "form-text mb-4", "This event shares the league’s registration, roster, fees, and waiver. Set event dates and location in Settings after creating it." }
                            form {
                                onsubmit: move |ev| {
                                    ev.prevent_default();
                                if busy() { return; }
                                    error.set(None);
                                    let name = get_form_value("name").trim().to_string();
                                    let url_slug = get_form_value("url").trim().to_string();
                                    if name.is_empty() || url_slug.is_empty() {
                                        error.set(Some("Name and URL slug are required.".to_string()));
                                        return;
                                    }
                                    if !is_valid_url_slug(&url_slug) {
                                        error.set(Some(URL_SLUG_ALLOWED_HINT.to_string()));
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
                                    input { r#type: "text", class: "form-control", id: "name", name: "name", required: true }
                                }
                                div { class: "mb-3",
                                    label { r#for: "url", class: "form-label", "Event URL" }
                                    input {
                                        r#type: "text",
                                        class: "form-control",
                                        id: "url",
                                        name: "url",
                                    autocomplete: "off",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                        required: true,
                                        pattern: r"[A-Za-z0-9._~\-]+",
                                        title: URL_SLUG_ALLOWED_HINT,
                                    }
                                    div { class: "form-text", "This will be used in the URL (e.g., /my-event). {URL_SLUG_ALLOWED_HINT}" }
                                }
                                div { class: "d-flex gap-2",
                                    Link { to: Route::LeagueHome { league_url: lu_for_cancel.clone() }, class: "btn btn-outline-secondary", "Cancel" }
                                    button { r#type: "submit", class: "btn btn-primary", disabled: busy(), if busy() { "Adding event…" } else { "Add event" } }
                                }
                            }
                        }
                    }
                } else if let Some(Err(e)) = league_data.value().read().as_ref() {
                    div { class: "alert alert-danger", "{e}" }
                } else {
                    p { class: "text-muted", "Loading…" }
                }
            }
        }
    }
}
