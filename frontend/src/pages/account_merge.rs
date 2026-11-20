//! Merging another account into the signed-in one: sign in to it, review what
//! moves and any conflicts, then confirm.

use crate::api;
use crate::form_validation::focus_after_render;
use crate::types::{AccountMergePreview, User};
use crate::Route;
use dioxus::prelude::*;

fn edit_profile_route(account: &User) -> Route {
    if account.user_type == "team" {
        Route::EditTeamProfile {
            team_id: account.id.clone(),
        }
    } else {
        Route::EditPlayerProfile {
            player_id: account.id.clone(),
        }
    }
}

#[component]
pub fn AccountMerge() -> Element {
    let me = crate::auth::use_account();
    let mut refresh = use_signal(|| 0u32);
    let status = use_resource(move || async move {
        let _ = refresh();
        api::account_merge_status().await
    });
    let merged_from = use_signal(|| None::<String>);

    if let Some(Err(api::AccountError::Unauthenticated)) = &*me.read() {
        return rsx! {
            div { class: "alert alert-info",
                "Log in to merge accounts. "
                Link { to: Route::Login {}, "Log in" }
            }
        };
    }
    let account = match &*me.read() {
        Some(Ok(account)) => account.clone(),
        Some(Err(_)) => return rsx! {},
        None => return rsx! { p { role: "status", "Checking your account…" } },
    };
    let kind = if account.user_type == "team" {
        "team"
    } else {
        "player"
    };

    let body = if let Some(source_id) = merged_from() {
        rsx! {
            div { class: "alert alert-success", role: "status",
                "Merged. Everything from {source_id} is now part of {account.id}."
            }
            Link { class: "btn btn-primary", to: edit_profile_route(&account), "Back to your profile" }
        }
    } else {
        match &*status.read() {
            None => rsx! { p { role: "status", "Loading…" } },
            Some(Err(error)) => {
                rsx! { div { class: "alert alert-danger", role: "alert", "{error}" } }
            }
            Some(Ok(current)) => match &current.preview {
                None => rsx! {
                    SignInToOtherAccount {
                        account: account.clone(),
                        kind: kind.to_string(),
                        sign_in_error: current.sign_in_error.clone(),
                        on_verified: move |_| refresh += 1,
                    }
                },
                Some(preview) => rsx! {
                    ReviewMerge {
                        preview: preview.clone(),
                        on_cancelled: move |_| refresh += 1,
                        on_merged: move |source_id: String| { let mut merged_from = merged_from; merged_from.set(Some(source_id)); },
                    }
                },
            },
        }
    };

    rsx! {
        h1 { "Merge with another account" }
        {body}
    }
}

#[component]
fn SignInToOtherAccount(
    account: User,
    kind: String,
    sign_in_error: Option<String>,
    on_verified: EventHandler<()>,
) -> Element {
    let mut username = use_signal(String::new);
    let mut password = use_signal(String::new);
    let mut error = use_signal(|| sign_in_error.clone());
    let mut pending = use_signal(|| false);
    use_effect(move || {
        if error().is_some() {
            focus_after_render("account-merge-sign-in-error");
        }
    });

    rsx! {
        p {
            "Merging moves everything from another {kind} account into "
            strong { "{account.name} ({account.id})" }
            ": registrations, match history, notes, organizer roles, and the rest. The other account is then deleted."
        }
        p { "First, sign in to the account you want to merge in. You'll stay signed in as {account.id}." }
        if let Some(message) = error() {
            div { id: "account-merge-sign-in-error", tabindex: "-1", class: "alert alert-danger", role: "alert", "{message}" }
        }
        form { class: "account-merge-sign-in mb-3",
            onsubmit: move |event| {
                event.prevent_default();
                if pending() { return; }
                if username().trim().is_empty() || password().is_empty() {
                    error.set(Some("Enter the other account's username and password.".to_string()));
                    return;
                }
                pending.set(true);
                error.set(None);
                let (entered_username, entered_password) = (username(), password());
                spawn(async move {
                    match api::verify_account_merge(entered_username.trim(), &entered_password).await {
                        Ok(status) => match status.sign_in_error {
                            Some(message) => error.set(Some(message)),
                            None => on_verified.call(()),
                        },
                        Err(message) => error.set(Some(message)),
                    }
                    pending.set(false);
                });
            },
            div { class: "mb-2",
                label { class: "form-label", r#for: "account-merge-username", "Other account's username" }
                input { id: "account-merge-username", class: "form-control", autocomplete: "off", required: true,
                    value: "{username}", disabled: pending(), oninput: move |event| username.set(event.value()) }
            }
            div { class: "mb-3",
                label { class: "form-label", r#for: "account-merge-password", "Other account's password" }
                input { id: "account-merge-password", class: "form-control", r#type: "password", autocomplete: "off", required: true,
                    value: "{password}", disabled: pending(), oninput: move |event| password.set(event.value()) }
            }
            div { class: "d-flex flex-wrap gap-2",
                button { class: "btn btn-primary", r#type: "submit", disabled: pending(),
                    if pending() { "Checking…" } else { "Continue" }
                }
                a { class: "btn btn-outline-secondary", href: "{api::google_account_merge_url()}", "Sign in to it with Google" }
                Link { class: "btn btn-link", to: edit_profile_route(&account), "Cancel" }
            }
        }
    }
}

#[component]
fn ReviewMerge(
    preview: AccountMergePreview,
    on_cancelled: EventHandler<()>,
    on_merged: EventHandler<String>,
) -> Element {
    let mut confirmation = use_signal(String::new);
    let mut error = use_signal(|| None::<String>);
    let mut pending = use_signal(|| false);
    use_effect(move || {
        if error().is_some() {
            focus_after_render("account-merge-error");
        }
    });
    let source = preview.source.id.clone();
    let target = preview.target.id.clone();
    let confirmed = confirmation().trim() == source;

    let cancel = move |_| {
        if pending() {
            return;
        }
        pending.set(true);
        spawn(async move {
            if let Err(message) = api::cancel_account_merge().await {
                error.set(Some(message));
            } else {
                on_cancelled.call(());
            }
            pending.set(false);
        });
    };

    rsx! {
        p {
            "You're merging "
            strong { "{preview.source.name} ({source})" }
            " into "
            strong { "{preview.target.name} ({target})" }
            "."
        }
        if let Some(message) = error() {
            div { id: "account-merge-error", tabindex: "-1", class: "alert alert-danger", role: "alert", "{message}" }
        }
        if !preview.conflicts.is_empty() {
            div { class: "alert alert-warning",
                h2 { class: "h5", "These accounts can't be merged yet" }
                ul { class: "mb-2",
                    for conflict in preview.conflicts.iter() {
                        li { "{conflict}" }
                    }
                }
                p { class: "mb-0",
                    "Resolve each one first, for example by cancelling one account's registration for that event, "
                    "or by asking the event's organizers for help. Then start again."
                }
            }
            button { class: "btn btn-secondary", r#type: "button", disabled: pending(), onclick: cancel, "Start over" }
        } else {
            h2 { class: "h5", "What will happen" }
            ul {
                if preview.moves.is_empty() {
                    li { "{source} has no history to move." }
                } else {
                    li { "This moves from {source} to {target}:"
                        ul {
                            for item in preview.moves.iter() {
                                li { "{item.count} {item.description}" }
                            }
                        }
                    }
                }
                li { "Profile details on {source} fill in anything left blank on {target}'s profile." }
                if preview.source_has_password && preview.target_has_password {
                    li { "{source}'s password stops working. Keep signing in with {target}'s password." }
                } else if preview.source_has_password {
                    li { "{source}'s password becomes {target}'s password." }
                }
                if preview.google_sign_in == "moves" {
                    li { "{source}'s Google sign-in moves to {target}." }
                } else if preview.google_sign_in == "removed" {
                    li { "{source}'s Google sign-in is removed. {target} keeps its own." }
                }
                li { "Apps signed in as {source} are signed out." }
                li { "{source} is deleted, and its username can never be used again." }
            }
            div { class: "alert alert-danger", "This can't be undone." }
            form {
                onsubmit: move |event| {
                    event.prevent_default();
                    if pending() || !confirmed { return; }
                    pending.set(true);
                    error.set(None);
                    let source = source.clone();
                    spawn(async move {
                        match api::merge_account(&source).await {
                            Ok(()) => on_merged.call(source),
                            Err(message) => error.set(Some(message)),
                        }
                        pending.set(false);
                    });
                },
                div { class: "mb-3",
                    label { class: "form-label", r#for: "account-merge-confirm", "To confirm, type {source}" }
                    input { id: "account-merge-confirm", class: "form-control", autocomplete: "off",
                        value: "{confirmation}", disabled: pending(), oninput: move |event| confirmation.set(event.value()) }
                }
                div { class: "d-flex flex-wrap gap-2",
                    button { class: "btn btn-danger", r#type: "submit", disabled: pending() || !confirmed,
                        if pending() { "Merging…" } else { "Merge {source} into {target}" }
                    }
                    button { class: "btn btn-secondary", r#type: "button", disabled: pending(), onclick: cancel, "Cancel" }
                }
            }
        }
    }
}
