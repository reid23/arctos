#![allow(non_snake_case)]

mod api;
mod appearance;
mod auth;
mod components;
mod display;
mod form_validation;
mod pages;
mod time_format;
mod url_slug;
mod viewport;

mod stones_filter;
mod time_sync;
mod types;

use dioxus::prelude::*;
use pages::*;

#[derive(Routable, Clone, PartialEq)]
#[rustfmt::skip]
enum Route {
    #[layout(Layout)]
    #[route("/?:q&:view")]
    IndexWithQuery { q: String, view: String },

    #[route("/")]
    Index {},

    #[route("/login")]
    Login {},

    #[route("/register")]
    Register {},

    #[route("/register/player")]
    RegisterPlayer {},

    #[route("/register/team")]
    RegisterTeam {},

    #[route("/auth/google/choose-account-type")]
    GoogleChooseAccountType {},

    #[route("/auth/google/complete-profile")]
    GoogleCompleteProfile {},

    #[route("/leagues?:q&:view")]
    LeaguesIndexWithQuery { q: String, view: String },

    #[route("/leagues")]
    LeaguesIndex {},

    #[route("/create-event")]
    CreateEvent {},

    #[route("/leagues/new")]
    NewLeague {},

    #[route("/leagues/:league_url?:tab")]
    LeagueHomeWithTab { league_url: String, tab: String },

    #[route("/leagues/:league_url")]
    LeagueHome { league_url: String },

    #[route("/leagues/:league_url/register")]
    LeagueRegister { league_url: String },

    #[route("/leagues/:league_url/results")]
    LeagueResults { league_url: String },

    #[route("/leagues/:league_url/settings")]
    LeagueSettings { league_url: String },

    #[route("/leagues/:league_url/new-tournament")]
    LeagueNewTournament { league_url: String },

    #[route("/leagues/:league_url/manage")]
    LeagueManage { league_url: String },

    #[route("/leagues/:league_url/invitations")]
    LeagueInvitations { league_url: String },

    #[route("/:url?:tab")]
    TournamentHomeWithTab { url: String, tab: String },

    #[route("/:url")]
    TournamentHome { url: String },

    #[route("/:url/schedule/edit")]
    ScheduleEdit { url: String },

    #[route("/:url/schedule?:view&:team&:field")]
    Schedule { url: String, view: String, team: String, field: String },

    #[route("/:url/results")]
    Results { url: String },

    #[route("/:url/bracket")]
    Bracket { url: String },


    #[route("/:url/legacy-bracket")]
    LegacyBracket { url: String },

    #[route("/:url/settings")]
    TournamentSettings { url: String },

    #[route("/:url/register")]
    TournamentRegister { url: String },

    #[route("/:url/sidecomps/new")]
    SideCompNew { url: String },

    #[route("/:url/sidecomps/:comp_id")]
    SideCompDetail { url: String, comp_id: i32 },

    #[route("/:url/sidecomps/:comp_id/edit")]
    SideCompEdit { url: String, comp_id: i32 },

    #[route("/:url/sidecomps/:comp_id/register-player-as-to")]
    SideCompRegisterAsTo { url: String, comp_id: i32 },

    #[route("/:url/sidecomps/:comp_id/enter-results")]
    SideCompEnterResults { url: String, comp_id: i32 },

    #[route("/:url/sidecomps/:comp_id/results")]
    SideCompResults { url: String, comp_id: i32 },

    #[route("/:url/sidecomps/:comp_id/manage-results")]
    SideCompManageResults { url: String, comp_id: i32 },

    #[route("/:url/manage")]
    Manage { url: String },

    #[route("/:url/manage-footage")]
    ManageFootage { url: String },

    #[route("/:url/invitations")]
    Invitations { url: String },

    #[route("/:url/start-match/:match_id")]
    StartMatch { url: String, match_id: String },

    #[route("/:url/run-match/:match_id")]
    RunMatch { url: String, match_id: String },

    #[route("/:url/finalize-match/:match_id")]
    FinalizeMatch { url: String, match_id: String },

    #[route("/:url/scoreboard?:field")]
    Scoreboard { url: String, field: String },

    #[route("/:url/match")]
    MatchPage { url: String },

    #[route("/:url/match/:match_id")]
    MatchPageById { url: String, match_id: String },

    #[route("/players/:player_id/injuries/new")]
    AddInjury { player_id: String },

    #[route("/players/:player_id/injuries/:injury_id/edit")]
    EditInjury { player_id: String, injury_id: u32 },

    #[route("/players/:player_id/edit")]
    EditPlayerProfile { player_id: String },

    #[route("/teams/:team_id/edit")]
    EditTeamProfile { team_id: String },

    #[route("/players?:q&:page")]
    PlayersListWithQuery { q: String, page: String },

    #[route("/players")]
    PlayersList {},

    #[route("/players/:id?:tab")]
    PlayerProfilePageWithTab { id: String, tab: String },

    #[route("/players/:id")]
    PlayerProfilePage { id: String },

    #[route("/teams?:q")]
    TeamsListWithQuery { q: String },

    #[route("/teams")]
    TeamsList {},

    #[route("/teams/:id?:tab")]
    TeamProfilePageWithTab { id: String, tab: String },

    #[route("/teams/:id")]
    TeamProfilePage { id: String },

    #[route("/stones")]
    Stones {},

    #[route("/scoreboard-render")]
    ScoreboardRender {},

    #[route("/about")]
    About {},

    #[route("/new-tournament")]
    NewTournament {},

    #[route("/thanks")]
    Thanks {},

    #[route("/docs.md")]
    Docs {},

    #[route("/privacy-policy.md")]
    Privacy {},

    #[route("/terms.md")]
    Terms {},

    #[route("/license.md")]
    License {},

    #[route("/arctos-schedule-script.md")]
    ArctosScheduleScript {},

    #[route("/data-accessibility-guide.md")]
    DataAccessibilityGuide {},

    #[route("/developers.md")]
    Developers {},

    #[route("/developers/apps")]
    DeveloperApps {},

    #[route("/oauth/authorize")]
    OAuthAuthorize {},

    #[route("/:..segments")]
    NotFound { segments: Vec<String> },
}

/// The page's path and query string (without `?`) as it was loaded. The router
/// rewrites the address bar to its own form of the route before any page
/// renders, which drops query parameters the route doesn't declare and
/// percent-decodes the ones it does.
static INITIAL_LOCATION: std::sync::OnceLock<(String, String)> = std::sync::OnceLock::new();
static INITIAL_FRAGMENT: std::sync::OnceLock<(String, String)> = std::sync::OnceLock::new();

/// The query string the app was loaded with, if it was loaded at `path`.
pub fn initial_query(path: &str) -> Option<String> {
    let (initial_path, query) = INITIAL_LOCATION.get()?;
    (initial_path.trim_end_matches('/') == path.trim_end_matches('/')).then(|| query.clone())
}

/// The fragment before the router's initial normalization, including `#`.
pub(crate) fn initial_fragment(path: &str) -> Option<String> {
    let (initial_path, fragment) = INITIAL_FRAGMENT.get()?;
    (initial_path.trim_end_matches('/') == path.trim_end_matches('/') && !fragment.is_empty()).then(|| fragment.clone())
}

fn main() {
    #[cfg(target_arch = "wasm32")]
    if let Some(location) = web_sys::window().map(|window| window.location()) {
        let path = location.pathname().unwrap_or_default();
        let query = location.search().unwrap_or_default().trim_start_matches('?').to_string();
        let _ = INITIAL_FRAGMENT.set((path.clone(), location.hash().unwrap_or_default()));
        let _ = INITIAL_LOCATION.set((path, query));
    }
    dioxus::launch(|| {
        rsx! {
            document::Link { rel: "icon", r#type: "image/svg+xml", media: "(prefers-color-scheme: light)", href: asset!("/assets/arctos-light.png") }
            document::Link { rel: "icon", r#type: "image/svg+xml", media: "(prefers-color-scheme: dark)", href: asset!("/assets/arctos-dark.png") }
            Router::<Route> {}
        }
    });
}
