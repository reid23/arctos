use crate::Route;
use crate::api;
use crate::components::ToolPageHeader;
use crate::url_slug::{URL_SLUG_ALLOWED_HINT, is_valid_url_slug};
use dioxus::prelude::*;

#[derive(Clone, PartialEq)]
struct TeamDraft {
    id: u32,
    name: String,
    shortname: String,
}

#[component]
pub fn NewHistoricalTournament() -> Element {
    let navigator = use_navigator();
    let mut name = use_signal(String::new);
    let mut url_slug = use_signal(String::new);
    let mut location = use_signal(String::new);
    let mut start_date = use_signal(String::new);
    let mut end_date = use_signal(String::new);
    let mut next_team_id = use_signal(|| 1u32);
    let mut teams = use_signal(|| {
        vec![TeamDraft {
            id: 0,
            name: String::new(),
            shortname: String::new(),
        }]
    });
    let mut error = use_signal(|| None::<String>);
    let mut submitting = use_signal(|| false);

    let team_count = teams
        .read()
        .iter()
        .filter(|team| !team.name.trim().is_empty())
        .count();
    let user = crate::auth::use_account();

    if user.read().is_none() {
        return rsx! { div { class: "loading-state", role: "status", "Checking your account…" } };
    }
    if matches!(
        user.read().as_ref(),
        Some(Err(api::AccountError::Unauthenticated))
    ) {
        return rsx! {
            div { class: "row justify-content-center form-page",
                div { class: "col-12 ordinary-form-rail",
                    div { class: "edit-section",
                        div { class: "edit-section-body",
                            ToolPageHeader {
                                title: "Log past Event", standalone: true,
                                back: Some((Route::Index {}, "Back to Events".into())),
                            }
                            p { "Log in to record a tournament that already happened." }
                            a { class: "btn btn-primary", href: "/login?next=/new-historical-tournament", "Log in to continue" }
                        }
                    }
                }
            }
        };
    }
    if matches!(
        user.read().as_ref(),
        Some(Err(api::AccountError::Unavailable))
    ) {
        return rsx! {};
    }

    rsx! {
        div { class: "row justify-content-center form-page",
            div { class: "col-12 ordinary-form-rail",
                div { class: "edit-section",
                    ToolPageHeader {
                        title: "Log past Event",
                        standalone: true,
                        back: Some((Route::Index {}, "Back to Events".into())),
                        description: "Enter where and when it happened and who attended. You'll enter matches and scores on the next page. The record stays hidden until you publish it.",
                    }
                    div { class: "edit-section-body",
                        if let Some(ref err) = error() {
                            div { class: "alert alert-danger mb-3", "{err}" }
                        }
                        form {
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                if submitting() {
                                    return;
                                }
                                error.set(None);
                                let slug = url_slug().trim().to_string();
                                if name().trim().is_empty() || slug.is_empty() {
                                    error.set(Some("Name and URL slug are required.".to_string()));
                                    return;
                                }
                                if !is_valid_url_slug(&slug) {
                                    error.set(Some(URL_SLUG_ALLOWED_HINT.to_string()));
                                    return;
                                }
                                if start_date().is_empty() {
                                    error.set(Some("Start date is required.".to_string()));
                                    return;
                                }
                                let entries: Vec<(String, String)> = teams
                                    .read()
                                    .iter()
                                    .filter_map(|team| {
                                        let team_name = team.name.trim().to_string();
                                        let shortname = team.shortname.trim().to_string();
                                        if team_name.is_empty() && shortname.is_empty() {
                                            None
                                        } else {
                                            Some((team_name, shortname))
                                        }
                                    })
                                    .collect();
                                if entries.iter().any(|(team_name, _)| team_name.is_empty()) {
                                    error.set(Some("A short name needs a team name.".to_string()));
                                    return;
                                }
                                submitting.set(true);
                                let nav = navigator.clone();
                                spawn(async move {
                                    match api::create_historical_tournament(
                                        name().trim(),
                                        &slug,
                                        location().trim(),
                                        &start_date(),
                                        &end_date(),
                                        &entries,
                                    )
                                    .await
                                    {
                                        Ok(response) => match response.url {
                                            Some(url) => {
                                                nav.push(Route::HistoricalRecords { url });
                                            }
                                            None => error.set(Some(response.error.unwrap_or_else(|| "Creation failed.".to_string()))),
                                        },
                                        Err(e) => error.set(Some(e)),
                                    }
                                    submitting.set(false);
                                });
                            },
                            div { class: "mb-3",
                                label { r#for: "historical-name", class: "form-label", "Tournament name" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "historical-name",
                                    required: true,
                                    autofocus: true,
                                    value: "{name}",
                                    oninput: move |ev| name.set(ev.value()),
                                }
                            }
                            div { class: "mb-3",
                                label { r#for: "historical-url", class: "form-label", "URL slug" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "historical-url",
                                    required: true,
                                    pattern: r"[A-Za-z0-9\.\_\~\-]+",
                                    title: URL_SLUG_ALLOWED_HINT,
                                    value: "{url_slug}",
                                    oninput: move |ev| url_slug.set(ev.value()),
                                }
                                div { class: "form-text", "Used in the address, e.g. /{url_slug}. {URL_SLUG_ALLOWED_HINT}" }
                            }
                            div { class: "mb-3",
                                label { r#for: "historical-location", class: "form-label", "Location" }
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    id: "historical-location",
                                    placeholder: "e.g. Hearst North Field, Berkeley, CA",
                                    value: "{location}",
                                    oninput: move |ev| location.set(ev.value()),
                                }
                            }
                            div { class: "row g-3 mb-3",
                                div { class: "col-sm-6",
                                    label { r#for: "historical-start", class: "form-label", "Start date" }
                                    input {
                                        r#type: "date",
                                        class: "form-control",
                                        id: "historical-start",
                                        required: true,
                                        value: "{start_date}",
                                        oninput: move |ev| start_date.set(ev.value()),
                                    }
                                }
                                div { class: "col-sm-6",
                                    label { r#for: "historical-end", class: "form-label", "End date" }
                                    input {
                                        r#type: "date",
                                        class: "form-control",
                                        id: "historical-end",
                                        min: "{start_date}",
                                        value: "{end_date}",
                                        oninput: move |ev| end_date.set(ev.value()),
                                    }
                                    div { class: "form-text", "Leave blank for a one-day tournament." }
                                }
                            }
                            div { class: "mb-3",
                                label { class: "form-label",
                                    "Teams that attended "
                                    span { class: "text-muted", "({team_count})" }
                                }
                                for team in teams.read().iter().cloned() {
                                    {
                                        let team_name = team.name.clone();
                                        let team_shortname = team.shortname.clone();
                                        let name_id = team.id;
                                        let shortname_id = team.id;
                                        let remove_id = team.id;
                                        rsx! {
                                            div { class: "input-group mb-2", key: "{team.id}",
                                                input {
                                                    class: "form-control",
                                                    r#type: "text",
                                                    placeholder: "Team name",
                                                    value: "{team_name}",
                                                    oninput: move |ev| {
                                                        let value = ev.value();
                                                        if let Some(entry) = teams.write().iter_mut().find(|entry| entry.id == name_id) {
                                                            entry.name = value;
                                                        }
                                                    },
                                                }
                                                input {
                                                    class: "form-control",
                                                    r#type: "text",
                                                    placeholder: "Short name",
                                                    maxlength: "8",
                                                    style: "max-width: 8rem;",
                                                    value: "{team_shortname}",
                                                    oninput: move |ev| {
                                                        let value = ev.value();
                                                        if let Some(entry) = teams.write().iter_mut().find(|entry| entry.id == shortname_id) {
                                                            entry.shortname = value;
                                                        }
                                                    },
                                                }
                                                button {
                                                    class: "btn btn-outline-secondary",
                                                    r#type: "button",
                                                    title: "Remove team",
                                                    onclick: move |_| {
                                                        teams.write().retain(|entry| entry.id != remove_id);
                                                    },
                                                    i { class: "fas fa-times" }
                                                }
                                            }
                                        }
                                    }
                                }
                                button {
                                    class: "btn btn-sm btn-outline-secondary",
                                    r#type: "button",
                                    onclick: move |_| {
                                        let id = next_team_id();
                                        next_team_id.set(id + 1);
                                        teams.write().push(TeamDraft { id, name: String::new(), shortname: String::new() });
                                    },
                                    i { class: "fas fa-plus me-1" }
                                    "Add team"
                                }
                                div { class: "form-text",
                                    "Short names are optional. Teams are added as unclaimed placeholders. Invite each team's Arctos account from the next page; they see the invitation once you publish."
                                }
                            }
                            div { class: "d-grid",
                                button {
                                    r#type: "submit",
                                    class: "btn btn-primary",
                                    disabled: submitting(),
                                    if submitting() { "Creating..." } else { "Create and enter matches" }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
