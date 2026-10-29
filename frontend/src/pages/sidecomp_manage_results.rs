use crate::api;
use crate::components::{ActionRecovery, PompfenIcon, RecordTimestamp};
use crate::types::{SideCompManageResultRow, SideCompManageResults as ManageResultsData};
use crate::Route;
use dioxus::prelude::*;

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

#[derive(Clone, Debug, PartialEq)]
enum ResultChange {
    Validity(String, bool),
    Finalize,
}
impl ResultChange {
    fn applied(&self, data: &ManageResultsData) -> bool {
        match self {
            Self::Validity(id, valid) => data.results.iter().any(|row| row.uuid == *id && row.valid == *valid),
            Self::Finalize => data.finalized,
        }
    }
}

fn result_checkbox_value(id: &str, authoritative: bool, recovery: &ActionRecovery, requested: Option<&ResultChange>) -> bool {
    if recovery.blocks_write() {
        if let Some(ResultChange::Validity(target, value)) = requested {
            if target == id { return *value; }
        }
    }
    authoritative
}

async fn reconcile_result_change(
    comp_id: i32,
    change: ResultChange,
    mut refreshed: Signal<Option<ManageResultsData>>,
    mut recovery: Signal<ActionRecovery>,
    message: String,
) {
    recovery.set(ActionRecovery::Checking);
    match api::sidecomp_manage_results(comp_id).await {
        Ok(current) => {
            recovery.set(if change.applied(&current) { ActionRecovery::Idle } else {
                ActionRecovery::Retryable(format!("{message} Current results have been loaded; review them before trying again."))
            });
            refreshed.set(Some(current));
        }
        Err(error) => recovery.set(ActionRecovery::NeedsCheck(format!("Could not confirm the change. {error} Check status before making another change."))),
    }
}

#[component]
pub fn SideCompManageResults(url: String, comp_id: i32) -> Element {
    let mut data = use_resource(move || async move { api::sidecomp_manage_results(comp_id).await });
    let mut recovery = use_signal(ActionRecovery::default);
    let refreshed = use_signal(|| None::<ManageResultsData>);
    let mut requested = use_signal(|| None::<ResultChange>);
    let snapshot = refreshed().map(Ok).or_else(|| data.read().clone());

    let url_back = url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12",
                Link {
                    to: Route::SideCompDetail { url: url_back, comp_id },
                    class: "btn btn-link",
                    "<- Back to side competition"
                }
                if recovery().busy() { p { role: "status", "Saving and checking results…" } }
                if let Some(message) = recovery().message() { p { class: "text-danger", role: "alert", "{message}" } }
                if matches!(recovery(), ActionRecovery::NeedsCheck(_)) {
                    button { r#type: "button", class: "btn btn-outline-secondary mb-3", onclick: move |_| {
                        if recovery().busy() { return; }
                        if let Some(change) = requested() {
                            recovery.set(ActionRecovery::Checking);
                            spawn(reconcile_result_change(comp_id, change, refreshed, recovery, "The change was not confirmed.".into()));
                        }
                    }, "Check result status" }
                }
                match snapshot.as_ref() {
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
                            h2 { "Manage results — {name}" }
                            div { class: "mb-3 d-flex flex-wrap align-items-center gap-2",
                                if finalized {
                                    span { class: "badge bg-dark", "Finalized" }
                                }
                                button {
                                    class: "btn btn-warning",
                                    disabled: !can_finalize || recovery().blocks_write(),
                                    title: "{finalize_title}",
                                    aria_describedby: "sidecomp-finalize-help",
                                    onclick: move |_| {
                                        if !can_finalize || recovery().blocks_write() {
                                            return;
                                        }
                                        let change = ResultChange::Finalize;
                                        requested.set(Some(change.clone()));
                                        recovery.set(ActionRecovery::Pending);
                                        spawn(async move {
                                            let message = api::sidecomp_finalize(comp_id).await.err().unwrap_or_else(|| "Finalization was not confirmed.".into());
                                            reconcile_result_change(comp_id, change, refreshed, recovery, message).await;
                                        });
                                    },
                                    "Finalize results"
                                }
                            }
                            p { class: "small text-muted", id: "sidecomp-finalize-help",
                                if active { "Deactivate this side competition in Edit before finalizing." }
                                else if finalized { "Results are finalized. Validity corrections remain available." }
                                else { "Finalizing stops new scoring and prevents reactivation. You can still correct result validity." }
                            }
                            if results.is_empty() {
                                p { class: "text-muted", "No points logged yet." }
                            } else {
                                p { class: "small text-muted", "Times shown in your local timezone." }
                                div { class: "table-responsive", role: "region", "aria-label": "Side event result management", tabindex: "0",
                                    table { class: "table table-sm align-middle sidecomp-management-table",
                                        thead {
                                            tr {
                                                th { class: "table-nowrap", "Time" }
                                                th { class: "table-nowrap", "Player" }
                                                th { class: "table-nowrap", "Role" }
                                                th { class: "table-number", "Points" }
                                                th { class: "table-nowrap", "Ref" }
                                                th { class: "table-nowrap", "Flagged" }
                                                th { class: "table-nowrap", "Valid" }
                                            }
                                        }
                                        tbody {
                                            for row in results.into_iter() {
                                                {
                                                    let uuid = row.uuid.clone();
                                                    let uuid_busy = uuid.clone();
                                                    let is_flagged = row.flagged;
                                                    let is_valid = row.valid;
                                                    let busy = recovery().blocks_write();
                                                    let checked = result_checkbox_value(&uuid, is_valid, &recovery(), requested().as_ref());
                                                    let row_class = if is_flagged {
                                                        "table-warning"
                                                    } else if !is_valid {
                                                        "table-secondary"
                                                    } else {
                                                        ""
                                                    };
                                                    let label = player_label(&row);
                                                    let points_label = if row.points > 0 {
                                                        format!("+{}", row.points)
                                                    } else {
                                                        format!("{}", row.points)
                                                    };
                                                    let weapon = row.weapon.clone().unwrap_or_default();
                                                    let (row_style, row_title) = if row.untracked {
                                                        ("opacity: 0.45; text-decoration: line-through;", "Losses are not tracked for this player")
                                                    } else {
                                                        ("", "")
                                                    };
                                                    rsx! {
                                                        tr {
                                                            key: "{uuid}",
                                                            class: "{row_class}",
                                                            style: "{row_style}",
                                                            title: "{row_title}",
                                                            td { class: "table-nowrap small", RecordTimestamp { value: row.stamp.clone(), include_seconds: true } }
                                                            td { class: "table-copy", "{label}" }
                                                            td { class: "table-nowrap",
                                                                if !row.registered {
                                                                    i {
                                                                        class: "fas fa-exclamation-triangle text-warning",
                                                                        style: "font-size: 1.2em;",
                                                                        title: "unregistered",
                                                                    }
                                                                } else if !weapon.is_empty() {
                                                                    PompfenIcon { weapon: weapon, size: "1.4em".to_string() }
                                                                }
                                                            }
                                                            td {
                                                                class: if row.untracked { "table-number fw-bold text-muted" } else if row.points >= 0 { "table-number fw-bold text-success" } else { "table-number fw-bold text-danger" },
                                                                "{points_label}"
                                                            }
                                                            td { class: "small table-copy", "{row.result_ref}" }
                                                            td { class: "table-nowrap",
                                                                if is_flagged {
                                                                    span { class: "badge bg-warning text-dark", "Flagged" }
                                                                } else {
                                                                    span { class: "text-muted", "—" }
                                                                }
                                                            }
                                                            td { class: "table-nowrap",
                                                                div { class: "form-check form-switch m-0",
                                                                    input {
                                                                        class: "form-check-input",
                                                                        r#type: "checkbox",
                                                                        checked,
                                                                        aria_label: "Valid result for {label}: {points_label}",
                                                                        disabled: busy,
                                                                        onchange: move |evt| {
                                                                            if recovery().blocks_write() { return; }
                                                                            let next = evt.checked();
                                                                            let change = ResultChange::Validity(uuid_busy.clone(), next);
                                                                            let id = uuid_busy.clone();
                                                                            requested.set(Some(change.clone()));
                                                                            recovery.set(ActionRecovery::Pending);
                                                                            spawn(async move {
                                                                                let message = api::sidecomp_set_result_valid(&id, next).await.err().unwrap_or_else(|| "Validity change was not confirmed.".into());
                                                                                reconcile_result_change(comp_id, change, refreshed, recovery, message).await;
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
                    Some(Err(e)) => rsx! { div { class: "alert alert-danger", role: "alert", "Could not load results: {e}" } button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| data.restart(), "Retry loading results" } },
                    None => rsx! { p { role: "status", "Loading results…" } },
                }
            }
        }
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::*;
    #[test]
    fn rejected_validity_change_restores_checkbox_after_pending_value() {
        let request = ResultChange::Validity("a".into(), true);
        assert!(result_checkbox_value("a", false, &ActionRecovery::Pending, Some(&request)));
        assert!(result_checkbox_value("a", false, &ActionRecovery::NeedsCheck("offline".into()), Some(&request)));
        assert!(!result_checkbox_value("a", false, &ActionRecovery::Retryable("rejected".into()), Some(&request)));
        assert!(!result_checkbox_value("other", false, &ActionRecovery::Pending, Some(&request)));
        assert!(result_checkbox_value("a", true, &ActionRecovery::Idle, Some(&request)));
    }

    #[test]
    fn result_reconciliation_requires_the_requested_authoritative_value() {
        let mut data: ManageResultsData = serde_json::from_value(serde_json::json!({
            "id":1,"name":"Example","type":"DUELS","active":false,"finalized":false,
            "results":[{"uuid":"a","comp":1,"player":1,"points":1,"ref":"ref","valid":false}]
        })).unwrap();
        assert!(ResultChange::Validity("a".into(), false).applied(&data));
        assert!(!ResultChange::Validity("a".into(), true).applied(&data));
        assert!(!ResultChange::Validity("missing".into(), false).applied(&data));
        assert!(!ResultChange::Finalize.applied(&data));
        data.finalized = true;
        assert!(ResultChange::Finalize.applied(&data));
    }
}
