use crate::api;
use crate::components::{PompfenIcon, SideCompRegistrationMode, SideCompWeaponModal};
use crate::types::{EligiblePlayer, SideCompRegisterPlayerResponse};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn SideCompRegisterAsTo(url: String, comp_id: i32) -> Element {
    let mut eligible = use_signal(Vec::<EligiblePlayer>::new);
    let mut allowed_weapons = use_signal(Vec::<String>::new);
    let mut comp_name = use_signal(String::new);
    let mut filter = use_signal(String::new);
    let mut error = use_signal(|| None::<String>);

    use_effect(move || {
        spawn(async move {
            match api::sidecomp_eligible_players(comp_id).await {
                Ok(resp) => {
                    eligible.set(resp.players);
                    allowed_weapons.set(resp.allowed_weapons);
                    comp_name.set(resp.name);
                }
                Err(e) => error.set(Some(e)),
            }
        });
    });

    let url_for_back = url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12",
                Link {
                    to: Route::SideCompDetail { url: url_for_back, comp_id },
                    class: "btn btn-link",
                    "<- Back to side competition"
                }
                h1 { "Quick Register players" }
                input {
                    class: "form-control mb-3",
                    r#type: "text",
                    placeholder: "Filter by name...",
                    value: "{filter}",
                    oninput: move |evt| filter.set(evt.value()),
                }
                if let Some(err) = error() {
                    div { class: "alert alert-danger", "{err}" }
                }
                {
                    let q = filter().to_lowercase();
                    let rows: Vec<EligiblePlayer> = eligible()
                        .into_iter()
                        .filter(|p| q.is_empty() || p.player_name.to_lowercase().contains(&q))
                        .collect();
                    let weapons = allowed_weapons();
                    let name = comp_name();
                    rsx! {
                        if rows.is_empty() {
                            p { class: "text-muted", "No players found." }
                        } else {
                            ul { class: "list-group mb-4",
                                for p in rows.iter().cloned() {
                                    EligibleRow {
                                        key: "{p.player_id}",
                                        p: p.clone(),
                                        comp_id,
                                        comp_name: name.clone(),
                                        allowed_weapons: weapons.clone(),
                                        on_registered: move |registered: SideCompRegisterPlayerResponse| {
                                            let player_id = registered.player_id.clone();
                                            if let Some(row) = eligible.write().iter_mut().find(|x| x.player_id == player_id) {
                                                row.sidecomp_registered = true;
                                                row.entry_number = Some(registered.entry_number);
                                                row.weapon = registered.weapon.clone();
                                            }
                                        },
                                        on_updated: move |registered: SideCompRegisterPlayerResponse| {
                                            let player_id = registered.player_id.clone();
                                            if let Some(row) = eligible.write().iter_mut().find(|x| x.player_id == player_id) {
                                                row.weapon = registered.weapon.clone();
                                                row.entry_number = Some(registered.entry_number);
                                            }
                                        },
                                        on_deregistered: move |player_id: String| {
                                            if let Some(row) = eligible.write().iter_mut().find(|x| x.player_id == player_id) {
                                                row.sidecomp_registered = false;
                                                row.entry_number = None;
                                                row.weapon = None;
                                            }
                                        },
                                        on_error: move |e| error.set(Some(e)),
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
    on_registered: EventHandler<SideCompRegisterPlayerResponse>,
    on_updated: EventHandler<SideCompRegisterPlayerResponse>,
    on_deregistered: EventHandler<String>,
    on_error: EventHandler<String>,
) -> Element {
    let mut show_register_modal = use_signal(|| false);
    let mut show_edit_modal = use_signal(|| false);
    let p_render = p.clone();
    let player_id_register = p.player_id.clone();
    let player_id_update = p.player_id.clone();
    let player_id_deregister = p.player_id.clone();
    let weapons_reg = allowed_weapons.clone();
    let weapons_edit = allowed_weapons.clone();
    let name_reg = comp_name.clone();
    let name_edit = comp_name.clone();

    rsx! {
        li { class: "list-group-item d-flex justify-content-between align-items-center",
            div {
                strong { "{p_render.player_name}" }
                if let Some(team) = p_render.team_pseudonym.as_ref() {
                    span { class: "text-muted ms-2", "({team})" }
                }
            }
            if p_render.sidecomp_registered {
                div { class: "d-flex align-items-center gap-2",
                    span {
                        class: "badge bg-success",
                        if let Some(entry_number) = p_render.entry_number {
                            "Registered #{entry_number}"
                        } else {
                            "Registered"
                        }
                    }
                    if let Some(weapon) = p_render.weapon.as_ref() {
                        PompfenIcon { weapon: weapon.clone(), size: "1.5em".to_string() }
                    }
                    button {
                        class: "btn btn-sm btn-outline-secondary",
                        title: "Edit registration",
                        onclick: move |_| show_edit_modal.set(true),
                        i { class: "fas fa-pen" }
                    }
                }
            } else {
                button {
                    class: "btn btn-sm btn-primary",
                    onclick: move |_| show_register_modal.set(true),
                    "Quick Register"
                }
            }
            if show_register_modal() {
                SideCompWeaponModal {
                    title: format!("{name_reg} registration"),
                    mode: SideCompRegistrationMode::Register,
                    allowed_weapons: weapons_reg.clone(),
                    on_close: move |_| show_register_modal.set(false),
                    on_submit: move |weapon: String| {
                        let pid = player_id_register.clone();
                        spawn(async move {
                            match api::sidecomp_to_register_player_as_to(comp_id, &pid, &weapon).await {
                                Ok(registered) => {
                                    on_registered.call(registered);
                                    show_register_modal.set(false);
                                }
                                Err(e) => {
                                    on_error.call(e);
                                    show_register_modal.set(false);
                                }
                            }
                        });
                    },
                }
            }
            if show_edit_modal() {
                SideCompWeaponModal {
                    title: format!("{name_edit} registration"),
                    mode: SideCompRegistrationMode::Edit,
                    allowed_weapons: weapons_edit.clone(),
                    initial_weapon: p_render.weapon.clone(),
                    show_deregister: true,
                    on_close: move |_| show_edit_modal.set(false),
                    on_submit: move |weapon: String| {
                        let pid = player_id_update.clone();
                        spawn(async move {
                            match api::sidecomp_to_update_player_as_to(comp_id, &pid, &weapon).await {
                                Ok(registered) => {
                                    on_updated.call(registered);
                                    show_edit_modal.set(false);
                                }
                                Err(e) => {
                                    on_error.call(e);
                                    show_edit_modal.set(false);
                                }
                            }
                        });
                    },
                    on_deregister: move |_| {
                        let pid = player_id_deregister.clone();
                        spawn(async move {
                            match api::sidecomp_to_deregister_player_as_to(comp_id, &pid).await {
                                Ok(_) => {
                                    on_deregistered.call(pid);
                                    show_edit_modal.set(false);
                                }
                                Err(e) => {
                                    on_error.call(e);
                                    show_edit_modal.set(false);
                                }
                            }
                        });
                    },
                }
            }
        }
    }
}
