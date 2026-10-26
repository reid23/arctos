use crate::api;
use crate::components::{
    EventTab, EventTabs, Modal, ModalKind, PaymentStatusBadge, RegistrationStatusBadge,
};
use crate::types::{TeamCandidate, TeamCandidates, TeamEventData, TeamManagementData};
use crate::Route;
use dioxus::prelude::*;

const CSS: &str = include_str!("team_management.css");

#[component]
pub fn ManageTeams() -> Element {
    let account = crate::auth::use_account();
    let current = account.read();
    rsx! {
        style { {CSS} }
        div { class: "team-management",
            h1 { "Manage teams" }
            if let Some(Ok(user)) = current.as_ref() {
                if user.managed_teams.is_empty() { p { "You do not manage any teams." } }
                div { class: "team-management-list",
                    for team in &user.managed_teams {
                        Link { to: Route::ManageTeam { team_id: team.id.clone() }, class: "team-management-row",
                            div { strong { "{team.name}" } span { class: "text-muted", "@{team.id}" } }
                            i { class: "fas fa-chevron-right", aria_hidden: "true" }
                        }
                    }
                }
            } else if current.is_none() { p { role: "status", "Loading teams…" } }
            else if matches!(current.as_ref(), Some(Err(api::AccountError::Unauthenticated))) { p { "Log in to manage your teams." } Link { to: Route::Login {}, class: "btn btn-primary", "Log in" } }
        }
    }
}

#[component]
pub fn ManageTeam(team_id: String) -> Element {
    rsx! { TeamManagementHub { key: "{team_id}", team_id, tab: "registrations".to_string() } }
}

#[component]
pub fn ManageTeamWithTab(team_id: String, tab: String) -> Element {
    rsx! { TeamManagementHub { key: "{team_id}", team_id, tab } }
}

#[component]
fn TeamManagementHub(team_id: String, tab: String) -> Element {
    let mut data = use_resource(use_reactive(&team_id, |id| async move {
        api::team_management_get::<TeamManagementData>(&format!(
            "{}/management",
            urlencoding::encode(&id)
        ))
        .await
    }));
    let snapshot = data.read().clone();
    rsx! {
        style { {CSS} }
        div { class: "team-management",
            if let Some(Ok(info)) = snapshot {
                nav { aria_label: "Breadcrumb", class: "mb-3", Link { to: Route::ManageTeams {}, "Manage teams" } }
                header { class: "team-management-heading",
                    div { h1 { "{info.team.name}" } p { class: "text-muted mb-0", "@{info.team.id}" } }
                    Link { to: Route::EditTeamProfile { team_id: team_id.clone() }, class: "btn btn-outline-secondary", "Edit profile" }
                }
                if info.is_owner {
                    style { {include_str!("../components/event_details.css")} }
                    EventTabs { prefix: "team-management".to_string(), label: "Team management".to_string(),
                        active: if tab == "managers" { "managers".to_string() } else { "registrations".to_string() },
                        tabs: vec![
                            EventTab { id: "registrations".into(), label: "Registrations".into(), icon: "fas fa-calendar-alt".into(), href: Some(format!("/teams/{team_id}/manage?tab=registrations")) },
                            EventTab { id: "managers".into(), label: "Managers".into(), icon: "fas fa-users".into(), href: Some(format!("/teams/{team_id}/manage?tab=managers")) },
                        ]
                    }
                }
                if !info.is_owner || tab != "managers" {
                section { class: "team-management-section", aria_labelledby: "team-registration-heading",
                    div { class: "team-management-heading",
                        h2 { id: "team-registration-heading", class: "h4", "Registrations & rosters" }
                        Link { to: Route::Index {}, class: "btn btn-outline-secondary", "Find events" }
                    }
                    if info.registrations.is_empty() { p { class: "text-muted", "No event or league registrations yet. Open an event and choose Manage team to register." } }
                    div { class: "team-management-list",
                        for registration in &info.registrations {
                            Link { to: Route::ManageTeamEvent { team_id: team_id.clone(), kind: registration.kind.clone(), slug: registration.slug.clone() }, class: "team-management-row",
                                div { strong { "{registration.name}" }
                                    span { class: "text-muted", if let Some(date) = &registration.start_date { "{crate::time_format::format_calendar_range(date, None)}" } else if registration.kind == "leagues" { "League" } else { "Event" } }
                                }
                                div { class: "team-row-actions",
                                    if registration.pending_count > 0 { span { class: "badge arctos-badge-warning", "{registration.pending_count} pending" } }
                                    i { class: "fas fa-chevron-right", aria_hidden: "true" }
                                }
                            }
                        }
                    }
                }
                }
                if info.is_owner && tab == "managers" { TeamManagers { key: "{team_id}", team_id: team_id.clone(), team_name: info.team.name.clone() } }
            } else if let Some(Err(error)) = snapshot {
                h1 { "Manage team" } p { role: "alert", "{error}" }
                button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| data.restart(), "Retry" }
            } else { p { role: "status", "Loading team…" } }
        }
    }
}

#[component]
fn TeamManagers(team_id: String, team_name: String) -> Element {
    let mut data = use_resource(use_reactive(&team_id, |id| async move {
        api::team_management_get::<TeamCandidates>(&format!(
            "{}/managers",
            urlencoding::encode(&id)
        ))
        .await
    }));
    let mut picker = use_signal(|| false);
    let mut remove = use_signal(|| None::<TeamCandidate>);
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut notice = use_signal(|| None::<String>);
    let path = format!("{}/managers", urlencoding::encode(&team_id));
    let write = move |(player, adding): (TeamCandidate, bool)| {
        if busy() {
            return;
        }
        busy.set(true);
        error.set(None);
        notice.set(None);
        let path = format!("{}/{}", path, urlencoding::encode(&player.id));
        spawn(async move {
            match api::team_management_write(
                &path,
                if adding { "PUT" } else { "DELETE" },
                &serde_json::json!({}),
            )
            .await
            {
                Ok(_) => {
                    picker.set(false);
                    remove.set(None);
                    data.restart();
                    notice.set(Some(if adding {
                        format!("{} was added as a team manager.", player.name)
                    } else {
                        format!("{} no longer manages this team.", player.name)
                    }));
                }
                Err(message) => error.set(Some(message)),
            }
            busy.set(false);
        });
    };
    let on_write = EventHandler::new(write);
    let snapshot = data.read().clone();
    rsx! {
        section { class: "team-management-section", aria_labelledby: "team-managers-heading",
            div { class: "team-management-heading",
                h2 { id: "team-managers-heading", class: "h4", "Managers" }
                button { id: "add-team-manager", r#type: "button", class: "btn btn-outline-secondary", aria_haspopup: "dialog", onclick: move |_| { error.set(None); picker.set(true); }, "Add manager" }
            }
            p { class: "text-muted", "Managers can edit the team profile, manage registrations and rosters, and organize events and leagues assigned to the team. Only the team account can add or remove managers." }
            if let Some(message) = notice() { p { role: "status", "{message}" } }
            if !picker() && remove().is_none() { if let Some(message) = error() { p { class: "text-danger", role: "alert", "{message}" } } }
            if let Some(Ok(managers)) = snapshot {
                if managers.players.is_empty() { p { class: "text-muted", "No managers added." } }
                div { class: "team-management-list",
                    for player in managers.players {
                        div { class: "team-management-row",
                            PlayerIdentity { player: player.clone() }
                            button { r#type: "button", class: "btn btn-outline-secondary", aria_label: format!("Remove {} as manager", player.name), disabled: busy(), onclick: move |_| { error.set(None); remove.set(Some(player.clone())); }, "Remove" }
                        }
                    }
                }
            } else if let Some(Err(message)) = snapshot { p { role: "alert", "{message}" } button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| data.restart(), "Retry managers" } }
            else { p { role: "status", "Loading managers…" } }
        }
        if picker() {
            TeamPlayerPicker { path: format!("{}/manager-candidates", urlencoding::encode(&team_id)),
                title: format!("Add a manager to {team_name}"), help: "Choose a player account to add as a team manager. They can edit the team profile, manage registrations and rosters, and organize events and leagues assigned to the team. Players join event and league rosters separately.".to_string(),
                search_first: true, busy: busy(), error: error(), action: "Add manager".to_string(),
                on_close: move |_| picker.set(false), on_select: move |player| on_write.call((player, true)) }
        }
        if let Some(player) = remove() {
            Modal { id: "remove-team-manager", labelled_by: "remove-manager-title", kind: ModalKind::AlertDialog,
                dismissible: !busy(), on_close: move |_| remove.set(None),
                div { class: "modal-header", h2 { id: "remove-manager-title", class: "modal-title h5", tabindex: "-1", "Remove {player.name} as a manager?" } }
                div { class: "modal-body",
                    p { "They will lose access granted through {team_name}. Their player registrations and any separate organizer roles will stay unchanged." }
                    if let Some(message) = error() { p { role: "alert", class: "text-danger", "{message}" } }
                }
                div { class: "modal-footer",
                    button { r#type: "button", class: "btn btn-secondary", disabled: busy(), onclick: move |_| remove.set(None), "Keep manager" }
                    button { r#type: "button", class: "btn btn-danger", disabled: busy(), onclick: move |_| on_write.call((player.clone(), false)), if busy() { "Removing…" } else { "Remove manager" } }
                }
            }
        }
    }
}

#[component]
fn PlayerIdentity(player: TeamCandidate) -> Element {
    rsx! { div { class: "team-player-identity",
        if let Some(photo) = player.profile_photo { img { src: api::static_file_url(&photo), alt: "", class: "team-player-avatar" } }
        div { strong { "{player.name}" } span { class: "text-muted", "@{player.id}" } }
    } }
}

#[component]
fn TeamPlayerPicker(
    path: String,
    title: String,
    help: String,
    search_first: bool,
    busy: bool,
    error: Option<String>,
    action: String,
    on_close: EventHandler<()>,
    on_select: EventHandler<TeamCandidate>,
) -> Element {
    let mut search = use_signal(String::new);
    let data = use_resource(use_reactive(
        &(path, search, search_first),
        |(path, search, search_first)| async move {
            let query = search();
            if search_first && query.trim().is_empty() {
                return Ok(TeamCandidates { players: vec![] });
            }
            #[cfg(target_arch = "wasm32")]
            gloo_timers::future::TimeoutFuture::new(200).await;
            api::team_management_get::<TeamCandidates>(&format!(
                "{path}?search={}",
                urlencoding::encode(query.trim())
            ))
            .await
        },
    ));
    let pending = *data.state().read() == UseResourceState::Pending;
    rsx! {
        Modal { id: "team-player-picker", labelled_by: "team-player-picker-title", dismissible: !busy, on_close,
            div { class: "modal-header",
                h2 { id: "team-player-picker-title", class: "modal-title h5", tabindex: "-1", "{title}" }
                button { r#type: "button", class: "btn-close", aria_label: "Close", disabled: busy, onclick: move |_| on_close.call(()) }
            }
            div { class: "modal-body",
                p { id: "team-player-help", class: "text-muted", "{help}" }
                label { r#for: "team-player-search", class: "form-label", "Find a player" }
                input { id: "team-player-search", r#type: "search", class: "form-control", aria_describedby: "team-player-help", placeholder: "Name or username", value: search, disabled: busy, oninput: move |event| search.set(event.value()) }
                if let Some(message) = error { p { role: "alert", class: "text-danger mt-2", "{message}" } }
                if pending { p { role: "status", class: "mt-3", "Searching…" } }
                else if let Some(Ok(result)) = data.read().as_ref() {
                    if result.players.is_empty() {
                        p { class: "text-muted mt-3", role: "status", if search_first && search().trim().is_empty() { "Enter a name or username to search." } else { "No players found." } }
                    }
                    div { class: "team-management-list team-picker-results",
                        for player in &result.players {
                            div { class: "team-management-row",
                                PlayerIdentity { player: player.clone() }
                                button { r#type: "button", class: "btn btn-outline-primary", disabled: busy,
                                    aria_label: format!("{} {}", action, player.name),
                                    onclick: { let player = player.clone(); move |_| on_select.call(player.clone()) }, "{action}" }
                            }
                        }
                    }
                } else if let Some(Err(message)) = data.read().as_ref() { p { role: "alert", class: "text-danger mt-3", "{message}" } }
            }
            div { class: "modal-footer", button { r#type: "button", class: "btn btn-secondary", disabled: busy, onclick: move |_| on_close.call(()), "Cancel" } }
        }
    }
}

#[component]
pub fn ManageTeamEvent(team_id: String, kind: String, slug: String) -> Element {
    rsx! { TeamEventPanel { key: "{team_id}/{kind}/{slug}", team_id, kind, slug } }
}

#[derive(Clone)]
struct TeamAction {
    path: String,
    method: String,
    body: serde_json::Value,
    message: String,
}

fn team_action_applied(action: &TeamAction, current: &TeamEventData) -> bool {
    if action.path.ends_with("/registration") {
        if action.method == "DELETE" {
            return !current.registered;
        }
        return current.registered
            && action
                .body
                .get("pseudonym")
                .and_then(|value| value.as_str())
                == Some(current.pseudonym.as_str())
            && action
                .body
                .get("shortname")
                .and_then(|value| value.as_str())
                .unwrap_or("")
                == current.shortname.as_deref().unwrap_or("");
    }
    let parts: Vec<_> = action.path.rsplit('/').take(2).collect();
    let Some(id) = parts.get(1).and_then(|value| value.parse::<u32>().ok()) else {
        return false;
    };
    let member = current
        .roster
        .iter()
        .find(|member| member.registration_id == id);
    match parts.first().copied() {
        Some("approve" | "add") => member.is_some_and(|member| member.status == "CONFIRMED"),
        Some("remove" | "decline") => member.is_none(),
        _ => false,
    }
}

#[component]
pub fn TeamEventPanel(team_id: String, kind: String, slug: String) -> Element {
    let base_path = format!(
        "{}/{}/{}",
        urlencoding::encode(&team_id),
        urlencoding::encode(&kind),
        urlencoding::encode(&slug)
    );
    let mut data = use_resource(use_reactive(&base_path, |path| async move {
        api::team_management_get::<TeamEventData>(&format!("{path}/registration")).await
    }));
    let mut busy = use_signal(|| false);
    let mut needs_check = use_signal(|| false);
    let mut confirmed = use_signal(|| None::<TeamEventData>);
    let mut error = use_signal(|| None::<String>);
    let mut notice = use_signal(|| None::<String>);
    let mut picker = use_signal(|| false);
    let mut editing = use_signal(|| false);
    let mut confirmation = use_signal(|| None::<(TeamAction, String, String)>);
    let refresh_path = format!("{base_path}/registration");
    let write_path = refresh_path.clone();
    let write = EventHandler::new(move |action: TeamAction| {
        if busy() || needs_check() {
            return;
        }
        busy.set(true);
        error.set(None);
        notice.set(None);
        let path = write_path.clone();
        spawn(async move {
            let result =
                api::team_management_write(&action.path, &action.method, &action.body).await;
            match api::team_management_get::<TeamEventData>(&path).await {
                Ok(current) => {
                    let applied = team_action_applied(&action, &current);
                    confirmed.set(Some(current));
                    if applied {
                        picker.set(false);
                        confirmation.set(None);
                        editing.set(false);
                        notice.set(Some(action.message));
                    } else {
                        error.set(Some(result.err().unwrap_or_else(|| {
                            "The change was not saved. Review the current roster and try again."
                                .into()
                        })));
                    }
                }
                Err(message) => {
                    needs_check.set(true);
                    picker.set(false);
                    confirmation.set(None);
                    error.set(Some(format!(
                        "Could not confirm the change. Check status before trying again. {message}"
                    )));
                }
            }
            busy.set(false);
        });
    });
    let check = EventHandler::new(move |_| {
        if busy() {
            return;
        }
        busy.set(true);
        let path = refresh_path.clone();
        spawn(async move {
            match api::team_management_get::<TeamEventData>(&path).await {
                Ok(current) => {
                    confirmed.set(Some(current));
                    needs_check.set(false);
                    error.set(None);
                    picker.set(false);
                    confirmation.set(None);
                    notice.set(Some("Registration and roster refreshed. Review their current status before making another change.".into()));
                }
                Err(message) => error.set(Some(format!("Could not refresh the roster. {message}"))),
            }
            busy.set(false);
        });
    });
    let snapshot = confirmed().map(Ok).or_else(|| data.read().clone());
    rsx! {
        style { {CSS} }
        div { class: "team-management",
            if let Some(Ok(info)) = snapshot {
                nav { aria_label: "Breadcrumb", class: "mb-3", Link { to: Route::ManageTeam { team_id: team_id.clone() }, "{info.team.name}" } " / Registration & roster" }
                header { class: "team-management-heading",
                    div { p { class: "text-muted mb-1", "Managing {info.team.name}" } h1 { "{info.event.name}" } }
                    Link { to: if info.event.kind == "leagues" { Route::LeagueHome { league_url: info.event.slug.clone() } } else { Route::TournamentHome { url: info.event.slug.clone() } }, class: "btn btn-outline-secondary", if info.event.kind == "leagues" { "View league" } else { "View event" } }
                }
                if needs_check() {
                    div { class: "alert alert-warning", role: "status",
                        p { "The latest change could not be confirmed." }
                        button { r#type: "button", class: "btn btn-outline-secondary", disabled: busy(), onclick: move |_| check.call(()), "Check status" }
                    }
                }
                if let Some(message) = notice() { p { role: "status", class: "alert alert-success", "{message}" } }
                if confirmation().is_none() && !picker() { if let Some(message) = error() { p { role: "alert", class: "alert alert-danger", "{message}" } } }
                section { class: "team-management-section", aria_labelledby: "managed-registration-title",
                    div { class: "team-management-heading",
                        h2 { id: "managed-registration-title", class: "h4", "Team registration" }
                        if info.registered && !editing() {
                            button { r#type: "button", class: "btn btn-outline-secondary", disabled: !info.registration_open || busy() || needs_check(), onclick: move |_| editing.set(true), "Edit registration" }
                        }
                    }
                    if !info.registered || editing() {
                        TeamRegistrationForm { key: "{info.registered}", info: info.clone(), busy: busy() || needs_check(),
                            on_cancel: move |_| editing.set(false), on_save: {
                                let path = format!("{base_path}/registration"); let registered = info.registered;
                                move |body| write.call(TeamAction { path: path.clone(), method: if registered { "PUT" } else { "POST" }.into(), body, message: if registered { "Team registration saved." } else { "Team registered." }.into() })
                            }
                        }
                    } else {
                        div { class: "team-registration-summary",
                            strong { "{info.pseudonym}" }
                            if let Some(shortname) = info.shortname { span { class: "text-muted", "{shortname}" } }
                            span { class: "badge arctos-badge-success", "Registered" }
                            PaymentStatusBadge { paid: info.paid }
                        }
                        if !info.registration_open { p { class: "text-muted mt-2", "Registration changes are closed." } }
                    }
                }
                if info.registered {
                    { let confirmed = info.roster.iter().filter(|player| player.status == "CONFIRMED").count();
                      let full = info.roster_limit.filter(|limit| *limit > 0).is_some_and(|limit| confirmed >= limit as usize);
                    rsx! {
                        section { class: "team-management-section", aria_labelledby: "managed-roster-title",
                            div { class: "team-management-heading",
                                h2 { id: "managed-roster-title", class: "h4", "Roster" span { class: "text-muted ms-2 small", "{confirmed}" if let Some(limit) = info.roster_limit.filter(|limit| *limit > 0) { " / {limit}" } } }
                                button { r#type: "button", class: "btn btn-outline-primary", disabled: !info.roster_open || full || busy() || needs_check(), aria_haspopup: "dialog", onclick: move |_| { error.set(None); picker.set(true); }, "Add player" }
                            }
                            if full { p { class: "text-muted", "This team has reached the roster limit." } }
                            if !info.roster_open { p { class: "text-muted", "Adding or removing players is closed. You can still review pending requests." } }
                            if info.roster.is_empty() { p { class: "text-muted", "No players on this roster yet." } }
                            div { class: "table-responsive team-roster-wrapper",
                                table { class: "table roster-table team-managed-roster", role: "table",
                                    caption { class: "visually-hidden", "{info.team.name} roster for {info.event.name}" }
                                    thead { role: "rowgroup", tr { role: "row", th { role: "columnheader", scope: "col", "Player" } th { role: "columnheader", scope: "col", "Jersey name" } th { role: "columnheader", scope: "col", class: "table-number", "Number" } th { role: "columnheader", scope: "col", "Status" } th { role: "columnheader", scope: "col", "Actions" } } }
                                    tbody { role: "rowgroup",
                                        for member in &info.roster {
                                            { let pending = member.status == "PENDING_TEAM_APPROVAL";
                                              let active = member.status == "CONFIRMED";
                                              let member_path = format!("{base_path}/roster/{}", member.registration_id);
                                              let name = member.player.name.clone();
                                            rsx! { tr { role: "row",
                                                td { role: "cell", class: "table-copy", Link { to: Route::PlayerProfilePage { id: member.player.id.clone() }, "{member.player.name}" } span { class: "d-block small text-muted", "@{member.player.id}" } }
                                                td { role: "cell", class: "table-copy", "data-label": "Jersey name", "{member.jersey_name.as_deref().filter(|value| !value.trim().is_empty()).unwrap_or(\"—\")}" }
                                                td { role: "cell", class: "table-number", "data-label": "Number", "{member.jersey_number.as_deref().filter(|value| !value.trim().is_empty()).unwrap_or(\"—\")}" }
                                                td { role: "cell", "data-label": "Status", if pending { span { class: "badge arctos-badge-warning", "Awaiting approval" } } else { if !active { RegistrationStatusBadge { status: member.status.clone() } } PaymentStatusBadge { paid: member.paid } if member.amount_paid > 0.0 { span { class: "small text-muted ms-1", "${member.amount_paid:.2}" } } } }
                                                td { role: "cell", class: "roster-actions", "data-label": "Actions",
                                                    if active || pending {
                                                    div { class: "team-row-actions",
                                                        if pending {
                                                            button { r#type: "button", class: "btn btn-outline-success", disabled: busy() || needs_check() || full, aria_label: format!("Approve {}", name), onclick: { let path = format!("{member_path}/approve"); move |_| write.call(TeamAction { path: path.clone(), method: "POST".into(), body: serde_json::json!({}), message: "Player approved.".into() }) },
                                                                i { class: "fas fa-check", aria_hidden: "true" }
                                                                "Approve"
                                                            }
                                                        }
                                                        button { r#type: "button", class: if pending { "btn btn-outline-danger" } else { "btn btn-outline-secondary" }, disabled: busy() || needs_check() || (!pending && !info.roster_open), aria_haspopup: "dialog",
                                                            aria_label: if pending { format!("Decline {}", name) } else { format!("Remove {} from team", name) },
                                                            onclick: { let path = format!("{member_path}/{}", if pending { "decline" } else { "remove" }); let name = name.clone(); move |_| {
                                                                error.set(None); confirmation.set(Some((TeamAction { path: path.clone(), method: "POST".into(), body: serde_json::json!({}), message: if pending { "Player request declined." } else { "Player removed from team." }.into() },
                                                                    if pending { format!("Decline {name}'s request?") } else { format!("Remove {name} from this team?") },
                                                                    "The player will remain registered for the event, unattached to a team. Their payment and waiver will stay unchanged.".into())));
                                                            } },
                                                            if pending {
                                                                i { class: "fas fa-xmark", aria_hidden: "true" }
                                                                "Decline"
                                                            } else { "Remove" }
                                                        }
                                                    }
                                                }
                                                }
                                            } }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    } }
                    if info.registration_open {
                        div { class: "team-management-footer",
                            button { r#type: "button", class: "btn btn-outline-danger", disabled: busy() || needs_check(), aria_haspopup: "dialog",
                                onclick: { let path = format!("{base_path}/registration"); let name = info.team.name.clone(); let event = info.event.name.clone(); let is_league = info.event.kind == "leagues"; move |_| {
                                    error.set(None); confirmation.set(Some((TeamAction { path: path.clone(), method: "DELETE".into(), body: serde_json::json!({}), message: "Team registration cancelled.".into() }, format!("Cancel {name}'s registration?"), format!("The team and its players will be withdrawn from {event}.{}", if is_league { "" } else { " Their side-event entries will also be removed." }))));
                                } }, "Cancel team registration"
                            }
                        }
                    }
                }
                if picker() {
                    TeamPlayerPicker { path: format!("{base_path}/roster-candidates"), title: format!("Add a player to {}", info.team.name),
                        help: "Choose a player who is registered for this event and is not on another team.".to_string(), search_first: false, busy: busy(), error: error(), action: "Add player".to_string(), on_close: move |_| picker.set(false),
                        on_select: { let path = base_path.clone(); move |player: TeamCandidate| { if let Some(id) = player.registration_id { write.call(TeamAction { path: format!("{path}/roster/{id}/add"), method: "POST".into(), body: serde_json::json!({}), message: format!("{} added to the roster.", player.name) }); } } }
                    }
                }
            } else if let Some(Err(message)) = snapshot { h1 { "Manage team" } p { role: "alert", "{message}" } button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| data.restart(), "Retry" } }
            else { p { role: "status", "Loading registration & roster…" } }
        }
        if let Some((action, title, description)) = confirmation() {
            Modal { id: "team-action-confirmation", labelled_by: "team-action-title", kind: ModalKind::AlertDialog, dismissible: !busy(), on_close: move |_| confirmation.set(None),
                div { class: "modal-header", h2 { id: "team-action-title", class: "modal-title h5", tabindex: "-1", "{title}" } }
                div { class: "modal-body", p { "{description}" } if let Some(message) = error() { p { role: "alert", class: "text-danger", "{message}" } } }
                div { class: "modal-footer",
                    button { r#type: "button", class: "btn btn-secondary", disabled: busy(), onclick: move |_| confirmation.set(None), "Go back" }
                    button { r#type: "button", class: "btn btn-danger", disabled: busy() || needs_check(), onclick: move |_| write.call(action.clone()), if busy() { "Saving…" } else if action.method == "DELETE" { "Cancel registration" } else if action.path.ends_with("decline") { "Decline request" } else { "Remove from team" } }
                }
            }
        }
    }
}

#[component]
fn TeamRegistrationForm(
    info: TeamEventData,
    busy: bool,
    on_save: EventHandler<serde_json::Value>,
    on_cancel: EventHandler<()>,
) -> Element {
    let mut name = use_signal(|| info.pseudonym.clone());
    let mut shortname = use_signal(|| info.shortname.clone().unwrap_or_default());
    rsx! {
        if info.registration_open {
            form { class: "team-registration-form", onsubmit: move |event| { event.prevent_default(); if !busy { on_save.call(serde_json::json!({"pseudonym": name().trim(), "shortname": shortname().trim()})); } },
                div { label { class: "form-label", r#for: "managed-team-name", "Team name for this event" }
                    input { id: "managed-team-name", class: "form-control", required: true, maxlength: "100", disabled: busy, value: name, oninput: move |event| name.set(event.value()) } }
                div { label { class: "form-label", r#for: "managed-team-shortname", "Short name (optional)" }
                    input { id: "managed-team-shortname", class: "form-control", maxlength: "8", disabled: busy, value: shortname, oninput: move |event| shortname.set(event.value()), aria_describedby: "managed-shortname-help" }
                    p { id: "managed-shortname-help", class: "form-text", "Up to 8 characters for schedules and brackets." } }
                if !info.registered {
                    if let Some(fee) = info.team_fee.filter(|fee| *fee > 0.0) {
                        p { class: "mb-0", strong { "Team fee: ${fee:.2}" } }
                        p { class: "text-muted mb-0", "Pay the organizer separately. They will confirm payment in Arctos." }
                    }
                    if let Some(payment) = info.payment_info.as_ref().filter(|value| !value.trim().is_empty()) {
                        p { class: "team-payment-info", "{payment}" }
                    }
                    if let Some(terms) = info.terms_link.as_ref().filter(|url| url.starts_with("https://") || url.starts_with("http://")) {
                        a { href: "{terms}", target: "_blank", rel: "noopener noreferrer", "Registration terms" }
                    }
                }
                div { class: "team-row-actions",
                    button { r#type: "submit", class: "btn btn-primary", disabled: busy, if busy { "Saving…" } else if info.registered { "Save registration" } else { "Register team" } }
                    if info.registered { button { r#type: "button", class: "btn btn-outline-secondary", disabled: busy, onclick: move |_| on_cancel.call(()), "Cancel" } }
                }
            }
        } else { p { class: "text-muted", "Team registration is closed." } }
    }
}

#[component]
pub fn ManagedTeamActions(kind: String, slug: String) -> Element {
    let account = crate::auth::use_account();
    let mut open = use_signal(|| false);
    let current = account.read();
    let Some(Ok(user)) = current.as_ref() else {
        return rsx! {};
    };
    if user.user_type != "player" || user.managed_teams.is_empty() {
        return rsx! {};
    }
    if user.managed_teams.len() == 1 {
        return rsx! { Link { to: Route::ManageTeamEvent { team_id: user.managed_teams[0].id.clone(), kind, slug }, class: "btn btn-outline-secondary", "Manage team" } };
    }
    rsx! {
        div { class: if open() { "organize-menu is-open" } else { "organize-menu" },
            onkeydown: move |event| { if event.key() == Key::Escape && open() { open.set(false); event.stop_propagation(); crate::appearance::focus_control("managed-team-trigger"); } },
            button { r#type: "button", id: "managed-team-trigger", class: "btn btn-outline-secondary", aria_expanded: open(), aria_controls: "managed-team-options", onclick: move |_| open.set(!open()), "Manage team" i { class: "fas fa-chevron-down ms-2", aria_hidden: "true" } }
            if open() {
                button { r#type: "button", class: "organize-dismiss", tabindex: "-1", aria_label: "Close team choices", onclick: move |_| { open.set(false); crate::appearance::focus_control("managed-team-trigger"); } }
                div { id: "managed-team-options", class: "organize-dropdown",
                    for team in &user.managed_teams {
                        Link { to: Route::ManageTeamEvent { team_id: team.id.clone(), kind: kind.clone(), slug: slug.clone() }, class: "dropdown-item", onclick: move |_| open.set(false), "{team.name}" }
                    }
                }
            }
        }
    }
}
