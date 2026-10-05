use crate::api;
use crate::components::PompfenIcon;
use crate::types::SideCompManageResultRow;
use crate::Route;
use dioxus::prelude::*;

fn format_stamp(stamp: &Option<String>) -> String {
    stamp.clone().unwrap_or_else(|| "—".to_string())
}

fn player_label(row: &SideCompManageResultRow) -> String {
    let entry = row
        .entry_number
        .map(|n| format!("#{n}"))
        .unwrap_or_else(|| "#?".to_string());
    let jersey = row.jersey_name.clone().unwrap_or_else(|| "—".to_string());
    let short = row
        .team_shortname
        .clone()
        .unwrap_or_else(|| "merc".to_string());
    format!("{entry} {} ({jersey} · {short})", row.display_name)
}

#[component]
pub fn SideCompManageResults(url: String, comp_id: i32) -> Element {
    let mut data = use_resource(move || async move { api::sidecomp_manage_results(comp_id).await });
    let mut error = use_signal(|| None::<String>);
    let mut finalizing = use_signal(|| false);
    let mut busy_uuid = use_signal(|| None::<String>);

    let url_back = url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12",
                Link {
                    to: Route::SideCompDetail { url: url_back, comp_id },
                    class: "btn btn-link",
                    "<- Back to side competition"
                }
                match data.read().as_ref() {
                    Some(Ok(payload)) => {
                        let name = payload.name.clone();
                        let active = payload.active;
                        let finalized = payload.finalized;
                        let results = payload.results.clone();
                        let can_finalize = !active && !finalized;
                        let finalize_title = if active {
                            "Deactivate the side competition before finalizing".to_string()
                        } else if finalized {
                            "Results have already been finalized".to_string()
                        } else {
                            "Permanently finalize results".to_string()
                        };
                        rsx! {
                            h1 { "Manage results — {name}" }
                            div { class: "mb-3 d-flex flex-wrap align-items-center gap-2",
                                if finalized {
                                    span { class: "badge bg-dark", "Finalized" }
                                }
                                button {
                                    class: "btn btn-warning",
                                    disabled: !can_finalize || finalizing(),
                                    title: "{finalize_title}",
                                    onclick: move |_| {
                                        if !can_finalize {
                                            return;
                                        }
                                        finalizing.set(true);
                                        error.set(None);
                                        spawn(async move {
                                            match api::sidecomp_finalize(comp_id).await {
                                                Ok(_) => data.restart(),
                                                Err(e) => error.set(Some(e)),
                                            }
                                            finalizing.set(false);
                                        });
                                    },
                                    "Finalize results"
                                }
                            }
                            if let Some(err) = error() {
                                div { class: "alert alert-danger", "{err}" }
                            }
                            if results.is_empty() {
                                p { class: "text-muted", "No points logged yet." }
                            } else {
                                div { class: "table-responsive",
                                    table { class: "table table-sm align-middle",
                                        thead {
                                            tr {
                                                th { "Time" }
                                                th { "Player" }
                                                th { "Role" }
                                                th { "Points" }
                                                th { "Ref" }
                                                th { "Flagged" }
                                                th { "Valid" }
                                            }
                                        }
                                        tbody {
                                            for row in results.into_iter() {
                                                {
                                                    let uuid = row.uuid.clone();
                                                    let uuid_busy = uuid.clone();
                                                    let is_flagged = row.flagged;
                                                    let is_valid = row.valid;
                                                    let busy = busy_uuid().as_deref() == Some(uuid.as_str());
                                                    let row_class = if is_flagged {
                                                        "table-warning"
                                                    } else if !is_valid {
                                                        "table-secondary"
                                                    } else {
                                                        ""
                                                    };
                                                    let label = player_label(&row);
                                                    let stamp = format_stamp(&row.stamp);
                                                    let points_label = if row.points > 0 {
                                                        format!("+{}", row.points)
                                                    } else {
                                                        format!("{}", row.points)
                                                    };
                                                    let weapon = row.weapon.clone().unwrap_or_default();
                                                    rsx! {
                                                        tr {
                                                            key: "{uuid}",
                                                            class: "{row_class}",
                                                            td { class: "text-nowrap small", "{stamp}" }
                                                            td { "{label}" }
                                                            td {
                                                                if !weapon.is_empty() {
                                                                    PompfenIcon { weapon: weapon, size: "1.4em".to_string() }
                                                                }
                                                            }
                                                            td {
                                                                class: if row.points >= 0 { "fw-bold text-success" } else { "fw-bold text-danger" },
                                                                "{points_label}"
                                                            }
                                                            td { class: "small", "{row.result_ref}" }
                                                            td {
                                                                if is_flagged {
                                                                    span { class: "badge bg-warning text-dark", "Flagged" }
                                                                } else {
                                                                    span { class: "text-muted", "—" }
                                                                }
                                                            }
                                                            td {
                                                                div { class: "form-check form-switch m-0",
                                                                    input {
                                                                        class: "form-check-input",
                                                                        r#type: "checkbox",
                                                                        checked: is_valid,
                                                                        disabled: busy,
                                                                        onchange: move |evt| {
                                                                            let next = evt.checked();
                                                                            let id = uuid_busy.clone();
                                                                            busy_uuid.set(Some(id.clone()));
                                                                            error.set(None);
                                                                            spawn(async move {
                                                                                match api::sidecomp_set_result_valid(&id, next).await {
                                                                                    Ok(_) => data.restart(),
                                                                                    Err(e) => error.set(Some(e)),
                                                                                }
                                                                                busy_uuid.set(None);
                                                                            });
                                                                        },
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
                    Some(Err(e)) => rsx! { div { class: "alert alert-danger", "{e}" } },
                    None => rsx! { div { class: "spinner-border" } },
                }
            }
        }
    }
}
