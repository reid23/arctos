//! Voluntary license entry for event and league settings.

use super::Modal;
use super::event_header::EventLicenseStatus;
use crate::Route;
use crate::api;
use dioxus::prelude::*;

fn dollars(cents: u32) -> String {
    if cents % 100 == 0 {
        format!("${}", cents / 100)
    } else {
        format!("${}.{:02}", cents / 100, cents % 100)
    }
}

fn listed_price(kind: &str, reported: u32) -> u32 {
    if reported > 0 {
        reported
    } else if kind == "league" {
        1000
    } else {
        500
    }
}

fn coverage(teams: u32) -> String {
    if teams == 1 {
        " This license covers 1 team.".into()
    } else {
        format!(" This license covers {teams} teams.")
    }
}

fn license_covers(allowance: Option<u32>, registered: u32) -> bool {
    allowance.is_some_and(|teams| teams >= registered)
}

fn license_notice(valid: bool, allowance: Option<u32>, registered: u32, spaced: bool) -> Element {
    let class = match (valid, spaced) {
        (true, true) => "alert alert-success mb-3",
        (true, false) => "alert alert-success",
        (false, true) => "alert alert-warning mb-3",
        (false, false) => "alert alert-warning",
    };
    let teams = allowance.unwrap_or(0);
    let invalid = format!(
        "License invalid: your license covers {teams} teams, but there are {registered} teams registered."
    );
    rsx! {
        div {
            class: "{class}",
            role: if valid { "status" } else { "alert" },
            i {
                class: if valid { "fas fa-circle-check me-2" } else { "fas fa-exclamation-triangle me-2" },
                aria_hidden: "true",
            }
            if valid {
                "Thank you for supporting Arctos!"
                if let Some(covered) = allowance {
                    "{coverage(covered)}"
                }
            } else {
                "{invalid}"
            }
        }
    }
}

#[component]
pub fn LicenseKeySection(
    kind: String,
    target_url: String,
    price_cents: u32,
    licensed: bool,
    team_allowance: Option<u32>,
    registered_teams: u32,
    #[props(default)] inherited_league_url: Option<String>,
) -> Element {
    let price = dollars(listed_price(&kind, price_cents));
    let noun = if kind == "league" { "league" } else { "event" };
    let mut key_text = use_signal(String::new);
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut licensed_now = use_signal(move || licensed);
    let mut allowance_now = use_signal(move || team_allowance);
    let mut rendered_for = use_signal(|| target_url.clone());
    if rendered_for() != target_url {
        rendered_for.set(target_url.clone());
        key_text.set(String::new());
        error.set(None);
        licensed_now.set(licensed);
        allowance_now.set(team_allowance);
    }
    let mut help_open = use_signal(|| false);
    let license_status = try_use_context::<EventLicenseStatus>();
    let purchase = if kind == "league" {
        format!("You can buy a license to use Arctos for a league for {price} per registered team.")
    } else {
        format!(
            "You can buy a license to use Arctos on a per-event basis for {price} per registered team."
        )
    };
    let price_label = price.clone();
    let registered_teams = registered_teams;
    let covered = license_covers(allowance_now(), registered_teams);

    rsx! {
        div { class: "edit-section ordinary-form-rail mb-3",
            div { class: "edit-section-heading d-flex justify-content-between align-items-center",
                h3 { class: "h5 mb-0", "License" }
                button {
                    r#type: "button",
                    id: "license-help-button",
                    class: "btn btn-link btn-sm",
                    onclick: move |_| help_open.set(true),
                    "How licensing works"
                }
            }
            div { class: "edit-section-body",
                if let Some(league_url) = inherited_league_url.clone() {
                    if licensed_now() {
                        {license_notice(covered, allowance_now(), registered_teams, true)}
                    } else {
                        p { "A league license is {price_label.clone()} per registered team. Enter it in the league settings." }
                    }
                    Link { to: Route::LeagueSettings { league_url }, "Open league settings" }
                } else if licensed_now() {
                    {license_notice(covered, allowance_now(), registered_teams, false)}
                    button {
                        r#type: "button",
                        class: "btn btn-sm btn-outline-secondary mt-2",
                        disabled: busy(),
                        onclick: move |_| {
                            let message = format!("Remove this license from this {noun}? The key can then be entered on a different {noun}.");
                            let confirmed = web_sys::window()
                                .and_then(|window| window.confirm_with_message(&message).ok())
                                .unwrap_or(false);
                            if !confirmed || busy() { return; }
                            busy.set(true);
                            error.set(None);
                            let kind = kind.clone();
                            let target_url = target_url.clone();
                            spawn(async move {
                                match api::clear_license(&kind, &target_url).await {
                                    Ok(()) => {
                                        licensed_now.set(false);
                                        allowance_now.set(None);
                                        if let Some(EventLicenseStatus(mut status)) = license_status {
                                            status.set(false);
                                        }
                                    }
                                    Err(message) => error.set(Some(message)),
                                }
                                busy.set(false);
                            });
                        },
                        "Remove license"
                    }
                    if let Some(message) = error() {
                        div { class: "alert alert-danger mt-2 mb-0", role: "alert", "{message}" }
                    }
                } else {
                    form {
                        onsubmit: move |event| {
                            event.prevent_default();
                            if busy() { return; }
                            let entered = key_text().trim().to_string();
                            if entered.is_empty() {
                                error.set(Some("Enter a license key.".into()));
                                return;
                            }
                            busy.set(true);
                            error.set(None);
                            let kind = kind.clone();
                            let target_url = target_url.clone();
                            spawn(async move {
                                match api::redeem_license(&kind, &target_url, &entered).await {
                                    Ok(teams) => {
                                        key_text.set(String::new());
                                        allowance_now.set(Some(teams));
                                        licensed_now.set(true);
                                        if let Some(EventLicenseStatus(mut status)) = license_status {
                                            status.set(teams >= registered_teams);
                                        }
                                    }
                                    Err(message) => error.set(Some(message)),
                                }
                                busy.set(false);
                            });
                        },
                        div { class: "mb-3",
                            label { r#for: "license-key", class: "form-label", "License key" }
                            input {
                                r#type: "text",
                                class: "form-control",
                                id: "license-key",
                                autocomplete: "off",
                                spellcheck: "false",
                                value: "{key_text}",
                                disabled: busy(),
                                aria_invalid: error().is_some(),
                                aria_describedby: "license-key-help",
                                oninput: move |event| key_text.set(event.value()),
                            }
                        }
                        if let Some(message) = error() {
                            div { class: "alert alert-danger", role: "alert", "{message}" }
                        }
                        button { r#type: "submit", class: "btn btn-primary", disabled: busy(), "Apply license" }
                    }
                }
            }
        }
        if help_open() {
            Modal {
                id: "license-help-dialog",
                labelled_by: "license-help-title",
                return_focus_id: Some("license-help-button".to_string()),
                on_close: move |_| help_open.set(false),
                div { class: "modal-header",
                    h2 { id: "license-help-title", class: "modal-title h5", tabindex: "-1", "Buy a License" }
                    button {
                        r#type: "button",
                        class: "btn-close",
                        aria_label: "Close",
                        onclick: move |_| help_open.set(false),
                    }
                }
                div { class: "modal-body",
                    p { "{purchase}" }
                    p {
                        "As we're committed to not adding paywalls, this purchase will "
                        em { "not" }
                        " grant you any additional features in Arctos. We rely on users like you to support Arctos's ongoing development and hosting costs."
                    }
                    p { class: "mb-0",
                        Link { to: Route::Support {}, "Learn more." }
                    }
                }
                div { class: "modal-footer",
                    button {
                        r#type: "button",
                        class: "btn btn-secondary",
                        onclick: move |_| help_open.set(false),
                        "Close"
                    }
                }
            }
        }
    }
}
