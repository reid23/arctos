use crate::api;
use crate::components::{OrganizerTools, PompfenIcon, SideCompRegistrationMode, SideCompWeaponModal};
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
        style { {include_str!("../components/organizer_tools.css")} }
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
                        let viewer_registration_incomplete = d.viewer_registration_incomplete;
                        let registration_open = d.registration_open;
                        let active = d.active;
                        let finalized = d.finalized;
                        let description = d.description.clone();
                        let allowed_weapons = d.allowed_weapons.clone();
                        let comp_name = d.name.clone();
                        let viewer_weapon = d.viewer_weapon.clone();
                        let results_page_enabled = d.results_page_enabled;
                        rsx! {
                            div { class: "sidecomp-heading sidecomp-registration-header",
                                div { class: "sidecomp-identity",
                                    h2 { "{d.name}" }
                                    p { class: "sidecomp-badges",
                                        span { class: "badge bg-secondary", "{d.type_}" }
                                        if registration_open {
                                            span { class: "badge bg-success", "Registration open" }
                                        } else {
                                            span { class: "badge bg-secondary", "Registration closed" }
                                        }
                                        if finalized { span { class: "badge bg-dark", "Finalized" } }
                                    }
                                }
                                div { class: "sidecomp-actions",
                                    div { class: "event-participant-actions", role: "group", aria_label: "Side competition registration",
                                        if viewer_registration_incomplete {
                                            button {
                                                r#type: "button", class: "btn btn-primary event-registration-action",
                                                onclick: move |_| show_edit_modal.set(true),
                                                "Complete registration"
                                            }
                                        } else if viewer_is_registered_in_comp {
                                            button {
                                                r#type: "button", class: "btn btn-outline-secondary",
                                                onclick: move |_| show_edit_modal.set(true),
                                                "Edit registration"
                                            }
                                        } else if viewer_can_register {
                                            button {
                                                r#type: "button", class: "btn btn-primary event-registration-action",
                                                onclick: move |_| show_register_modal.set(true),
                                                "Register"
                                            }
                                        } else if !registration_open {
                                            button { r#type: "button", class: "btn btn-secondary", disabled: true, "Registration closed" }
                                        } else if matches!(me.read().as_ref(), Some(Err(_))) {
                                            a {
                                                href: format!("/login?next={}", urlencoding::encode(&format!("/{url}/sidecomps/{comp_id}"))),
                                                class: "btn btn-primary event-registration-action",
                                                "Log in to register"
                                            }
                                        } else if matches!(me.read().as_ref(), Some(Ok(user)) if user.user_type == "player") {
                                            Link {
                                                to: Route::TournamentRegister { url: url.clone() },
                                                class: "btn btn-outline-primary",
                                                "Register for event"
                                            }
                                            small { class: "text-muted", "Event registration is required first." }
                                        }
                                    }
                                    div { class: "sidecomp-result-actions",
                                        if signed_in && active {
                                            Link {
                                                to: Route::SideCompEnterResults { url: url_for_enter.clone(), comp_id },
                                                class: if viewer_can_register || viewer_registration_incomplete { "btn btn-outline-secondary" } else { "btn btn-primary" },
                                                "Enter results"
                                            }
                                        }
                                        if results_page_enabled {
                                            Link {
                                                to: Route::SideCompResults { url: url_for_results.clone(), comp_id },
                                                class: "btn btn-outline-secondary",
                                                "Results"
                                            }
                                        }
                                    }
                                    if viewer_is_to {
                                        OrganizerTools {
                                            id_prefix: "sidecomp-organizer-tools".to_string(),
                                            label: "Side competition tools".to_string(),
                                            Link {
                                                to: Route::SideCompEdit { url: url_for_edit.clone(), comp_id },
                                                class: "dropdown-item", "Edit"
                                            }
                                            Link {
                                                to: Route::SideCompRegisterAsTo { url: url_for_register.clone(), comp_id },
                                                class: "dropdown-item", "Quick Register players"
                                            }
                                            Link {
                                                to: Route::SideCompManageResults { url: url_for_manage.clone(), comp_id },
                                                class: "dropdown-item", "Manage results"
                                            }
                                        }
                                    }
                                }
                            }
                            if let Some(desc) = description.as_ref() {
                                if !desc.is_empty() { p { style: "white-space: pre-wrap;", "{desc}" } }
                            }
                            section { class: "card data-section",
                                div { class: "card-header", h2 { class: "h5 mb-0", "Registrants ({registrants.len()})" } }
                                div { class: "card-body",
                                    if registrants.is_empty() {
                                        p { class: "text-muted", "No registrants yet." }
                                    } else {
                                        div { class: "table-responsive", role: "region", "aria-label": "Side competition registrants", tabindex: "0",
                                            table { class: "table roster-table",
                                                thead {
                                                    tr {
                                                        th { class: "table-nowrap", "Player" }
                                                        th { class: "table-number", "Entry #" }
                                                        th { class: "table-nowrap", "Role" }
                                                        th { class: "table-nowrap", "Registered" }
                                                        th { class: "table-nowrap", "Source" }
                                                    }
                                                }
                                                tbody {
                                                    for r in registrants.iter() {
                                                        tr { key: "{r.player_id}",
                                                            td { class: "table-copy", Link { to: Route::PlayerProfilePage { id: r.player_id.clone() }, class: "roster-name", "{r.player_name}" } }
                                                            td { class: "table-number", "data-label": "Entry #", "{r.entry_number}" }
                                                            td { class: "table-nowrap", "data-label": "Role",
                                                                if let Some(weapon) = r.weapon.as_ref() {
                                                                    PompfenIcon { weapon: weapon.clone(), size: "1.5em".to_string() }
                                                                }
                                                            }
                                                            td { class: "table-nowrap roster-wide", "data-label": "Registered", {crate::time_format::format_registration_timestamp(r.registered_at.as_deref().unwrap_or("—"))} }
                                                            td { class: "table-nowrap", "data-label": "Source", if r.registered_by_to { "TO" } else { "Self" } }
                                                        }
                                                    }
                                                }
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
                                    mode: if viewer_registration_incomplete { SideCompRegistrationMode::Register } else { SideCompRegistrationMode::Edit },
                                    allowed_weapons: allowed_weapons.clone(),
                                    initial_weapon: viewer_weapon.clone(),
                                    show_deregister: !viewer_registration_incomplete,
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
