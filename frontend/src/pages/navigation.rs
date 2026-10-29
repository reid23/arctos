//! Main-section context is independent of an entity's local tabs and URL spelling.

use crate::Route;
use dioxus::html::{input_data::MouseButton, Modifiers};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum MainSection {
    Events,
    Leagues,
    Teams,
    Players,
    Stones,
}

pub(super) fn main_section(route: &Route) -> Option<MainSection> {
    use MainSection::*;
    match route {
        Route::Index { .. }
        | Route::IndexWithQuery { .. }
        | Route::CreateEvent { .. }
        | Route::NewTournament { .. }
        | Route::TournamentHome { .. }
        | Route::TournamentHomeWithTab { .. }
        | Route::Schedule { .. }
        | Route::ScheduleEdit { .. }
        | Route::Results { .. }
        | Route::Bracket { .. }
        | Route::LegacyBracket { .. }
        | Route::TournamentSettings { .. }
        | Route::TournamentRegister { .. }
        | Route::Manage { .. }
        | Route::ManageFootage { .. }
        | Route::Invitations { .. }
        | Route::StartMatch { .. }
        | Route::RunMatch { .. }
        | Route::FinalizeMatch { .. }
        | Route::Scoreboard { .. }
        | Route::MatchPage { .. }
        | Route::MatchPageById { .. }
        | Route::SideCompNew { .. }
        | Route::SideCompDetail { .. }
        | Route::SideCompEdit { .. }
        | Route::SideCompRegisterAsTo { .. }
        | Route::SideCompEnterResults { .. }
        | Route::SideCompResults { .. }
        | Route::SideCompManageResults { .. } => Some(Events),
        Route::LeaguesIndex { .. }
        | Route::LeaguesIndexWithQuery { .. }
        | Route::NewLeague { .. }
        | Route::LeagueHome { .. }
        | Route::LeagueHomeWithTab { .. }
        | Route::LeagueRegister { .. }
        | Route::LeagueResults { .. }
        | Route::LeagueSettings { .. }
        | Route::LeagueNewTournament { .. }
        | Route::LeagueManage { .. }
        | Route::LeagueInvitations { .. } => Some(Leagues),
        Route::TeamsList { .. }
        | Route::TeamsListWithQuery { .. }
        | Route::TeamProfilePage { .. }
        | Route::TeamProfilePageWithTab { .. }
        | Route::EditTeamProfile { .. } => Some(Teams),
        Route::PlayersList { .. }
        | Route::PlayersListWithQuery { .. }
        | Route::PlayerProfilePage { .. }
        | Route::PlayerProfilePageWithTab { .. }
        | Route::EditPlayerProfile { .. }
        | Route::AddInjury { .. }
        | Route::EditInjury { .. } => Some(Players),
        Route::Stones { .. } => Some(Stones),
        Route::Login { .. }
        | Route::Register { .. }
        | Route::RegisterPlayer { .. }
        | Route::RegisterTeam { .. }
        | Route::GoogleChooseAccountType { .. }
        | Route::GoogleCompleteProfile { .. }
        | Route::ScoreboardRender { .. }
        | Route::About { .. }
        | Route::Thanks { .. }
        | Route::Docs { .. }
        | Route::Privacy { .. }
        | Route::Terms { .. }
        | Route::License { .. }
        | Route::ArctosScheduleScript { .. }
        | Route::DataAccessibilityGuide { .. }
        | Route::Developers { .. }
        | Route::DeveloperApps { .. }
        | Route::OAuthAuthorize { .. }
        | Route::NotFound { .. } => None,
    }
}

/// A child page belongs to a section, but the directory link is not its current page.
pub(super) fn main_link_current(
    route: &Route,
    destination: &Route,
    section: MainSection,
) -> Option<&'static str> {
    if main_section(route) != Some(section) {
        None
    } else if route == destination || matches!((route, destination),
        (Route::IndexWithQuery { .. }, Route::Index {})
        | (Route::LeaguesIndexWithQuery { .. }, Route::LeaguesIndex {})
        | (Route::PlayersListWithQuery { .. }, Route::PlayersList {})
        | (Route::TeamsListWithQuery { .. }, Route::TeamsList {})
    ) {
        Some("page")
    } else {
        Some("true")
    }
}

/// Match the router Link's native-anchor behavior, including keyboard activation
/// (browsers synthesize a primary click). Never intercept modified or middle clicks.
pub(super) fn use_client_navigation(button: Option<MouseButton>, modifiers: Modifiers) -> bool {
    button == Some(MouseButton::Primary) && modifiers.is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_context_survives_local_views_and_operations() {
        let url = "coast-round".to_string();
        let routes = [
            Route::Index {},
            Route::NewTournament {},
            Route::TournamentHome { url: url.clone() },
            Route::TournamentHomeWithTab {
                url: url.clone(),
                tab: "teams".into(),
            },
            Route::Schedule {
                url: url.clone(),
                view: "field".into(),
                team: "otters".into(),
                field: "2".into(),
            },
            Route::ScheduleEdit { url: url.clone() },
            Route::Results { url: url.clone() },
            Route::Bracket { url: url.clone() },
            Route::TournamentSettings { url: url.clone() },
            Route::TournamentRegister { url: url.clone() },
            Route::Manage { url: url.clone() },
            Route::ManageFootage { url: url.clone() },
            Route::Invitations { url: url.clone() },
            Route::SideCompEdit {
                url: url.clone(),
                comp_id: 4,
            },
            Route::SideCompResults {
                url: url.clone(),
                comp_id: 4,
            },
            Route::StartMatch {
                url: url.clone(),
                match_id: "match-1".into(),
            },
            Route::RunMatch {
                url: url.clone(),
                match_id: "match-1".into(),
            },
            Route::FinalizeMatch {
                url: url.clone(),
                match_id: "match-1".into(),
            },
            Route::MatchPageById {
                url,
                match_id: "match-1".into(),
            },
        ];
        for route in routes {
            assert_eq!(main_section(&route), Some(MainSection::Events), "{route}");
        }
    }

    #[test]
    fn league_operations_stay_in_leagues_including_attached_event_creation() {
        let league_url = "coast".to_string();
        for route in [
            Route::LeaguesIndex {},
            Route::NewLeague {},
            Route::LeagueHome {
                league_url: league_url.clone(),
            },
            Route::LeagueHomeWithTab {
                league_url: league_url.clone(),
                tab: "events".into(),
            },
            Route::LeagueResults {
                league_url: league_url.clone(),
            },
            Route::LeagueSettings {
                league_url: league_url.clone(),
            },
            Route::LeagueRegister {
                league_url: league_url.clone(),
            },
            Route::LeagueManage {
                league_url: league_url.clone(),
            },
            Route::LeagueInvitations {
                league_url: league_url.clone(),
            },
            Route::LeagueNewTournament { league_url },
        ] {
            assert_eq!(main_section(&route), Some(MainSection::Leagues), "{route}");
        }
    }

    #[test]
    fn profile_edits_and_injuries_keep_their_directory_context() {
        let player_id = "player-1".to_string();
        for route in [
            Route::PlayersList {},
            Route::PlayerProfilePage {
                id: player_id.clone(),
            },
            Route::PlayerProfilePageWithTab {
                id: player_id.clone(),
                tab: "history".into(),
            },
            Route::EditPlayerProfile {
                player_id: player_id.clone(),
            },
            Route::AddInjury {
                player_id: player_id.clone(),
            },
            Route::EditInjury {
                player_id,
                injury_id: 9,
            },
        ] {
            assert_eq!(main_section(&route), Some(MainSection::Players), "{route}");
        }
        for route in [
            Route::TeamsList {},
            Route::TeamProfilePage {
                id: "otters".into(),
            },
            Route::TeamProfilePageWithTab {
                id: "otters".into(),
                tab: "notes".into(),
            },
            Route::EditTeamProfile {
                team_id: "otters".into(),
            },
        ] {
            assert_eq!(main_section(&route), Some(MainSection::Teams), "{route}");
        }
        assert_eq!(main_section(&Route::Stones {}), Some(MainSection::Stones));
    }

    #[test]
    fn account_docs_and_explicit_not_found_routes_are_not_event_fallbacks() {
        for route in [
            Route::Login {},
            Route::Register {},
            Route::RegisterPlayer {},
            Route::RegisterTeam {},
            Route::GoogleChooseAccountType {},
            Route::GoogleCompleteProfile {},
            Route::About {},
            Route::Thanks {},
            Route::Docs {},
            Route::Privacy {},
            Route::Terms {},
            Route::License {},
            Route::ArctosScheduleScript {},
            Route::DataAccessibilityGuide {},
            Route::Developers {},
            Route::DeveloperApps {},
            Route::OAuthAuthorize {},
            Route::ScoreboardRender {},
            Route::NotFound {
                segments: vec!["missing".into(), "path".into()],
            },
        ] {
            assert_eq!(main_section(&route), None, "{route}");
        }
        // A dynamic event slug remains an event route until its data loads. This
        // presentation helper must not try to infer a server-side 404 or league.
        assert_eq!(
            main_section(&Route::TournamentHome {
                url: "unknown-slug".into()
            }),
            Some(MainSection::Events)
        );
    }

    #[test]
    fn current_section_does_not_claim_the_directory_is_the_current_page() {
        let destination = Route::Index {};
        assert_eq!(
            main_link_current(&destination, &destination, MainSection::Events),
            Some("page")
        );
        let detail = Route::TournamentHomeWithTab {
            url: "coast-round".into(),
            tab: "schedule".into(),
        };
        assert_eq!(
            main_link_current(&detail, &destination, MainSection::Events),
            Some("true")
        );
        assert_eq!(
            main_link_current(&detail, &Route::LeaguesIndex {}, MainSection::Leagues),
            None
        );
        assert_eq!(
            main_link_current(&Route::About {}, &destination, MainSection::Events),
            None
        );
    }

    #[test]
    fn filtered_directories_keep_current_page_context() {
        for (route, destination, section) in [
            (Route::IndexWithQuery { q: "Open".into(), view: "past".into(), page: "2".into() }, Route::Index {}, MainSection::Events),
            (Route::LeaguesIndexWithQuery { q: "Coast".into(), view: "past".into(), page: "2".into() }, Route::LeaguesIndex {}, MainSection::Leagues),
            (Route::PlayersListWithQuery { q: "Lee".into(), page: "2".into() }, Route::PlayersList {}, MainSection::Players),
            (Route::TeamsListWithQuery { q: "Otters".into() }, Route::TeamsList {}, MainSection::Teams),
        ] {
            assert_eq!(main_section(&route), Some(section));
            assert_eq!(main_link_current(&route, &destination, section), Some("page"));
        }
    }

    #[test]
    fn only_unmodified_primary_activation_uses_the_client_router() {
        assert!(use_client_navigation(
            Some(MouseButton::Primary),
            Modifiers::empty()
        ));
        for modifiers in [
            Modifiers::CONTROL,
            Modifiers::META,
            Modifiers::SHIFT,
            Modifiers::ALT,
            Modifiers::CONTROL | Modifiers::SHIFT,
        ] {
            assert!(!use_client_navigation(
                Some(MouseButton::Primary),
                modifiers
            ));
        }
        for button in [
            None,
            Some(MouseButton::Auxiliary),
            Some(MouseButton::Secondary),
            Some(MouseButton::Fourth),
        ] {
            assert!(!use_client_navigation(button, Modifiers::empty()));
        }
    }
}
