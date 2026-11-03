//! Self-service registration of OAuth apps ("Sign in with Arctos").

use crate::api;
use crate::components::{Modal, ModalKind};
use crate::form_validation::{described_by, field_message, focus_after_render, FieldError};
use crate::types::OAuthClient;
use crate::Route;
use dioxus::prelude::*;

fn redirect_uri_lines(text: &str) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(String::from)
        .collect()
}

fn app_field_error(prefix: &str, name: &str, homepage: &str, redirects: &str) -> Option<FieldError> {
    let invalid = if name.trim().is_empty() {
        Some(("name", "Enter an app name."))
    } else if !homepage.trim().is_empty()
        && !matches!(homepage.trim().split_once(':').map(|(scheme, _)| scheme.to_ascii_lowercase()).as_deref(), Some("http" | "https")) {
        Some(("homepage", "Use an http or https URL for the homepage."))
    } else if redirect_uri_lines(redirects).is_empty() {
        Some(("redirects", "Add at least one redirect URI."))
    } else if redirect_uri_lines(redirects).len() > 10 {
        Some(("redirects", "Add no more than 10 redirect URIs."))
    } else { None };
    // The service remains authoritative for redirect URI protocol validation.
    invalid.map(|(field, message)| FieldError::new(format!("{prefix}-{field}"), message))
}

#[derive(Clone, Debug, PartialEq)]
struct AppCreateAttempt {
    before_ids: Vec<String>,
    name: String,
    homepage: String,
    redirect_uris: Vec<String>,
    confidential: bool,
    failure: String,
}

impl AppCreateAttempt {
    fn matches_new(&self, client: &OAuthClient) -> bool {
        !self.before_ids.contains(&client.client_id)
            && client.name == self.name.trim()
            && client.homepage_url.as_deref().unwrap_or_default() == self.homepage.trim()
            && client.redirect_uris == self.redirect_uris
            && client.confidential == self.confidential
    }
}

#[component]
pub fn DeveloperApps() -> Element {
    let me = crate::auth::use_account();
    let mut refresh = use_signal(|| 0u32);
    let clients = use_resource(move || async move {
        let _ = refresh();
        api::oauth_clients().await
    });
    let mut new_secret = use_signal(|| None::<(String, String)>);
    let create_pending = use_signal(|| false);

    if let Some(Err(api::AccountError::Unauthenticated)) = &*me.read() {
        return rsx! {
            div { class: "alert alert-info",
                "Log in to register apps. "
                Link { to: Route::Login {}, "Log in" }
            }
        };
    }

    if me.read().is_none() { return rsx! { p { role: "status", "Checking your account…" } }; }
    if matches!(me.read().as_ref(), Some(Err(api::AccountError::Unavailable))) { return rsx! {}; }

    rsx! {
        h1 { "Your apps" }
        p {
            "Register an app to let people sign in to it with their Arctos account and use the "
            Link { to: Route::Developers {}, "Arctos API" }
            "."
        }
        if let Some((name, secret)) = new_secret() {
            div { class: "alert alert-warning",
                strong { "Client secret for {name}: " }
                code { "{secret}" }
                div { class: "small mt-1", "Copy it now. Arctos only stores a hash, so it can't be shown again." }
                button { class: "btn btn-sm btn-outline-secondary mt-2", onclick: move |_| new_secret.set(None), "Done" }
            }
        }
        match &*clients.read() {
            None => rsx! { p { "Loading…" } },
            Some(Err(message)) => rsx! { div { class: "alert alert-danger", "{message}" } },
            Some(Ok(clients)) => rsx! {
                for client in clients.iter() {
                    AppCard {
                        key: "{client.client_id}",
                        client: client.clone(),
                        secret_pending: new_secret().is_some() || create_pending(),
                        on_change: move |_| refresh += 1,
                        on_secret: move |secret| new_secret.set(Some(secret)),
                    }
                }
                if clients.is_empty() {
                    p { class: "text-muted", "You haven't registered any apps yet." }
                }
            },
        }
        NewAppCard {
            secret_pending: new_secret().is_some(),
            pending: create_pending,
            on_created: move |secret: Option<(String, String)>| {
                refresh += 1;
                if secret.is_some() {
                    new_secret.set(secret);
                }
            },
        }
    }
}

#[component]
fn AppCard(
    client: OAuthClient,
    secret_pending: bool,
    on_change: EventHandler<()>,
    on_secret: EventHandler<(String, String)>,
) -> Element {
    let mut editing = use_signal(|| false);
    let mut name = use_signal(|| client.name.clone());
    let mut homepage = use_signal(|| client.homepage_url.clone().unwrap_or_default());
    let mut redirect_uris = use_signal(|| client.redirect_uris.join("\n"));
    let mut error = use_signal(|| None::<String>);
    let mut field_error = use_signal(|| None::<FieldError>);
    let feedback_id = format!("app-error-{}", client.client_id);
    let focus_id = feedback_id.clone();
    use_effect(move || { if error().is_some() { focus_after_render(focus_id.clone()); } });
    let mut pending = use_signal(|| false);
    let mut confirm = use_signal(|| None::<bool>); // true: rotate, false: delete
    let mut confirmation_error = use_signal(|| None::<String>);
    let client_id = client.client_id.clone();
    let original = client.clone();
    let dialog_client = client.clone();
    let confirm_id = format!("app-confirm-{}", client.client_id);
    let title_id = format!("app-confirm-title-{}", client.client_id);
    let description_id = format!("app-confirm-description-{}", client.client_id);
    let cancel_id = format!("app-confirm-cancel-{}", client.client_id);

    let save = {
        let client_id = client_id.clone();
        EventHandler::new(move |_| {
            if pending() { return; }
            if let Some(invalid) = app_field_error(&client_id, &name(), &homepage(), &redirect_uris()) {
                focus_after_render(invalid.field_id.clone());
                field_error.set(Some(invalid));
                return;
            }
            field_error.set(None);
            pending.set(true);
            error.set(None);
            let client_id = client_id.clone();
            let draft = (name(), homepage(), redirect_uri_lines(&redirect_uris()));
            spawn(async move {
                match api::update_oauth_client(&client_id, &draft.0, &draft.1, &draft.2).await {
                    Ok(_) => {
                        error.set(None);
                        editing.set(false);
                        on_change.call(());
                    }
                    Err(message) => error.set(Some(message)),
                }
                pending.set(false);
            });
        })
    };
    let perform = move |_| {
        if pending() {
            return;
        }
        let Some(rotate) = confirm() else {
            return;
        };
        pending.set(true);
        confirmation_error.set(None);
        let client = dialog_client.clone();
        spawn(async move {
            if rotate {
                match api::rotate_oauth_client_secret(&client.client_id).await {
                    Ok(secret) => {
                        on_secret.call((client.name, secret));
                        confirm.set(None);
                    }
                    Err(message) => confirmation_error.set(Some(format!("Could not confirm the new secret. The old secret may no longer work. Check your integration before choosing Replace secret again. {message}"))),
                }
            } else {
                match api::delete_oauth_client(&client.client_id).await {
                    Ok(()) => {
                        confirm.set(None);
                        on_change.call(());
                    }
                    Err(message) => {
                        // A lost delete response is safe to reconcile with the owner's list.
                        match api::oauth_clients().await {
                            Ok(clients)
                                if !clients.iter().any(|c| c.client_id == client.client_id) =>
                            {
                                confirm.set(None);
                                on_change.call(());
                            }
                            _ => confirmation_error.set(Some(message)),
                        }
                    }
                }
            }
            pending.set(false);
        });
    };

    rsx! {
        div { id: "app-client-{client.client_id}", class: if editing() { "edit-section mb-3" } else { "card mb-3" },
            div { class: if editing() { "edit-section-heading d-flex justify-content-between align-items-center" } else { "card-header d-flex justify-content-between align-items-center" },
                h2 { class: "h5 mb-0", "{client.name}" }
                span { class: "badge bg-secondary", if client.confidential { "Confidential" } else { "Public" } }
            }
            div { class: if editing() { "edit-section-body" } else { "card-body" },
                if let Some(message) = error() {
                    div { id: feedback_id, tabindex: "-1", class: "alert alert-danger py-2", role: "alert", "{message}" }
                }
                p { class: "mb-1", "Client ID: " code { "{client.client_id}" } }
                if editing() {
                    form { onsubmit: move |event| { event.prevent_default(); save.call(()); },
                        oninput: move |_| error.set(None),
                    AppFields { name, homepage, redirect_uris, field_error, prefix: client.client_id.clone(), disabled: pending() }
                    div { class: "d-flex gap-2",
                        button { r#type: "submit", class: "btn btn-primary btn-sm", disabled: pending(), if pending() { "Saving…" } else { "Save" } }
                        button { r#type: "button", class: "btn btn-outline-secondary btn-sm", disabled: pending(), onclick: move |_| {
                            name.set(original.name.clone());
                            homepage.set(original.homepage_url.clone().unwrap_or_default());
                            redirect_uris.set(original.redirect_uris.join("\n"));
                            error.set(None);
                            field_error.set(None);
                            editing.set(false);
                        }, "Cancel" }
                    }
                    }
                } else {
                    if let Some(homepage) = client.homepage_url.as_ref() {
                        p { class: "mb-1", "Homepage: " a { href: "{homepage}", target: "_blank", "{homepage}" } }
                    }
                    p { class: "mb-1", "Redirect URIs:" }
                    ul {
                        for uri in client.redirect_uris.iter() {
                            li { key: "{uri}", code { "{uri}" } }
                        }
                    }
                    div { class: "d-flex gap-2",
                        button { class: "btn btn-outline-primary btn-sm", disabled: pending(), onclick: move |_| editing.set(true), "Edit" }
                        if client.confidential {
                            button { class: "btn btn-outline-warning btn-sm", id: "app-secret-{client.client_id}", disabled: pending() || secret_pending, onclick: move |_| { confirmation_error.set(None); confirm.set(Some(true)); }, "New secret" }
                        }
                        button { class: "btn btn-outline-danger btn-sm", id: "app-delete-{client.client_id}", disabled: pending() || secret_pending, onclick: move |_| { confirmation_error.set(None); confirm.set(Some(false)); }, "Delete" }
                    }
                }
            }
        }
        if let Some(rotate) = confirm() {
            Modal {
                id: confirm_id,
                labelled_by: title_id.clone(),
                described_by: Some(description_id.clone()),
                initial_focus_id: Some(cancel_id.clone()),
                return_focus_id: Some(format!("app-{}-{}", if rotate { "secret" } else { "delete" }, client.client_id)),
                kind: ModalKind::AlertDialog,
                dismissible: !pending(),
                on_close: move |_| confirm.set(None),
                div { class: "modal-header",
                    h2 { class: "modal-title h5", id: title_id, tabindex: "-1", if rotate { "Replace secret for {client.name}?" } else { "Delete {client.name}?" } }
                }
                div { class: "modal-body",
                    p { id: description_id,
                        if rotate { "The current secret will stop working immediately. Update your integration with the new secret, which is shown only once." }
                        else { "This permanently deletes this app registration and its authorizations. People will no longer be able to use it to sign in with Arctos. This cannot be undone." }
                    }
                    if let Some(message) = confirmation_error() { p { class: "alert alert-danger", role: "alert", "{message}" } }
                }
                div { class: "modal-footer",
                    button { id: cancel_id, r#type: "button", class: "btn btn-secondary", disabled: pending(), onclick: move |_| confirm.set(None), "Cancel" }
                    button { r#type: "button", class: "btn btn-danger", disabled: pending(), onclick: perform,
                        if pending() { "Working…" } else if rotate { "Replace secret" } else { "Delete app" }
                    }
                }
            }
        }
    }
}

#[component]
fn AppFields(
    mut name: Signal<String>,
    mut homepage: Signal<String>,
    mut redirect_uris: Signal<String>,
    mut field_error: Signal<Option<FieldError>>,
    prefix: String,
    disabled: bool,
) -> Element {
    let name_id = format!("{prefix}-name");
    let homepage_id = format!("{prefix}-homepage");
    let redirects_id = format!("{prefix}-redirects");
    rsx! {
        div { class: "mb-2",
            label { class: "form-label", r#for: name_id.clone(), "Name" }
            input { id: name_id.clone(), disabled, required: true, class: "form-control", value: "{name}",
                aria_invalid: field_message(&field_error(), &name_id).is_some(),
                aria_describedby: described_by(&field_error(), &name_id, &format!("{name_id}-help")),
                oninput: move |e| { name.set(e.value()); field_error.set(None); } }
            div { id: "{name_id}-help", class: "form-text", "Shown to people when they sign in." }
            if let Some(message) = field_message(&field_error(), &name_id) { p { id: "{name_id}-error", class: "text-danger small mt-1", "{message}" } }
        }
        div { class: "mb-2",
            label { class: "form-label", r#for: homepage_id.clone(), "Homepage (optional)" }
            input { id: homepage_id.clone(), disabled, class: "form-control", value: "{homepage}",
                inputmode: "url", autocapitalize: "none", spellcheck: "false",
                aria_invalid: field_message(&field_error(), &homepage_id).is_some(),
                aria_describedby: described_by(&field_error(), &homepage_id, ""),
                oninput: move |e| { homepage.set(e.value()); field_error.set(None); } }
            if let Some(message) = field_message(&field_error(), &homepage_id) { p { id: "{homepage_id}-error", class: "text-danger small mt-1", "{message}" } }
        }
        div { class: "mb-2",
            label { class: "form-label", r#for: redirects_id.clone(), "Redirect URIs" }
            textarea { id: redirects_id.clone(), disabled, required: true,
                class: "form-control font-monospace", rows: "3", value: "{redirect_uris}",
                autocapitalize: "none", spellcheck: "false",
                aria_invalid: field_message(&field_error(), &redirects_id).is_some(),
                aria_describedby: described_by(&field_error(), &redirects_id, &format!("{redirects_id}-help")),
                oninput: move |e| { redirect_uris.set(e.value()); field_error.set(None); },
                placeholder: "https://example.com/callback\nhttp://127.0.0.1/callback",
            }
            div { id: "{redirects_id}-help", class: "form-text",
                "One per line, up to 10. Use https, an http loopback address (127.0.0.1, [::1], or localhost), or a private scheme such as com.example.app:/callback. Loopback ports may vary."
            }
            if let Some(message) = field_message(&field_error(), &redirects_id) { p { id: "{redirects_id}-error", class: "text-danger small mt-1", "{message}" } }
        }
    }
}

#[component]
fn NewAppCard(
    secret_pending: bool,
    mut pending: Signal<bool>,
    on_created: EventHandler<Option<(String, String)>>,
) -> Element {
    let mut name = use_signal(String::new);
    let mut homepage = use_signal(String::new);
    let mut redirect_uris = use_signal(String::new);
    let mut confidential = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut field_error = use_signal(|| None::<FieldError>);
    use_effect(move || { if error().is_some() { focus_after_render("app-create-error"); } });
    let mut uncertain = use_signal(|| None::<AppCreateAttempt>);
    let mut recovered = use_signal(Vec::<OAuthClient>::new);

    let check_saved = EventHandler::new(move |_| {
        if pending() {
            return;
        }
        let Some(attempt) = uncertain() else {
            return;
        };
        pending.set(true);
        error.set(None);
        spawn(async move {
            match api::oauth_clients().await {
                Ok(clients) => {
                    let matches: Vec<_> = clients.into_iter()
                        .filter(|client| attempt.matches_new(client)).collect();
                    uncertain.set(None);
                    on_created.call(None);
                    if matches.is_empty() {
                        error.set(Some(format!("{} No new matching app was found. Your entries are kept; review your apps before registering again.", attempt.failure)));
                    } else {
                        // List responses never contain a secret. Replacing a lost secret
                        // must go through the app's existing explicit confirmation.
                        recovered.set(matches);
                        name.set(String::new());
                        homepage.set(String::new());
                        redirect_uris.set(String::new());
                    }
                }
                Err(message) => error.set(Some(format!("Registration could not be confirmed, and the app list could not be checked. Your entries are kept. Check saved apps before trying to register again. {message}"))),
            }
            pending.set(false);
        });
    });

    let create = EventHandler::new(move |_| {
                        if pending() || secret_pending || uncertain().is_some() { return; }
                        if let Some(invalid) = app_field_error("new-app", &name(), &homepage(), &redirect_uris()) {
                            focus_after_render(invalid.field_id.clone());
                            field_error.set(Some(invalid));
                            return;
                        }
                        field_error.set(None);
                        pending.set(true);
                        error.set(None);
                        recovered.set(Vec::new());
                        let mut attempt = AppCreateAttempt {
                            before_ids: Vec::new(), name: name(), homepage: homepage(),
                            redirect_uris: redirect_uri_lines(&redirect_uris()), confidential: confidential(),
                            failure: String::new(),
                        };
                        spawn(async move {
                            // Fresh IDs distinguish this attempt from identical old apps.
                            match api::oauth_clients().await {
                                Ok(clients) => attempt.before_ids = clients.into_iter().map(|client| client.client_id).collect(),
                                Err(message) => {
                                    error.set(Some(format!("Could not check your apps. Nothing was submitted. Try again when the app list is available. {message}")));
                                    pending.set(false);
                                    return;
                                }
                            }
                            match api::create_oauth_client(&attempt.name, &attempt.homepage, &attempt.redirect_uris, attempt.confidential).await {
                                Ok(created) => {
                                    error.set(None);
                                    name.set(String::new());
                                    homepage.set(String::new());
                                    redirect_uris.set(String::new());
                                    on_created.call(created.client_secret.map(|secret| (created.client.name, secret)));
                                }
                                Err(message) => {
                                    attempt.failure = message;
                                    uncertain.set(Some(attempt));
                                    pending.set(false);
                                    check_saved.call(());
                                    return;
                                }
                            }
                            pending.set(false);
                        });
    });

    rsx! {
        div { class: "edit-section mb-3",
            div { class: "edit-section-heading", h2 { class: "h5 mb-0", "Register a new app" } }
            div { class: "edit-section-body",
                if let Some(message) = error() {
                    div { id: "app-create-error", tabindex: "-1", class: "alert alert-danger py-2", role: "alert", "{message}" }
                }
                if !recovered().is_empty() {
                    div { id: "app-create-recovery", class: "alert alert-warning", role: "status",
                        p { "Matching new app registrations were found. Nothing was registered again." }
                        ul { class: "mb-0",
                            for client in recovered() {
                                li { key: "{client.client_id}",
                                    strong { "{client.name}" }
                                    " was created. "
                                    if client.confidential {
                                        "Its one-time secret is unavailable because the response was lost. "
                                        a { href: "#app-secret-{client.client_id}", "Choose New secret for this app" }
                                        " to review and confirm a replacement."
                                    } else {
                                        "This public app does not use a client secret. "
                                        a { href: "#app-client-{client.client_id}", "Review app" }
                                    }
                                }
                            }
                        }
                    }
                }
                form { onsubmit: move |event| { event.prevent_default(); create.call(()); },
                    oninput: move |_| error.set(None),
                AppFields { name, homepage, redirect_uris, field_error, prefix: "new-app", disabled: pending() || uncertain().is_some() }
                fieldset { class: "mb-3",
                    legend { class: "form-label fs-6", "App type" }
                    div { class: "form-check",
                        input {
                            class: "form-check-input",
                            r#type: "radio",
                            id: "app-public", name: "app-kind", disabled: pending() || uncertain().is_some(),
                            checked: !confidential(),
                            onchange: move |_| confidential.set(false),
                        }
                        label { class: "form-check-label", r#for: "app-public",
                            "Public: runs on the user's device (desktop, mobile, or browser app) and can't keep a secret. Uses PKCE."
                        }
                    }
                    div { class: "form-check",
                        input {
                            class: "form-check-input",
                            r#type: "radio",
                            id: "app-confidential", name: "app-kind", disabled: pending() || uncertain().is_some(),
                            checked: confidential(),
                            onchange: move |_| confidential.set(true),
                        }
                        label { class: "form-check-label", r#for: "app-confidential",
                            "Confidential: runs on a server you control and can keep a client secret."
                        }
                    }
                }
                button {
                    r#type: "submit", class: "btn btn-primary",
                    disabled: pending() || secret_pending || uncertain().is_some(),
                    if pending() { "Checking…" } else { "Register app" }
                }
                if uncertain().is_some() {
                    button { r#type: "button", class: "btn btn-outline-secondary ms-2", disabled: pending(), onclick: move |_| check_saved.call(()), "Check saved apps" }
                }
                }
            }
        }
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::*;

    #[test]
    fn app_preflight_identifies_fields_and_leaves_redirect_protocols_to_service() {
        assert_eq!(app_field_error("new-app", " ", "", "").unwrap().field_id, "new-app-name");
        assert_eq!(app_field_error("edit", "App", "ftp://example.com", "https://example.com").unwrap().field_id, "edit-homepage");
        assert_eq!(app_field_error("edit", "App", "", "\n ").unwrap().field_id, "edit-redirects");
        assert!(app_field_error("edit", "App", "HTTPS://example.com", "com.example.app:/callback\nhttp://[::1]/callback\nhttp://localhost/callback").is_none());
        assert!(app_field_error("edit", "App", "", &vec!["https://example.com"; 10].join("\n")).is_none());
        assert_eq!(app_field_error("edit", "App", "", &vec!["https://example.com"; 11].join("\n")).unwrap().field_id, "edit-redirects");
    }

    fn attempt() -> AppCreateAttempt {
        AppCreateAttempt {
            before_ids: vec!["old".into()],
            name: "  Match recorder  ".into(),
            homepage: "  https://example.com  ".into(),
            redirect_uris: redirect_uri_lines(
                " https://example.com/callback\n\nhttp://127.0.0.1/callback ",
            ),
            confidential: true,
            failure: "response lost".into(),
        }
    }

    fn client(id: &str) -> OAuthClient {
        OAuthClient {
            client_id: id.into(),
            name: "Match recorder".into(),
            homepage_url: Some("https://example.com".into()),
            redirect_uris: vec![
                "https://example.com/callback".into(),
                "http://127.0.0.1/callback".into(),
            ],
            confidential: true,
            created_at: None,
        }
    }

    #[test]
    fn app_creation_reconciliation_excludes_existing_identical_app() {
        assert!(!attempt().matches_new(&client("old")));
        assert!(attempt().matches_new(&client("new")));
    }

    #[test]
    fn app_creation_reconciliation_requires_all_submitted_fields_and_kind() {
        let attempt = attempt();
        let mut changed = client("new");
        changed.name = "Another app".into();
        assert!(!attempt.matches_new(&changed));
        changed = client("new");
        changed.homepage_url = None;
        assert!(!attempt.matches_new(&changed));
        changed = client("new");
        changed.redirect_uris.pop();
        assert!(!attempt.matches_new(&changed));
        changed = client("new");
        changed.confidential = false;
        assert!(!attempt.matches_new(&changed));
    }

    #[test]
    fn app_creation_reconciliation_accepts_empty_homepage_and_keeps_all_matches() {
        let mut attempt = attempt();
        attempt.homepage = "  ".into();
        let mut first = client("new-one");
        first.homepage_url = None;
        let mut second = first.clone();
        second.client_id = "new-two".into();
        let clients = [client("old"), first, second];
        let found: Vec<_> = clients
            .iter()
            .filter(|client| attempt.matches_new(client))
            .map(|client| client.client_id.as_str())
            .collect();
        assert_eq!(found, ["new-one", "new-two"]);
    }
}
