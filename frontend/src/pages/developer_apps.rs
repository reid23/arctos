//! Self-service registration of OAuth apps ("Sign in with Arctos").

use crate::api;
use crate::types::OAuthClient;
use crate::Route;
use dioxus::prelude::*;

fn redirect_uri_lines(text: &str) -> Vec<String> {
    text.lines().map(str::trim).filter(|line| !line.is_empty()).map(String::from).collect()
}

#[component]
pub fn DeveloperApps() -> Element {
    let me = use_resource(api::me);
    let mut refresh = use_signal(|| 0u32);
    let clients = use_resource(move || async move {
        let _ = refresh();
        api::oauth_clients().await
    });
    let mut new_secret = use_signal(|| None::<(String, String)>);

    if let Some(Err(_)) = &*me.read() {
        return rsx! {
            div { class: "alert alert-info",
                "Log in to register apps. "
                Link { to: Route::Login {}, "Log in" }
            }
        };
    }

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
            on_created: move |secret: Option<(String, String)>| {
                refresh += 1;
                new_secret.set(secret);
            },
        }
    }
}

#[component]
fn AppCard(client: OAuthClient, on_change: EventHandler<()>, on_secret: EventHandler<(String, String)>) -> Element {
    let mut editing = use_signal(|| false);
    let name = use_signal(|| client.name.clone());
    let homepage = use_signal(|| client.homepage_url.clone().unwrap_or_default());
    let redirect_uris = use_signal(|| client.redirect_uris.join("\n"));
    let mut error = use_signal(|| None::<String>);
    let client_id = client.client_id.clone();

    let save = {
        let client_id = client_id.clone();
        move |_| {
            let client_id = client_id.clone();
            spawn(async move {
                match api::update_oauth_client(&client_id, &name(), &homepage(), &redirect_uri_lines(&redirect_uris()))
                    .await
                {
                    Ok(_) => {
                        error.set(None);
                        editing.set(false);
                        on_change.call(());
                    }
                    Err(message) => error.set(Some(message)),
                }
            });
        }
    };
    let rotate = {
        let client_id = client_id.clone();
        let app_name = client.name.clone();
        move |_| {
            let client_id = client_id.clone();
            let app_name = app_name.clone();
            spawn(async move {
                match api::rotate_oauth_client_secret(&client_id).await {
                    Ok(secret) => on_secret.call((app_name, secret)),
                    Err(message) => error.set(Some(message)),
                }
            });
        }
    };
    let delete = {
        let client_id = client_id.clone();
        move |_| {
            let client_id = client_id.clone();
            spawn(async move {
                match api::delete_oauth_client(&client_id).await {
                    Ok(()) => on_change.call(()),
                    Err(message) => error.set(Some(message)),
                }
            });
        }
    };

    rsx! {
        div { class: if editing() { "edit-section mb-3" } else { "card mb-3" },
            div { class: if editing() { "edit-section-heading d-flex justify-content-between align-items-center" } else { "card-header d-flex justify-content-between align-items-center" },
                h5 { class: "mb-0", "{client.name}" }
                span { class: "badge bg-secondary", if client.confidential { "Confidential" } else { "Public" } }
            }
            div { class: if editing() { "edit-section-body" } else { "card-body" },
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", "{message}" }
                }
                p { class: "mb-1", "Client ID: " code { "{client.client_id}" } }
                if editing() {
                    AppFields { name, homepage, redirect_uris }
                    div { class: "d-flex gap-2",
                        button { class: "btn btn-primary btn-sm", onclick: save, "Save" }
                        button { class: "btn btn-outline-secondary btn-sm", onclick: move |_| editing.set(false), "Cancel" }
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
                        button { class: "btn btn-outline-primary btn-sm", onclick: move |_| editing.set(true), "Edit" }
                        if client.confidential {
                            button { class: "btn btn-outline-warning btn-sm", onclick: rotate, "New secret" }
                        }
                        button { class: "btn btn-outline-danger btn-sm", onclick: delete, "Delete" }
                    }
                }
            }
        }
    }
}

#[component]
fn AppFields(name: Signal<String>, homepage: Signal<String>, redirect_uris: Signal<String>) -> Element {
    rsx! {
        div { class: "mb-2",
            label { class: "form-label", "Name" }
            input { class: "form-control", value: "{name}", oninput: move |e| name.set(e.value()) }
            div { class: "form-text", "Shown to people when they sign in." }
        }
        div { class: "mb-2",
            label { class: "form-label", "Homepage (optional)" }
            input { class: "form-control", value: "{homepage}", oninput: move |e| homepage.set(e.value()) }
        }
        div { class: "mb-2",
            label { class: "form-label", "Redirect URIs" }
            textarea {
                class: "form-control font-monospace",
                rows: "3",
                value: "{redirect_uris}",
                oninput: move |e| redirect_uris.set(e.value()),
                placeholder: "https://example.com/callback\nhttp://127.0.0.1/callback",
            }
            div { class: "form-text",
                "One per line. Use https, or http://127.0.0.1/… for apps running on the user's computer (any port is accepted)."
            }
        }
    }
}

#[component]
fn NewAppCard(on_created: EventHandler<Option<(String, String)>>) -> Element {
    let mut name = use_signal(String::new);
    let mut homepage = use_signal(String::new);
    let mut redirect_uris = use_signal(String::new);
    let mut confidential = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);

    rsx! {
        div { class: "edit-section mb-3",
            div { class: "edit-section-heading", h5 { class: "mb-0", "Register a new app" } }
            div { class: "edit-section-body",
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", "{message}" }
                }
                AppFields { name, homepage, redirect_uris }
                div { class: "mb-3",
                    div { class: "form-check",
                        input {
                            class: "form-check-input",
                            r#type: "radio",
                            id: "app-public",
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
                            id: "app-confidential",
                            checked: confidential(),
                            onchange: move |_| confidential.set(true),
                        }
                        label { class: "form-check-label", r#for: "app-confidential",
                            "Confidential: runs on a server you control and can keep a client secret."
                        }
                    }
                }
                button {
                    class: "btn btn-primary",
                    onclick: move |_| {
                        spawn(async move {
                            match api::create_oauth_client(&name(), &homepage(), &redirect_uri_lines(&redirect_uris()), confidential()).await {
                                Ok(created) => {
                                    error.set(None);
                                    name.set(String::new());
                                    homepage.set(String::new());
                                    redirect_uris.set(String::new());
                                    on_created.call(created.client_secret.map(|secret| (created.client.name, secret)));
                                }
                                Err(message) => error.set(Some(message)),
                            }
                        });
                    },
                    "Register app"
                }
            }
        }
    }
}
