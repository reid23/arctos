use crate::api;
use crate::components::{ScheduleItemDialog, ScheduleItemDetails, activity_time_range,ActionRecovery, RegistrationIntent, OrganizerTools, PompfenIcon, SideCompRegistrationMode, SideCompWeaponModal};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn SideCompDetail(url: String, comp_id: i32) -> Element {
    let mut detail = use_resource(move || async move { api::sidecomp_detail(comp_id).await });
    let me = crate::auth::use_account();
    let mut recovery = use_signal(ActionRecovery::default);
    let mut requested = use_signal(|| None::<(RegistrationIntent, (String, String))>);
    let mut schedule_open = use_signal(|| false);
    let mut schedule_edit = use_signal(|| None::<crate::types::ScheduleItemData>);
    let mut schedule_details = use_signal(|| None::<i32>);
    let mut show_register_modal = use_signal(|| false);
    let mut show_edit_modal = use_signal(|| false);

    let mut change_registration = move |intent: Option<RegistrationIntent>| {
        if recovery().busy() || (intent.is_some() && recovery().blocks_write()) { return; }
        let send_write = intent.is_some();
        let (intent, owner) = if let Some(intent) = intent {
            let Some(Ok(user)) = me.read().as_ref().cloned() else {
                recovery.set(ActionRecovery::Retryable("Check your account before saving registration.".into()));
                return;
            };
            (intent, (user.id, user.user_type))
        } else if let Some(previous) = requested() { previous } else { return; };
        requested.set(Some((intent.clone(), owner.clone())));
        recovery.set(if send_write { ActionRecovery::Pending } else { ActionRecovery::Checking });
        spawn(async move {
            let result = if send_write {
                match &intent {
                    RegistrationIntent::Register(weapon) => api::sidecomp_register(comp_id, weapon).await,
                    RegistrationIntent::Update(weapon) => api::sidecomp_update_registration(comp_id, weapon).await,
                    RegistrationIntent::Remove => api::sidecomp_deregister(comp_id).await,
                }.map(|_| ())
            } else { Err("Registration was not confirmed.".into()) };
            let applied = if result.is_ok() { Ok(true) } else {
                recovery.set(ActionRecovery::Checking);
                match api::sidecomp_detail(comp_id).await {
                    Ok(current) => match api::me().await {
                        Ok(user) if user.id == owner.0 && user.user_type == owner.1 => Ok(intent.applied(current.viewer_is_registered_in_comp, current.viewer_weapon.as_deref())),
                        _ => Err("Your account could not be confirmed for this registration. Sign back in with the same account, then check status.".into()),
                    },
                    Err(error) => Err(error),
                }
            };
            match applied {
                Ok(true) => { recovery.set(ActionRecovery::Idle); requested.set(None); show_register_modal.set(false); show_edit_modal.set(false); detail.restart(); }
                Ok(false) => recovery.set(ActionRecovery::Retryable(result.err().unwrap_or_else(|| "Registration has not changed. Review your choice and try again.".into()))),
                Err(error) => recovery.set(ActionRecovery::NeedsCheck(format!("Could not confirm registration. {error} Check status before trying again."))),
            }
        });
    };
    let mut register = change_registration.clone();
    let mut update = change_registration.clone();
    let mut remove = change_registration.clone();
    let mut check_register = change_registration.clone();

    let url_for_back = url.clone();
    let url_for_edit = url.clone();
    let url_for_register = url.clone();
    let url_for_enter = url.clone();
    let url_for_results = url.clone();
    let url_for_manage = url.clone();
    let signed_in = matches!(me.read().as_ref(), Some(Ok(_)));

    rsx! {
        style { {include_str!("../components/organizer_tools.css")} }
        style { {include_str!("../components/schedule_item.css")} }
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
                                        if matches!(me.read().as_ref(), Some(Err(api::AccountError::Unavailable))) {
                                            // Layout provides the shared account-check retry.
                                        } else if viewer_registration_incomplete {
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
                                        } else if matches!(me.read().as_ref(), Some(Err(api::AccountError::Unauthenticated))) {
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
                                            button { r#type: "button", class: "dropdown-item", onclick: move |_| { schedule_edit.set(None); schedule_open.set(true); }, "Add to schedule" }
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
                            if !d.schedule_items.is_empty() {
                                div { class: "sidecomp-schedule-times",
                                    for item in &d.schedule_items {
                                        {
                                            let item = item.clone();
                                            let label = activity_time_range(&item.start_time, &item.end_time);
                                            rsx! { button { r#type: "button", class: "btn btn-link text-start", aria_haspopup: "dialog", aria_label: if viewer_is_to { format!("Edit scheduled time: {label}") } else { format!("View scheduled time: {label}") },
                                                onclick: move |_| { if viewer_is_to { schedule_edit.set(Some(item.clone())); schedule_open.set(true); } else { schedule_details.set(Some(item.id)); } },
                                                i { class: "far fa-calendar me-2", aria_hidden: true } "{label}"
                                            } }
                                        }
                                    }
                                }
                            }
                            if schedule_open() && viewer_is_to {
                                ScheduleItemDialog { tournament_url: url.clone(), existing: schedule_edit(), initial_sidecomp: Some(comp_id),
                                    on_close: move |_| schedule_open.set(false), on_saved: move |_| { schedule_open.set(false); detail.restart(); }
                                }
                            }
                            if let Some(item_id) = schedule_details() { ScheduleItemDetails { tournament_url: url.clone(), item_id, on_close: move |_| schedule_details.set(None) } }
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
                                    title: format!("{comp_name} registration"), mode: SideCompRegistrationMode::Register,
                                    allowed_weapons: allowed_weapons.clone(), busy: recovery().busy(),
                                    error: recovery().message().map(str::to_owned), needs_check: matches!(recovery(), ActionRecovery::NeedsCheck(_)),
                                    on_close: move |_| { if !recovery().busy() { show_register_modal.set(false); } },
                                    on_submit: move |weapon: String| register(Some(RegistrationIntent::Register(weapon))),
                                    on_check: move |_| check_register(None),
                                }
                            }
                            if show_edit_modal() {
                                SideCompWeaponModal {
                                    title: format!("{comp_name} registration"),
                                    mode: if viewer_registration_incomplete { SideCompRegistrationMode::Register } else { SideCompRegistrationMode::Edit },
                                    allowed_weapons: allowed_weapons.clone(), initial_weapon: viewer_weapon.clone(), show_deregister: !viewer_registration_incomplete,
                                    busy: recovery().busy(), error: recovery().message().map(str::to_owned), needs_check: matches!(recovery(), ActionRecovery::NeedsCheck(_)),
                                    on_close: move |_| { if !recovery().busy() { show_edit_modal.set(false); } },
                                    on_submit: move |weapon: String| update(Some(RegistrationIntent::Update(weapon))),
                                    on_deregister: move |_| remove(Some(RegistrationIntent::Remove)),
                                    on_check: move |_| change_registration(None),
                                }
                            }
                        }
                    }
                    Some(Err(e)) => rsx! { div { class: "alert alert-danger", role: "alert", "Could not load side competition: {e}" } button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| detail.restart(), "Retry loading side competition" } },
                    None => rsx! { p { role: "status", "Loading side competition…" } },
                }

            }
        }
    }
}
