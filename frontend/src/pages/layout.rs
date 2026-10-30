use crate::api;
use crate::appearance::{focus_control, use_appearance};
use crate::components::AppearanceMenu;
use crate::Route;
use dioxus::prelude::*;
use super::navigation::{main_link_current, main_section, use_client_navigation, MainSection};

// These ordinary event tools share their event's identity and navigation.
// Public schedule/results/bracket pages already supply this shell themselves;
// live match controls, scoring keypads, and overlays keep their dedicated space.
fn event_tool_shell(route: &Route) -> Option<(bool, String, String, Option<String>)> {
    let (league, url, tab, title) = match route {
        Route::TournamentSettings { url } => (false, url, "settings", Some("Settings")),
        Route::TournamentRegister { url } => (false, url, "registration", Some("Registration")),
        Route::Manage { url } => (false, url, "manage", Some("Registration management")),
        Route::ManageFootage { url } => (false, url, "footage", Some("Footage")),
        Route::Invitations { url } => (false, url, "roster", Some("Manage roster")),
        Route::ScheduleEdit { url } => (false, url, "schedule-edit", Some("Edit schedule")),
        Route::LegacyBracket { url } => (false, url, "legacy-bracket", Some("Legacy bracket")),
        Route::SideCompNew { url }
        | Route::SideCompDetail { url, .. }
        | Route::SideCompEdit { url, .. }
        | Route::SideCompRegisterAsTo { url, .. }
        | Route::SideCompResults { url, .. }
        | Route::SideCompManageResults { url, .. } => (false, url, "sidecomps", None),
        Route::LeagueSettings { league_url } => (true, league_url, "settings", Some("Settings")),
        Route::LeagueRegister { league_url } => (true, league_url, "registration", Some("Registration")),
        Route::LeagueManage { league_url } => (true, league_url, "manage", Some("Registration management")),
        Route::LeagueInvitations { league_url } => (true, league_url, "roster", Some("Manage roster")),
        Route::LeagueNewTournament { league_url } => (true, league_url, "new-event", Some("Add event")),
        _ => return None,
    };
    Some((league, url.clone(), tab.to_string(), title.map(str::to_string)))
}

/// Signal bumped when login/logout succeeds so the layout refetches user.
pub fn use_auth_invalidate() -> Signal<u32> {
    use_context::<Signal<u32>>()
}

#[derive(Clone, Copy)]
struct PageBannerPreferences(Signal<std::collections::HashSet<String>>);

pub fn use_page_banner_preferences() -> Signal<std::collections::HashSet<String>> {
    use_context::<PageBannerPreferences>().0
}

#[cfg(target_arch = "wasm32")]
const PAGE_BANNERS_STORAGE_KEY: &str = "arctos.page-banners.hidden.v1";

fn stored_page_banners() -> std::collections::HashSet<String> {
    #[cfg(target_arch = "wasm32")]
    if let Some(storage) = web_sys::window().and_then(|window| window.local_storage().ok().flatten()) {
        if let Some(saved) = storage.get_item(PAGE_BANNERS_STORAGE_KEY).ok().flatten() {
            if let Ok(keys) = serde_json::from_str(&saved) {
                return keys;
            }
        }
        // Keep the previous Events banner preference when upgrading.
        if storage.get_item("arctos.introduction.hidden.v1").ok().flatten().as_deref() == Some("true") {
            return std::collections::HashSet::from(["events".to_string()]);
        }
    }
    std::collections::HashSet::new()
}

pub fn set_page_banner_hidden(
    mut preferences: Signal<std::collections::HashSet<String>>,
    key: &str,
    hidden: bool,
) {
    if hidden {
        preferences.write().insert(key.to_string());
    } else {
        preferences.write().remove(key);
    }
    #[cfg(target_arch = "wasm32")]
    if let Some(storage) = web_sys::window().and_then(|window| window.local_storage().ok().flatten()) {
        if let Ok(saved) = serde_json::to_string(&*preferences.read()) {
            let _ = storage.set_item(PAGE_BANNERS_STORAGE_KEY, &saved);
        }
    }
}

pub fn page_banner_key(route: &Route) -> Option<String> {
    match route {
        Route::Index {} | Route::IndexWithQuery { .. } => Some("events".into()),
        Route::LeaguesIndex {} | Route::LeaguesIndexWithQuery { .. } => Some("leagues".into()),
        Route::PlayersList {} | Route::PlayersListWithQuery { .. } => Some("players".into()),
        Route::TeamsList {} | Route::TeamsListWithQuery { .. } => Some("teams".into()),
        Route::Stones {} => Some("stones".into()),
        _ => None,
    }
}

/// Returns a short page name for the tab title: "Page Name | Arctos".
fn page_title_for_route(route: &Route) -> String {
    match route {
        Route::Index { .. } | Route::IndexWithQuery { .. } => "Events".into(),
        Route::NotFound { .. } => "Page not found".into(),
        Route::Login { .. } => "Login".into(),
        Route::Register { .. } => "Register".into(),
        Route::RegisterPlayer { .. } => "Register as Player".into(),
        Route::RegisterTeam { .. } => "Register as Team".into(),
        Route::GoogleChooseAccountType { .. } => "Choose Account Type".into(),
        Route::GoogleCompleteProfile { .. } => "Complete Profile".into(),
        Route::TournamentHome { url } | Route::TournamentHomeWithTab { url, .. } => {
            format!("{url}")
        }
        Route::Schedule { url, .. } => format!("{url} Schedule"),
        Route::ScheduleEdit { url } => format!("{url} Edit Schedule"),
        Route::Results { url } => format!("{url} Results"),
        Route::Bracket { url } => format!("{url} Bracket"),
        Route::LegacyBracket { url } => format!("{url} Legacy Bracket"),
        Route::TournamentSettings { url } => format!("{url} Settings"),
        Route::TournamentRegister { url } => format!("{url} Register"),
        Route::Manage { url } => format!("{url} Manage"),
        Route::ManageFootage { url } => format!("{url} Footage"),
        Route::Invitations { url } => format!("{url} Roster"),
        Route::StartMatch { .. } => format!("Start Match").into(),
        Route::RunMatch { .. } => format!("Run Match").into(),
        Route::FinalizeMatch { .. } => format!("Finalize Match").into(),
        Route::Scoreboard { .. } => format!("Scoreboard"),
        Route::ScoreboardRender { .. } => "Scoreboard Render".into(),
        Route::MatchPage { .. } => format!("Match"),
        Route::MatchPageById { .. } => format!("Match").into(),
        Route::AddInjury { .. } => "Add Injury".into(),
        Route::EditInjury { .. } => "Edit Injury".into(),
        Route::EditPlayerProfile { .. } => "Edit Profile".into(),
        Route::EditTeamProfile { .. } => "Edit Profile".into(),
        Route::PlayersList { .. } | Route::PlayersListWithQuery { .. } => "Players".into(),
        Route::PlayerProfilePage { id } | Route::PlayerProfilePageWithTab { id, .. } => format!("{id}"),
        Route::TeamsList { .. } | Route::TeamsListWithQuery { .. } => "Teams".into(),
        Route::TeamProfilePage { id } | Route::TeamProfilePageWithTab { id, .. } => format!("{id}"),
        Route::Stones { .. } => "Stones".into(),
        Route::About { .. } => "About".into(),
        Route::NewTournament { .. } => "Create an Event".into(),
        Route::LeaguesIndex { .. } | Route::LeaguesIndexWithQuery { .. } => "Leagues".into(),
        Route::CreateEvent { .. } => "Create event".into(),
        Route::NewLeague { .. } => "Create League".into(),
        Route::LeagueHome { league_url } | Route::LeagueHomeWithTab { league_url, .. } => league_url.clone(),
        Route::LeagueRegister { .. } => "Register".into(),
        Route::LeagueResults { .. } => "Results".into(),
        Route::LeagueSettings { .. } => "Settings".into(),
        Route::LeagueNewTournament { league_url } => format!("Add Event | {league_url}"),
        Route::LeagueManage { league_url } => format!("{league_url} Manage"),
        Route::LeagueInvitations { league_url } => format!("{league_url} Roster"),
        Route::Docs { .. } => "User Docs".into(),
        Route::Privacy { .. } => "Privacy Policy".into(),
        Route::Terms { .. } => "Terms".into(),
        Route::Thanks { .. } => "Thanks".into(),
        Route::License { .. } => "License".into(),
        Route::ArctosScheduleScript { .. } => "Arctos Schedule Script".into(),
        Route::DataAccessibilityGuide { .. } => "Data Accessibility Guide".into(),
        Route::Developers { .. } => "For Developers".into(),
        Route::DeveloperApps { .. } => "Your Apps".into(),
        Route::OAuthAuthorize { .. } => "Sign in with Arctos".into(),
        Route::SideCompNew { url } => format!("{url} New Side Competition"),
        Route::SideCompDetail { url, .. } => format!("{url} Side Competition"),
        Route::SideCompEdit { url, .. } => format!("{url} Edit Side Competition"),
        Route::SideCompRegisterAsTo { url, .. } => format!("{url} Side Competition Quick Register"),
        Route::SideCompEnterResults { url, .. } => format!("{url} Enter Results"),
        Route::SideCompResults { url, .. } => format!("{url} Side Comp Results"),
        Route::SideCompManageResults { url, .. } => format!("{url} Manage Side Comp Results"),
    }
}

const BOOTSTRAP: Asset = asset!("/assets/vendor/bootstrap.min.css");
const ICONS: Asset = asset!("/assets/vendor/fontawesome");
const BRAND_MARK_LIGHT: Asset = asset!("/assets/arctos-light.png");
const BRAND_MARK_DARK: Asset = asset!("/assets/arctos-dark.png");

#[component]
fn AccountAvatar(photo: Option<String>) -> Element {
    let mut failed = use_signal(|| false);
    rsx! {
        if let Some(path) = photo.filter(|_| !failed()) {
            img { class: "account-avatar", src: "{api::base_url()}/static/{path}", alt: "", onerror: move |_| failed.set(true) }
        } else {
            i { class: "fa-regular fa-user", aria_hidden: "true" }
        }
    }
}

#[component]
pub fn Layout() -> Element {
    let auth_version = use_signal(|| 0u32);
    provide_context(auth_version);
    let banner_preferences = use_signal(stored_page_banners);
    provide_context(PageBannerPreferences(banner_preferences));
    let user = use_resource(move || {
        let _ = auth_version();
        async move { api::me().await }
    });
    let checking_account = *user.state().read() == UseResourceState::Pending;
    provide_context(crate::auth::AccountContext(user));
    let mut account_open = use_signal(|| false);
    let mut utility_open = use_signal(|| false);
    let mut appearance_open = use_signal(|| false);
    let appearance = use_appearance();
    let palette = appearance.preferences().palette.id();
    let color_scheme = appearance.scheme();
    let mut logging_out = use_signal(|| false);
    let mut auth_error = use_signal(|| None::<String>);
    let navigator = use_navigator();
    let route = use_route::<Route>();
    let page_title = format!("{} | Arctos", page_title_for_route(&route));
    let current_section = main_section(&route);
    let event_shell = event_tool_shell(&route);
    let event_page = event_shell.is_some() || matches!(route, Route::TournamentHome { .. } | Route::TournamentHomeWithTab { .. }
        | Route::Schedule { .. } | Route::Results { .. } | Route::Bracket { .. }
        | Route::LeagueHome { .. } | Route::LeagueHomeWithTab { .. } | Route::LeagueResults { .. }
        | Route::PlayerProfilePage { .. } | Route::PlayerProfilePageWithTab { .. }
        | Route::TeamProfilePage { .. } | Route::TeamProfilePageWithTab { .. });
    let current_user = user
        .read()
        .as_ref()
        .and_then(|result| result.as_ref().ok())
        .cloned();
    let account_label = current_user.as_ref().map_or_else(String::new, |user| {
        format!("Account: {} ({})", user.name, user.user_type)
    });
    use_effect(use_reactive(&route, move |_| {
        account_open.set(false);
        utility_open.set(false);
        appearance_open.set(false);
    }));

    // Exact-size offline frames and OBS overlays intentionally have no app theme.
    if matches!(route, Route::ScoreboardRender { .. }) {
        return rsx! { Outlet::<Route> {} };
    }
    if matches!(route, Route::Scoreboard { .. }) {
        return rsx! {
            Title { "{page_title}" }
            link { rel: "stylesheet", href: BOOTSTRAP }
            link { rel: "stylesheet", href: "{ICONS}/css/all.min.css" }
            Outlet::<Route> {}
        };
    }
    // Keep the side competition keypad's full-height, purpose-built mobile layout.
    if matches!(route, Route::SideCompEnterResults { .. }) {
        return rsx! {
            Title { "{page_title}" }
            link { rel: "stylesheet", href: BOOTSTRAP }
            link { rel: "stylesheet", href: "{ICONS}/css/all.min.css" }
            style { {include_str!("../theme.css")} }
            div {
                class: "arctos-app arctos-scoring-surface",
                "data-palette": palette, "data-bs-theme": color_scheme,
                Outlet::<Route> {}
            }
        };
    }

    let brand_mark = match color_scheme {
        "dark" => BRAND_MARK_DARK,
        _ => BRAND_MARK_LIGHT,
    };
    rsx! {
        Title { "{page_title}" }
        link { rel: "stylesheet", href: BOOTSTRAP }
        link { rel: "stylesheet", href: "{ICONS}/css/all.min.css" }
        style { {include_str!("../theme.css")} }
        style { {include_str!("public_pages.css")} }
        style { {include_str!("operator.css")} }
        style { {include_str!("edit_forms.css")} }
        div {
            id: "app-layout",
            class: "arctos-app",
            "data-palette": palette, "data-bs-theme": color_scheme,
            onkeydown: move |event| {
                if event.key() == Key::Escape && (account_open() || utility_open() || appearance_open()) {
                    event.prevent_default();
                    let trigger = if account_open() { "account-trigger" } else { "utility-trigger" };
                    appearance_open.set(false);
                    utility_open.set(false);
                    account_open.set(false);
                    focus_control(trigger);
                }
            },
            crate::viewport::ViewportMetrics {}
            a { class: "skip-link", href: "#page-content", "Skip to content" }
            header { class: "app-header",
                div { class: "app-header-inner",
                    Link { to: Route::Index {}, class: "app-brand", aria_label: "Arctos events",
                        img { class: "brand-mark", src: brand_mark, alt: "" }
                        span { "Arctos" }
                    }
                    nav { class: "app-nav", aria_label: "Main navigation",
                        for (to, label, icon, section) in [
                            (Route::Index {}, "Events", "fa-calendar-days", MainSection::Events),
                            (Route::LeaguesIndex {}, "Leagues", "fa-trophy", MainSection::Leagues),
                            (Route::TeamsList {}, "Teams", "fa-users", MainSection::Teams),
                            (Route::PlayersList {}, "Players", "fa-user-group", MainSection::Players),
                            (Route::Stones {}, "Stones", "fa-stopwatch", MainSection::Stones),
                        ] {
                            a {
                                href: to.to_string(),
                                class: if current_section == Some(section) { "app-nav-link active" } else { "app-nav-link" },
                                aria_current: main_link_current(&route, &to, section),
                                onclick: move |event: MouseEvent| {
                                    if !use_client_navigation(event.trigger_button(), event.modifiers()) { return; }
                                    event.prevent_default();
                                    navigator.push(to.clone());
                                },
                                i { class: "fa-solid {icon}", aria_hidden: "true" }
                                span { "{label}" }
                            }
                        }
                    }
                    div { class: "app-header-actions",
                        if let Some(u) = current_user.as_ref() {
                            div { class: "account-menu",
                                button {
                                    class: "app-icon-button account-trigger",
                                    id: "account-trigger", r#type: "button",
                                    aria_label: "{account_label}", aria_expanded: "{account_open()}",
                                    aria_controls: "account-options",
                                    onclick: move |_| {
                                        utility_open.set(false);
                                        appearance_open.set(false);
                                        let was_open = account_open();
                                        account_open.set(!was_open);
                                    },
                                    AccountAvatar { key: "{u.profile_photo:?}", photo: u.profile_photo.clone() }
                                }
                                if account_open() {
                                    button {
                                        class: "account-dismiss", r#type: "button", tabindex: "-1",
                                        aria_label: "Dismiss account menu",
                                        onclick: move |_| {
                                            account_open.set(false);
                                            focus_control("account-trigger");
                                        }
                                    }
                                    div {
                                        class: "account-dropdown profile-dropdown", id: "account-options", tabindex: "-1",
                                        onmounted: move |_| focus_control("account-profile-link"),
                                        div { class: "account-identity",
                                            strong { "{u.name}" }
                                            span { class: "text-muted", "{u.user_type} account · @{u.id}" }
                                        }
                                        if u.user_type == "player" {
                                            Link { to: Route::PlayerProfilePage { id: u.id.clone() }, id: "account-profile-link", class: "dropdown-item", "My profile" }
                                        } else {
                                            Link { to: Route::TeamProfilePage { id: u.id.clone() }, id: "account-profile-link", class: "dropdown-item", "Team profile" }
                                        }
                                        Link { to: Route::DeveloperApps {}, class: "dropdown-item", "Developer apps" }
                                        button {
                                            r#type: "button", class: "dropdown-item", disabled: logging_out(),
                                            onclick: move |_| {
                                                if logging_out() { return; }
                                                logging_out.set(true);
                                                auth_error.set(None);
                                                let nav = navigator;
                                                let mut auth_version = auth_version;
                                                spawn(async move {
                                                    match api::logout().await {
                                                        Ok(_) => {
                                                            auth_version += 1;
                                                            account_open.set(false);
                                                            nav.push(Route::Index {});
                                                        }
                                                        Err(e) => auth_error.set(Some(e)),
                                                    }
                                                    logging_out.set(false);
                                                });
                                            },
                                            if logging_out() { "Logging out…" } else { "Log out" }
                                        }
                                    }
                                }
                            }
                        } else if matches!(user.read().as_ref(), Some(Err(api::AccountError::Unauthenticated))) {
                            Link { to: Route::Login {}, class: "btn btn-outline-secondary app-login", "Log in" }
                        } else {
                            button { r#type: "button", class: "app-icon-button", disabled: true,
                                aria_label: if checking_account { "Checking your account" } else { "Account unavailable" },
                                title: if checking_account { "Checking your account" } else { "Account unavailable" },
                                i { class: "fa-regular fa-user", aria_hidden: "true" }
                            }
                        }
                        div { class: "account-menu utility-menu",
                            button {
                                class: "app-icon-button account-trigger utility-trigger",
                                id: "utility-trigger", r#type: "button", aria_label: "More options",
                                aria_expanded: "{utility_open() || appearance_open()}",
                                aria_controls: if appearance_open() { "appearance-options" } else { "utility-options" },
                                onclick: move |_| {
                                    let was_open = utility_open() || appearance_open();
                                    account_open.set(false);
                                    appearance_open.set(false);
                                    utility_open.set(!was_open);
                                },
                                i { class: "fa-solid fa-ellipsis", aria_hidden: "true" }
                            }
                            if utility_open() || appearance_open() {
                                button {
                                    class: "account-dismiss utility-dismiss", r#type: "button", tabindex: "-1",
                                    aria_label: "Dismiss More options",
                                    onclick: move |_| {
                                        utility_open.set(false);
                                        appearance_open.set(false);
                                        focus_control("utility-trigger");
                                    }
                                }
                            }
                            if utility_open() {
                                div {
                                    class: "account-dropdown utility-dropdown", id: "utility-options", tabindex: "-1",
                                    button {
                                        id: "appearance-menu-item", r#type: "button", class: "dropdown-item",
                                        aria_haspopup: "dialog", aria_controls: "appearance-options",
                                        onclick: move |_| { utility_open.set(false); appearance_open.set(true); },
                                        "Appearance"
                                    }
                                    Link { to: Route::Docs {}, class: "dropdown-item", "Help & documentation" }
                                    Link { to: Route::About {}, class: "dropdown-item", "About Arctos" }
                                }
                            }
                            AppearanceMenu { open: appearance_open, utility_open, appearance }
                        }
                    }
                }
            }
            main { class: if event_page { "app-main event-full-width" } else { "container-wide app-main" }, id: "page-content", tabindex: "-1",
                crate::auth::AccountCheckNotice {}
                if let Some(error) = auth_error() {
                    div { class: "alert alert-danger", role: "alert", "Could not log out. {error}" }
                }
                SuspenseBoundary {
                    fallback: |_| rsx! { div { class: "loading-state", role: "status", "Loading…" } },
                    if let Some((league, url, tab, section_title)) = event_shell {
                        if league {
                            super::league_home::LeagueHomeContent {
                                key: "league-{url}", league_url: url, initial_tab: Some(tab), section_title,
                                content: Some(rsx! { div { class: "event-tool-content", Outlet::<Route> {} } }),
                            }
                        } else {
                            super::tournament_home::TournamentHomeContent {
                                key: "event-{url}", url, initial_tab: Some(tab), section_title,
                                content: Some(rsx! { div { class: "event-tool-content", Outlet::<Route> {} } }),
                            }
                        }
                    } else {
                        Outlet::<Route> {}
                    }
                }
            }
            footer { class: "app-footer",
                div { class: "container-wide",
                    div { class: "footer-top",
                        nav { class: "footer-links", aria_label: "Help and information",
                            Link { to: Route::About {}, "About" }
                            Link { to: Route::Docs {}, "Documentation" }
                            Link { to: Route::Developers {}, "Developers" }
                            Link { to: Route::Thanks {}, "Thanks" }
                            Link { to: Route::Privacy {}, "Privacy" }
                            Link { to: Route::Terms {}, "Terms" }
                            Link { to: Route::License {}, "License" }
                        }
                    }
                    div { class: "footer-bottom",
                        p { a { href: "https://github.com/reid23/arctos", "Source code" } }
                        p { "An independent project, not affiliated with CAJA or the US NJA." }
                    }
                }
            }
        }
    }
}
