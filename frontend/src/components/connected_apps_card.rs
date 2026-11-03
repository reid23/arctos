//! Apps the signed-in user has authorized via "Sign in with Arctos", for their own profile edit page.

use crate::api;
use crate::time_format;
use dioxus::prelude::*;

#[component]
pub fn ConnectedAppsCard() -> Element {
    let mut refresh = use_signal(|| 0u32);
    let apps = use_resource(move || async move {
        let _ = refresh();
        api::connected_apps().await
    });
    let mut error = use_signal(|| None::<String>);
    let mut pending = use_signal(std::collections::HashSet::<String>::new);
    let mut status = use_signal(|| None::<String>);

    rsx! {
        div { class: "edit-section data-section mt-3",
            div { class: "edit-section-heading",
                h5 { class: "mb-0", "Connected apps" }
            }
            div { class: "edit-section-body",
                p { class: "text-muted small", "Apps you've signed in to with Arctos. Only you can see this list." }
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", role: "alert", "{message}" }
                }
                if let Some(message) = status() { p { role: "status", "{message}" } }
                p { class: "small text-muted", "Disconnecting removes this app's access to your account. You can connect it again by signing in through the app." }
                match &*apps.read() {
                    None => rsx! { p { class: "mb-0", "Loading…" } },
                    Some(Err(message)) => rsx! { p { class: "text-danger mb-0", "{message}" } },
                    Some(Ok(apps)) if apps.is_empty() => rsx! { p { class: "mb-0", "No connected apps." } },
                    Some(Ok(apps)) => rsx! {
                        ul { class: "list-group list-group-flush",
                            for app in apps.iter().cloned() {
                                li { key: "{app.client_id}", class: "list-group-item data-list-row d-flex justify-content-between align-items-start gap-2",
                                    div {
                                        strong {
                                            if let Some(homepage) = app.homepage_url.as_ref() {
                                                a { href: "{homepage}", target: "_blank", rel: "noopener noreferrer", "{app.name}" }
                                            } else {
                                                "{app.name}"
                                            }
                                        }
                                        div { class: "small text-muted",
                                            "Public data"
                                            for scope in app.scopes.iter() {
                                                "; {scope.description.to_lowercase()}"
                                            }
                                        }
                                        if let Some(last_used) = app.last_used_at.as_deref() {
                                            div { class: "small text-muted", "Last used {time_format::format_match_display_local(last_used)}" }
                                        }
                                    }
                                    button {
                                        class: "btn btn-outline-danger btn-sm",
                                        disabled: pending().contains(&app.client_id),
                                        aria_label: format!("Disconnect {}", app.name),
                                        onclick: move |_| {
                                            if pending.peek().contains(&app.client_id) { return; }
                                            pending.write().insert(app.client_id.clone());
                                            error.set(None);
                                            status.set(None);
                                            let name = app.name.clone();
                                            let client_id = app.client_id.clone();
                                            spawn(async move {
                                                match api::disconnect_app(&client_id).await {
                                                    Ok(()) => {
                                                        error.set(None);
                                                        status.set(Some(format!("Disconnected {name}.")));
                                                        refresh += 1;
                                                    }
                                                    Err(message) => error.set(Some(format!("Could not disconnect {name}. {message}"))),
                                                }
                                                pending.write().remove(&client_id);
                                            });
                                        },
                                        "Disconnect"
                                    }
                                }
                            }
                        }
                    },
                }
            }
        }
    }
}
