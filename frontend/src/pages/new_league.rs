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
pub fn NewLeague() -> Element {
    let navigator = use_navigator();
    let mut error = use_signal(|| None::<String>);
    let mut busy = use_signal(|| false);
    let user = use_resource(move || async move { api::me().await });

    if user.read().is_none() {
        return rsx! { div { class: "loading-state", role: "status", "Checking your account…" } };
    }
    if !matches!(user.read().as_ref(), Some(Ok(_))) {
        return rsx! {
            div { class: "row justify-content-center form-page",
                div { class: "col-12 form-column",
                    div { class: "edit-section",
                        div { class: "edit-section-body",
                            h1 { "Create a league" }
                            p { "Log in to create and manage your league. Both player and team accounts can organize events." }
                            a { class: "btn btn-primary", href: "/login?next=/leagues/new", "Log in to continue" }
                        }
                    }
                }
            }
        };
    }

    rsx! {
        div { class: "row justify-content-center form-page",
            div { class: "col-12 form-column",
                div { class: "edit-section",
                    div { class: "edit-section-heading",
                        h1 { class: "mb-2", "Create a league" }
                        p { class: "text-muted mb-0", "Start a private draft for a season of events, with shared registration and rosters." }
                    }
                    div { class: "edit-section-body",
                        if let Some(ref err) = error() {
                            div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
                        }
                        p { class: "form-text mb-4", "Next, you’ll go to Settings to set registration, fees, and waivers. Your league stays private until you publish it." }
                        form {
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                if busy() { return; }
                                error.set(None);
                                let league_name = get_form_value("league_name").trim().to_string();
                                let league_url = get_form_value("league_url").trim().to_string();
                                if league_name.is_empty() || league_url.is_empty() {
                                    error.set(Some("League name and URL slug are required.".to_string()));
                                    return;
                                }
                                if !is_valid_url_slug(&league_url) {
                                    error.set(Some(URL_SLUG_ALLOWED_HINT.to_string()));
                                    return;
                                }
                                busy.set(true);
                                let nav = navigator.clone();
                                spawn(async move {
                                    match api::create_league(&league_name, &league_url).await {
                                        Ok(res) if res.success => {
                                            if let Some(lu) = res.league_url {
                                                nav.push(Route::LeagueSettings { league_url: lu });
                                            } else {
                                                error.set(Some("League created but no URL returned.".to_string()));
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
                                label { r#for: "league_name", class: "form-label", "League Name" }
                                input { r#type: "text", class: "form-control", id: "league_name", name: "league_name", required: true }
                                div { class: "form-text", "For example, NorCal League 2026" }
                            }
                            div { class: "mb-3",
                                label { r#for: "league_url", class: "form-label", "League URL" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "league_url",
                                    name: "league_url",
                                    autocomplete: "off",
                                    autocapitalize: "none",
                                    spellcheck: "false",
                                    required: true,
                                    pattern: r"[A-Za-z0-9._~\-]+",
                                    title: URL_SLUG_ALLOWED_HINT,
                                }
                                div { class: "form-text", "Used in the URL, e.g. /leagues/norcal-2026. {URL_SLUG_ALLOWED_HINT}" }
                            }
                            div { class: "d-grid",
                                button { r#type: "submit", class: "btn btn-primary", disabled: busy(), if busy() { "Creating league…" } else { "Create league" } }
                            }
                        }
                    }
                }
            }
        }
    }
}
