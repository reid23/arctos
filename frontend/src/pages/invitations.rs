use dioxus::prelude::*;

#[component]
pub fn Invitations(url: String) -> Element {
    let account = crate::auth::use_account();
    let current = account.read();
    match current.as_ref() {
        Some(Ok(user)) if user.user_type == "team" => rsx! {
            super::TeamEventPanel { key: "{user.id}/events/{url}", team_id: user.id.clone(), kind: "events".to_string(), slug: url }
        },
        Some(Ok(_)) => rsx! { super::ManagedTeamActions { kind: "events".to_string(), slug: url } },
        Some(Err(crate::api::AccountError::Unavailable)) => rsx! {},
        Some(Err(_)) => rsx! { p { "Log in to manage a team roster." } },
        None => rsx! { p { role: "status", "Loading your account…" } },
    }
}
