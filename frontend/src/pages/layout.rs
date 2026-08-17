use crate::api;
use crate::Route;
use dioxus::prelude::*;

/// Signal bumped when login/logout succeeds so the layout refetches user.
pub fn use_auth_invalidate() -> Signal<u32> {
    use_context::<Signal<u32>>()
}

/// Returns a short page name for the tab title: "Page Name | Arctos".
fn page_title_for_route(route: &Route) -> String {
    match route {
        Route::Index { .. } => "Home".into(),
        Route::Login { .. } => "Login".into(),
        Route::Register { .. } => "Register".into(),
        Route::RegisterPlayer { .. } => "Register as Player".into(),
        Route::RegisterTeam { .. } => "Register as Team".into(),
        Route::GoogleChooseAccountType { .. } => "Choose Account Type".into(),
        Route::GoogleCompleteProfile { .. } => "Complete Profile".into(),
        Route::TournamentHome { url } | Route::TournamentHomeWithTab { url, .. } => format!("{url}"),
        Route::Schedule { url, .. } => format!("{url} Schedule"),
        Route::Results { url } => format!("{url} Results"),
        Route::Bracket { url } => format!("{url} Bracket"),
        Route::LegacyBracket { url } => format!("{url} Legacy Bracket"),
        Route::TournamentSettings { url } => format!("{url} Settings"),
        Route::TournamentRegister { url } => format!("{url} Register"),
        Route::Manage { url } => format!("{url} Manage"),
        Route::ManageUserUploads { url } => format!("{url} Uploaded Videos"),
        Route::Invitations { url } => format!("{url} Roster"),
        Route::StartMatch { .. } => format!("Start Match").into(),
        Route::RunMatch { .. } => format!("Run Match").into(),
        Route::FinalizeMatch { .. } => format!("Finalize Match").into(),
        Route::Scoreboard { .. } => format!("Scoreboard"),
        Route::Record { field, .. } => format!("Record Field {field}").into(),
        Route::MatchPage { .. } => format!("Match"),
        Route::MatchPageById { .. } => format!("Match").into(),
        Route::AddInjury { .. } => "Add Injury".into(),
        Route::EditInjury { .. } => "Edit Injury".into(),
        Route::EditPlayerProfile { .. } => "Edit Profile".into(),
        Route::EditTeamProfile { .. } => "Edit Profile".into(),
        Route::PlayersList { .. } => "Players".into(),
        Route::PlayerProfilePage { id } => format!("{id}"),
        Route::TeamsList { .. } => "Teams".into(),
        Route::TeamProfilePage { id } => format!("{id}"),
        Route::Stones { .. } => "Stones".into(),
        Route::About { .. } => "About".into(),
        Route::NewTournament { .. } => "Create Tournament".into(),
        Route::LeaguesIndex { .. } => "Leagues".into(),
        Route::CreateEvent { .. } => "Create event".into(),
        Route::NewLeague { .. } => "Create League".into(),
        Route::LeagueHome { league_url } => league_url.clone(),
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
        Route::SideCompNew { url } => format!("{url} New Side Competition"),
        Route::SideCompDetail { url, .. } => format!("{url} Side Competition"),
        Route::SideCompEdit { url, .. } => format!("{url} Edit Side Competition"),
        Route::SideCompRegisterAsTo { url, .. } => format!("{url} Side Competition Quick Register"),
    }
}

#[component]
pub fn Layout() -> Element {
    let auth_version = use_signal(|| 0u32);
    provide_context(auth_version);

    let user = use_resource(move || {
        let av = auth_version;
        async move {
            let _ = av();
            api::me().await
        }
    });
    let mut nav_expanded = use_signal(|| false);
    let mut user_dropdown_open = use_signal(|| false);
    let mut register_dropdown_open = use_signal(|| false);
    let navigator = use_navigator();
    let route = use_route::<Route>();
    let page_title = format!("{} | Arctos", page_title_for_route(&route));

    // Scoreboard is embedded elsewhere (e.g. OBS); render only the raw scoreboard, no nav/footer.
    if matches!(route, Route::Scoreboard { .. }) {
        return rsx! { Outlet::<Route> {} };
    }

    rsx! {
        Title { "{page_title}" }
        link { rel: "stylesheet", href: "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" }
        link { rel: "stylesheet", href: "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" }
        style { r#"
            .navbar-brand {{ font-weight: bold; }}
            .tournament-card {{ margin-bottom: 1rem; }}
            .status-badge {{ font-size: 0.8em; }}
            .container-wide {{ max-width: 1600px; margin-left: auto; margin-right: auto; padding-left: 15px; padding-right: 15px; width: 100%; }}
            .error {{ color: var(--bs-danger); }}
            .muted {{ color: var(--bs-secondary); }}
            .layout-nav .nav-link {{ color: rgba(255,255,255,.85) !important; }}
            .layout-nav .nav-link:hover {{ color: #fff !important; }}
            .dropdown-menu {{ position: absolute; z-index: 1050; }}
            .markdown-content {{ line-height: 1.6; }}
            .markdown-content h1, .markdown-content h2, .markdown-content h3, .markdown-content h4, .markdown-content h5, .markdown-content h6 {{ margin-top: 1em; margin-bottom: 0.5em; font-weight: 600; }}
            .markdown-content h1 {{ font-size: 1.5em; }} .markdown-content h2 {{ font-size: 1.3em; }} .markdown-content h3 {{ font-size: 1.15em; }}
            .markdown-content p {{ margin-bottom: 0.75em; }}
            .markdown-content ul, .markdown-content ol {{ margin-bottom: 0.75em; padding-left: 1.5em; }}
            .markdown-content li {{ margin-bottom: 0.25em; }}
            .markdown-content blockquote {{ border-left: 4px solid var(--bs-secondary, #6c757d); padding-left: 1em; margin: 0.75em 0; color: var(--bs-secondary); }}
            .markdown-content code {{ padding: 0.2em 0.4em; font-size: 0.9em; background: rgba(0,0,0,0.06); border-radius: 4px; }}
            .markdown-content pre {{ padding: 0.75em; overflow-x: auto; background: rgba(0,0,0,0.06); border-radius: 4px; margin-bottom: 0.75em; }}
            .markdown-content pre code {{ padding: 0; background: none; }}
            .markdown-content table {{ border-collapse: collapse; margin-bottom: 0.75em; width: 100%; }}
            .markdown-content th, .markdown-content td {{ border: 1px solid var(--bs-border-color, #dee2e6); padding: 0.4em 0.6em; text-align: left; }}
            .markdown-content th {{ font-weight: 600; background: rgba(0,0,0,0.04); }}
            .markdown-content a {{ color: var(--bs-link-color, #0d6efd); text-decoration: none; }}
            .markdown-content a:hover {{ text-decoration: underline; }}
            .markdown-content img {{ max-width: 100%; height: auto; }}
            .markdown-content hr {{ margin: 1em 0; border: 0; border-top: 1px solid var(--bs-border-color, #dee2e6); }}
            .markdown-content .admonition {{ margin: 1em 0; padding: 0; border-radius: 6px; border: 1px solid; overflow: hidden; }}
            .markdown-content .admonition .admonition-title {{ margin: 0; padding: 0.5em 0.75em; font-weight: 600; }}
            .markdown-content .admonition p:not(.admonition-title) {{ padding: 0.5em 0.75em; margin-bottom: 0.5em; }}
            .markdown-content .admonition p:not(.admonition-title):last-child {{ margin-bottom: 0; }}
            .markdown-content .admonition.note {{ border-color: #0d6efd; background: rgba(13, 110, 253, 0.08); }}
            .markdown-content .admonition.note .admonition-title {{ background: rgba(13, 110, 253, 0.2); color: #0a58ca; }}
            .markdown-content .admonition.warning {{ border-color: #ffc107; background: rgba(255, 193, 7, 0.12); }}
            .markdown-content .admonition.warning .admonition-title {{ background: rgba(255, 193, 7, 0.25); color: #856404; }}
            .markdown-content .admonition.attention {{ border-color: #ffc107; background: rgba(255, 193, 7, 0.12); }}
            .markdown-content .admonition.attention .admonition-title {{ background: rgba(255, 193, 7, 0.25); color: #856404; }}
            .markdown-content .admonition.caution {{ border-color: #fd7e14; background: rgba(253, 126, 20, 0.1); }}
            .markdown-content .admonition.caution .admonition-title {{ background: rgba(253, 126, 20, 0.2); color: #b35a0e; }}
            .markdown-content .admonition.danger {{ border-color: #dc3545; background: rgba(220, 53, 69, 0.08); }}
            .markdown-content .admonition.danger .admonition-title {{ background: rgba(220, 53, 69, 0.2); color: #b02a37; }}
            .markdown-content .admonition.important {{ border-color: #fd7e14; background: rgba(253, 126, 20, 0.1); }}
            .markdown-content .admonition.important .admonition-title {{ background: rgba(253, 126, 20, 0.2); color: #b35a0e; }}
            .markdown-content .admonition.tip {{ border-color: #198754; background: rgba(25, 135, 84, 0.08); }}
            .markdown-content .admonition.tip .admonition-title {{ background: rgba(25, 135, 84, 0.2); color: #146c43; }}
            .markdown-content .admonition.hint {{ border-color: #198754; background: rgba(25, 135, 84, 0.08); }}
            .markdown-content .admonition.hint .admonition-title {{ background: rgba(25, 135, 84, 0.2); color: #146c43; }}
        "# }

        nav { class: "navbar navbar-expand-lg navbar-dark bg-dark",
            div { class: "container",
                Link { to: Route::Index {}, class: "navbar-brand", "Home" }
                button {
                    class: "navbar-toggler",
                    r#type: "button",
                    "aria-expanded": "{nav_expanded()}",
                    "aria-label": "Toggle navigation",
                    onclick: move |_| nav_expanded.toggle(),
                    span { class: "navbar-toggler-icon" }
                }
                div {
                    class: if nav_expanded() { "collapse navbar-collapse show" } else { "collapse navbar-collapse" },
                    id: "navbarNav",
                    ul { class: "navbar-nav me-auto",
                        li { class: "nav-item",
                            Link { to: Route::TeamsList {}, class: "nav-link", "Teams" }
                        }
                        li { class: "nav-item",
                            Link { to: Route::PlayersList {}, class: "nav-link", "Players" }
                        }
                        li { class: "nav-item",
                            Link { to: Route::Stones {}, class: "nav-link", "Stones" }
                        }
                        li { class: "nav-item",
                            Link { to: Route::LeaguesIndex {}, class: "nav-link", "Leagues" }
                        }
                        if let Some(Ok(_u)) = user.read().as_ref() {
                            li { class: "nav-item",
                                Link { to: Route::CreateEvent {}, class: "nav-link", "Create event" }
                            }
                        }
                        li { class: "nav-item",
                            Link { to: Route::About {}, class: "nav-link", "About" }
                        }
                    }
                    ul { class: "navbar-nav",
                        if let Some(Ok(u)) = user.read().as_ref() {
                            li { class: "nav-item dropdown",
                                button {
                                    class: "nav-link dropdown-toggle btn btn-link",
                                    style: "color: rgba(255,255,255,.85); text-decoration: none;",
                                    onclick: move |_| user_dropdown_open.toggle(),
                                    "{u.name}"
                                }
                                if user_dropdown_open() {
                                    ul { class: "dropdown-menu dropdown-menu-end show",
                                        li {
                                            if u.user_type == "player" {
                                                Link {
                                                    to: Route::PlayerProfilePage { id: u.id.clone() },
                                                    class: "dropdown-item",
                                                    "Profile"
                                                }
                                            } else {
                                                Link {
                                                    to: Route::TeamProfilePage { id: u.id.clone() },
                                                    class: "dropdown-item",
                                                    "Profile"
                                                }
                                            }
                                        }
                                        li { hr { class: "dropdown-divider" } }
                                        li {
                                            button {
                                                class: "dropdown-item",
                                                onclick: move |_| {
                                                    let nav = navigator.clone();
                                                    let mut auth_version = auth_version;
                                                    spawn(async move {
                                                        let _ = api::logout().await;
                                                        auth_version.set(auth_version() + 1);
                                                        nav.push("/");
                                                    });
                                                },
                                                "Logout"
                                            }
                                        }
                                    }
                                }
                            }
                        } else {
                            li { class: "nav-item",
                                Link { to: Route::Login {}, class: "nav-link", "Login" }
                            }
                            li { class: "nav-item dropdown",
                                button {
                                    class: "nav-link dropdown-toggle btn btn-link",
                                    style: "color: rgba(255,255,255,.85); text-decoration: none;",
                                    onclick: move |_| register_dropdown_open.toggle(),
                                    "Register"
                                }
                                if register_dropdown_open() {
                                    ul { class: "dropdown-menu dropdown-menu-end show",
                                        li {
                                            Link { to: Route::RegisterPlayer {}, class: "dropdown-item", "Register as Player" }
                                        }
                                        li {
                                            Link { to: Route::RegisterTeam {}, class: "dropdown-item", "Register as Team" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        main { class: "container-wide mt-4",
            SuspenseBoundary {
                fallback: |_| rsx! { div { class: "text-center py-5 text-muted", "Loading…" } },
                Outlet::<Route> {}
            }
        }

        footer { class: "container-wide mt-4",
            div { id: "footer", class: "text-center py-4 text-muted",
                p {
                    "Arctos is "
                    a { href: "https://github.com/reid23/arctos", "open source" }
                    ". Help improve it!"
                }
                p {
                    Link { to: Route::About {}, "About" }
                    " - "
                    Link { to: Route::Thanks {}, "Thanks" }
                    " - "
                    Link { to: Route::Docs {}, "User Docs" }
                    " - "
                    Link { to: Route::Privacy {}, "Privacy" }
                    " - "
                    Link { to: Route::License {}, "License" }
                    " - "
                    Link { to: Route::Terms {}, "Terms" }
                }
                p { style: "font-size: 0.8em;", "Arctos is an independent project and does not belong to nor represent CAJA or the US NJA in any way." }
            }
        }
    }
}
