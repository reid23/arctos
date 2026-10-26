use crate::api;
use crate::components::{EventTab, EventTabs, ProfileBio, ProfileHeader};
use crate::components::{PaymentStatusBadge, RecordTimestamp, RegistrationStatusBadge};
use crate::types::{TeamRegItem, TournamentPlayerItem};
use crate::Route;
use dioxus::prelude::*;
use std::collections::HashMap;

/// Cache value: None = loading, Some(Ok(players)) = loaded, Some(Err(_)) = error.
type PlayersCache = HashMap<String, Option<Result<Vec<TournamentPlayerItem>, String>>>;

#[component]
fn RegistrationRow(
    registration: TeamRegItem,
    is_expanded: bool,
    on_toggle: EventHandler<()>,
    is_own_team: bool,
    team_id: String,
    backend: String,
    players_cache: Signal<PlayersCache>,
) -> Element {
    let event_key = registration.event.clone();
    let is_league = event_key.starts_with("league:");
    let league_url = event_key
        .strip_prefix("league:")
        .unwrap_or(&event_key)
        .to_string();
    rsx! {
        tr {
            class: if is_expanded { "profile-team-record table-active" } else { "profile-team-record" },
            onclick: move |_| on_toggle.call(()),
            style: "cursor: pointer;",
            td { class: "profile-history-toggle p-1",
                button {
                    r#type: "button", class: "btn btn-link p-0", style: "width: 44px;",
                    aria_label: if is_expanded { format!("Hide players for {event_key}") } else { format!("Show players for {event_key}") },
                    aria_expanded: "{is_expanded}",
                    onclick: move |event| { event.stop_propagation(); on_toggle.call(()); },
                    i {
                        class: if is_expanded { "fas fa-chevron-down" } else { "fas fa-chevron-right" },
                        style: "font-size: 0.7rem;", aria_hidden: "true",
                    }
                }
            }
            td { class: "profile-record-name table-copy", "data-label": "Tournament",
                if is_league {
                    Link { to: Route::LeagueHome { league_url: league_url.clone() }, "League: {league_url}" }
                } else {
                    Link { to: Route::TournamentHome { url: event_key.clone() }, "{event_key}" }
                }
            }
            td { class: "table-copy", "data-label": "Team name", strong { "{registration.pseudonym.as_deref().unwrap_or(\"-\")}" } }
            td { class: "table-nowrap", "data-label": "Date",
                if let Some(date) = &registration.start_date {
                    {crate::time_format::format_calendar_date(date)}
                } else {
                    "Date not set"
                }
            }
            td { class: "table-nowrap", "data-label": "Status",
                RegistrationStatusBadge { status: registration.status.clone() }
            }
            td { class: "table-nowrap", "data-label": "Payment",
                if registration.paid {
                    PaymentStatusBadge { paid: true }
                    if registration.amount_paid > 0.0 {
                        { let amt = format!("${:.2}", registration.amount_paid); rsx! { small { class: "text-muted ms-1", "{amt}" } } }
                    }
                } else {
                    PaymentStatusBadge { paid: false }
                }
            }
            td { class: "profile-record-actions table-nowrap", "data-label": "Actions",
                onclick: move |e: Event<MouseData>| e.stop_propagation(),
                if is_own_team {
                    Link {
                        to: Route::ManageTeamEvent { team_id: team_id.clone(), kind: if is_league { "leagues".into() } else { "events".into() }, slug: league_url.clone() },
                        class: "btn btn-sm btn-outline-primary",
                        "Manage roster"
                    }
                }
            }
        }
        if is_expanded {
            tr {
                key: "{event_key}-players", class: "profile-roster-expanded",
                td { colspan: 7, class: "bg-light",
                    {
                        let ev = registration.event.clone();
                        let cached = players_cache().get(&ev).cloned();
                        let (loading, err_msg, players) = match &cached {
                            None => (true, false, None),
                            Some(None) => (true, false, None),
                            Some(Some(Ok(list))) => (false, false, Some(list.clone())),
                            Some(Some(Err(_))) => (false, true, None),
                        };
                        rsx! {
                            div { class: "profile-roster-panel", onclick: move |e: Event<MouseData>| e.stop_propagation(),
                                if let Some(players_list) = players {
                                    if players_list.is_empty() {
                                        p { class: "text-muted mb-0", "No players registered yet." }
                                    } else {
                                        ul { class: "profile-roster",
                                            for p in players_list.iter() {
                                                li { key: "{p.registration.player}",
                                                    Link {
                                                        to: Route::PlayerProfilePage { id: p.registration.player.clone() },
                                                        class: "profile-roster-link",
                                                        if let Some(player) = &p.player {
                                                            if let Some(photo) = &player.profile_photo {
                                                                img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle", style: "width: 32px; height: 32px; object-fit: cover; flex-shrink: 0;" }
                                                            }
                                                        }
                                                        div { class: "profile-roster-copy",
                                                            strong { "{p.player.as_ref().map(|player| player.name.as_str()).unwrap_or(&p.registration.player)}" }
                                                            small { class: "profile-roster-meta",
                                                                span { class: "table-jersey",
                                                                    span { class: "table-copy", {p.registration.jersey_name.as_deref().unwrap_or("-")} }
                                                                    if let Some(num) = &p.registration.jersey_number { span { class: "table-nowrap", "#{num}" } }
                                                                }
                                                                span { class: "profile-roster-handle", "@{p.registration.player}" }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                } else if loading {
                                    p { class: "text-muted mb-0", "Loading…" }
                                } else if err_msg {
                                    p { class: "text-danger mb-0", "Failed to load players." }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/// Page component for the router. Reads id from use_route() so navigation
/// between profiles updates the view (router reuses the same component and
/// does not pass new props).
#[component]
pub fn TeamProfilePage(id: String) -> Element {
    let route = use_route::<Route>();
    let (id, tab) = match &route {
        Route::TeamProfilePage { id } => (id.clone(), "info".to_string()),
        Route::TeamProfilePageWithTab { id, tab } => (id.clone(), tab.clone()),
        _ => return rsx! { div { class: "alert alert-danger", "Invalid route" } },
    };
    let mut id_signal = use_signal(|| id.clone());
    if *id_signal.peek() != id {
        id_signal.set(id.clone());
    }
    rsx! {
        TeamProfile { key: "{id}", id: id_signal, tab }
    }
}

#[component]
pub fn TeamProfilePageWithTab(id: String, tab: String) -> Element {
    rsx! { TeamProfilePage { id } }
}

#[component]
pub fn TeamProfile(id: Signal<String>, tab: String) -> Element {
    let data = use_resource(use_reactive(&id, move |sid| {
        let i = sid().clone();
        async move { api::team_profile(&i).await.map_err(|e| e.to_string()) }
    }));
    let me = crate::auth::use_account();
    let val = data.value();
    let backend = api::base_url();

    let mut expanded_event = use_signal(|| Option::<String>::None);
    let mut players_cache = use_signal(|| PlayersCache::new());
    // When switching to a different team, clear roster cache and collapse so we don't show the previous team's players.
    use_effect(move || {
        let current_id = id();
        players_cache.set(PlayersCache::new());
        expanded_event.set(None);
    });
    let team_id_for_fetch = id().clone();
    use_effect(move || {
        let expanded = expanded_event();
        if let Some(ev) = &expanded {
            if !players_cache().contains_key(ev) {
                let mut c = players_cache().clone();
                c.insert(ev.clone(), None);
                players_cache.set(c);
                let tid = team_id_for_fetch.clone();
                let ev_fetch = ev.clone();
                let mut cache_sig = players_cache;
                spawn(async move {
                    let res = api::team_registration_players(&tid, &ev_fetch).await;
                    cache_sig.set({
                        let mut c = cache_sig();
                        c.insert(ev_fetch, Some(res));
                        c
                    });
                });
            }
        }
    });

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            {
                let own_profile = d.can_manage;
                let show_notes = own_profile || !d.team_notes.is_empty();
                let active_tab = match tab.as_str() {
                    "history" => "history",
                    "notes" if show_notes => "notes",
                    _ => "info",
                };
                let mut tabs = vec![
                    EventTab { id: "info".into(), label: "Information".into(), icon: "fas fa-info-circle".into(), href: Some(format!("/teams/{}?tab=info", d.team.id)) },
                    EventTab { id: "history".into(), label: format!("History ({})", d.registrations.len()), icon: "fas fa-history".into(), href: Some(format!("/teams/{}?tab=history", d.team.id)) },
                ];
                if show_notes {
                    tabs.push(EventTab { id: "notes".into(), label: format!("Notes ({})", d.team_notes.len()), icon: "far fa-note-sticky".into(), href: Some(format!("/teams/{}?tab=notes", d.team.id)) });
                }
                rsx! {
            div { class: "profile-page",
            ProfileHeader {
                account_id: d.team.id.clone(), kind: "team".to_string(), name: d.team.name.clone(),
                avatar: d.team.profile_photo.clone(), banner: d.team.banner_photo.clone(), location: d.team.location.clone(),
                past_count: d.past_event_count,
                edit_route: None,
                manage_route: own_profile.then(|| Route::ManageTeam { team_id: d.team.id.clone() }),
            }
            EventTabs { prefix: "team".to_string(), label: "Team sections".to_string(), active: active_tab.to_string(), tabs }
            div { class: "profile-detail-content",
                    section { id: "team-section-info", class: "profile-section", hidden: active_tab != "info", aria_labelledby: "team-tab-info",
                        div { class: "profile-section-body",
                            if let Some(site) = d.team.website.as_ref().filter(|value| !value.trim().is_empty()) {
                                p { strong { "Website: " } a { href: "{site}", target: "_blank", "{site}" } }
                            }
                            if let Some(email) = d.team.email.as_ref().filter(|value| !value.trim().is_empty()) {
                                p { strong { "Email: " } "{email}" }
                            }
                            if let Some(source) = d.team.about.as_ref().filter(|value| !value.trim().is_empty()) {
                                ProfileBio { source: source.clone() }
                            }
                            if d.team.website.as_ref().is_none_or(|value| value.trim().is_empty())
                                && d.team.email.as_ref().is_none_or(|value| value.trim().is_empty())
                                && d.team.about.as_ref().is_none_or(|value| value.trim().is_empty()) {
                                p { class: "text-muted mb-0", "No profile details added yet." }
                            }
                        }
                    }

                    section {
                        id: "team-tournament-registrations", key: "{d.team.id}-registrations",
                        class: "profile-section", hidden: active_tab != "history", aria_labelledby: "team-tab-history",
                        div { class: "profile-section-heading",
                            h2 { class: "h5 mb-0", "Tournament Registrations ({d.registrations.len()})" }
                        }
                        div { class: "profile-section-body",
                            if d.registrations.is_empty() {
                                p { class: "text-muted", "No tournament registrations yet." }
                            } else {
                                div { class: "profile-table-scroll", role: "region", tabindex: "0",
                                    aria_label: "Tournament registrations, scroll to see all columns",
                                    table { class: "table profile-history-table", aria_label: "Tournament registrations",
                                        thead {
                                            tr {
                                                th { scope: "col", "Players" }
                                                th { scope: "col", "Tournament" }
                                                th { scope: "col", "Team Name" }
                                                th { scope: "col", class: "table-nowrap", "Date" }
                                                th { scope: "col", class: "table-nowrap", "Status" }
                                                th { scope: "col", class: "table-nowrap", "Payment" }
                                                th { scope: "col", class: "table-nowrap", "Actions" }
                                            }
                                        }
                                        tbody {
                                            { {
                                                let mut row_views = Vec::new();
                                                for reg_item in d.registrations.iter() {
                                                    let reg_clone = reg_item.clone();
                                                    let ev = reg_clone.event.clone();
                                                    let is_expanded = expanded_event() == Some(ev.clone());
                                                    let ev_for_toggle = ev.clone();
                                                    let is_own = me.read().as_ref().and_then(|r| r.as_ref().ok()).map(|u| u.can_manage_team(&d.team.id)).unwrap_or(false);
                                                    row_views.push(rsx! {
                                                        RegistrationRow {
                                                            key: "{ev}",
                                                            registration: reg_clone,
                                                            is_expanded,
                                                            on_toggle: move |_| {
                                                                expanded_event.set(if is_expanded { None } else { Some(ev_for_toggle.clone()) });
                                                            },
                                                            is_own_team: is_own,
                                                            team_id: d.team.id.clone(),
                                                            backend: backend.clone(),
                                                            players_cache,
                                                        }
                                                    });
                                                }
                                                rsx! { for row in row_views { {row} } }
                                            } }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if show_notes {
                        section {
                            id: "team-notes", key: "{d.team.id}-notes",
                            class: "profile-section", hidden: active_tab != "notes", aria_labelledby: "team-tab-notes",
                            div { class: "profile-section-heading",
                                h2 { class: "h5 mb-0", "Notes Received ({d.team_notes.len()})" }
                            }
                            div { class: "profile-section-body",
                                if d.team_notes.is_empty() { p { class: "text-muted", "No notes recorded." } }
                                if !d.team_notes.is_empty() { p { class: "small text-muted", "Times shown in your local timezone." } }
                                div { class: "profile-table-scroll", role: "region", tabindex: "0",
                                    aria_label: "Notes received, scroll to see all columns",
                                    table { class: "table table-sm", aria_label: "Notes received",
                                        thead {
                                            tr {
                                                th { scope: "col", class: "table-nowrap", "Date" }
                                                th { scope: "col", class: "table-copy", "Note" }
                                                th { scope: "col", class: "table-number", "Point #" }
                                                th { scope: "col", class: "table-copy", "Match" }
                                            }
                                        }
                                        tbody {
                                            for note in d.team_notes.iter() {
                                                tr { key: "{note.created_at.as_deref().unwrap_or(\"-\")}-{note.point_index}",
                                                    td { class: "table-nowrap", RecordTimestamp { value: note.created_at.clone() } }
                                                    td { class: "table-copy", "{note.text}" }
                                                    td { class: "table-number", "{note.point_index}" }
                                                    td { class: "table-copy",
                                                        a { href: "/{note.match_info.event}/match/{note.match_info.uuid}", "{note.match_info.name}" }
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
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "error", "{e}" }
        } else {
            p { "Loading…" }
        }
    }
}
