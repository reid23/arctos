use crate::api;
use crate::components::{ActionRecovery, PompfenIcon, RegistrationIntent, SideCompRegistrationMode, SideCompWeaponModal};
use crate::types::{EligiblePlayer, EligiblePlayersResponse};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn SideCompRegisterAsTo(url: String, comp_id: i32) -> Element {
    let mut load = use_resource(move || async move { api::sidecomp_eligible_players(comp_id).await });
    let mut refreshed = use_signal(|| None::<EligiblePlayersResponse>);
    let mut filter = use_signal(String::new);
    let snapshot = refreshed().map(Ok).or_else(|| load.read().clone());
    rsx! {
        div { class: "row",
            div { class: "col-12",
                Link { to: Route::SideCompDetail { url, comp_id }, class: "btn btn-link", "← Back to side competition" }
                h2 { "Quick Register players" }
                match snapshot.as_ref() {
                    None => rsx! { p { role: "status", "Loading eligible players…" } },
                    Some(Err(message)) => rsx! {
                        p { class: "text-danger", role: "alert", "Could not load eligible players: {message}" }
                        button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| load.restart(), "Retry loading players" }
                    },
                    Some(Ok(payload)) => {
                        let query = filter().trim().to_lowercase();
                        let rows: Vec<_> = payload.players.iter().filter(|player| query.is_empty() || player.player_name.to_lowercase().contains(&query) || player.player_id.to_lowercase().contains(&query)).cloned().collect();
                        rsx! {
                            label { r#for: "sidecomp-player-filter", class: "form-label", "Filter players by name or username" }
                            input { id: "sidecomp-player-filter", class: "form-control mb-3", r#type: "search", value: "{filter}", oninput: move |event| filter.set(event.value()) }
                            if rows.is_empty() {
                                p { class: "text-muted", if payload.players.is_empty() { "No eligible players are available." } else { "No players match this filter." } }
                            } else {
                                ul { class: "list-group mb-4",
                                    for player in rows {
                                        EligibleRow { key: "{player.player_id}", p: player, comp_id,
                                            comp_name: payload.name.clone(), allowed_weapons: payload.allowed_weapons.clone(),
                                            on_refreshed: move |current| refreshed.set(Some(current)),
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

#[component]
fn EligibleRow(
    p: EligiblePlayer,
    comp_id: i32,
    comp_name: String,
    allowed_weapons: Vec<String>,
    on_refreshed: EventHandler<EligiblePlayersResponse>,
) -> Element {
    let mut show_modal = use_signal(|| false);
    let mut recovery = use_signal(ActionRecovery::default);
    let mut requested = use_signal(|| None::<RegistrationIntent>);
    let player_id = p.player_id.clone();
    let mut change_registration = move |intent: Option<RegistrationIntent>| {
        if recovery().busy() || (intent.is_some() && recovery().blocks_write()) { return; }
        let send_write = intent.is_some();
        let Some(intent) = intent.or_else(|| requested()) else { return; };
        requested.set(Some(intent.clone()));
        recovery.set(if send_write { ActionRecovery::Pending } else { ActionRecovery::Checking });
        let player_id = player_id.clone();
        spawn(async move {
            let result = if send_write {
                match &intent {
                    RegistrationIntent::Register(weapon) => api::sidecomp_to_register_player_as_to(comp_id, &player_id, weapon).await.map(|_| ()),
                    RegistrationIntent::Update(weapon) => api::sidecomp_to_update_player_as_to(comp_id, &player_id, weapon).await.map(|_| ()),
                    RegistrationIntent::Remove => api::sidecomp_to_deregister_player_as_to(comp_id, &player_id).await.map(|_| ()),
                }
            } else { Err("Registration was not confirmed.".into()) };
            recovery.set(ActionRecovery::Checking);
            match api::sidecomp_eligible_players(comp_id).await {
                Ok(current) => {
                    let applied = current.players.iter().find(|row| row.player_id == player_id)
                        .is_some_and(|row| intent.applied(row.sidecomp_registered, row.weapon.as_deref()));
                    if applied {
                        recovery.set(ActionRecovery::Idle);
                        requested.set(None);
                        show_modal.set(false);
                        on_refreshed.call(current);
                    } else {
                        recovery.set(ActionRecovery::Retryable(result.err().unwrap_or_else(|| "Registration has not changed. Review your choice and try again.".into())));
                    }
                }
                Err(error) => recovery.set(ActionRecovery::NeedsCheck(format!("Could not confirm registration. {error} Check status before trying again."))),
            }
        });
    };
    let mut save = change_registration.clone();
    let mut remove = change_registration.clone();
    let registered = p.sidecomp_registered;
    rsx! {
        li { class: "list-group-item data-list-row d-flex flex-wrap gap-2 justify-content-between align-items-center",
            div {
                strong { "{p.player_name}" }
                if let Some(team) = p.team_pseudonym.as_ref() { span { class: "text-muted ms-2", "({team})" } }
            }
            if registered {
                div { class: "d-flex flex-wrap align-items-center gap-2",
                    span { class: "badge bg-success", if let Some(number) = p.entry_number { "Registered #{number}" } else { "Registered" } }
                    if let Some(weapon) = p.weapon.as_ref() { PompfenIcon { weapon: weapon.clone(), size: "1.5em".to_string() } }
                    button { r#type: "button", class: "btn btn-sm btn-outline-secondary", aria_label: "Edit registration for {p.player_name}", aria_haspopup: "dialog", onclick: move |_| show_modal.set(true),
                        i { class: "fas fa-pen", aria_hidden: "true" }
                    }
                }
            } else {
                button { r#type: "button", class: "btn btn-sm btn-primary", aria_label: "Quick Register {p.player_name}", aria_haspopup: "dialog", onclick: move |_| show_modal.set(true), "Quick Register" }
            }
            if show_modal() {
                SideCompWeaponModal {
                    title: format!("{comp_name} — {}", p.player_name),
                    mode: if registered { SideCompRegistrationMode::Edit } else { SideCompRegistrationMode::Register },
                    initial_weapon: p.weapon.clone(), allowed_weapons, show_deregister: registered,
                    busy: recovery().busy(), error: recovery().message().map(str::to_owned), needs_check: matches!(recovery(), ActionRecovery::NeedsCheck(_)),
                    on_close: move |_| { if !recovery().busy() { show_modal.set(false); } },
                    on_submit: move |weapon| save(Some(if registered { RegistrationIntent::Update(weapon) } else { RegistrationIntent::Register(weapon) })),
                    on_deregister: move |_| remove(Some(RegistrationIntent::Remove)),
                    on_check: move |_| change_registration(None),
                }
            }
        }
    }
}
