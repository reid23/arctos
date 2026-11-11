//! Prepaid registration entries: the notice on registration pages, the
//! keep-or-transfer choice when withdrawing, and the recipient search they share.

use crate::api;
use crate::components::{Modal, ModalKind};
use crate::types::{PaidEntriesResponse, PaidEntry, PaidEntryAccount};
use dioxus::prelude::*;

const RECIPIENT_RESULT_LIMIT: usize = 8;

/// An event (`"events"`) or league (`"leagues"`) whose entries a page shows.
#[derive(Clone, Debug, PartialEq)]
pub struct PaidEntryScope {
    kind: &'static str,
    slug: String,
}

impl PaidEntryScope {
    pub fn event(slug: &str) -> Self {
        Self { kind: "events", slug: slug.to_string() }
    }

    pub fn league(slug: &str) -> Self {
        Self { kind: "leagues", slug: slug.to_string() }
    }

    /// `events` or `leagues`.
    pub fn kind(&self) -> &'static str {
        self.kind
    }

    pub fn slug(&self) -> &str {
        &self.slug
    }

    fn contains(&self, entry: &PaidEntry) -> bool {
        entry.event.kind == self.kind && entry.event.slug == self.slug
    }

    fn noun(&self) -> &'static str {
        if self.kind == "leagues" { "league" } else { "event" }
    }
}

/// The signed-in account's (or a managed team's) entries for one scope.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ScopePaidEntries {
    pub held: Option<PaidEntry>,
    pub sent: Vec<PaidEntry>,
}

impl ScopePaidEntries {
    fn from_response(scope: &PaidEntryScope, response: PaidEntriesResponse) -> Self {
        Self {
            held: response.held.into_iter().find(|entry| scope.contains(entry)),
            sent: response.sent.into_iter().filter(|entry| scope.contains(entry)).collect(),
        }
    }

    /// Held and not covering a registration, so registering won't need payment.
    pub fn prepaid(&self) -> bool {
        self.held.as_ref().is_some_and(PaidEntry::is_available)
    }

    /// Covering the current registration, so withdrawing can keep or pass it on.
    pub fn applied_entry(&self) -> Option<&PaidEntry> {
        self.held.as_ref().filter(|entry| entry.status == "APPLIED")
    }
}

pub fn use_scope_paid_entries(
    scope: PaidEntryScope,
    team_id: Option<String>,
) -> Resource<Result<ScopePaidEntries, String>> {
    use_resource(use_reactive(&(scope, team_id), |(scope, team_id)| async move {
        api::paid_entries(team_id.as_deref())
            .await
            .map(|response| ScopePaidEntries::from_response(&scope, response))
    }))
}

/// Shown above a registration form: a prepaid entry the account can use,
/// decline, or pass on, and transfers it sent that haven't been claimed.
#[component]
pub fn PrepaidEntryNotice(
    scope: PaidEntryScope,
    mut entries: Resource<Result<ScopePaidEntries, String>>,
    registration_open: bool,
) -> Element {
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut transferring = use_signal(|| None::<PaidEntry>);
    let Some(Ok(current)) = entries.read().clone() else {
        return rsx! {};
    };
    let mut act = move |entry_id: u32, action: &'static str| {
        if busy() {
            return;
        }
        busy.set(true);
        error.set(None);
        spawn(async move {
            match api::paid_entry_action(entry_id, action, &serde_json::json!({})).await {
                Ok(response) if response.success => entries.restart(),
                Ok(response) => error.set(Some(response.error.unwrap_or_else(|| "Could not update the prepaid entry.".into()))),
                Err(message) => error.set(Some(message)),
            }
            busy.set(false);
        });
    };
    let noun = scope.noun();
    rsx! {
        if let Some(entry) = current.held.clone().filter(PaidEntry::is_available) {
            div { class: "alert alert-success", role: "status",
                if let Some(giver) = entry.transferred_from.clone() {
                    p { class: "mb-2", strong { "Prepaid entry from {giver.name}." } " Register below and you won't be asked to pay." }
                    button { r#type: "button", class: "btn btn-sm btn-outline-secondary", disabled: busy(),
                        onclick: move |_| act(entry.id, "decline"), "Decline and return it" }
                } else {
                    p { class: "mb-2", strong { "You have a prepaid entry for this {noun}." } " Register again below at no charge, or pass it to someone else." }
                    button { r#type: "button", class: "btn btn-sm btn-outline-secondary", disabled: busy() || !registration_open, aria_haspopup: "dialog",
                        onclick: move |_| transferring.set(Some(entry.clone())), "Transfer to someone else" }
                }
            }
        }
        for entry in current.sent.clone() {
            div { key: "{entry.id}", class: "alert alert-info", role: "status",
                p { class: "mb-2", "You transferred your prepaid entry to " strong { "{entry.holder.name}" } ". They haven't registered yet." }
                button { r#type: "button", class: "btn btn-sm btn-outline-secondary", disabled: busy(),
                    onclick: move |_| act(entry.id, "revoke"), "Take it back" }
            }
        }
        if let Some(message) = error() {
            div { class: "alert alert-danger", role: "alert", "{message}" }
        }
        if let Some(entry) = transferring() {
            TransferEntryDialog { entry, on_close: move |_| transferring.set(None), on_transferred: move |_| { transferring.set(None); entries.restart(); } }
        }
    }
}

#[component]
fn TransferEntryDialog(entry: PaidEntry, on_close: EventHandler<()>, on_transferred: EventHandler<()>) -> Element {
    let recipient = use_signal(|| None::<PaidEntryAccount>);
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let entry_id = entry.id;
    rsx! {
        Modal { id: "transfer-entry-dialog", labelled_by: "transfer-entry-title", dismissible: !busy(), on_close,
            div { class: "modal-header",
                h2 { id: "transfer-entry-title", class: "modal-title h5", tabindex: "-1", "Transfer your prepaid entry" }
                button { r#type: "button", class: "btn-close", aria_label: "Close", disabled: busy(), onclick: move |_| on_close.call(()) }
            }
            div { class: "modal-body",
                p { class: "text-muted", "They register as usual and won't be asked to pay. You can take it back until they register." }
                RecipientSearch { kind: entry.kind.clone(), exclude: entry.holder.id.clone(), selected: recipient, disabled: busy() }
                if let Some(message) = error() { p { role: "alert", class: "text-danger mt-2", "{message}" } }
            }
            div { class: "modal-footer",
                button { r#type: "button", class: "btn btn-secondary", disabled: busy(), onclick: move |_| on_close.call(()), "Cancel" }
                button { r#type: "button", class: "btn btn-primary", disabled: busy() || recipient().is_none(),
                    onclick: move |_| {
                        let Some(account) = recipient() else { return; };
                        if busy() { return; }
                        busy.set(true);
                        error.set(None);
                        spawn(async move {
                            match api::paid_entry_action(entry_id, "transfer", &serde_json::json!({ "recipient": account.id })).await {
                                Ok(response) if response.success => on_transferred.call(()),
                                Ok(response) => error.set(Some(response.error.unwrap_or_else(|| "Could not transfer the entry.".into()))),
                                Err(message) => error.set(Some(message)),
                            }
                            busy.set(false);
                        });
                    },
                    if busy() { "Transferring…" } else { "Transfer" }
                }
            }
        }
    }
}

/// Keep the entry for re-registering (the default) or pass it on while withdrawing.
/// `transfer_to` is `Some` once a recipient is picked; `transferring` tracks the radio.
#[component]
pub fn WithdrawEntryChoice(
    kind: String,
    exclude: String,
    mut transferring: Signal<bool>,
    mut transfer_to: Signal<Option<PaidEntryAccount>>,
    disabled: bool,
) -> Element {
    let noun = if kind == "team" { "team" } else { "player" };
    rsx! {
        fieldset { class: "mb-3", disabled,
            legend { class: "form-label h6", "Your prepaid entry" }
            div { class: "form-check",
                input { class: "form-check-input", r#type: "radio", id: "withdraw-keep-entry", name: "withdraw-entry", checked: !transferring(),
                    onchange: move |_| { transferring.set(false); transfer_to.set(None); } }
                label { class: "form-check-label", r#for: "withdraw-keep-entry", "Keep it. Register again later without paying." }
            }
            div { class: "form-check",
                input { class: "form-check-input", r#type: "radio", id: "withdraw-transfer-entry", name: "withdraw-entry", checked: transferring(),
                    onchange: move |_| transferring.set(true) }
                label { class: "form-check-label", r#for: "withdraw-transfer-entry", "Transfer it to another {noun}." }
            }
            if transferring() {
                div { class: "mt-2", RecipientSearch { kind, exclude, selected: transfer_to, disabled } }
            }
        }
    }
}

/// A registration an organizer is about to withdraw.
#[derive(Clone, Debug, PartialEq)]
pub struct OrganizerWithdrawal {
    pub is_team: bool,
    pub account_id: String,
    pub name: String,
    pub paid: bool,
}

/// Confirms an organizer withdrawal. `on_confirm` receives whether to void the entry.
#[component]
pub fn OrganizerWithdrawDialog(
    withdrawal: OrganizerWithdrawal,
    busy: bool,
    error: Option<String>,
    on_cancel: EventHandler<()>,
    on_confirm: EventHandler<bool>,
) -> Element {
    let mut void_entry = use_signal(|| false);
    let name = withdrawal.name.clone();
    rsx! {
        Modal { id: "organizer-withdraw-dialog", labelled_by: "organizer-withdraw-title", kind: ModalKind::AlertDialog, dismissible: !busy, on_close: on_cancel,
            div { class: "modal-header", h2 { id: "organizer-withdraw-title", class: "modal-title h5", tabindex: "-1", "Deregister {name}?" } }
            div { class: "modal-body",
                if withdrawal.is_team {
                    p { "The team's players stay registered without a team." }
                }
                if withdrawal.paid {
                    p { "They keep their prepaid entry and can register again without paying, unless you void it." }
                    div { class: "form-check",
                        input { class: "form-check-input", r#type: "checkbox", id: "organizer-void-entry", disabled: busy, checked: void_entry(),
                            onchange: move |event| void_entry.set(event.checked()) }
                        label { class: "form-check-label", r#for: "organizer-void-entry", "Void their prepaid entry" }
                    }
                }
                if let Some(message) = error { p { role: "alert", class: "text-danger mt-2", "{message}" } }
            }
            div { class: "modal-footer",
                button { r#type: "button", class: "btn btn-secondary", disabled: busy, onclick: move |_| on_cancel.call(()), "Go back" }
                button { r#type: "button", class: "btn btn-danger", disabled: busy, onclick: move |_| on_confirm.call(void_entry()),
                    if busy { "Deregistering…" } else { "Deregister" } }
            }
        }
    }
}

#[component]
fn RecipientSearch(kind: String, exclude: String, mut selected: Signal<Option<PaidEntryAccount>>, disabled: bool) -> Element {
    let mut search = use_signal(String::new);
    let is_team = kind == "team";
    let results = use_resource(use_reactive(&(search, is_team, exclude), |(search, is_team, exclude)| async move {
        let query = search().trim().to_string();
        if query.is_empty() {
            return Ok(vec![]);
        }
        #[cfg(target_arch = "wasm32")]
        gloo_timers::future::TimeoutFuture::new(200).await;
        let accounts = if is_team {
            api::teams_list(&query).await?.teams.into_iter().map(|team| PaidEntryAccount { id: team.id, name: team.name }).collect::<Vec<_>>()
        } else {
            api::players_list(&query, 1).await?.players.into_iter().map(|player| PaidEntryAccount { id: player.id, name: player.name }).collect()
        };
        Ok::<_, String>(accounts.into_iter().filter(|account| account.id != exclude).take(RECIPIENT_RESULT_LIMIT).collect())
    }));
    let noun = if is_team { "team" } else { "player" };
    rsx! {
        if let Some(account) = selected() {
            div { class: "d-flex align-items-center justify-content-between gap-2 border rounded p-2",
                div { strong { "{account.name}" } span { class: "text-muted ms-1", "@{account.id}" } }
                button { r#type: "button", class: "btn btn-sm btn-outline-secondary", disabled, onclick: move |_| selected.set(None), "Change" }
            }
        } else {
            label { r#for: "entry-recipient-search", class: "form-label", "Find a {noun}" }
            input { id: "entry-recipient-search", r#type: "search", class: "form-control", placeholder: "Name or username", disabled,
                value: search, oninput: move |event| search.set(event.value()) }
            match results.read().as_ref() {
                Some(Ok(accounts)) if accounts.is_empty() && !search().trim().is_empty() => rsx! { p { class: "text-muted mt-2 mb-0", role: "status", "No {noun}s found." } },
                Some(Ok(accounts)) => rsx! {
                    div { class: "list-group mt-2",
                        for account in accounts.clone() {
                            button { key: "{account.id}", r#type: "button", class: "list-group-item list-group-item-action", disabled,
                                onclick: { let account = account.clone(); move |_| selected.set(Some(account.clone())) },
                                strong { "{account.name}" } span { class: "text-muted ms-1", "@{account.id}" }
                            }
                        }
                    }
                },
                Some(Err(message)) => rsx! { p { class: "text-danger mt-2 mb-0", role: "alert", "{message}" } },
                None => rsx! { p { class: "text-muted mt-2 mb-0", role: "status", "Searching…" } },
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{PaidEntryScope, ScopePaidEntries};
    use crate::types::{PaidEntriesResponse, PaidEntry, PaidEntryAccount, TeamEventRef};

    fn entry(id: u32, kind: &str, slug: &str, status: &str) -> PaidEntry {
        PaidEntry {
            id,
            kind: "player".into(),
            event: TeamEventRef { slug: slug.into(), kind: kind.into(), name: slug.into() },
            status: status.into(),
            holder: PaidEntryAccount { id: "holder".into(), name: "Holder".into() },
            transferred_from: None,
        }
    }

    #[test]
    fn scope_keeps_only_entries_for_its_event_or_league() {
        let response = PaidEntriesResponse {
            held: vec![entry(1, "leagues", "summer", "AVAILABLE"), entry(2, "events", "summer", "APPLIED")],
            sent: vec![entry(3, "events", "summer", "AVAILABLE"), entry(4, "events", "winter", "AVAILABLE")],
        };

        let current = ScopePaidEntries::from_response(&PaidEntryScope::event("summer"), response);

        assert_eq!(current.held.map(|held| held.id), Some(2));
        assert_eq!(current.sent.iter().map(|sent| sent.id).collect::<Vec<_>>(), vec![3]);
    }

    #[test]
    fn available_entry_is_prepaid_and_applied_entry_can_be_passed_on() {
        let available = ScopePaidEntries { held: Some(entry(1, "events", "summer", "AVAILABLE")), sent: vec![] };
        let applied = ScopePaidEntries { held: Some(entry(2, "events", "summer", "APPLIED")), sent: vec![] };

        assert!(available.prepaid());
        assert!(available.applied_entry().is_none());
        assert!(!applied.prepaid());
        assert_eq!(applied.applied_entry().map(|held| held.id), Some(2));
        assert!(!ScopePaidEntries::default().prepaid());
    }
}
