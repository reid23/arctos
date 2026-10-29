use crate::api;
use crate::components::{
    ActionRecovery, EditRegistrationContext, EditRegistrationModal, EventAbout, EventBannerPhoto, EventHeader, EventTab, EventTabs,
    EventTeamsList, LeagueRegistrationButtons, Modal, ModalKind, OrganizerTools, PompfenIcon,
};
use crate::types::{ToEntry, User};
use crate::Route;
use dioxus::prelude::*;

fn is_current_user_to(me: Option<&Result<User, api::AccountError>>, to_entries: &[ToEntry]) -> bool {
    me.and_then(|r| r.as_ref().ok()).map_or(false, |u| {
        to_entries
            .iter()
            .any(|e| e.user_id == u.id && e.user_type == u.user_type)
    })
}

fn format_date_display(start: &str, end: Option<&String>) -> String {
    crate::time_format::format_calendar_range(start, end.map(String::as_str))
}

#[component]
pub fn TournamentHome(url: String) -> Element {
    rsx! {
        TournamentHomeContent { key: "{url}", url, initial_tab: None::<String> }
    }
}

#[component]
pub fn TournamentHomeWithTab(url: String, tab: String) -> Element {
    rsx! {
        TournamentHomeContent { key: "{url}", url, initial_tab: Some(tab) }
    }
}

#[component]
pub fn TournamentHomeContent(
    url: String,
    initial_tab: Option<String>,
    #[props(default)] content: Option<Element>,
    #[props(default)] section_title: Option<String>,
) -> Element {
    let url_for_data = url.clone();
    let navigator = use_navigator();
    let route = use_route::<Route>();
    let has_overview = content.is_none();
    let show_overview = use_memo(use_reactive(&has_overview, |value| value));
    let mut banner_photo = use_signal(|| None::<String>);
    let mut banner_initialized = use_signal(|| false);
    use_context_provider(|| EventBannerPhoto(banner_photo));
    let mut refresh = use_signal(|| 0u32);
    let data = use_resource(move || {
        let _ = refresh();
        let u = url_for_data.clone();
        async move { api::tournament_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let me_res = crate::auth::use_account();
    let val = data.value();
    let backend = api::base_url();
    let mut delete_modal_open = use_signal(|| false);
    let mut delete_confirm_url = use_signal(|| String::new());
    let mut delete_error = use_signal(|| None::<String>);
    let mut delete_pending = use_signal(|| false);
    use_effect(move || {
        if delete_error().is_some() {
            crate::form_validation::focus_after_render("delete-tournament-error");
        }
    });
    let mut show_edit_player_modal = use_signal(|| false);
    let mut show_edit_team_modal = use_signal(|| false);
    let mut show_league_edit_modal = use_signal(|| false);
    let url_for_delete_confirm = url.clone();
    let mut about_markdown = use_signal(|| Option::<String>::None);
    let mut delete_redirect_league = use_signal(|| None as Option<String>);
    use_effect(move || {
        let v = val.read();
        if let Some(Ok(d)) = v.as_ref() {
            if !banner_initialized() {
                banner_photo.set(d.tournament.banner_photo.clone());
                banner_initialized.set(true);
            }
            about_markdown.set(d.tournament.about.clone());
            delete_redirect_league.set(d.tournament.league.as_ref().map(|l| l.league_url.clone()));
        } else {
            about_markdown.set(None);
            delete_redirect_league.set(None);
        }
    });
    let about_html = use_resource(move || {
        let md = about_markdown();
        let has_overview = show_overview();
        async move {
            let rendered = if !has_overview { Ok(String::new()) } else {
                match md.as_deref() {
                    Some(m) if !m.is_empty() => api::render_markdown(m).await,
                    _ => Ok(String::new()),
                }
            };
            (md, rendered)
        }
    });

    let url_for_sidecomps = url.clone();
    let mut sidecomps = use_resource(move || {
        let u = url_for_sidecomps.clone();
        let has_overview = show_overview();
        async move {
            if has_overview { api::sidecomps_list(&u).await } else { Ok(Vec::new()) }
        }
    });

    // The initial empty markdown request can finish before event details
    // arrive. Match the rendered source before restoring Back's position, so
    // the short loading/raw-description layout cannot consume it too early.
    let restoration_tab = initial_tab.as_deref().unwrap_or("info");
    let scroll_ready = has_overview && me_res.read().is_some()
        && val.read().as_ref().is_some_and(|result| result.as_ref().is_ok_and(|details| {
            match restoration_tab {
                "teams" => true,
                "sidecomps" => sidecomps.value().read().is_some(),
                _ => about_html.value().read().as_ref()
                    .is_some_and(|(source, _)| *source == details.tournament.about),
            }
        }));
    super::directory_state::use_page_scroll(scroll_ready);

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            {{
                let unattached_count = d.unattached_players.len();
                let viewer_is_to = is_current_user_to(me_res.read().as_ref(), &d.to_entries);
                let active_tab = initial_tab.as_deref().filter(|tab| {
                    matches!(*tab, "teams" | "sidecomps")
                        || (content.is_some() && (section_title.is_some() || matches!(*tab, "schedule" | "results" | "bracket")))
                }).unwrap_or("info").to_string();
                let mut tabs = vec![
                    EventTab { id: "info".into(), label: "Info".into(), icon: "fas fa-info-circle".into(), href: Some(format!("/{url}")) },
                    EventTab { id: "teams".into(), label: format!("Teams ({})", d.teams_with_counts.len()), icon: "fas fa-users".into(), href: Some(format!("/{url}?tab=teams")) },
                    EventTab { id: "schedule".into(), label: "Schedule".into(), icon: "far fa-calendar-alt".into(), href: d.can_view_schedule.then(|| format!("/{url}/schedule")) },
                    EventTab { id: "results".into(), label: "Results".into(), icon: "fas fa-trophy".into(), href: Some(format!("/{url}/results")) },
                ];
                if d.tournament.bracket_published || viewer_is_to {
                    tabs.push(EventTab { id: "bracket".into(), label: "Bracket".into(), icon: "fas fa-sitemap".into(), href: Some(format!("/{url}/bracket")) });
                }
                tabs.push(EventTab { id: "sidecomps".into(), label: "Side events".into(), icon: "fas fa-medal".into(), href: Some(format!("/{url}?tab=sidecomps")) });
                if let Some(title) = section_title.as_ref() {
                    tabs.push(EventTab { id: active_tab.clone(), label: title.clone(), icon: "fas fa-sliders-h".into(), href: Some(route.to_string()) });
                }
                let league = d.tournament.league.as_ref();
                let team_open = league.map_or(d.tournament.team_registration_open, |l| l.team_registration_open);
                let player_open = league.map_or(d.tournament.player_registration_open, |l| l.player_registration_open);
                rsx! {
            div { class: "event-detail-page",
                EventHeader {
                    banner: banner_photo(),
                    title: d.tournament.name.clone(),
                    date: Some(format_date_display(&d.tournament.start_date, d.tournament.end_date.as_ref())),
                    location: Some(d.tournament.location.clone().filter(|s| !s.trim().is_empty()).unwrap_or_else(|| "Location to be announced".into())),
                    team_count: d.teams_with_counts.len(),
                    max_teams: d.tournament.n_max_teams,
                    event_count: None,
                    registration_open: team_open || player_open,
                    badge_league_url: league.map(|l| l.league_url.clone()),
                    badge_name: league.map(|l| l.name.clone()),
                    if (content.is_none() || matches!(active_tab.as_str(), "schedule" | "results" | "bracket")) && !matches!(me_res.read().as_ref(), Some(Err(api::AccountError::Unavailable))) {
                    div { class: "event-participant-actions", role: "group", aria_label: "Event registration",
                    if let Some(ref l) = d.tournament.league {
                        LeagueRegistrationButtons {
                            league_url: l.league_url.clone(),
                            registration_open: l.registration_open,
                            team_registration_open: Some(l.team_registration_open),
                            player_registration_open: Some(l.player_registration_open),
                            current_user: me_res.read().as_ref().cloned(),
                            is_team_registered: d.is_current_team_registered,
                            is_player_registered: d.is_current_player_registered,
                            use_edit_modal: true,
                            on_edit_registration: move |_| show_league_edit_modal.set(true),
                        }
                    } else {
                        if me_res.read().is_none() {
                            span { class: "text-muted", role: "status", "Loading registration…" }
                        } else if let Some(current_user) = me_res.read().as_ref().and_then(|r| r.as_ref().ok()) {
                            if current_user.user_type == "team" {
                                if d.is_current_team_registered {
                                    Link { to: Route::Invitations { url: url.clone() }, class: "btn btn-outline-secondary", "Manage roster" }
                                    button {
                                        r#type: "button",
                                        class: "btn btn-outline-secondary",
                                        aria_haspopup: "dialog",
                                        onclick: move |_| show_edit_team_modal.set(true),
                                        "Edit registration"
                                    }
                                } else if d.tournament.team_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-primary event-registration-action", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Team registration closed"
                                    }
                                }
                            } else if current_user.user_type == "player" {
                                if d.is_current_player_registered {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-outline-secondary",
                                        aria_haspopup: "dialog",
                                        onclick: move |_| show_edit_player_modal.set(true),
                                        "Edit registration"
                                    }
                                } else if d.tournament.player_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-primary event-registration-action", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Player registration closed"
                                    }
                                }
                            } else {
                                if d.tournament.team_registration_open || d.tournament.player_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-primary event-registration-action", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Registration closed"
                                    }
                                }
                            }
                        } else if matches!(me_res.read().as_ref(), Some(Err(api::AccountError::Unauthenticated))) && (team_open || player_open) {
                            a {
                                href: format!("/login?next={}", urlencoding::encode(&format!("/{url}/register"))),
                                class: "btn btn-primary event-registration-action",
                                "Log in to register"
                            }
                        } else {
                            button { r#type: "button", class: "btn btn-secondary", disabled: true, "Registration closed" }
                        }
                    }
                    }
                    }

                    if viewer_is_to {
                        OrganizerTools {
                            Link { to: Route::TournamentSettings { url: url.clone() }, class: "dropdown-item", i { class: "fas fa-sliders-h", aria_hidden: "true" } "Settings" }
                            Link { to: Route::ScheduleEdit { url: url.clone() }, class: "dropdown-item", i { class: "far fa-calendar-alt", aria_hidden: "true" } "Edit Schedule" }
                            if let Some(l) = league {
                                Link { to: Route::LeagueManage { league_url: l.league_url.clone() }, class: "dropdown-item", i { class: "fas fa-users", aria_hidden: "true" } "Registration Management" }
                            } else {
                                Link { to: Route::Manage { url: url.clone() }, class: "dropdown-item", i { class: "fas fa-users", aria_hidden: "true" } "Registration Management" }
                            }
                            Link { to: Route::ManageFootage { url: url.clone() }, class: "dropdown-item", i { class: "fas fa-video", aria_hidden: "true" } "Manage Footage" }
                            Link { to: Route::SideCompNew { url: url.clone() }, class: "dropdown-item", i { class: "fas fa-plus", aria_hidden: "true" } "Add side competition" }
                            button { r#type: "button", class: "dropdown-item text-danger", onclick: move |_| { delete_modal_open.set(true); delete_confirm_url.set(String::new()); delete_error.set(None); }, i { class: "fas fa-trash", aria_hidden: "true" } "Delete Tournament" }
                        }
                    }
                }
                EventTabs {
                    prefix: "event".to_string(), label: "Event sections".to_string(), active: active_tab.clone(), tabs,
                }
                div { class: "container-wide event-detail-content",
                if let Some(page_content) = content.as_ref() {
                    {page_content.clone()}
                } else {
                section { id: "event-section-info", class: "event-section", aria_labelledby: "event-tab-info", hidden: active_tab != "info",
                    EventAbout {
                        card_title: "About this event".to_string(),
                        about_html: about_html.value().read().as_ref()
                            .filter(|(source, _)| *source == d.tournament.about)
                            .and_then(|(_, result)| result.as_ref().ok()).cloned(),
                        about_raw: d.tournament.about.clone(),
                        empty_message: "Event details have not been added yet.".to_string(),
                        team_registration_open: team_open,
                        player_registration_open: player_open,
                        team_fee: league.map_or(d.tournament.team_reg_fee, |l| l.team_reg_fee),
                        player_fee: league.map_or(d.tournament.player_reg_fee, |l| l.player_reg_fee),
                        max_teams: d.tournament.n_max_teams,
                        max_roster: d.tournament.max_team_size_roster,
                        max_field: d.tournament.max_team_size_field,
                        waiver_required: d.tournament.waiver_required,
                        terms_link: d.tournament.terms_link.clone(),
                        league_url: league.map(|l| l.league_url.clone()),
                    }
                }
                section { id: "event-section-teams", class: "event-section", aria_labelledby: "event-tab-teams", hidden: active_tab != "teams",
                    if d.teams_with_counts.is_empty() {
                        div { class: "card", div { class: "card-body", h2 { "Registered teams" } p { class: "text-muted mb-0", "No teams have registered yet." } } }
                    }
            EventTeamsList {
                teams: d.teams_with_counts.clone(),
                event_scope: league.map(|l| format!("league:{}", l.league_url)).unwrap_or_else(|| url.clone()),
                revision: refresh(),
                card_title: "Registered teams".to_string(),
                show_registered_at: true,
                max_team_size_roster: d.tournament.max_team_size_roster,
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
                section { id: "event-section-sidecomps", class: "event-section", aria_labelledby: "event-tab-sidecomps", hidden: active_tab != "sidecomps",
                    div { class: "card",
                        div { class: "card-header sidecomp-heading",
                            h2 { "Side events" }
                            if viewer_is_to {
                                Link { to: Route::SideCompNew { url: url.clone() }, class: "btn btn-outline-secondary",
                                    i { class: "fas fa-plus", aria_hidden: "true" } "Add side competition"
                                }
                            }
                        }
                        div { class: "card-body",
                                match sidecomps.read().as_ref() {
                                    Some(Ok(rows)) => {
                                        if rows.is_empty() {
                                            rsx! { p { class: "text-muted mb-0", "No side competitions for this tournament." } }
                                        } else {
                                            rsx! {
                                                ul { class: "list-group list-group-flush event-side-list",
                                                    for row in rows.iter() {
                                                        {
                                                            let comp_id = row.id;
                                                            let row_name = row.name.clone();
                                                            let mut sidecomps_for_row = sidecomps;
                                                            rsx! {
                                                                li {
                                                                    key: "{comp_id}",
                                                                    class: "list-group-item d-flex flex-wrap justify-content-between align-items-center p-0",
                                                                    Link {
                                                                        to: Route::SideCompDetail { url: url.clone(), comp_id },
                                                                        class: "text-decoration-none text-reset p-3 flex-grow-1",
                                                                        div { class: "d-flex align-items-center flex-wrap gap-2",
                                                                            strong { "{row.name}" }
                                                                            span { class: "badge bg-secondary", "{row.type_}" }
                                                                            if row.registration_open {
                                                                                span { class: "badge bg-success", "Open" }
                                                                            } else {
                                                                                span { class: "badge bg-secondary", "Closed" }
                                                                            }
                                                                            span { class: "text-muted", "({row.registrant_count} registered)" }
                                                                            if row.viewer_registration_incomplete {
                                                                                span { class: "badge bg-warning text-dark",
                                                                                    i { class: "fas fa-exclamation-triangle me-1" }
                                                                                    "Registration incomplete"
                                                                                }
                                                                            } else if row.viewer_is_registered {
                                                                                span {
                                                                                    class: "badge bg-success",
                                                                                    if let Some(n) = row.viewer_entry_number {
                                                                                        "Registered #{n}"
                                                                                    } else {
                                                                                        "Registered"
                                                                                    }
                                                                                }
                                                                                if let Some(weapon) = row.viewer_weapon.as_ref() {
                                                                                    PompfenIcon { weapon: weapon.clone(), size: "1.5em".to_string() }
                                                                                }
                                                                            }
                                                                        }
                                                                    }
                                                                    if viewer_is_to {
                                                                        div { class: "d-flex align-items-start gap-1 px-3",
                                                                            Link {
                                                                                to: Route::SideCompEdit { url: url.clone(), comp_id },
                                                                                class: "btn btn-sm btn-outline-secondary flex-shrink-0",
                                                                                style: "min-width: 44px; min-height: 44px;",
                                                                                title: "Edit",
                                                                                aria_label: "Edit {row.name}",
                                                                                i { class: "fas fa-pen", aria_hidden: "true" }
                                                                            }
                                                                            SideCompDeleteAction { url: url.clone(), comp_id, name: row_name,
                                                                                on_deleted: move |_| sidecomps_for_row.restart(),
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
                                    Some(Err(error)) => rsx! { p { role: "alert", class: "text-danger", "Could not load side competitions: {error}" } button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| sidecomps.restart(), "Retry loading side competitions" } },
                                    None => rsx! { p { role: "status", "Loading side competitions…" } },
                                }

                        }
                    }
                }
                }

                }
            if show_edit_player_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::Tournament { tournament_url: url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_player_modal.set(false),
                        on_success: move |_| {
                            show_edit_player_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }

            if show_edit_team_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::Tournament { tournament_url: url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_team_modal.set(false),
                        on_success: move |_| {
                            show_edit_team_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }

            if show_league_edit_modal() {
                if let Some(ref l) = d.tournament.league {
                    if let Some(Ok(me)) = me_res.read().as_ref() {
                        EditRegistrationModal {
                            context: EditRegistrationContext::League { league_url: l.league_url.clone() },
                            user_type: me.user_type.clone(),
                            on_close: move |_| show_league_edit_modal.set(false),
                            on_success: move |_| {
                                show_league_edit_modal.set(false);
                                refresh.set(refresh() + 1);
                            },
                        }
                    }
                }
            }

            if is_current_user_to(me_res.read().as_ref(), &d.to_entries) && delete_modal_open() {
                Modal {
                    id: "delete-tournament-dialog",
                    labelled_by: "delete-tournament-title",
                    described_by: Some("delete-tournament-warning".to_string()),
                    initial_focus_id: Some("delete-tournament-cancel".to_string()),
                    return_focus_id: Some("organizer-tools-trigger".to_string()),
                    kind: ModalKind::AlertDialog,
                    dismissible: !delete_pending(),
                    on_close: move |_| { if !delete_pending() { delete_modal_open.set(false); } },
                    div { class: "modal-header",
                        h2 { id: "delete-tournament-title", class: "modal-title h5", tabindex: "-1", "Delete Tournament" }
                        button {
                            r#type: "button", class: "btn-close", aria_label: "Close delete tournament confirmation",
                            disabled: delete_pending(),
                            onclick: move |_| { if !delete_pending() { delete_modal_open.set(false); } },
                        }
                    }
                    div { class: "modal-body",
                        if let Some(ref err) = delete_error() {
                            div { id: "delete-tournament-error", class: "alert alert-danger mb-3", role: "alert", tabindex: "-1", "{err}" }
                        }
                        div { id: "delete-tournament-warning", class: "alert alert-danger",
                            strong { "Warning: " }
                            "This action cannot be undone. All matches, registrations, and data will be permanently removed."
                        }
                        p { id: "delete-tournament-instructions", "To confirm, type the tournament URL exactly:" }
                        p { class: "text-center mb-2", strong { "{url}" } }
                        form {
                            id: "delete-tournament-form",
                            aria_busy: "{delete_pending()}",
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                if delete_pending() || delete_confirm_url() != url_for_delete_confirm {
                                    return;
                                }
                                delete_pending.set(true);
                                delete_error.set(None);
                                let nav = navigator.clone();
                                let url_submit = url_for_delete_confirm.clone();
                                let confirm = delete_confirm_url();
                                let redirect_league = delete_redirect_league();
                                spawn(async move {
                                    let result = api::delete_tournament(&url_submit, &confirm).await;
                                    delete_pending.set(false);
                                    match result {
                                        Ok(res) if res.success => {
                                            delete_modal_open.set(false);
                                            if let Some(lu) = redirect_league {
                                                let _ = nav.push(Route::LeagueHome { league_url: lu });
                                            } else {
                                                let _ = nav.push(Route::Index {});
                                            }
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
                                label { class: "form-label", r#for: "delete-tournament-confirm-url", "Tournament URL:" }
                                input {
                                    id: "delete-tournament-confirm-url", class: "form-control", name: "confirm_url", r#type: "text",
                                    aria_describedby: "delete-tournament-instructions",
                                    placeholder: "{url}", value: "{delete_confirm_url()}", required: true,
                                    disabled: delete_pending(),
                                    oninput: move |e| { if !delete_pending() { delete_confirm_url.set(e.value()); } },
                                }
                            }
                        }
                    }
                    div { class: "modal-footer",
                        button {
                            id: "delete-tournament-cancel", r#type: "button", class: "btn btn-secondary", disabled: delete_pending(),
                            onclick: move |_| { if !delete_pending() { delete_modal_open.set(false); } },
                            "Cancel"
                        }
                        button {
                            class: "btn btn-danger", r#type: "submit", form: "delete-tournament-form",
                            disabled: delete_pending() || delete_confirm_url() != url,
                            if delete_pending() { "Deleting…" } else { "Delete Tournament" }
                        }
                    }
                }
            }

            }
                }
            }}
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", role: "alert", "{e}" }
        } else {
            p { class: "text-muted", role: "status", "Loading…" }
        }
    }
}


/// Shared by the overview and edit page; a failed DELETE is checked before retry.
#[component]
pub(super) fn SideCompDeleteAction(
    url: String, comp_id: i32, name: String, on_deleted: EventHandler<()>,
    #[props(default = false)] full_label: bool,
    #[props(default = false)] disabled: bool,
    #[props(default)] on_blocked: EventHandler<bool>,
) -> Element {
    let mut state = use_signal(ActionRecovery::default);
    let mut confirm_delete = use_signal(|| false);
    let accessible_name = format!("Delete {name}");
    let mut action = move |send_delete: bool| {
        if disabled || state().busy() || (send_delete && state().blocks_write()) { return; }
        state.set(if send_delete { ActionRecovery::Pending } else { ActionRecovery::Checking });
        on_blocked.call(true);
        let url = url.clone();
        spawn(async move {
            let result = if send_delete { api::sidecomp_delete(comp_id).await.map(|_| ()) } else { Err("Delete was not confirmed.".into()) };
            if result.is_ok() { on_deleted.call(()); return; }
            let message = result.err().unwrap_or_default();
            state.set(ActionRecovery::Checking);
            let present = api::sidecomps_list(&url).await.map(|rows| rows.iter().any(|row| row.id == comp_id));
            let next = ActionRecovery::after_check(present, &message);
            if next == ActionRecovery::Idle { on_deleted.call(()); }
            on_blocked.call(next.blocks_write());
            state.set(next);
        });
    };
    let mut check = action.clone();
    rsx! {
        div { class: "d-flex flex-column align-items-start", style: "min-width: 0; max-width: min(18rem, 100%);", aria_busy: state().busy(),
            button { id: "sidecomp-delete-trigger-{comp_id}", r#type: "button", class: "btn btn-sm btn-outline-danger", disabled: disabled || state().blocks_write(),
                aria_label: accessible_name, aria_haspopup: "dialog", onclick: move |_| confirm_delete.set(true),
                if full_label { "Delete this side competition" } else { i { class: "fas fa-trash", aria_hidden: "true" } }
            }
            if state().busy() { p { class: "small mb-0", role: "status", "Checking deletion…" } }
            if let Some(message) = state().message() { p { class: "small text-danger mb-0", role: "alert", "{message}" } }
            if matches!(state(), ActionRecovery::NeedsCheck(_)) { button { r#type: "button", class: "btn btn-sm btn-outline-secondary", onclick: move |_| check(false), "Check deletion status" } }
        }
        if confirm_delete() {
            Modal {
                id: format!("sidecomp-delete-confirmation-{comp_id}"),
                labelled_by: format!("sidecomp-delete-title-{comp_id}"),
                described_by: Some(format!("sidecomp-delete-description-{comp_id}")),
                initial_focus_id: Some(format!("sidecomp-delete-cancel-{comp_id}")),
                return_focus_id: Some(format!("sidecomp-delete-trigger-{comp_id}")),
                kind: ModalKind::AlertDialog,
                dismissible: !state().busy(),
                on_close: move |_| { if !state().busy() { confirm_delete.set(false); } },
                div { class: "modal-header",
                    h2 { id: "sidecomp-delete-title-{comp_id}", class: "modal-title h5", "Delete {name}?" }
                }
                div { class: "modal-body",
                    p { id: "sidecomp-delete-description-{comp_id}", "This permanently deletes the side competition, its registrations, and its results. This cannot be undone." }
                }
                div { class: "modal-footer",
                    button { id: "sidecomp-delete-cancel-{comp_id}", r#type: "button", class: "btn btn-secondary", disabled: state().busy(),
                        onclick: move |_| confirm_delete.set(false), "Cancel"
                    }
                    button { r#type: "button", class: "btn btn-danger", disabled: disabled || state().blocks_write(),
                        onclick: move |_| { confirm_delete.set(false); action(true); }, "Delete side competition"
                    }
                }
            }
        }
    }
}
