use crate::api;
use crate::components::{
    EditRegistrationContext, EditRegistrationModal, EventAbout, EventBannerPhoto, EventHeader, EventTab, EventTabs,
    EventTeamsList, LeagueRegistrationButtons, OrganizerTools,
};
use crate::types::{ToEntry, User};
use crate::Route;
use dioxus::prelude::*;

fn get_query_param(name: &str) -> Option<String> {
    let window = web_sys::window()?;
    let search = window.location().search().ok()?;
    let params = web_sys::UrlSearchParams::new_with_str(&search).ok()?;
    params.get(name)
}

fn is_current_user_to(me: Option<&Result<User, String>>, to_entries: &[ToEntry]) -> bool {
    me.and_then(|r| r.as_ref().ok()).map_or(false, |u| {
        to_entries
            .iter()
            .any(|e| e.user_id == u.id && e.user_type == u.user_type)
    })
}

fn format_date(iso: &str) -> String {
    let date = iso.split('T').next().unwrap_or(iso);
    chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d")
        .map(|date| date.format("%b %-d, %Y").to_string())
        .unwrap_or_else(|_| date.to_string())
}

fn format_date_display(start: &str, end: Option<&String>) -> String {
    let start_fmt = format_date(start);
    match end {
        None => start_fmt,
        Some(e) if format_date(e) == start_fmt => start_fmt,
        Some(e) => format!("{} – {}", start_fmt, format_date(e)),
    }
}

#[component]
pub fn LeagueHome(league_url: String) -> Element {
    rsx! { LeagueHomeContent { key: "{league_url}", league_url, initial_tab: None::<String> } }
}

#[component]
pub fn LeagueHomeWithTab(league_url: String, tab: String) -> Element {
    rsx! { LeagueHomeContent { key: "{league_url}", league_url, initial_tab: Some(tab) } }
}

#[component]
pub fn LeagueHomeContent(
    league_url: String,
    initial_tab: Option<String>,
    #[props(default)] content: Option<Element>,
    #[props(default)] section_title: Option<String>,
) -> Element {
    let route = use_route::<Route>();
    let has_overview = content.is_none();
    let show_overview = use_memo(use_reactive(&has_overview, |value| value));
    let mut banner_photo = use_signal(|| None::<String>);
    let mut banner_initialized = use_signal(|| false);
    use_context_provider(|| EventBannerPhoto(banner_photo));
    let url_for_data = league_url.clone();
    let mut refresh = use_signal(|| 0u32);
    let mut show_edit_modal = use_signal(|| false);
    let mut delete_modal_open = use_signal(|| false);
    let mut delete_confirm_url = use_signal(|| String::new());
    let mut delete_error = use_signal(|| None::<String>);
    let mut show_registration_success = use_signal(|| false);
    let navigator = use_navigator();
    let url_for_delete_confirm = league_url.clone();
    let data = use_resource(move || {
        let _ = refresh();
        let lu = url_for_data.clone();
        async move { api::league_detail(&lu).await.map_err(|e| e.to_string()) }
    });
    let me_res = use_resource(move || async move { api::me().await });
    let league_url_for_warning = league_url.clone();
    let waiver_warning = use_resource(move || {
        let lu = league_url_for_warning.clone();
        let player_signed_in = matches!(me_res.read().as_ref(), Some(Ok(user)) if user.user_type == "player");
        async move {
            if !player_signed_in { return false; }
            match api::get_my_player_registration_league(&lu).await {
                Ok(res) => res.waiver_required && !res.waiver_signature_valid,
                Err(_) => false,
            }
        }
    });
    let val = data.value();
    let mut about_markdown = use_signal(|| Option::<String>::None);
    use_effect(move || {
        let v = val.read();
        if let Some(Ok(d)) = v.as_ref() {
            if !banner_initialized() {
                banner_photo.set(d.league.banner_photo.clone());
                banner_initialized.set(true);
            }
            about_markdown.set(d.league.about.clone());
        } else {
            about_markdown.set(None);
        }
    });
    let about_html = use_resource(move || {
        let md = about_markdown();
        let has_overview = show_overview();
        async move {
            if !has_overview { return Ok(String::new()); }
            match md.as_deref() {
                Some(m) if !m.is_empty() => api::render_markdown(m).await,
                _ => Ok(String::new()),
            }
        }
    });

    use_effect(move || {
        if get_query_param("registered").as_deref() == Some("1") {
            show_registration_success.set(true);
            if let Some(window) = web_sys::window() {
                if let Ok(loc) = window.location().pathname() {
                    let _ = window.history().and_then(|h| {
                        h.replace_state_with_url(&wasm_bindgen::JsValue::NULL, "", Some(&loc))
                    });
                }
            }
        }
    });

    let active_tab = initial_tab.as_deref().filter(|tab| {
        matches!(*tab, "events" | "teams") || (content.is_some() && (section_title.is_some() || *tab == "results"))
    }).unwrap_or("info").to_string();
    let backend = api::base_url();

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            {{
                let viewer_is_to = is_current_user_to(me_res.read().as_ref(), &d.to_entries);
                let unattached_count = d.unattached_players.len();
                let wide = content.is_some() && matches!(active_tab.as_str(), "results" | "manage");
                let mut tabs = vec![
                    EventTab { id: "info".into(), label: "Info".into(), icon: "fas fa-info-circle".into(), href: Some(format!("/leagues/{league_url}")) },
                    EventTab { id: "events".into(), label: format!("Events ({})", d.events.len()), icon: "far fa-calendar-alt".into(), href: Some(format!("/leagues/{league_url}?tab=events")) },
                    EventTab { id: "teams".into(), label: format!("Teams ({})", d.teams_with_counts.len()), icon: "fas fa-users".into(), href: Some(format!("/leagues/{league_url}?tab=teams")) },
                    EventTab { id: "results".into(), label: "Results".into(), icon: "fas fa-trophy".into(), href: Some(format!("/leagues/{league_url}/results")) },
                ];
                if let Some(title) = section_title.as_ref() {
                    tabs.push(EventTab { id: active_tab.clone(), label: title.clone(), icon: "fas fa-sliders-h".into(), href: Some(route.to_string()) });
                }
                rsx! {
            div { class: if wide { "event-detail-page is-wide" } else { "event-detail-page" },
                EventHeader {
                    banner: banner_photo(),
                    title: d.league.name.clone(),
                    kind: "League".to_string(),
                    date: None,
                    location: None,
                    team_count: d.teams_with_counts.len(),
                    max_teams: d.league.n_max_teams,
                    event_count: Some(d.events.len()),
                    registration_open: d.league.team_registration_open || d.league.player_registration_open,
                    badge_league_url: None,
                    badge_name: None,
                    if content.is_none() || active_tab == "results" {
                    div { class: "event-participant-actions", role: "group", aria_label: "League registration",
                    if (d.league.team_registration_open || d.league.player_registration_open) || d.is_current_team_registered || d.is_current_player_registered {
                        LeagueRegistrationButtons {
                            league_url: league_url.clone(),
                            registration_open: d.league.registration_open,
                            team_registration_open: Some(d.league.team_registration_open),
                            player_registration_open: Some(d.league.player_registration_open),
                            current_user: me_res.read().as_ref().cloned(),
                            is_team_registered: d.is_current_team_registered,
                            is_player_registered: d.is_current_player_registered,
                            use_edit_modal: true,
                            on_edit_registration: move |_| show_edit_modal.set(true),
                            show_edit_warning: waiver_warning.value().read().as_ref().copied().unwrap_or(false),
                        }
                    } else {
                        button { r#type: "button", class: "btn btn-secondary", disabled: true, "Registration closed" }
                    }
                    }
                    }
                    if viewer_is_to {
                        OrganizerTools {
                            Link { to: Route::LeagueSettings { league_url: league_url.clone() }, class: "dropdown-item", i { class: "fas fa-sliders-h", aria_hidden: "true" } "Settings" }
                            Link { to: Route::LeagueNewTournament { league_url: league_url.clone() }, class: "dropdown-item", i { class: "fas fa-plus", aria_hidden: "true" } "Add Event" }
                            Link { to: Route::LeagueManage { league_url: league_url.clone() }, class: "dropdown-item", i { class: "fas fa-users", aria_hidden: "true" } "Registration Management" }
                            button { r#type: "button", class: "dropdown-item text-danger", onclick: move |_| { delete_modal_open.set(true); delete_confirm_url.set(String::new()); delete_error.set(None); }, i { class: "fas fa-trash", aria_hidden: "true" } "Delete League" }
                        }
                    }
                }
                if show_registration_success() {
                    div { class: "alert alert-success mb-0", role: "status", "Registration submitted!" }
                }
                EventTabs {
                    prefix: "league".to_string(), label: "League sections".to_string(), active: active_tab.clone(),
                    tabs,
                }
                div { class: "container-wide event-detail-content",
                if let Some(page_content) = content.as_ref() {
                    {page_content.clone()}
                } else {
                section { id: "league-section-info", class: "event-section", aria_labelledby: "league-tab-info", hidden: active_tab != "info",
                    EventAbout {
                        card_title: "About this league".to_string(),
                        about_html: about_html.value().read().as_ref().and_then(|r| r.as_ref().ok()).cloned(),
                        about_raw: d.league.about.clone(),
                        empty_message: "League details have not been added yet.".to_string(),
                        team_registration_open: d.league.team_registration_open,
                        player_registration_open: d.league.player_registration_open,
                        team_fee: d.league.team_reg_fee,
                        player_fee: d.league.player_reg_fee,
                        max_teams: d.league.n_max_teams,
                        max_roster: d.league.max_team_size_roster,
                        max_field: d.league.max_team_size_field,
                        waiver_required: d.league.waiver_required,
                        terms_link: d.league.terms_link.clone(),
                        league_url: Some(league_url.clone()),
                    }
                }
                section { id: "league-section-events", class: "event-section", aria_labelledby: "league-tab-events", hidden: active_tab != "events",
                    div { class: "card",
                        div { class: "card-header event-side-heading",
                            h2 { "League events ({d.events.len()})" }
                            if viewer_is_to {
                                Link { to: Route::LeagueNewTournament { league_url: league_url.clone() }, class: "btn btn-outline-primary", i { class: "fas fa-plus", aria_hidden: "true" } "Add Event" }
                            }
                        }
                        if d.events.is_empty() {
                            div { class: "card-body", p { class: "text-muted mb-0", "No events have been added to this league yet." } }
                        } else {
                            ul { class: "event-series-list",
                                for event in d.events.iter() {
                                    li { key: "{event.url}",
                                        Link { to: Route::TournamentHome { url: event.url.clone() },
                                            strong { "{event.name}" }
                                            div { class: "event-series-meta",
                                                span { i { class: "far fa-calendar-alt", aria_hidden: "true" } "{format_date_display(&event.start_date, event.end_date.as_ref())}" }
                                                if let Some(location) = event.location.as_ref().filter(|s| !s.trim().is_empty()) {
                                                    span { i { class: "fas fa-map-marker-alt", aria_hidden: "true" } "{location}" }
                                                }
                                            }
                                            i { class: "fas fa-chevron-right", aria_hidden: "true" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                section { id: "league-section-teams", class: "event-section", aria_labelledby: "league-tab-teams", hidden: active_tab != "teams",
                    if d.teams_with_counts.is_empty() {
                        div { class: "card", div { class: "card-body", h2 { "Registered teams" } p { class: "text-muted mb-0", "No teams have registered yet." } } }
                    }
            EventTeamsList {
                teams: d.teams_with_counts.clone(),
                card_title: "Registered teams".to_string(),
                show_registered_at: true,
                max_team_size_roster: d.league.max_team_size_roster,
            }

            if !d.unattached_players.is_empty() {
                div { class: "row mt-4",
                    div { class: "col-12",
                        div { class: "card data-section event-roster",
                            div { class: "card-header", h5 { class: "mb-0", "Unattached Players ({unattached_count})" } }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table roster-table",
                                        thead {
                                            tr {
                                                th { scope: "col", "Player Name" }
                                                th { scope: "col", "Jersey" }
                                                th { scope: "col", class: "table-nowrap", "Registration Date" }
                                            }
                                        }
                                        tbody {
                                            for p in d.unattached_players.iter() {
                                                tr { key: "{p.player_id}",
                                                    td { class: "table-copy",
                                                        div { class: "roster-identity",
                                                            if let Some(photo) = &p.profile_photo {
                                                                img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle me-2", style: "width: 32px; height: 32px; object-fit: cover; flex-shrink: 0;" }
                                                            }
                                                            div {
                                                                Link { to: Route::PlayerProfilePage { id: p.player_id.clone() }, class: "roster-name",
                                                                    strong { "{p.player_name}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    td { class: "roster-wide", "data-label": "Jersey",
                                                        if p.jersey_name.is_some() || p.jersey_number.is_some() {
                                                            span { class: "table-jersey",
                                                                if let Some(name) = &p.jersey_name { span { class: "table-copy", "{name}" } }
                                                                if let Some(num) = &p.jersey_number { span { class: "table-nowrap", "#{num}" } }
                                                            }
                                                        } else {
                                                            span { class: "text-muted", "No jersey info" }
                                                        }
                                                    }
                                                    td { class: "table-nowrap roster-wide", "data-label": "Registered", {crate::time_format::format_registration_timestamp(p.registered_at.as_deref().unwrap_or("—"))} }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }




                }
                }

                }
            if show_edit_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::League { league_url: league_url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_modal.set(false),
                        on_success: move |_| {
                            show_edit_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }
            if delete_modal_open() {
                div { class: "modal d-block", tabindex: "-1", style: "background: rgba(0,0,0,0.5);",
                    div { class: "modal-dialog modal-dialog-centered",
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Delete League" }
                                button { class: "btn-close", onclick: move |_| delete_modal_open.set(false) }
                            }
                            div { class: "modal-body",
                                if let Some(ref err) = delete_error() {
                                    div { class: "alert alert-danger mb-3", "{err}" }
                                }
                                div { class: "alert alert-danger",
                                    strong { "Warning: " }
                                    "This action cannot be undone. The league and all its events, registrations, and data will be permanently removed."
                                }
                                p { "To confirm, type the league URL exactly:" }
                                p { class: "text-center mb-2", strong { "{league_url}" } }
                                form {
                                    id: "delete-league-form",
                                    onsubmit: move |ev| {
                                        ev.prevent_default();
                                        if delete_confirm_url() != url_for_delete_confirm {
                                            return;
                                        }
                                        delete_error.set(None);
                                        let nav = navigator.clone();
                                        let url_submit = url_for_delete_confirm.clone();
                                        let confirm = delete_confirm_url();
                                        spawn(async move {
                                            match api::delete_league(&url_submit, &confirm).await {
                                                Ok(res) if res.success => {
                                                    nav.push(Route::Index {});
                                                }
                                                Ok(res) => {
                                                    delete_error.set(Some(res.error.unwrap_or_else(|| "Delete failed.".to_string())));
                                                }
                                                Err(e) => {
                                                    delete_error.set(Some(e));
                                                }
                                            }
                                        });
                                    },
                                    div { class: "mb-3",
                                        label { class: "form-label", "League URL:" }
                                        input {
                                            class: "form-control",
                                            name: "confirm_url",
                                            r#type: "text",
                                            placeholder: "{league_url}",
                                            value: "{delete_confirm_url()}",
                                            oninput: move |e| delete_confirm_url.set(e.value()),
                                        }
                                    }
                                }
                            }
                            div { class: "modal-footer",
                                button { class: "btn btn-secondary", onclick: move |_| delete_modal_open.set(false), "Cancel" }
                                button {
                                    class: "btn btn-danger",
                                    r#type: "submit",
                                    form: "delete-league-form",
                                    disabled: delete_confirm_url() != league_url,
                                    "Delete League"
                                }
                            }
                        }
                    }
                }
            }

            }
                }
            }}
        } else if let Some(Err(e)) = val.read().as_ref() {
            div { class: "alert alert-danger", role: "alert", "{e}" }
        } else {
            p { class: "text-muted", role: "status", "Loading…" }
        }
    }
}
