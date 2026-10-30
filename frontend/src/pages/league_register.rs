use crate::api;
use crate::components::TeamRegistrationHelpModal;
use dioxus::prelude::*;
use wasm_bindgen::JsCast;

fn get_form_value(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

fn get_form_select_value(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlSelectElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

#[component]
pub fn LeagueRegister(league_url: String) -> Element {
    let navigator = use_navigator();
    let mut error = use_signal(|| None::<String>);
    use_effect(move || { if error().is_some() { crate::form_validation::focus_after_render("registration-error"); } });
    let mut busy = use_signal(|| false);
    let login_url = format!("/login?next={}", urlencoding::encode(&format!("/leagues/{league_url}/register")));
    let lu = league_url.clone();
    let data = use_resource(move || {
        let u = lu.clone();
        async move { api::league_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let me = crate::auth::use_account();
    let mut show_help_modal = use_signal(|| false);

    rsx! {
        div { class: "row justify-content-center form-page",
            div { class: "col-12 form-column",
                if let Some(Ok(d)) = data.value().read().as_ref() {
                    div { class: "edit-section",
                        div { class: "edit-section-heading",
                            h2 { class: "h3 mb-0", "Registration" }
                        }
                        div { class: "edit-section-body",
                            p { class: "text-muted mb-3",
                                "One registration covers all events in this league season."
                            }
                            if let Some(terms) = &d.league.terms_link {
                                p { class: "form-text",
                                    a {
                                        href: "{terms}",
                                        target: "_blank",
                                        rel: "noopener noreferrer",
                                        "Read the league’s registration terms"
                                    }
                                }
                            }
                            if let Some(ref err) = error() {
                                div {
                                    id: "registration-error", tabindex: "-1",
                                    class: "alert alert-danger mb-3", role: "alert",
                                    "{err}"
                                }
                            }
                            if let Some(Ok(me)) = me.value().read().as_ref() {
                                p { class: "form-text mb-3", "Registering as {me.name} (@{me.id})" }
                                if me.user_type == "team" {
                                    if let Some(fee) = d.league.team_reg_fee.filter(|fee| *fee > 0.0) {
                                        div { class: "alert alert-info",
                                            strong { {format!("Registration fee: ${:.2}", fee)} }
                                            p { class: "small mb-0 mt-1",
                                                "Pay the organizer separately. They will confirm payment in Arctos."
                                            }
                                        }
                                    }
                                    form {
                                        onsubmit: move |ev| {
                                            ev.prevent_default();
                                            if busy() {
                                                return;
                                            }
                                            error.set(None);
                                            let pseudonym = get_form_value("pseudonym");
                                            if pseudonym.is_empty() {
                                                error.set(Some("Team name is required.".to_string()));
                                                return;
                                            }
                                            let shortname_raw = get_form_value("shortname");
                                            let shortname = if shortname_raw.trim().is_empty() {
                                                None
                                            } else {
                                                Some(shortname_raw)
                                            };
                                            let nav = navigator.clone();
                                            let lu = league_url.clone();
                                            busy.set(true);
                                            spawn(async move {
                                                match api::league_register_team(&lu, &pseudonym, shortname.as_deref()).await
                                                {
                                                    Ok(res) if res.success => {
                                                        let path = format!("/leagues/{}?registered=1", lu);
                                                        let _ = nav.push(path);
                                                    }
                                                    Ok(res) => {
                                                        error
                                                            .set(
                                                                Some(
                                                                    res
                                                                        .error
                                                                        .unwrap_or_else(|| "Registration failed.".to_string()),
                                                                ),
                                                            );
                                                    }
                                                    Err(e) => error.set(Some(e)),
                                                }
                                                busy.set(false);
                                            });
                                        },
                                        div { class: "mb-3",
                                            label {
                                                r#for: "pseudonym",
                                                class: "form-label",
                                                "Team Name"
                                            }
                                            input {
                                                r#type: "text",
                                                class: "form-control",
                                                id: "pseudonym",
                                                name: "pseudonym",
                                                required: true,
                                            }
                                        }
                                        div { class: "mb-3",
                                            label {
                                                r#for: "shortname",
                                                class: "form-label",
                                                "Short name (optional)"
                                            }
                                            input {
                                                r#type: "text",
                                                class: "form-control",
                                                id: "shortname", aria_describedby: "shortname-help",
                                                name: "shortname",
                                                maxlength: "8",
                                                placeholder: "e.g. UWaoW",
                                            }
                                            div { id: "shortname-help", class: "form-text",
                                                "Used in schedules and brackets. Leave blank to auto-shorten."
                                            }
                                        }
                                        div { class: "d-grid",
                                            if !d.league.team_registration_open {
                                                p { class: "alert alert-warning",
                                                    "Team registration is currently closed. Contact an organizer if you need help."
                                                }
                                            }
                                            button {
                                                r#type: "submit",
                                                class: "btn btn-primary",
                                                disabled: busy() || !d.league.team_registration_open,
                                                if busy() {
                                                    "Registering…"
                                                } else {
                                                    "Register"
                                                }
                                            }
                                        }
                                    }
                                } else if me.user_type == "player" {
                                    if let Some(fee) = d.league.player_reg_fee.filter(|fee| *fee > 0.0) {
                                        div { class: "alert alert-info",
                                            strong { {format!("Registration fee: ${:.2}", fee)} }
                                            p { class: "small mb-0 mt-1",
                                                "Pay the organizer separately. They will confirm payment in Arctos."
                                            }
                                        }
                                    }
                                    form {
                                        onsubmit: move |ev| {
                                            ev.prevent_default();
                                            if busy() {
                                                return;
                                            }
                                            error.set(None);
                                            let jersey_name = get_form_value("jersey_name");
                                            let jersey_number = get_form_value("jersey_number");
                                            let team_id = get_form_select_value("team");
                                            let team_opt = if team_id.is_empty() { None } else { Some(team_id.clone()) };
                                            let waiver_legal_name_signature = get_form_value("waiver_legal_name_signature");
                                            let nav = navigator.clone();
                                            let lu = league_url.clone();
                                            busy.set(true);
                                            spawn(async move {
                                                match api::league_register_player(
                                                        &lu,
                                                        team_opt.as_deref(),
                                                        &jersey_number,
                                                        &jersey_name,
                                                        &waiver_legal_name_signature,
                                                    )
                                                    .await
                                                {
                                                    Ok(res) if res.success => {
                                                        let path = format!("/leagues/{}?registered=1", lu);
                                                        let _ = nav.push(path);
                                                    }
                                                    Ok(res) => {
                                                        error
                                                            .set(
                                                                Some(
                                                                    res
                                                                        .error
                                                                        .unwrap_or_else(|| "Registration failed.".to_string()),
                                                                ),
                                                            )
                                                    }
                                                    Err(e) => error.set(Some(e)),
                                                }
                                                busy.set(false);
                                            });
                                        },
                                        div { class: "mb-3",
                                            label {
                                                r#for: "jersey_name",
                                                class: "form-label",
                                                "Jersey Name"
                                            }
                                            input {
                                                r#type: "text",
                                                class: "form-control",
                                                id: "jersey_name",
                                                name: "jersey_name",
                                            }
                                        }
                                        div { class: "mb-3",
                                            label {
                                                r#for: "jersey_number",
                                                class: "form-label",
                                                "Jersey Number"
                                            }
                                            input {
                                                r#type: "text",
                                                class: "form-control",
                                                id: "jersey_number",
                                                name: "jersey_number",
                                            }
                                        }
                                        div { class: "mb-3",
                                            div { class: "d-flex align-items-center flex-wrap gap-2 mb-2",
                                                label { r#for: "team", class: "form-label mb-0", "Team" }
                                                button { id: "registration-team-help", r#type: "button",
                                                    class: "btn btn-link text-decoration-none",
                                                    onclick: move |_| show_help_modal.set(true),
                                                    "Can’t find your team?"
                                                }
                                            }
                                            select {
                                                class: "form-select",
                                                id: "team", aria_describedby: "team-help",
                                                name: "team",
                                                option { value: "", "No team — join as a free merc" }
                                                for team in d.teams_with_counts.iter() {
                                                    option { value: "{team.team_id}",
                                                        "{team.pseudonym.as_deref().unwrap_or(&team.team_name)}"
                                                    }
                                                }
                                            }
                                            div { id: "team-help", class: "form-text",
                                                "Your team must approve your request before you join its roster. Without a team, you register as a free merc."
                                            }
                                        }
                                        if d.league.waiver_required {
                                            div { class: "mb-3",
                                                label {
                                                    r#for: "waiver_legal_name_signature",
                                                    class: "form-label",
                                                    "Full legal name for the waiver"
                                                }
                                                if let Some(link) = &d.league.waiver_filepath {
                                                    div { class: "form-text mb-2",

                                                        a {
                                                            href: api::static_file_url(&link),
                                                            target: "_blank",
                                                            rel: "noopener noreferrer",
                                                            class: "text-decoration-none",
                                                            "Read the waiver (opens a new tab)"
                                                        }
                                                        if let Some(sha) = &d.league.waiver_sha256 {
                                                            details { class: "waiver-verification mt-2",
                                                                summary { "Document verification (SHA-256)" }
                                                                pre {
                                                                    class: "p-2 border rounded bg-light mt-1 mb-0",
                                                                    style: "white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;",
                                                                    code { "{sha}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                                p { id: "waiver-privacy", class: "form-text mb-2",
                                                    "Your legal name is shared only with you and event organizers."
                                                }
                                                p { id: "waiver-consent", class: "form-text mb-2",
                                                    "By entering your full legal name below, you agree to the terms of the waiver linked above, and affirm that the waiver you viewed matches the SHA-256 hash displayed."
                                                }
                                                input {
                                                    r#type: "text",
                                                    class: "form-control",
                                                    id: "waiver_legal_name_signature", aria_describedby: "waiver-privacy waiver-consent",
                                                    name: "waiver_legal_name_signature",
                                                    autocomplete: "name",
                                                    required: true,
                                                }
                                            }
                                        }
                                        div { class: "d-grid",
                                            if !d.league.player_registration_open {
                                                p { class: "alert alert-warning",
                                                    "Player registration is currently closed. Contact an organizer if you need help."
                                                }
                                            }
                                            button {
                                                r#type: "submit",
                                                class: "btn btn-primary",
                                                disabled: busy() || !d.league.player_registration_open,
                                                if busy() {
                                                    "Registering…"
                                                } else {
                                                    "Register"
                                                }
                                            }
                                        }
                                    }
                                }
                            } else if me.value().read().is_none() {
                                p { role: "status", class: "text-muted", "Checking your account…" }
                            } else if matches!(me.read().as_ref(), Some(Err(api::AccountError::Unauthenticated))) {
                                p { class: "text-muted",
                                    a { href: "{login_url}", "Sign in" }
                                    " to register."
                                }
                            }
                        }
                    }
                } else if let Some(Err(e)) = data.value().read().as_ref() {
                    div { class: "alert alert-danger", "{e}" }
                } else {
                    p { class: "text-muted", "Loading…" }
                }
                if show_help_modal() {
                    TeamRegistrationHelpModal {
                    return_focus_id: Some("registration-team-help".into()),
                        context: String::from("league"),
                        on_close: move |_| show_help_modal.set(false),
                    }
                }
            }
        }
    }
}
