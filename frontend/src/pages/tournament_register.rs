use crate::api;
use crate::components::TeamRegistrationHelpModal;
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

fn get_form_select_value(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlSelectElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

#[component]
pub fn TournamentRegister(url: String) -> Element {
    let navigator = use_navigator();
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        async move { api::tournament_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let me = crate::auth::use_account();
    let val = data.value();
    let mut show_help_modal = use_signal(|| false);
    let mut register_error = use_signal(|| None::<String>);
    use_effect(move || { if register_error().is_some() { crate::form_validation::focus_after_render("registration-error"); } });
    let mut busy = use_signal(|| false);
    let login_url = format!("/login?next={}", urlencoding::encode(&format!("/{url}/register")));
    let league_url = use_memo(move || {
        val.read()
            .as_ref()
            .and_then(|result| result.as_ref().ok())
            .and_then(|detail| detail.tournament.league.as_ref())
            .map(|league| league.league_url.clone())
    });
    use_effect(move || {
        if let Some(league_url) = league_url() {
            navigator.replace(Route::LeagueRegister { league_url });
        }
    });

    // League events share registration, fees, and rosters with their league.
    // Keep the standalone form unmounted while replacing a direct event URL.
    if league_url().is_some() {
        return rsx! { p { role: "status", "Loading registration…" } };
    }

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h2 { "Registration" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::TournamentHome { url: url.clone() }, "{d.tournament.name}" } }
                            li { class: "breadcrumb-item active", "Registration" }
                        }
                    }
                }
            }

            div { class: "row justify-content-center form-page",
                div { class: "col-12 form-column",
                    if let Some(Ok(u)) = me.read().as_ref() {
                        if u.user_type == "player" {
                            div { class: "edit-section",
                                div { class: "edit-section-heading", h3 { class: "h5 mb-0", "Player registration" } p { class: "text-muted mb-0 mt-2", "Registering as {u.name} (@{u.id})" } }
                                div { class: "edit-section-body",
                                    if let Some(terms) = &d.tournament.terms_link {
                                        p { class: "form-text", a { href: "{terms}", target: "_blank", rel: "noopener noreferrer", "Read the event’s registration terms" } }
                                    }
                                    if let Some(err) = register_error() {
                                        div { id: "registration-error", tabindex: "-1", class: "alert alert-danger mb-3", role: "alert", "{err}" }
                                    }
                                    form {
                                        id: "player-register-form",
                                        onsubmit: move |ev| {
                                            ev.prevent_default();
                                            if busy() { return; }
                                            register_error.set(None);
                                            let u = url.clone();
                                            busy.set(true);
                                            spawn(async move {
                                                let jersey_name = get_form_value("jersey_name");
                                                let jersey_number = get_form_value("jersey_number");
                                                let team = get_form_select_value("team");
                                                let waiver_legal_name_signature =
                                                    get_form_value("waiver_legal_name_signature");
                                                match api::register_player(
                                                    &u,
                                                    &jersey_name,
                                                    &jersey_number,
                                                    &team,
                                                    &waiver_legal_name_signature,
                                                )
                                                .await
                                                {
                                                    Ok(res) if res.success => {
                                                        navigator.push(Route::TournamentHome { url: u });
                                                    }
                                                    Ok(res) => {
                                                        register_error.set(Some(res.error.unwrap_or_else(|| "Registration failed.".to_string())));
                                                    }
                                                    Err(e) => {
                                                        register_error.set(Some(e));
                                                    }
                                                }
                                                busy.set(false);
                                            });
                                        },
                                        div { class: "mb-3",
                                            label { r#for: "jersey_name", class: "form-label", "Jersey Name" }
                                            input { r#type: "text", class: "form-control", id: "jersey_name", name: "jersey_name", required: true, aria_describedby: "jersey_name-help" }
                                            div { id: "jersey_name-help", class: "form-text", "Your name for this tournament" }
                                        }
                                        div { class: "mb-3",
                                            label { r#for: "jersey_number", class: "form-label", "Jersey Number" }
                                            input { r#type: "text", class: "form-control", id: "jersey_number", name: "jersey_number" }
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
                                            select { class: "form-select", id: "team", aria_describedby: "team-help", name: "team",
                                                option { value: "", "No team — join as a free merc" }
                                                for team in d.teams_with_counts.iter() {
                                                    option { value: "{team.team_id}", "{team.pseudonym.as_deref().unwrap_or(&team.team_name)}" }
                                                }
                                            }
                                            div { id: "team-help", class: "form-text", "Your team must approve your request before you join its roster. Without a team, you register as a free merc." }
                                        }
                                        if let Some(fee) = d.tournament.player_reg_fee {
                                            if fee > 0.0 {
                                                div { class: "mb-3",
                                                    div { class: "alert alert-info",
                                                        h6 { "Registration fee" }
                                                        p { class: "small mb-2", "Pay the organizer separately. They will confirm payment in Arctos." }
                                                        p {
                                                            strong { "Player Registration: " }
                                                            {format!("${:.2}", fee)}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        if d.tournament.waiver_required {
                                            div { class: "mb-3",
                                                label { r#for: "waiver_legal_name_signature", class: "form-label", "Full legal name for the waiver" }
                                                if let Some(link) = &d.tournament.waiver_filepath {
                                                    div { class: "form-text mb-2",

                                                        a { href: api::static_file_url(link), target: "_blank", rel: "noopener noreferrer", class: "text-decoration-none", "Read the waiver (opens a new tab)" }
                                                        if let Some(sha) = &d.tournament.waiver_sha256 {
                                                            details { class: "waiver-verification mt-2",
                                                                summary { "Document verification (SHA-256)" }
                                                            pre { class: "p-2 border rounded bg-light mt-1 mb-0", style: "white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;", code { "{sha}" } }
                                                            }
                                                        }
                                                    }
                                                }
                                                p { id: "waiver-privacy", class: "form-text mb-2", "Your legal name is shared only with you and event organizers." }
                                                p { id: "waiver-consent", class: "form-text mb-2", "By entering your full legal name below, you agree to the terms of the waiver linked above, and affirm that the waiver you viewed matches the SHA-256 hash displayed." }
                                                input {
                                                    r#type: "text",
                                                    class: "form-control",
                                                    id: "waiver_legal_name_signature", aria_describedby: "waiver-privacy waiver-consent",
                                                    name: "waiver_legal_name_signature",
                                                    autocomplete: "name",
                                                    required: true
                                                }
                                            }
                                        }
                                        div { class: "d-grid",
                                            if !d.tournament.player_registration_open {
                                                p { class: "alert alert-warning", "Player registration is currently closed. Contact an organizer if you need help." }
                                            }
                                            button { r#type: "submit", class: "btn btn-primary", disabled: busy() || !d.tournament.player_registration_open, if busy() { "Registering…" } else { "Register" } }
                                        }
                                    }
                                }
                            }
                        } else if u.user_type == "team" {
                            div { class: "edit-section",
                                div { class: "edit-section-heading", h3 { class: "h5 mb-0", "Team registration" } p { class: "text-muted mb-0 mt-2", "Registering as {u.name} (@{u.id})" } }
                                div { class: "edit-section-body",
                                    if let Some(terms) = &d.tournament.terms_link {
                                        p { class: "form-text", a { href: "{terms}", target: "_blank", rel: "noopener noreferrer", "Read the event’s registration terms" } }
                                    }
                                    if let Some(err) = register_error() {
                                        div { id: "registration-error", tabindex: "-1", class: "alert alert-danger mb-3", role: "alert", "{err}" }
                                    }
                                    form {
                                        id: "team-register-form",
                                        onsubmit: move |ev| {
                                            ev.prevent_default();
                                            if busy() { return; }
                                            register_error.set(None);
                                            let u = url.clone();
                                            busy.set(true);
                                            spawn(async move {
                                                let pseudonym = get_form_value("pseudonym");
                                                let shortname_raw = get_form_value("shortname");
                                                let shortname = if shortname_raw.trim().is_empty() { None } else { Some(shortname_raw) };
                                                    match api::register_team(&u, &pseudonym, shortname.as_deref()).await {
                                                    Ok(res) if res.success => {
                                                        navigator.push(Route::TournamentHome { url: u });
                                                    }
                                                    Ok(res) => {
                                                        register_error.set(Some(res.error.unwrap_or_else(|| "Registration failed.".to_string())));
                                                    }
                                                    Err(e) => {
                                                        register_error.set(Some(e));
                                                    }
                                                }
                                                busy.set(false);
                                            });
                                        },
                                        div { class: "mb-3",
                                            label { r#for: "pseudonym", class: "form-label", "Team Name for This Tournament" }
                                            input { r#type: "text", class: "form-control", id: "pseudonym", name: "pseudonym", required: true, aria_describedby: "pseudonym-help" }
                                            div { id: "pseudonym-help", class: "form-text", "This is how your team will be referred to in this tournament" }
                                        }
                                        div { class: "mb-3",
                                            label { r#for: "shortname", class: "form-label", "Short name (optional)" }
                                            input { r#type: "text", class: "form-control", id: "shortname", aria_describedby: "shortname-help", name: "shortname", maxlength: "8", placeholder: "e.g. UWaoW" }
                                            div { id: "shortname-help", class: "form-text", "Used in schedules and brackets. Leave blank to auto-shorten." }
                                        }
                                        if let Some(fee) = d.tournament.team_reg_fee {
                                            if fee > 0.0 {
                                                div { class: "mb-3",
                                                    div { class: "alert alert-info",
                                                        h6 { "Registration fee" }
                                                        p { class: "small mb-2", "Pay the organizer separately. They will confirm payment in Arctos." }
                                                        p {
                                                            strong { "Team Registration: " }
                                                            {format!("${:.2}", fee)}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        div { class: "d-grid",
                                            if !d.tournament.team_registration_open {
                                                p { class: "alert alert-warning", "Team registration is currently closed. Contact an organizer if you need help." }
                                            }
                                            button { r#type: "submit", class: "btn btn-primary", disabled: busy() || !d.tournament.team_registration_open, if busy() { "Registering…" } else { "Register" } }
                                        }
                                    }
                                }
                            }
                        } else {
                            div { class: "alert alert-warning",
                                h4 { "Login Required" }
                                p { "You need to be logged in to register for this tournament." }
                                div { class: "d-flex gap-2",
                                    a { href: "{login_url}", class: "btn btn-primary", "Sign in to register" }
                                }
                            }
                        }
                    } else if me.read().is_none() {
                        p { role: "status", class: "text-muted", "Checking your account…" }
                    } else if matches!(me.read().as_ref(), Some(Err(api::AccountError::Unauthenticated))) {
                        div { class: "alert alert-warning",
                            h4 { "Login Required" }
                            p { "You need to be logged in to register for this tournament." }
                            div { class: "d-flex gap-2",
                                a { href: "{login_url}", class: "btn btn-primary", "Sign in to register" }
                            }
                        }
                    }
                }
            }

            if show_help_modal() {
                TeamRegistrationHelpModal {
                    return_focus_id: Some("registration-team-help".into()),
                    context: String::from("tournament"),
                    on_close: move |_| show_help_modal.set(false),
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { "Loading…" }
        }
    }
}
