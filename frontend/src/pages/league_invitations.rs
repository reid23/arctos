use dioxus::prelude::*;

#[component]
pub fn LeagueInvitations(league_url: String) -> Element {
    let account = crate::auth::use_account();
    let current = account.read();
    match current.as_ref() {
        Some(Ok(user)) if user.user_type == "team" => rsx! {
            super::TeamEventPanel { key: "{user.id}/leagues/{league_url}", team_id: user.id.clone(), kind: "leagues".to_string(), slug: league_url }
        },
        Some(Ok(_)) => {
            rsx! { super::ManagedTeamActions { kind: "leagues".to_string(), slug: league_url } }
        }
        Some(Err(crate::api::AccountError::Unavailable)) => rsx! {},
        Some(Err(_)) => rsx! { p { "Log in to manage a team roster." } },
        None => rsx! { p { role: "status", "Loading your account…" } },
    }
}
