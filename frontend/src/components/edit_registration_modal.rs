//! Edit registration modal for league or tournament context.
//! Shared between league home, tournament home (league events), etc.

use crate::api;
use crate::types::{UpdatePlayerRegistrationRequest, UpdateTeamRegistrationRequest};
use dioxus::prelude::*;

/// Context for the edit registration modal: league or tournament.
#[derive(Clone, PartialEq)]
pub enum EditRegistrationContext {
    League { league_url: String },
    Tournament { tournament_url: String },
}

#[component]
pub fn EditRegistrationModal(
    context: EditRegistrationContext,
    user_type: String,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
) -> Element {
    let mut show_deregister_confirm = use_signal(|| false);
    let busy = use_signal(|| true);
    let is_team = user_type == "team";

    rsx! {
        div {
            class: "modal show d-block",
            style: "background: rgba(0,0,0,0.5);",
            tabindex: "-1",
            role: "dialog",
            aria_modal: "true",
            aria_labelledby: "edit-registration-title",
            onclick: move |_| {
                if busy() { return; }
                on_close.call(());
                show_deregister_confirm.set(false);
            },
            div {
                class: "modal-dialog modal-dialog-centered modal-dialog-scrollable",
                onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                div { class: "modal-content",
                    div { class: "modal-header",
                        h5 { class: "modal-title", id: "edit-registration-title",
                            if is_team { "Edit Team Registration" } else { "Edit Player Registration" }
                        }
                        button {
                            r#type: "button",
                            class: "btn-close",
                            aria_label: "Close",
                            disabled: busy(),
                            onclick: move |_| {
                                on_close.call(());
                                show_deregister_confirm.set(false);
                            },
                        }
                    }
                    div { class: "modal-body", style: "position: relative;",
                        if is_team {
                            EditTeamRegistrationContent {
                                context: context.clone(),
                                busy,
                                on_close: on_close.clone(),
                                on_success: on_success.clone(),
                            }
                        } else {
                            EditPlayerRegistrationContent {
                                context: context.clone(),
                                busy,
                                on_close: on_close.clone(),
                                on_success: on_success.clone(),
                            }
                        }
                        if show_deregister_confirm() {
                            DeregisterConfirmOverlay {
                                context: context.clone(),
                                busy,
                                is_team,
                                on_close: on_close.clone(),
                                on_success: on_success.clone(),
                                on_cancel: move |_| show_deregister_confirm.set(false),
                            }
                        }
                    }
                    div { class: "modal-footer",
                        button {
                            r#type: "button",
                            class: "btn btn-outline-danger",
                            disabled: busy() || show_deregister_confirm(),
                            onclick: move |_| show_deregister_confirm.set(true),
                            if is_team { "Deregister Team" } else { "Deregister Player" }
                        }
                        button {
                            r#type: "submit",
                            form: if is_team { "edit-team-registration-form" } else { "edit-player-registration-form" },
                            class: "btn btn-primary",
                            disabled: busy() || show_deregister_confirm(),
                            if busy() { "Please wait…" } else { "Save changes" }
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn DeregisterConfirmOverlay(
    context: EditRegistrationContext,
    is_team: bool,
    mut busy: Signal<bool>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
    on_cancel: EventHandler<()>,
) -> Element {
    let mut deregister_error = use_signal(|| None::<String>);
    let scope_description = match &context {
        EditRegistrationContext::League { .. } => {
            "This changes your registration for the entire league season."
        }
        EditRegistrationContext::Tournament { .. } => {
            "This changes your registration for this tournament."
        }
    };
    rsx! {
        div {
            class: "position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center p-3",
            style: "background: rgba(0,0,0,0.3); z-index: 1050; border-radius: 0.25rem;",
            onclick: move |_| { if !busy() { on_cancel.call(()); } },
            div {
                class: "card shadow",
                role: "alertdialog",
                aria_labelledby: "deregister-title",
                aria_describedby: "deregister-description",
                onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                div { class: "card-body",
                    h5 { id: "deregister-title", if is_team { "Withdraw your team?" } else { "Withdraw your registration?" } }
                    p { id: "deregister-description", "{scope_description}" }
                    p { class: "small text-muted", "Registration must be open to register again. Contact an organizer about any payment or refund." }
                    if let Some(ref err) = deregister_error() {
                        div { class: "alert alert-danger small py-2 mb-3", role: "alert", "{err}" }
                    }
                    div { class: "d-flex gap-2 justify-content-end flex-wrap",
                        button { r#type: "button", class: "btn btn-secondary", disabled: busy(), onclick: move |_| on_cancel.call(()), "Keep registration" }
                        button {
                            r#type: "button",
                            class: "btn btn-danger",
                            disabled: busy(),
                            onclick: move |_| {
                                if busy() { return; }
                                busy.set(true);
                                deregister_error.set(None);
                                let ctx = context.clone();
                                spawn(async move {
                                    let result = match (&ctx, is_team) {
                                        (EditRegistrationContext::League { league_url }, true) => api::league_deregister_team(league_url).await,
                                        (EditRegistrationContext::League { league_url }, false) => api::league_deregister_player(league_url).await,
                                        (EditRegistrationContext::Tournament { tournament_url }, true) => api::deregister_team(tournament_url).await,
                                        (EditRegistrationContext::Tournament { tournament_url }, false) => api::deregister_player(tournament_url).await,
                                    };
                                    busy.set(false);
                                    match result {
                                        Ok(res) if res.success => { on_close.call(()); on_success.call(()); }
                                        Ok(res) => deregister_error.set(Some(res.error.unwrap_or_else(|| "Could not withdraw registration.".into()))),
                                        Err(e) => deregister_error.set(Some(e)),
                                    }
                                });
                            },
                            if busy() { "Withdrawing…" } else { "Withdraw registration" }
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn EditPlayerRegistrationContent(
    context: EditRegistrationContext,
    mut busy: Signal<bool>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
) -> Element {
    let mut jersey_name = use_signal(|| "".to_string());
    let mut jersey_number = use_signal(|| "".to_string());
    let mut team = use_signal(|| "".to_string());
    let mut current_team_name = use_signal(|| "".to_string());
    let mut status = use_signal(|| "".to_string());
    let mut teams = use_signal(|| vec![]);
    let mut waiver_required = use_signal(|| false);
    let mut waiver_signature_valid = use_signal(|| false);
    let mut waiver_filepath = use_signal(|| None::<String>);
    let mut waiver_sha256 = use_signal(|| None::<String>);
    let mut waiver_legal_name_signature = use_signal(|| "".to_string());
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);

    let context_clone = context.clone();
    let _fetch = use_resource(move || {
        let ctx = context_clone.clone();
        async move {
            loading.set(true);
            let (reg_res, teams_res) = match &ctx {
                EditRegistrationContext::League { league_url } => (
                    api::get_my_player_registration_league(league_url).await,
                    api::league_detail(league_url)
                        .await
                        .map(|d| d.teams_with_counts),
                ),
                EditRegistrationContext::Tournament { tournament_url } => (
                    api::get_my_player_registration(tournament_url).await,
                    api::tournament_detail(tournament_url)
                        .await
                        .map(|d| d.teams_with_counts),
                ),
            };

            match (reg_res, teams_res) {
                (Ok(res), Ok(teams_list)) => {
                    jersey_name.set(res.registration.jersey_name.unwrap_or_default());
                    jersey_number.set(res.registration.jersey_number.unwrap_or_default());
                    status.set(res.registration.status.clone());

                    waiver_required.set(res.waiver_required);
                    waiver_signature_valid.set(res.waiver_signature_valid);
                    waiver_filepath.set(res.waiver_filepath);
                    waiver_sha256.set(res.waiver_sha256);
                    waiver_legal_name_signature
                        .set(res.waiver_legal_name_signature.unwrap_or_default());

                    if let Some(ref ct) = res.current_team {
                        current_team_name
                            .set(ct.pseudonym.clone().unwrap_or_else(|| ct.id.clone()));
                    }
                    let mut t_list = vec![];
                    for t in teams_list {
                        t_list.push((t.team_id.clone(), t.pseudonym.unwrap_or(t.team_name)));
                    }
                    teams.set(t_list);
                    let selected_team = res
                        .registration
                        .team
                        .clone()
                        .or_else(|| res.current_team.as_ref().map(|c| c.id.clone()))
                        .unwrap_or_default();
                    team.set(selected_team);
                }
                (Err(e), _) => error.set(Some(format!("Failed to load registration: {}", e))),
                (_, Err(e)) => error.set(Some(format!("Failed to load details: {}", e))),
            }
            loading.set(false);
            busy.set(false);
        }
    });

    let context_for_submit = context.clone();
    let onsubmit = move |evt: Event<FormData>| {
        evt.prevent_default();
        let ctx = context_for_submit.clone();
        let on_close = on_close.clone();
        let on_success = on_success.clone();
        async move {
            if busy() || loading() {
                return;
            }
            busy.set(true);
            error.set(None);
            let t_val = team();
            let team_opt = if t_val.is_empty() { None } else { Some(t_val) };
            let req = UpdatePlayerRegistrationRequest {
                jersey_name: Some(jersey_name()),
                jersey_number: Some(jersey_number()),
                team: team_opt,
                waiver_legal_name_signature: if waiver_required() && !waiver_signature_valid() {
                    Some(waiver_legal_name_signature())
                } else {
                    None
                },
            };
            let res = match &ctx {
                EditRegistrationContext::League { league_url } => {
                    api::update_my_player_registration_league(league_url, &req).await
                }
                EditRegistrationContext::Tournament { tournament_url } => {
                    api::update_my_player_registration(tournament_url, &req).await
                }
            };
            busy.set(false);
            match res {
                Ok(_) => {
                    on_close.call(());
                    on_success.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    loading.set(false);
                }
            }
        }
    };

    rsx! {
        if loading() {
            div { class: "d-flex justify-content-center",
                div { class: "spinner-border", role: "status",
                    span { class: "visually-hidden", "Loading..." }
                }
            }
        } else {
            if let Some(err) = error() {
                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
            }
            form {
                id: "edit-player-registration-form",
                onsubmit: onsubmit,
                div { class: "mb-3",
                    label { class: "form-label", r#for: "edit-jersey-name", "Jersey Name" }
                    input {
                        class: "form-control",
                        "type": "text",
                        id: "edit-jersey-name",
                        value: "{jersey_name}",
                        oninput: move |e| jersey_name.set(e.value()),
                        required: true
                    }
                    div { class: "form-text", "Your jersey name for this event or league season." }
                }
                div { class: "mb-3",
                    label { class: "form-label", r#for: "edit-jersey-number", "Jersey Number" }
                    input {
                        class: "form-control",
                        "type": "text",
                        id: "edit-jersey-number",
                        value: "{jersey_number}",
                        oninput: move |e| jersey_number.set(e.value())
                    }
                }
                div { class: "mb-3",
                    label { class: "form-label", r#for: "edit-registration-team", "Team" }
                    select {
                        class: "form-select",
                        id: "edit-registration-team",
                        value: "{team}",
                        onchange: move |e| team.set(e.value()),
                        option { value: "", selected: team().is_empty(), "No team — join as a free merc" }
                        for (id, name) in teams() {
                            option { value: "{id}", selected: id == team(), "{name}" }
                        }
                    }
                    div { class: "form-text",
                        if !current_team_name().is_empty() {
                            span { "Current team: {current_team_name} " }
                            if status() == "PENDING_TEAM_APPROVAL" {
                                span { class: "badge bg-warning", "Pending Approval" }
                            }
                        }
                        br {}
                        "If you change teams, your new team must approve your request."
                    }
                }
                if waiver_required() {
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "edit-waiver-signature", "Waiver Signature" }
                        if let Some(link) = waiver_filepath() {
                            div { class: "form-text mb-2",

                                a { href: api::static_file_url(&link), target: "_blank", rel: "noopener noreferrer", class: "text-decoration-none", "Read the waiver (opens a new tab)" }
                                if let Some(sha) = waiver_sha256() {
                                    details { class: "waiver-verification mt-2",
                                        summary { "Document verification (SHA-256)" }
                                    pre { class: "p-2 border rounded bg-light mt-1 mb-0", style: "white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;", code { "{sha}" } }
                                    }
                                }
                            }
                        }
                        p { class: "form-text mb-2", "Your legal name is shared only with you and event organizers." }
                        p { class: "form-text mb-2", "By entering your full legal name below, you agree to the terms of the waiver linked above, and affirm that the waiver you viewed matches the SHA-256 hash displayed." }
                        input {
                            class: if waiver_signature_valid() {
                                "form-control bg-light text-muted"
                            } else {
                                "form-control"
                            },
                            r#type: "text",
                            autocomplete: "name",
                            id: "edit-waiver-signature",
                        value: "{waiver_legal_name_signature}",
                            disabled: waiver_signature_valid(),
                            required: !waiver_signature_valid(),
                            oninput: move |e| waiver_legal_name_signature.set(e.value()),
                        }
                        div { class: "form-text mb-2",
                            "Waiver signature:"
                            if waiver_signature_valid() {
                                span { class: "text-success ms-2", "Valid" }
                            } else {
                                span { class: "text-warning ms-2", "Needs signing / re-signing" }
                            }
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn EditTeamRegistrationContent(
    context: EditRegistrationContext,
    mut busy: Signal<bool>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
) -> Element {
    let mut pseudonym = use_signal(|| "".to_string());
    let mut shortname = use_signal(|| "".to_string());
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| true);

    let context_clone = context.clone();
    let _fetch = use_resource(move || {
        let ctx = context_clone.clone();
        async move {
            loading.set(true);
            let res = match &ctx {
                EditRegistrationContext::League { league_url } => {
                    api::get_my_team_registration_league(league_url).await
                }
                EditRegistrationContext::Tournament { tournament_url } => {
                    api::get_my_team_registration(tournament_url).await
                }
            };
            match res {
                Ok(r) => {
                    pseudonym.set(r.registration.pseudonym.unwrap_or_default());
                    shortname.set(r.registration.shortname.clone().unwrap_or_default());
                }
                Err(e) => error.set(Some(e)),
            }
            loading.set(false);
            busy.set(false);
        }
    });

    let context_for_submit = context.clone();
    let onsubmit = move |evt: Event<FormData>| {
        evt.prevent_default();
        let ctx = context_for_submit.clone();
        let on_close = on_close.clone();
        let on_success = on_success.clone();
        async move {
            if busy() || loading() {
                return;
            }
            busy.set(true);
            error.set(None);
            let trimmed_short = shortname().trim().to_string();
            let req = UpdateTeamRegistrationRequest {
                pseudonym: Some(pseudonym()),
                shortname: Some(trimmed_short),
            };
            let res = match &ctx {
                EditRegistrationContext::League { league_url } => {
                    api::update_my_team_registration_league(league_url, &req).await
                }
                EditRegistrationContext::Tournament { tournament_url } => {
                    api::update_my_team_registration(tournament_url, &req).await
                }
            };
            busy.set(false);
            match res {
                Ok(_) => {
                    on_close.call(());
                    on_success.call(());
                }
                Err(e) => {
                    error.set(Some(e));
                    loading.set(false);
                }
            }
        }
    };

    rsx! {
        if loading() {
            div { class: "d-flex justify-content-center",
                div { class: "spinner-border", role: "status",
                    span { class: "visually-hidden", "Loading..." }
                }
            }
        } else {
            if let Some(err) = error() {
                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
            }
            form {
                id: "edit-team-registration-form",
                onsubmit: onsubmit,
                div { class: "mb-3",
                    label { class: "form-label", r#for: "edit-team-pseudonym", "Team name for this registration" }
                    input {
                        class: "form-control",
                        "type": "text",
                        id: "edit-team-pseudonym",
                        value: "{pseudonym}",
                        oninput: move |e| pseudonym.set(e.value()),
                        required: true
                    }
                }
                div { class: "mb-3",
                    label { class: "form-label", r#for: "shortname", "Short name (optional)" }
                    input {
                        class: "form-control",
                        "type": "text",
                        id: "shortname",
                        maxlength: "8",
                        value: "{shortname}",
                        oninput: move |e| shortname.set(e.value()),
                    }
                    div { class: "form-text", "Used in schedules and brackets. Leave blank to auto-shorten." }
                }
            }
        }
    }
}
