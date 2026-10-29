use dioxus::prelude::*;
use super::profile_edit::ProfileEditPage;

#[component]
pub fn EditPlayerProfile(player_id: String) -> Element {
    rsx! { ProfileEditPage { account_id: player_id, team: false } }
}
