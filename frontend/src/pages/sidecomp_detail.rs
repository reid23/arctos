use crate::api;
use crate::components::{PompfenIcon, SideCompRegistrationMode, SideCompWeaponModal};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn SideCompDetail(url: String, comp_id: i32) -> Element {
    let mut detail = use_resource(move || async move { api::sidecomp_detail(comp_id).await });
    let me = use_resource(move || async move { api::me().await });
    let mut action_error = use_signal(|| None::<String>);
    let mut show_register_modal = use_signal(|| false);
    let mut show_edit_modal = use_signal(|| false);

    let url_for_back = url.clone();
    let url_for_edit = url.clone();
    let url_for_register = url.clone();
    let url_for_enter = url.clone();
    let url_for_results = url.clone();
    let url_for_manage = url.clone();
    let signed_in = matches!(me.read().as_ref(), Some(Ok(_)));

    rsx! {
        div { class: "row",
            div { class: "col-12",
                Link {
                    to: Route::TournamentHomeWithTab { url: url_for_back, tab: "sidecomps".to_string() },
                    class: "btn btn-link",
                    "<- Back to side competitions"
                }
                match detail.read().as_ref() {
                    Some(Ok(d)) => {
                        let registrants = d.registrants.clone();
                        let viewer_is_to = d.viewer_is_to;
                        let viewer_can_register = d.viewer_can_register;
                        let viewer_is_registered_in_comp = d.viewer_is_registered_in_comp;
                        let registration_open = d.registration_open;
                        let active = d.active;
                        let finalized = d.finalized;
                        let description = d.description.clone();
                        let allowed_weapons = d.allowed_weapons.clone();
                        let comp_name = d.name.clone();
                        let viewer_weapon = d.viewer_weapon.clone();
                        let results_page_enabled = d.results_page_enabled;
                        rsx! {
                            h1 { "{d.name}" }
                            p {
                                span { class: "badge bg-secondary me-2", "{d.type_}" }
                                if registration_open {
                                    span { class: "badge bg-success me-2", "Open" }
                                } else {
                                    span { class: "badge bg-secondary me-2", "Closed" }
                                }
                                if finalized {
                                    span { class: "badge bg-dark", "Finalized" }
                                }
                            }
                            if let Some(desc) = description.as_ref() {
                                if !desc.is_empty() {
                                    p { style: "white-space: pre-wrap;", "{desc}" }
                                }
                            }
                            if viewer_is_to {
                                div { class: "mb-3",
                                    Link {
                                        to: Route::SideCompEdit { url: url_for_edit.clone(), comp_id },
                                        class: "btn btn-outline-secondary me-2",
                                        "Edit"
                                    }
                                    Link {
                                        to: Route::SideCompRegisterAsTo { url: url_for_register.clone(), comp_id },
                                        class: "btn btn-outline-primary me-2",
                                        "Quick Register players"
                                    }
                                    Link {
                                        to: Route::SideCompManageResults { url: url_for_manage.clone(), comp_id },
                                        class: "btn btn-outline-warning",
                                        "Manage results"
                                    }
                                }
                            }
                            div { class: "mb-3 d-flex flex-wrap gap-2",
                                if viewer_can_register {
                                    button {
                                        class: "btn btn-success",
                                        onclick: move |_| show_register_modal.set(true),
                                        "Register me"
                                    }
                                }
                                if viewer_is_registered_in_comp {
                                    button {
                                        class: "btn btn-outline-secondary",
                                        onclick: move |_| show_edit_modal.set(true),
                                        "Edit registration"
                                    }
                                }
                                if signed_in && active {
                                    Link {
                                        to: Route::SideCompEnterResults { url: url_for_enter.clone(), comp_id },
                                        class: "btn btn-primary",
                                        "Enter results"
                                    }
                                }
                                if results_page_enabled {
                                    Link {
                                        to: Route::SideCompResults { url: url_for_results.clone(), comp_id },
                                        class: "btn btn-outline-dark",
                                        "Results"
                                    }
                                }
                            }
                            h2 { "Registrants ({registrants.len()})" }
                            if registrants.is_empty() {
                                p { class: "text-muted", "No registrants yet." }
                            } else {
                                table { class: "table",
                                    thead {
                                        tr {
                                            th { "#" }
                                            th { "Player" }
                                            th { "Role" }
                                            th { "Registered" }
                                            th { "Source" }
                                        }
                                    }
                                    tbody {
                                        for r in registrants.iter() {
                                            tr {
                                                td { "{r.entry_number}" }
                                                td { "{r.player_name}" }
                                                td {
                                                    if let Some(weapon) = r.weapon.as_ref() {
                                                        PompfenIcon { weapon: weapon.clone(), size: "1.75em".to_string() }
                                                    }
                                                }
                                                td { "{r.registered_at.clone().unwrap_or_default()}" }
                                                td { if r.registered_by_to { "TO" } else { "Self" } }
                                            }
                                        }
                                    }
                                }
                            }
                            if show_register_modal() {
                                SideCompWeaponModal {
                                    title: format!("{comp_name} registration"),
                                    mode: SideCompRegistrationMode::Register,
                                    allowed_weapons: allowed_weapons.clone(),
                                    on_close: move |_| show_register_modal.set(false),
                                    on_submit: move |weapon: String| {
                                        let mut err = action_error;
                                        let mut detail_res = detail;
                                        spawn(async move {
                                            match api::sidecomp_register(comp_id, &weapon).await {
                                                Ok(_) => {
                                                    show_register_modal.set(false);
                                                    detail_res.restart();
                                                }
                                                Err(e) => {
                                                    err.set(Some(e));
                                                    show_register_modal.set(false);
                                                }
                                            }
                                        });
                                    },
                                }
                            }
                            if show_edit_modal() {
                                SideCompWeaponModal {
                                    title: format!("{comp_name} registration"),
                                    mode: SideCompRegistrationMode::Edit,
                                    allowed_weapons: allowed_weapons.clone(),
                                    initial_weapon: viewer_weapon.clone(),
                                    show_deregister: true,
                                    on_close: move |_| show_edit_modal.set(false),
                                    on_submit: move |weapon: String| {
                                        let mut err = action_error;
                                        let mut detail_res = detail;
                                        spawn(async move {
                                            match api::sidecomp_update_registration(comp_id, &weapon).await {
                                                Ok(_) => {
                                                    show_edit_modal.set(false);
                                                    detail_res.restart();
                                                }
                                                Err(e) => {
                                                    err.set(Some(e));
                                                    show_edit_modal.set(false);
                                                }
                                            }
                                        });
                                    },
                                    on_deregister: move |_| {
                                        let mut err = action_error;
                                        let mut detail_res = detail;
                                        spawn(async move {
                                            match api::sidecomp_deregister(comp_id).await {
                                                Ok(_) => {
                                                    show_edit_modal.set(false);
                                                    detail_res.restart();
                                                }
                                                Err(e) => {
                                                    err.set(Some(e));
                                                    show_edit_modal.set(false);
                                                }
                                            }
                                        });
                                    },
                                }
                            }
                        }
                    }
                    Some(Err(e)) => rsx! { div { class: "alert alert-danger", "Error: {e}" } },
                    None => rsx! { div { class: "spinner-border" } },
                }
                if let Some(err) = action_error() {
                    div { class: "alert alert-danger mt-3", "{err}" }
                }
            }
        }
    }
}
