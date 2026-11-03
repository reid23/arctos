use crate::api;
use crate::types::{IncomingRosterRequest, InvitationTournament, MyInvitationsResponse, TeamClaimInvitationItem};
use crate::Route;
use dioxus::prelude::*;

fn tournament_date(tournament: &InvitationTournament) -> String {
    tournament
        .start_date
        .as_deref()
        .map(|date| date.split('T').next().unwrap_or(date).to_string())
        .unwrap_or_default()
}

/// Historical-tournament invitations for the signed-in team (claims) or player
/// (rosters). Renders nothing when there are none.
#[component]
pub fn PendingInvitations(
    /// Only show invitations for this tournament.
    #[props(optional)]
    tournament_url: Option<String>,
    /// Called after the user accepts or declines.
    #[props(optional)]
    on_respond: Option<EventHandler<()>>,
) -> Element {
    let mut refresh = use_signal(|| 0u32);
    let mut error = use_signal(|| None::<String>);
    let mut responding = use_signal(|| false);
    let auth_version = use_context::<Signal<u32>>();
    let account = crate::auth::use_account();
    let tournament_for_personal = tournament_url.clone();
    let invitations = use_resource(move || {
        let _ = refresh();
        let _ = auth_version();
        let tournament_url = tournament_for_personal.clone();
        async move { api::my_invitations(tournament_url.as_deref()).await }
    });
    let managed_invitations = use_resource(move || {
        let _ = refresh();
        let _ = auth_version();
        let user = account.read().as_ref().and_then(|result| result.as_ref().ok()).cloned();
        let tournament_url = tournament_url.clone();
        async move {
            let mut results = Vec::new();
            if let Some(user) = user {
                for team in user.managed_teams {
                    // The personal query already includes a team account's own invitations.
                    if user.user_type == "team" && team.id == user.id {
                        continue;
                    }
                    let result = api::managed_team_invitations(tournament_url.as_deref(), &team.id).await;
                    results.push((team.name, result));
                }
            }
            results
        }
    });

    let respond = use_callback(move |(is_claim, invitation_id, accept): (bool, u32, bool)| {
        if responding() {
            return;
        }
        responding.set(true);
        spawn(async move {
            let result = if is_claim {
                api::respond_to_claim_invitation(invitation_id, accept).await
            } else {
                api::respond_to_roster_invitation(invitation_id, accept).await
            };
            match result {
                Ok(()) => {
                    error.set(None);
                    refresh.set(refresh() + 1);
                    if let Some(handler) = on_respond {
                        handler.call(());
                    }
                }
                Err(message) => error.set(Some(message)),
            }
            responding.set(false);
        });
    });

    let data = invitations.read().as_ref().and_then(|result| result.as_ref().ok()).cloned().unwrap_or_default();
    let managed_data = managed_invitations.read().as_ref().cloned().unwrap_or_default();
    let mut claims: Vec<(Option<String>, TeamClaimInvitationItem)> = data.team_claims.iter().cloned().map(|item| (None, item)).collect();
    let mut incoming: Vec<(Option<String>, IncomingRosterRequest)> = data.incoming_roster_requests.iter().cloned().map(|item| (None, item)).collect();
    let mut load_errors = Vec::new();
    for (team_name, result) in managed_data {
        match result {
            Ok(team_data) => {
                claims.extend(team_data.team_claims.into_iter().map(|item| (Some(team_name.clone()), item)));
                incoming.extend(team_data.incoming_roster_requests.into_iter().map(|item| (Some(team_name.clone()), item)));
            }
            Err(message) => load_errors.push(format!("Could not load invitations for {team_name}: {message}")),
        }
    }
    if claims.is_empty() && data.roster_invitations.is_empty() && incoming.is_empty() && load_errors.is_empty() {
        return rsx! {};
    }

    rsx! {
        section { class: "edit-section mb-4",
            div { class: "edit-section-heading",
                h2 { class: "h5 mb-0",
                    i { class: "fas fa-envelope-open-text me-2", aria_hidden: "true" }
                    "Invitations"
                }
            }
            if let Some(message) = error() {
                div { class: "alert alert-danger mb-3 py-2", role: "alert", "{message}" }
            }
            for message in load_errors {
                p { class: "alert alert-danger mb-3 py-2", role: "alert", "{message}" }
            }
            ul { class: "list-group list-group-flush",
                for (team_name, claim) in claims.iter() {
                    {
                        let invitation_id = claim.id;
                        let date = tournament_date(&claim.tournament);
                        rsx! {
                            li { key: "claim-{claim.id}", class: "list-group-item d-flex flex-wrap justify-content-between align-items-center gap-2",
                                div {
                                    if let Some(name) = team_name { div { class: "small text-muted", "For {name}" } }
                                    div {
                                        "Claim the results of "
                                        strong { "{claim.placeholder_name}" }
                                        " at "
                                        Link { to: Route::TournamentHome { url: claim.tournament.url.clone() }, "{claim.tournament.name}" }
                                        if !date.is_empty() {
                                            span { class: "text-muted", " ({date})" }
                                        }
                                    }
                                    div { class: "small text-muted",
                                        "Accepting lists your team in this tournament's results and adds it to your team's history."
                                    }
                                }
                                div { class: "d-flex gap-2",
                                    button {
                                        class: "btn btn-primary",
                                        disabled: responding(),
                                        onclick: move |_| respond.call((true, invitation_id, true)),
                                        "Accept"
                                    }
                                    button {
                                        class: "btn btn-outline-secondary",
                                        disabled: responding(),
                                        onclick: move |_| respond.call((true, invitation_id, false)),
                                        "Decline"
                                    }
                                }
                            }
                        }
                    }
                }
                for invitation in data.roster_invitations.iter() {
                    {
                        let invitation_id = invitation.id;
                        let date = tournament_date(&invitation.tournament);
                        let jersey = match (&invitation.jersey_number, &invitation.jersey_name) {
                            (Some(number), Some(name)) => format!("#{number} {name}"),
                            (None, Some(name)) => name.clone(),
                            (Some(number), None) => format!("#{number}"),
                            (None, None) => String::new(),
                        };
                        rsx! {
                            li { key: "roster-{invitation.id}", class: "list-group-item d-flex flex-wrap justify-content-between align-items-center gap-2",
                                div {
                                    div {
                                        "You played for "
                                        strong { "{invitation.team_name}" }
                                        " at "
                                        Link { to: Route::TournamentHome { url: invitation.tournament.url.clone() }, "{invitation.tournament.name}" }
                                        if !date.is_empty() {
                                            span { class: "text-muted", " ({date})" }
                                        }
                                        "?"
                                    }
                                    if !jersey.is_empty() {
                                        div { class: "small text-muted", "Jersey: {jersey}" }
                                    }
                                }
                                div { class: "d-flex gap-2",
                                    button {
                                        class: "btn btn-primary",
                                        disabled: responding(),
                                        onclick: move |_| respond.call((false, invitation_id, true)),
                                        "Yes, add me"
                                    }
                                    button {
                                        class: "btn btn-outline-secondary",
                                        disabled: responding(),
                                        onclick: move |_| respond.call((false, invitation_id, false)),
                                        "Decline"
                                    }
                                }
                            }
                        }
                    }
                }
                for (team_name, request) in incoming.iter() {
                    {
                        let registration_id = request.id;
                        let approve_url = request.tournament.url.clone();
                        let decline_url = request.tournament.url.clone();
                        let approve_team = request.team_id.clone();
                        let decline_team = request.team_id.clone();
                        let date = tournament_date(&request.tournament);
                        rsx! {
                            li { key: "roster-request-{request.id}", class: "list-group-item d-flex flex-wrap justify-content-between align-items-center gap-2",
                                div {
                                    if let Some(name) = team_name { div { class: "small text-muted", "For {name}" } }
                                    strong { "{request.player_name}" }
                                    " asked to join "
                                    strong { "{request.team_name}" }
                                    " at "
                                    Link { to: Route::TournamentHome { url: request.tournament.url.clone() }, "{request.tournament.name}" }
                                    if !date.is_empty() {
                                        span { class: "text-muted", " ({date})" }
                                    }
                                }
                                div { class: "d-flex gap-2",
                                    button {
                                        class: "btn btn-primary",
                                        disabled: responding(),
                                        onclick: move |_| {
                                            let tournament_url = approve_url.clone();
                                            let team_id = approve_team.clone();
                                            spawn(async move {
                                                responding.set(true);
                                                match api::historical_decide_roster_request(&tournament_url, &team_id, registration_id, true).await {
                                                    Ok(_) => {
                                                        error.set(None);
                                                        refresh.set(refresh() + 1);
                                                        if let Some(handler) = on_respond {
                                                            handler.call(());
                                                        }
                                                    }
                                                    Err(message) => error.set(Some(message)),
                                                }
                                                responding.set(false);
                                            });
                                        },
                                        "Approve"
                                    }
                                    button {
                                        class: "btn btn-outline-secondary",
                                        disabled: responding(),
                                        onclick: move |_| {
                                            let tournament_url = decline_url.clone();
                                            let team_id = decline_team.clone();
                                            spawn(async move {
                                                responding.set(true);
                                                match api::historical_decide_roster_request(&tournament_url, &team_id, registration_id, false).await {
                                                    Ok(_) => {
                                                        error.set(None);
                                                        refresh.set(refresh() + 1);
                                                        if let Some(handler) = on_respond {
                                                            handler.call(());
                                                        }
                                                    }
                                                    Err(message) => error.set(Some(message)),
                                                }
                                                responding.set(false);
                                            });
                                        },
                                        "Decline"
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
