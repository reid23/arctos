use crate::api;
use crate::components::{default_allowed_weapons, AllowedWeaponsCheckboxes, ToolPageHeader};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn SideCompEdit(url: String, comp_id: i32) -> Element {
    let navigator = use_navigator();
    let mut detail = use_resource(move || async move { api::sidecomp_detail(comp_id).await });

    let mut name = use_signal(String::new);
    let mut type_ = use_signal(String::new);
    let mut description = use_signal(String::new);
    let mut registration_open = use_signal(|| false);
    let mut active = use_signal(|| false);
    let mut finalized = use_signal(|| false);
    let mut track_losses = use_signal(|| false);
    let mut track_chain_losses = use_signal(|| false);
    let mut track_break_losses = use_signal(|| false);
    let mut track_matchups = use_signal(|| false);
    let mut allowed_weapons = use_signal(default_allowed_weapons);
    let mut top_n_text = use_signal(String::new);
    let mut initialised = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut submitting = use_signal(|| false);
    let mut deleting = use_signal(|| false);

    if !initialised() {
        if let Some(Ok(d)) = detail.read().as_ref().filter(|result| result.as_ref().is_ok_and(|d| d.viewer_is_to)) {
            name.set(d.name.clone());
            type_.set(d.type_.clone());
            description.set(d.description.clone().unwrap_or_default());
            registration_open.set(d.registration_open);
            active.set(d.active);
            finalized.set(d.finalized);
            track_losses.set(d.track_losses);
            track_chain_losses.set(d.track_chain_losses);
            track_break_losses.set(d.track_break_losses);
            track_matchups.set(d.track_matchups);
            if d.allowed_weapons.is_empty() {
                allowed_weapons.set(Vec::new());
            } else {
                allowed_weapons.set(d.allowed_weapons.clone());
            }
            top_n_text.set(
                d.only_show_top_n_results
                    .map(|n| n.to_string())
                    .unwrap_or_default(),
            );
            initialised.set(true);
        }
    }

    let disabled_warning_count = {
        let selected = allowed_weapons();
        match detail.read().as_ref() {
            Some(Ok(d)) => {
                let n = d
                    .registrants
                    .iter()
                    .filter(|r| match r.weapon.as_deref() {
                        Some(w) => !selected.iter().any(|s| s == w),
                        None => true,
                    })
                    .count() as i32;
                Some(n)
            }
            _ => None,
        }
    };

    let url_for_back = url.clone();
    let url_for_submit = url.clone();
    let url_for_delete = url.clone();

    rsx! {
        style { {include_str!("../components/organizer_tools.css")} }
        div { class: "row",
            div { class: "col-12 ordinary-form-rail",
                ToolPageHeader {
                    title: "Edit side competition",
                    back: Some((Route::SideCompDetail { url: url_for_back.clone(), comp_id }, "Back to side competition".into())),
                }
                match detail.read().as_ref() {
                    None => rsx! { p { role: "status", "Loading side competition…" } },
                    Some(Err(message)) => rsx! {
                        p { class: "text-danger", role: "alert", "Could not load side competition: {message}" }
                        button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| detail.restart(), "Retry loading side competition" }
                    },
                    Some(Ok(d)) if !d.viewer_is_to => rsx! { p { role: "alert", "Only an organizer can edit this side competition." } },
                    Some(Ok(_)) => rsx! {
                form {
                    onsubmit: move |evt| {
                        evt.prevent_default();
                        if submitting() || deleting() || !initialised() { return; }
                        let url_inner = url_for_submit.clone();
                        let n = name();
                        let t = type_();
                        let d = description();
                        let open = registration_open();
                        let is_active = active();
                        let is_chain_breaking = t == "CHAIN_BREAKING";
                        let losses = (!is_chain_breaking).then_some(track_losses());
                        let chain_losses = is_chain_breaking.then_some(track_chain_losses());
                        let break_losses = is_chain_breaking.then_some(track_break_losses());
                        let matchups = Some(losses == Some(true) && track_matchups());
                        let weapons = allowed_weapons();
                        let top_n = {
                            let raw = top_n_text().trim().to_string();
                            if raw.is_empty() {
                                None
                            } else {
                                match raw.parse::<i32>() {
                                    Ok(v) if v >= 0 => Some(v),
                                    _ => {
                                        error.set(Some("Public result limit must be a non-negative integer or blank".into()));
                                        return;
                                    }
                                }
                            }
                        };
                        error.set(None);
                        submitting.set(true);
                        spawn(async move {
                            match api::sidecomp_update(
                                comp_id,
                                Some(&n),
                                Some(&t),
                                Some(&d),
                                Some(open),
                                Some(is_active),
                                losses,
                                chain_losses,
                                break_losses,
                                matchups,
                                Some(&weapons),
                                top_n,
                            ).await {
                                Ok(_) => {
                                    navigator.push(Route::SideCompDetail { url: url_inner, comp_id });
                                }
                                Err(e) => error.set(Some(e)),
                            }
                            submitting.set(false);
                        });
                    },
                    fieldset { disabled: submitting() || deleting(),
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "sidecomp-name", "Name" }
                        input {
                            id: "sidecomp-name", required: true,
                            class: "form-control",
                            r#type: "text",
                            value: "{name}",
                            oninput: move |evt| name.set(evt.value()),
                        }
                    }
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "sidecomp-type", "Type" }
                        select {
                            id: "sidecomp-type",
                            class: "form-select",
                            value: "{type_}",
                            onchange: move |evt| type_.set(evt.value()),
                            option { value: "DUELING", "Dueling" }
                            option { value: "CHAIN_BREAKING", "Chain / Breaking" }
                            option { value: "OTHER", "Other" }
                        }
                    }
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "sidecomp-description", "Description" }
                        textarea {
                            id: "sidecomp-description", aria_describedby: "sidecomp-description-help",
                            class: "form-control",
                            rows: "4",
                            value: "{description}",
                            oninput: move |evt| description.set(evt.value()),
                        }
                        div { id: "sidecomp-description-help", class: "form-text", "Optional. Leave blank to clear." }
                    }
                    div { class: "mb-3 form-check",
                        input {
                            class: "form-check-input",
                            r#type: "checkbox",
                            id: "registration-open-toggle", aria_describedby: "registration-open-toggle-help",
                            checked: registration_open(),
                            onchange: move |evt| registration_open.set(evt.checked()),
                        }
                        label {
                            class: "form-check-label",
                            r#for: "registration-open-toggle",
                            "Registration open"
                        }
                        div { id: "registration-open-toggle-help", class: "form-text",
                            "When unchecked, only organizers can register players."
                        }
                    }
                    div { class: "mb-3 form-check",
                        input {
                            class: "form-check-input",
                            r#type: "checkbox",
                            id: "active-toggle", aria_describedby: "active-toggle-help",
                            checked: active(),
                            disabled: finalized(),
                            onchange: move |evt| active.set(evt.checked()),
                        }
                        label {
                            class: "form-check-label",
                            r#for: "active-toggle",
                            "Active"
                        }
                        div { id: "active-toggle-help", class: "form-text",
                            if finalized() {
                                "Results are finalized; this side competition cannot be activated again."
                            } else {
                                "When unchecked, results cannot be entered."
                            }
                        }
                    }
                    details { class: "settings-advanced", id: "sidecomp-advanced",
                        summary { "Advanced options" small {
                            "Loss tracking, weapon rules, and standings"
                            if allowed_weapons().len() != default_allowed_weapons().len() { " · restricted weapons" }
                            if !top_n_text().trim().is_empty() { " · results display configured" }
                        } }
                        div { class: "settings-advanced-body",
                    if type_() == "CHAIN_BREAKING" {
                        div { class: "mb-3 form-check",
                            input {
                                class: "form-check-input",
                                r#type: "checkbox",
                                id: "track-chain-losses-toggle",
                                checked: track_chain_losses(),
                                onchange: move |evt| track_chain_losses.set(evt.checked()),
                            }
                            label {
                                class: "form-check-label",
                                r#for: "track-chain-losses-toggle",
                                "Track losses (chains)"
                            }
                        }
                        div { class: "mb-3 form-check",
                            input {
                                class: "form-check-input",
                                r#type: "checkbox",
                                id: "track-break-losses-toggle", aria_describedby: "track-break-losses-toggle-help",
                                checked: track_break_losses(),
                                onchange: move |evt| track_break_losses.set(evt.checked()),
                            }
                            label {
                                class: "form-check-label",
                                r#for: "track-break-losses-toggle",
                                "Track losses (breakers)"
                            }
                            div { id: "track-break-losses-toggle-help", class: "form-text",
                                "When unchecked, only wins are entered and ranked for that group."
                            }
                        }
                    } else {
                        div { class: "mb-3 form-check",
                            input {
                                class: "form-check-input",
                                r#type: "checkbox",
                                id: "track-losses-toggle", aria_describedby: "track-losses-toggle-help",
                                checked: track_losses(),
                                onchange: move |evt| {
                                    let checked = evt.checked();
                                    track_losses.set(checked);
                                    if !checked {
                                        track_matchups.set(false);
                                    }
                                },
                            }
                            label {
                                class: "form-check-label",
                                r#for: "track-losses-toggle",
                                "Track losses"
                            }
                            div { id: "track-losses-toggle-help", class: "form-text",
                                "When checked, standings rank by win percentage. Otherwise, only wins are entered and ranked."
                            }
                        }
                        if track_losses() {
                            div { class: "mb-3 form-check ms-4",
                                input {
                                    class: "form-check-input",
                                    r#type: "checkbox",
                                    id: "track-matchups-toggle", aria_describedby: "track-matchups-toggle-help",
                                    checked: track_matchups(),
                                    onchange: move |evt| track_matchups.set(evt.checked()),
                                }
                                label {
                                    class: "form-check-label",
                                    r#for: "track-matchups-toggle",
                                    "Track matchups"
                                }
                                div { id: "track-matchups-toggle-help", class: "form-text",
                                    "Score two people at a time. A win for one is a loss for the other."
                                }
                            }
                        }
                    }
                    if finalized() {
                        div { class: "alert alert-secondary", "Results have been finalized." }
                    }
                    AllowedWeaponsCheckboxes {
                        selected: allowed_weapons,
                        disabled_warning_count,
                    }
                    div { class: "mb-3",
                        label { class: "form-label", r#for: "sidecomp-top-n", "Public result limit" }
                        input {
                            id: "sidecomp-top-n",
                            aria_describedby: "sidecomp-top-n-help",
                            class: "form-control",
                            r#type: "number",
                            min: "0",
                            placeholder: "Leave blank to show all",
                            value: "{top_n_text}",
                            oninput: move |evt| top_n_text.set(evt.value()),
                        }
                        div { id: "sidecomp-top-n-help", class: "form-text",
                            "Applies to All weapons; a weapon filter shows its top player. Leave blank to publish everyone, or enter 0 to hide results."
                        }
                    }
                        }
                    }
                    if let Some(err) = error() {
                        div { class: "alert alert-danger", role: "alert", "{err}" }
                    }
                    div { class: "form-actions",
                        button { class: "btn btn-primary", r#type: "submit", disabled: submitting(), if submitting() { "Saving…" } else { "Save" } }
                    }
                    }
                }
                hr {}
                super::tournament_home::SideCompDeleteAction {
                    url: url_for_delete.clone(), comp_id, name: name(), full_label: true, disabled: submitting(),
                    on_blocked: move |blocked| deleting.set(blocked),
                    on_deleted: move |_| { navigator.push(Route::TournamentHomeWithTab { url: url_for_delete.clone(), tab: "sidecomps".into() }); },
                }
                    },
                }
            }
        }
    }
}
