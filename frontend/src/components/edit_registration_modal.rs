//! Edit registration modal for league or tournament context.
//! Shared between league home, tournament home (league events), etc.

use crate::api;
use crate::components::{use_scope_paid_entries, Modal, ModalKind, PaidEntryScope, PayNowNotice, WithdrawEntryChoice};
use crate::types::{PaidEntryAccount, UpdatePlayerRegistrationRequest, UpdateTeamRegistrationRequest};
use dioxus::prelude::*;

const REGISTRATION_ACTIONS_CSS: &str = include_str!("registration_actions.css");

/// Context for the edit registration modal: league or tournament.
#[derive(Clone, PartialEq)]
pub enum EditRegistrationContext {
    League { league_url: String },
    Tournament { tournament_url: String },
}

#[derive(Clone, Copy, PartialEq)]
enum RegistrationLoad {
    Loading,
    Ready,
    Failed,
}

#[derive(Clone, Copy, PartialEq)]
enum RegistrationMutation {
    Idle,
    Saving,
    Withdrawing,
}

#[component]
pub fn EditRegistrationModal(
    context: EditRegistrationContext,
    user_type: String,
    #[props(default = false)] historical: bool,
    #[props(optional)] team_id: Option<String>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
) -> Element {
    let mut show_deregister_confirm = use_signal(|| false);
    let load = use_signal(|| RegistrationLoad::Loading);
    let mutation = use_signal(|| RegistrationMutation::Idle);
    let save_error = use_signal(|| None::<String>);
    let is_team = user_type == "team";
    let pending = mutation() != RegistrationMutation::Idle;
    let actions_disabled =
        pending || load() != RegistrationLoad::Ready || show_deregister_confirm();
    let current_route = use_route::<crate::Route>();
    let entry_scope = match &context {
        EditRegistrationContext::League { league_url } => PaidEntryScope::league(league_url),
        EditRegistrationContext::Tournament { tournament_url } => PaidEntryScope::event(tournament_url),
    };

    rsx! {
        style { {REGISTRATION_ACTIONS_CSS} }
        Modal {
            id: "edit-registration-dialog",
            labelled_by: "edit-registration-title",
            initial_focus_id: "edit-registration-title",
            dismissible: !pending,
            on_close: move |_| { if mutation() == RegistrationMutation::Idle { on_close.call(()); } },
            div { class: "modal-header",
                h5 { class: "modal-title", id: "edit-registration-title", tabindex: "-1",
                    if is_team { "Edit team registration" } else { "Edit player registration" }
                }
                button {
                    r#type: "button",
                    class: "btn-close",
                    aria_label: "Close",
                    disabled: pending,
                    onclick: move |_| { if mutation() == RegistrationMutation::Idle { on_close.call(()); } },
                }
            }
            div { class: "modal-body",
                if !historical && load() == RegistrationLoad::Ready {
                    PayNowNotice {
                        scope: entry_scope.clone(),
                        registrant: String::from(if is_team { "team" } else { "player" }),
                        team_id: team_id.clone(),
                        return_to: current_route.to_string(),
                    }
                }
                if is_team {
                    EditTeamRegistrationContent { context: context.clone(), team_id: team_id.clone(), load, mutation, save_error, on_close, on_success }
                } else {
                    EditPlayerRegistrationContent {
                        context: context.clone(), historical, load, mutation, save_error, on_close, on_success,
                    }
                }
                if load() == RegistrationLoad::Ready {
                    div { class: "registration-withdraw-section",
                        button {
                            r#type: "button",
                            id: "edit-registration-withdraw",
                            class: "btn btn-outline-danger",
                            disabled: actions_disabled,
                            aria_haspopup: "dialog",
                            onclick: move |_| {
                                if mutation() == RegistrationMutation::Idle && load() == RegistrationLoad::Ready {
                                    show_deregister_confirm.set(true);
                                }
                            },
                            if historical {
                                "Un-claim"
                            } else if is_team {
                                "Withdraw team"
                            } else {
                                "Withdraw registration"
                            }
                        }
                    }
                }
                if show_deregister_confirm() {
                    DeregisterConfirmOverlay {
                        context: context.clone(), team_id: team_id.clone(), mutation, is_team, historical, on_close, on_success,
                        on_cancel: move |_| show_deregister_confirm.set(false),
                    }
                }
            }
            div { class: "modal-footer registration-dialog-footer",
                if let Some(message) = save_error() {
                    p { class: "registration-dialog-feedback text-danger", role: "alert", tabindex: "0", "{message}" }
                }
                if mutation() == RegistrationMutation::Saving {
                    span { class: "visually-hidden", role: "status", "Saving registration…" }
                }
                button {
                    r#type: "button",
                    id: "edit-registration-cancel",
                    class: "btn btn-outline-secondary",
                    disabled: pending,
                    onclick: move |_| { if mutation() == RegistrationMutation::Idle { on_close.call(()); } },
                    "Cancel"
                }
                if load() == RegistrationLoad::Ready {
                    button {
                        r#type: "submit",
                        id: "edit-registration-save",
                        form: if is_team { "edit-team-registration-form" } else { "edit-player-registration-form" },
                        class: "btn btn-primary",
                        disabled: actions_disabled,
                        if mutation() == RegistrationMutation::Saving { "Saving…" } else { "Save changes" }
                    }
                }
            }
        }
    }
}

#[component]
fn DeregisterConfirmOverlay(
    context: EditRegistrationContext,
    team_id: Option<String>,
    is_team: bool,
    historical: bool,
    mut mutation: Signal<RegistrationMutation>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
    on_cancel: EventHandler<()>,
) -> Element {
    let mut deregister_error = use_signal(|| None::<String>);
    let entry_scope = match &context {
        EditRegistrationContext::League { league_url } => PaidEntryScope::league(league_url),
        EditRegistrationContext::Tournament { tournament_url } => PaidEntryScope::event(tournament_url),
    };
    let entries = use_scope_paid_entries(entry_scope, team_id.clone());
    let applied_entry = (!historical)
        .then(|| entries.read().as_ref().and_then(|result| result.as_ref().ok()).and_then(|current| current.applied_entry().cloned()))
        .flatten();
    let transferring = use_signal(|| false);
    let transfer_to = use_signal(|| None::<PaidEntryAccount>);
    let scope_description = if historical && is_team {
        "The results stay, listed again as an unclaimed team, and your account is no longer linked to them."
    } else if historical {
        "You will be removed from the roster."
    } else {
        match &context {
            EditRegistrationContext::League { .. } => {
                "This changes your registration for the entire league season."
            }
            EditRegistrationContext::Tournament { .. } => {
                "This changes your registration for this tournament."
            }
        }
    };
    let confirm_title = if historical && is_team {
        "Un-claim this team?"
    } else if historical {
        "Leave this roster?"
    } else if is_team {
        "Withdraw your team?"
    } else {
        "Withdraw your registration?"
    };
    let pending = mutation() != RegistrationMutation::Idle;
    rsx! {
        Modal {
            id: "deregister-dialog",
            kind: ModalKind::AlertDialog,
            labelled_by: "deregister-title",
            described_by: "deregister-description",
            initial_focus_id: "keep-registration",
            return_focus_id: "edit-registration-withdraw",
            dismissible: !pending,
            on_close: move |_| { if mutation() == RegistrationMutation::Idle { on_cancel.call(()); } },
            div { class: "modal-header",
                h5 { class: "modal-title", id: "deregister-title", "{confirm_title}" }
            }
            div { class: "modal-body",
                p { id: "deregister-description", "{scope_description}" }
                if is_team && !historical {
                    p { "Your players stay registered without a team." }
                }
                if let Some(entry) = applied_entry.clone() {
                    WithdrawEntryChoice { kind: entry.kind.clone(), exclude: entry.holder.id.clone(), transferring, transfer_to, disabled: pending }
                }
                if !historical {
                    p { class: "small text-muted", "Registration must be open to register again. Contact an organizer about any refund." }
                }
                if let Some(ref err) = deregister_error() {
                    div { class: "alert alert-danger small py-2 mb-0", role: "alert", "{err}" }
                }
            }
            div { class: "modal-footer registration-confirm-footer",
                button { id: "keep-registration", r#type: "button", class: "btn btn-outline-secondary", disabled: pending, onclick: move |_| { if mutation() == RegistrationMutation::Idle { on_cancel.call(()); } }, "Keep registration" }
                button {
                    r#type: "button",
                    class: "btn btn-danger",
                    disabled: pending || (transferring() && transfer_to().is_none()),
                    onclick: move |_| {
                        if mutation() != RegistrationMutation::Idle { return; }
                        let recipient = if transferring() {
                            match transfer_to() {
                                Some(account) => Some(account.id),
                                None => return,
                            }
                        } else {
                            None
                        };
                        mutation.set(RegistrationMutation::Withdrawing);
                        deregister_error.set(None);
                        let ctx = context.clone();
                        let selected_team = team_id.clone();
                        spawn(async move {
                            let recipient = recipient.as_deref();
                            let result = match (&ctx, is_team) {
                                (EditRegistrationContext::League { league_url }, true) => api::league_deregister_team(league_url, recipient).await,
                                (EditRegistrationContext::League { league_url }, false) => api::league_deregister_player(league_url, recipient).await,
                                (EditRegistrationContext::Tournament { tournament_url }, true) => {
                                    if let Some(team_id) = selected_team.as_deref() {
                                        api::deregister_managed_team(tournament_url, team_id, recipient).await
                                    } else {
                                        api::deregister_team(tournament_url, recipient).await
                                    }
                                }
                                (EditRegistrationContext::Tournament { tournament_url }, false) => api::deregister_player(tournament_url, recipient).await,
                            };
                            mutation.set(RegistrationMutation::Idle);
                            match result {
                                Ok(res) if res.success => { on_close.call(()); on_success.call(()); }
                                Ok(res) => deregister_error.set(Some(res.error.unwrap_or_else(|| "Could not withdraw registration.".into()))),
                                Err(e) => deregister_error.set(Some(e)),
                            }
                        });
                    },
                    if pending {
                        "Withdrawing…"
                    } else if historical {
                        "Un-claim"
                    } else if transferring() {
                        "Withdraw and transfer"
                    } else {
                        "Withdraw registration"
                    }
                }
            }
        }
    }
}

#[component]
fn EditPlayerRegistrationContent(
    context: EditRegistrationContext,
    historical: bool,
    mut load: Signal<RegistrationLoad>,
    mut mutation: Signal<RegistrationMutation>,
    mut save_error: Signal<Option<String>>,
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

    let context_clone = context.clone();
    let mut fetch = use_resource(move || {
        let ctx = context_clone.clone();
        async move {
            load.set(RegistrationLoad::Loading);
            error.set(None);
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
                    load.set(RegistrationLoad::Ready);
                }
                (Err(e), _) => {
                    error.set(Some(format!("Failed to load registration: {}", e)));
                    load.set(RegistrationLoad::Failed);
                }
                (_, Err(e)) => {
                    error.set(Some(format!("Failed to load details: {}", e)));
                    load.set(RegistrationLoad::Failed);
                }
            }
        }
    });

    let context_for_submit = context.clone();
    let onsubmit = move |evt: Event<FormData>| {
        evt.prevent_default();
        let ctx = context_for_submit.clone();
        let on_close = on_close.clone();
        let on_success = on_success.clone();
        async move {
            if mutation() != RegistrationMutation::Idle || load() != RegistrationLoad::Ready {
                return;
            }
            mutation.set(RegistrationMutation::Saving);
            save_error.set(None);
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
            mutation.set(RegistrationMutation::Idle);
            match res {
                Ok(_) => {
                    on_close.call(());
                    on_success.call(());
                }
                Err(e) => {
                    save_error.set(Some(e));
                }
            }
        }
    };

    rsx! {
        if load() == RegistrationLoad::Loading {
            div { class: "d-flex justify-content-center",
                div { class: "spinner-border", role: "status",
                    span { class: "visually-hidden", "Loading..." }
                }
            }
        } else if load() == RegistrationLoad::Failed {
            if let Some(err) = error() {
                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
            }
            button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| fetch.restart(), "Retry loading registration" }
        } else {
            if let Some(err) = error() {
                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
            }
            form {
                id: "edit-player-registration-form",
                onsubmit: onsubmit,
                aria_busy: mutation() != RegistrationMutation::Idle,
                fieldset { disabled: mutation() != RegistrationMutation::Idle,
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
                if !historical {
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
}

#[component]
fn EditTeamRegistrationContent(
    context: EditRegistrationContext,
    team_id: Option<String>,
    mut load: Signal<RegistrationLoad>,
    mut mutation: Signal<RegistrationMutation>,
    mut save_error: Signal<Option<String>>,
    on_close: EventHandler<()>,
    on_success: EventHandler<()>,
) -> Element {
    let mut pseudonym = use_signal(|| "".to_string());
    let mut shortname = use_signal(|| "".to_string());
    let mut error = use_signal(|| None::<String>);

    let context_clone = context.clone();
    let team_for_fetch = team_id.clone();
    let mut fetch = use_resource(move || {
        let ctx = context_clone.clone();
        let selected_team = team_for_fetch.clone();
        async move {
            load.set(RegistrationLoad::Loading);
            error.set(None);
            let res = match &ctx {
                EditRegistrationContext::League { league_url } => {
                    api::get_my_team_registration_league(league_url).await
                }
                EditRegistrationContext::Tournament { tournament_url } => {
                    api::get_team_registration_for(tournament_url, selected_team.as_deref()).await
                }
            };
            match res {
                Ok(r) => {
                    pseudonym.set(r.registration.pseudonym.unwrap_or_default());
                    shortname.set(r.registration.shortname.clone().unwrap_or_default());
                    load.set(RegistrationLoad::Ready);
                }
                Err(e) => {
                    error.set(Some(format!("Failed to load registration: {}", e)));
                    load.set(RegistrationLoad::Failed);
                }
            }
        }
    });

    let context_for_submit = context.clone();
    let onsubmit = move |evt: Event<FormData>| {
        evt.prevent_default();
        let ctx = context_for_submit.clone();
        let selected_team = team_id.clone();
        let on_close = on_close.clone();
        let on_success = on_success.clone();
        async move {
            if mutation() != RegistrationMutation::Idle || load() != RegistrationLoad::Ready {
                return;
            }
            mutation.set(RegistrationMutation::Saving);
            save_error.set(None);
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
                    api::update_team_registration_for(tournament_url, selected_team.as_deref(), &req).await
                }
            };
            mutation.set(RegistrationMutation::Idle);
            match res {
                Ok(_) => {
                    on_close.call(());
                    on_success.call(());
                }
                Err(e) => {
                    save_error.set(Some(e));
                }
            }
        }
    };

    rsx! {
        if load() == RegistrationLoad::Loading {
            div { class: "d-flex justify-content-center",
                div { class: "spinner-border", role: "status",
                    span { class: "visually-hidden", "Loading..." }
                }
            }
        } else if load() == RegistrationLoad::Failed {
            if let Some(err) = error() {
                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
            }
            button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| fetch.restart(), "Retry loading registration" }
        } else {
            if let Some(err) = error() {
                div { class: "alert alert-danger mb-3", role: "alert", "{err}" }
            }
            form {
                id: "edit-team-registration-form",
                onsubmit: onsubmit,
                aria_busy: mutation() != RegistrationMutation::Idle,
                fieldset { disabled: mutation() != RegistrationMutation::Idle,
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
}
