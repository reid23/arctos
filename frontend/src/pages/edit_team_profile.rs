use dioxus::prelude::*;
use super::profile_edit::ProfileEditPage;

#[component]
pub fn EditTeamProfile(team_id: String) -> Element {
    rsx! { ProfileEditPage { account_id: team_id, team: true } }
}
