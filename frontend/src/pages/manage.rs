use crate::api;
use crate::types::{
    PlayerListItem, RegisterPlayerAsToResponse, RegisterTeamAsToResponse, TeamListItem,
};
use crate::Route;
use dioxus::prelude::*;
use wasm_bindgen::JsCast;

#[derive(Clone, Copy, Debug, PartialEq)]
enum ManageTab {
    Registrations,
    RegisterOnBehalf,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum RegisterSubTab {
    Players,
    Teams,
}

#[derive(Clone, Debug, PartialEq)]
enum SessionLogEntry {
    Player {
        player_name: String,
        team: Option<String>,
        jersey_name: String,
        jersey_number: String,
    },
    Team {
        team_name: String,
        pseudonym: String,
    },
}

/// Do not silently turn a typo into a zero-dollar payment.
pub(crate) fn validated_payment_amount(value: &str) -> Result<f64, &'static str> {
    value
        .trim()
        .parse::<f64>()
        .ok()
        .filter(|amount| amount.is_finite() && *amount >= 0.0)
        .ok_or("Enter a valid amount of zero or more before saving payment details.")
}

#[component]
pub fn Manage(url: String) -> Element {
    let mut search = use_signal(|| String::new());
    let mut search_type = use_signal(|| "both".to_string());
    let mut submitted_search = use_signal(|| String::new());
    let mut submitted_type = use_signal(|| "both".to_string());
    let mut refresh = use_signal(|| 0u32);
    let deregister_error = use_signal(|| None::<String>);
    let mut payment_error = use_signal(|| None::<String>);
    let mut payment_saved = use_signal(|| false);
    let mut payment_saving = use_signal(|| None::<(bool, u32)>);
    let mut payment_target = use_signal(|| None::<(bool, u32)>);
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        let s = submitted_search().clone();
        let t = submitted_type().clone();
        let _r = refresh();
        async move {
            api::tournament_manage(&u, &s, &t)
                .await
                .map_err(|e| e.to_string())
        }
    });
    let val = data.value();

    // Top-level tab state
    let mut active_tab = use_signal(|| ManageTab::Registrations);
    let mut register_sub_tab = use_signal(|| RegisterSubTab::Players);

    // Player registration form state
    let mut player_search_query = use_signal(String::new);
    let mut player_search_results = use_signal(Vec::<PlayerListItem>::new);
    let mut player_searching = use_signal(|| false);
    let mut selected_player = use_signal(|| None::<PlayerListItem>);
    let mut selected_team = use_signal(String::new);
    let mut jersey_number = use_signal(String::new);
    let mut jersey_name_input = use_signal(String::new);
    let mut waiver_signature = use_signal(String::new);
    let mut player_submit_error = use_signal(|| None::<String>);
    let mut submitting = use_signal(|| false);

    // Team registration form state
    let mut team_search_query = use_signal(String::new);
    let mut team_search_results = use_signal(Vec::<TeamListItem>::new);
    let mut team_searching = use_signal(|| false);
    let mut selected_team_for_register = use_signal(|| None::<TeamListItem>);
    let mut team_pseudonym_input = use_signal(String::new);
    let mut team_shortname_input = use_signal(String::new);
    let mut team_submit_error = use_signal(|| None::<String>);

    // Session log for Quick Register tab
    let mut session_log = use_signal(Vec::<SessionLogEntry>::new);

    // Player search effect
    use_effect(move || {
        let q = player_search_query();
        if q.trim().is_empty() {
            player_search_results.set(Vec::new());
            return;
        }
        player_searching.set(true);
        spawn(async move {
            match api::players_list(&q, 1).await {
                Ok(resp) => player_search_results.set(resp.players),
                Err(_) => player_search_results.set(Vec::new()),
            }
            player_searching.set(false);
        });
    });

    // Team search effect
    use_effect(move || {
        let q = team_search_query();
        if q.trim().is_empty() {
            team_search_results.set(Vec::new());
            return;
        }
        team_searching.set(true);
        spawn(async move {
            match api::teams_list(&q).await {
                Ok(resp) => team_search_results.set(resp.teams),
                Err(_) => team_search_results.set(Vec::new()),
            }
            team_searching.set(false);
        });
    });

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h2 { "Registration management" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::TournamentHome { url: url.clone() }, "{d.tournament.name}" } }
                            li { class: "breadcrumb-item active", "Registration Management" }
                        }
                    }
                }
            }

            // Top-level tabs
            ul { class: "nav nav-tabs mb-3",
                li { class: "nav-item",
                    button {
                        r#type: "button",
                        class: if active_tab() == ManageTab::Registrations { "nav-link active" } else { "nav-link" },
                        onclick: move |_| active_tab.set(ManageTab::Registrations),
                        "Registrations"
                    }
                }
                li { class: "nav-item",
                    button {
                        r#type: "button",
                        class: if active_tab() == ManageTab::RegisterOnBehalf { "nav-link active" } else { "nav-link" },
                        onclick: move |_| active_tab.set(ManageTab::RegisterOnBehalf),
                        "Quick Register"
                    }
                }
            }

            if active_tab() == ManageTab::Registrations {
                div { class: "row mb-3",
                    div { class: "col-12",
                        if let Some(ref err) = deregister_error() {
                            div { class: "alert alert-danger small py-2 mb-0", "{err}" }
                        }
                        if let Some(error) = payment_error() {
                            div { class: "alert alert-danger", role: "alert", "{error}" }
                        }
                        if payment_saved() {
                            div { class: "alert alert-success", role: "status", "Payment details saved." }
                        }
                        form { class: "row g-2",
                            onsubmit: move |ev| {
                                ev.prevent_default();
                                submitted_search.set(search().clone());
                                submitted_type.set(search_type().clone());
                            },
                            div { class: "col-md-6",
                                input {
                                    r#type: "text",
                                    class: "form-control",
                                    name: "search",
                                    aria_label: "Search registrations",
                                    placeholder: "Search teams, players, or signed waiver names",
                                    value: "{search()}",
                                    oninput: move |ev| search.set(ev.value().clone()),
                                }
                            }
                            div { class: "col-md-4",
                                select {
                                    class: "form-select",
                                    name: "type",
                                    aria_label: "Registration type",
                                    value: "{search_type()}",
                                    onchange: move |ev| search_type.set(ev.value().clone()),
                                    option { value: "both", "Teams and Players" }
                                    option { value: "teams", "Teams" }
                                    option { value: "players", "Players" }
                                }
                            }
                            div { class: "col-md-2 d-grid",
                                button { r#type: "submit", class: "btn btn-primary", "Search" }
                            }
                        }
                    }
                }

                div { class: "row",
                    div { class: "col-12",
                        div { class: "edit-section data-section",
                            div { class: "edit-section-heading", h5 { class: "mb-0", "Team Registrations" } }
                            div { class: "edit-section-body",
                                div { class: "table-responsive management-table-wrap",
                                    table { class: "table table-striped management-table",
                                        thead {
                                            tr {
                                                th { class: "table-nowrap", "Team Name" }
                                                th { class: "table-nowrap", "Status" }
                                                th { class: "table-nowrap", "Registration Date" }
                                                th { class: "table-nowrap", "Actions" }
                                            }
                                        }
                                        tbody {
                                            for (url_dereg, url_save, team_id_dereg, reg_id_save, team_data) in d.team_registrations.iter().map(|t| (url.clone(), url.clone(), t.registration.team.clone(), t.registration.id, t)) {
                                                tr { key: "{team_data.registration.id}",
                                                    td { class: "table-copy", "data-label": "Team",
                                                        a { href: "/teams/{team_data.registration.team}", class: "text-decoration-none",
                                                            strong { "{team_data.registration.pseudonym}" }
                                                        }
                                                    }
                                                    td { "data-label": "Status",
                                                        span {
                                                            class: format!(
                                                                "badge table-nowrap {}",
                                                                match team_data.registration.status.as_str() {
                                                                    "CONFIRMED" => "bg-success",
                                                                    "CANCELLED" => "bg-danger",
                                                                    _ => "bg-warning",
                                                                }
                                                            ),
                                                            "{team_data.registration.status}"
                                                        }
                                                        if team_data.registration.paid {
                                                            span { class: "badge table-nowrap bg-primary ms-1", "Paid" }
                                                        } else {
                                                            span { class: "badge table-nowrap bg-secondary ms-1", "Unpaid" }
                                                        }
                                                    }
                                                    td { class: "table-nowrap", "data-label": "Registered", {crate::time_format::format_registration_timestamp(team_data.registration.registered_at.as_deref().unwrap_or("—"))} }
                                                    td { "data-label": "Actions", class: "management-actions",
                                                        if team_data.registration.status == "CONFIRMED" {
                                                            button {
                                                                r#type: "button",
                                                                class: "btn btn-sm btn-outline-danger",
                                                                onclick: move |_| {
                                                                    let u = url_dereg.clone();
                                                                    let tid = team_id_dereg.clone();
                                                                    let mut deregister_error = deregister_error.clone();
                                                                    spawn(async move {
                                                                        deregister_error.set(None);
                                                                        match api::deregister_any_team(&u, &tid).await {
                                                                            Ok(_) => refresh.set(refresh() + 1),
                                                                            Err(e) => deregister_error.set(Some(e)),
                                                                        }
                                                                    });
                                                                },
                                                                "Deregister"
                                                            }
                                                        }
                                                        div { class: "management-payment",
                                                            div { class: "input-group input-group-sm management-payment-controls",
                                                                span { class: "input-group-text", "$" }
                                                                input {
                                                                    r#type: "number",
                                                                    step: "0.01",
                                                                    min: "0",
                                                                    class: "form-control",
                                                                    id: "team-amount-{team_data.registration.id}",
                                                                        aria_label: "Amount paid",
                                                                        inputmode: "decimal",
                                                                    placeholder: "Amount",
                                                                    value: format!("{:.2}", team_data.registration.amount_paid)
                                                                }
                                                                label { class: "input-group-text management-payment-confirmed",
                                                                    input {
                                                                        class: "form-check-input mt-0",
                                                                        r#type: "checkbox",
                                                                        id: "team-paid-{team_data.registration.id}",
                                                                        aria_label: "Payment complete",
                                                                        checked: team_data.registration.paid
                                                                    }
                                                                        "Payment complete"
                                                                }
                                                                button {
                                                                    r#type: "button",
                                                                    class: "btn btn-sm btn-outline-primary",
                                                                    disabled: payment_saving().is_some(),
                                                                    onclick: move |_| {
                                                                        if payment_saving().is_some() { return; }
                                                                        payment_error.set(None);
                                                                        payment_saved.set(false);
                                                                        let u = url_save.clone();
                                                                        let rid = reg_id_save;
                                                                        payment_target.set(Some((true, rid)));
                                                                        payment_saving.set(Some((true, rid)));
                                                                        spawn(async move {
                                                                            let amount_text = web_sys::window()
                                                                                .and_then(|w| w.document())
                                                                                .and_then(|d| d.get_element_by_id(&format!("team-amount-{}", rid)))
                                                                                .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
                                                                                .map(|e: web_sys::HtmlInputElement| e.value())
                                                                                .unwrap_or_default();
                                                                            let amount = match super::manage::validated_payment_amount(&amount_text) {
                                                                                Ok(amount) => amount,
                                                                                Err(error) => {
                                                                                    payment_error.set(Some(error.to_string()));
                                                                                    payment_saving.set(None);
                                                                                    return;
                                                                                }
                                                                            };
                                                                            let paid = web_sys::window()
                                                                                .and_then(|w| w.document())
                                                                                .and_then(|d| d.get_element_by_id(&format!("team-paid-{}", rid)))
                                                                                .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
                                                                                .map(|e: web_sys::HtmlInputElement| e.checked())
                                                                                .unwrap_or(false);
                                                                            match api::mark_team_paid(&u, rid, amount, paid, "", "", "").await {
                                                                                Ok(response) if response.success => {
                                                                                    payment_saved.set(true);
                                                                                    refresh.set(refresh() + 1);
                                                                                }
                                                                                Ok(response) => payment_error.set(Some(response.error.unwrap_or_else(|| "Payment details could not be saved. Please try again.".to_string()))),
                                                                                Err(error) => payment_error.set(Some(format!("Payment details could not be saved. {error}"))),
                                                                            }
                                                                            payment_saving.set(None);
                                                                        });
                                                                    },
                                                                    if payment_saving() == Some((true, reg_id_save)) { "Saving…" } else { "Save payment" }
                                                                }
                                                                if payment_target() == Some((true, reg_id_save)) {
                                                                    if let Some(error) = payment_error() {
                                                                        div { class: "w-100 small text-danger mt-2", role: "alert", "{error}" }
                                                                    } else if payment_saved() {
                                                                        div { class: "w-100 small text-success mt-2", role: "status", "Saved" }
                                                                    }
                                                                }
                                                            }
                                                            if let Some(paid_at) = &team_data.registration.paid_at {
                                                                div { class: "form-text", "Paid " {crate::time_format::format_registration_timestamp(paid_at)} }
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

                div { class: "row mt-4",
                    div { class: "col-12",
                        div { class: "edit-section data-section",
                            div { class: "edit-section-heading", h5 { class: "mb-0", "Player Registrations" } }
                            div { class: "edit-section-body",
                                div { class: "table-responsive management-table-wrap",
                                    table { class: "table table-striped management-table",
                                        thead {
                                            tr {
                                                th { class: "table-nowrap", "Player Name" }
                                                th { class: "table-nowrap", "Team" }
                                                th { class: "table-nowrap", "Jersey" }
                                                if d.tournament.waiver_required {
                                                    th { class: "table-nowrap", "Waiver Signed Name" }
                                                    th { class: "table-nowrap", "Waiver Status" }
                                                }
                                                th { class: "table-nowrap", "Status" }
                                                th { class: "table-nowrap", "Registration Date" }
                                                th { class: "table-nowrap", "Actions" }
                                            }
                                        }
                                        tbody {
                                            for (url_dereg, url_save, player_id_dereg, reg_id_save, player_data) in d.player_registrations.iter().map(|p| (url.clone(), url.clone(), p.registration.player.clone(), p.registration.id, p)) {
                                                tr { key: "{player_data.registration.id}",
                                                    td { class: "table-copy", "data-label": "Player",
                                                        a { href: "/players/{player_data.registration.player}", class: "text-decoration-none",
                                                            strong { "{player_data.player.name}" }
                                                        }
                                                    }
                                                    td { class: "table-copy", "data-label": "Team",
                                                        if let Some(team) = &player_data.team {
                                                            a { href: "/teams/{team.id}", class: "text-decoration-none", "{team.name}" }
                                                        } else {
                                                            span { class: "text-muted", "Unattached" }
                                                        }
                                                    }
                                                    td { class: "table-copy", "data-label": "Jersey",
                                                        if player_data.registration.jersey_name.is_some()
                                                            || player_data.registration.jersey_number.is_some()
                                                        {
                                                            span { class: "table-jersey",
                                                                if let Some(name) = &player_data.registration.jersey_name {
                                                                    span { class: "table-copy", "{name}" }
                                                                }
                                                                if let Some(num) = &player_data.registration.jersey_number {
                                                                    span { class: "table-nowrap", "#{num}" }
                                                                }
                                                            }
                                                        } else {
                                                            span { class: "text-muted", "No jersey info" }
                                                        }
                                                    }
                                                    if d.tournament.waiver_required {
                                                        td { class: "table-copy", "data-label": "Signed waiver name",
                                                            span { class: "text-muted",
                                                                "{player_data.registration.waiver_legal_name_signature.as_deref().unwrap_or(\"-\")}"
                                                            }
                                                        }
                                                        td { "data-label": "Waiver",
                                                            if player_data.registration.waiver_required {
                                                                {
                                                                    let ws = player_data.registration.waiver_status.as_deref().unwrap_or("NOT_SIGNED");
                                                                    let (cls, label) = match ws {
                                                                        "VALID" => ("bg-success", "Waiver valid"),
                                                                        "OUT_OF_DATE" => ("bg-warning text-dark", "Waiver out of date"),
                                                                        "NOT_SIGNED" => ("bg-danger", "Waiver not signed"),
                                                                        _ => ("bg-secondary", "Waiver status unknown"),
                                                                    };
                                                                    rsx! { span { class: "badge table-nowrap {cls}", "{label}" } }
                                                                }
                                                            } else {
                                                                span { class: "text-muted", "-" }
                                                            }
                                                        }
                                                    }
                                                    td { "data-label": "Status",
                                                        span {
                                                            class: format!(
                                                                "badge table-nowrap {}",
                                                                match player_data.registration.status.as_str() {
                                                                    "CONFIRMED" => "bg-success",
                                                                    "CANCELLED" => "bg-danger",
                                                                    "PENDING_TEAM_APPROVAL" => "bg-warning",
                                                                    _ => "bg-secondary",
                                                                }
                                                            ),
                                                            "{player_data.registration.status}"
                                                        }
                                                        if player_data.registration.paid {
                                                            span { class: "badge table-nowrap bg-primary ms-1", "Paid" }
                                                        } else {
                                                            span { class: "badge table-nowrap bg-secondary ms-1", "Unpaid" }
                                                        }
                                                    }
                                                    td { class: "table-nowrap", "data-label": "Registered", {crate::time_format::format_registration_timestamp(player_data.registration.registered_at.as_deref().unwrap_or("—"))} }
                                                    td { "data-label": "Actions", class: "management-actions",
                                                        if player_data.registration.status == "PENDING_TEAM_APPROVAL"
                                                            || player_data.registration.status == "CONFIRMED"
                                                        {
                                                            button {
                                                                r#type: "button",
                                                                class: "btn btn-sm btn-outline-danger",
                                                                onclick: move |_| {
                                                                    let u = url_dereg.clone();
                                                                    let pid = player_id_dereg.clone();
                                                                    let mut deregister_error = deregister_error.clone();
                                                                    spawn(async move {
                                                                        deregister_error.set(None);
                                                                        match api::deregister_any_player(&u, &pid).await {
                                                                            Ok(_) => refresh.set(refresh() + 1),
                                                                            Err(e) => deregister_error.set(Some(e)),
                                                                        }
                                                                    });
                                                                },
                                                                "Deregister"
                                                            }
                                                        }
                                                        div { class: "management-payment",
                                                            div { class: "input-group input-group-sm management-payment-controls",
                                                                span { class: "input-group-text", "$" }
                                                                input {
                                                                    r#type: "number",
                                                                    step: "0.01",
                                                                    min: "0",
                                                                    class: "form-control",
                                                                    id: "player-amount-{player_data.registration.id}",
                                                                        aria_label: "Amount paid",
                                                                        inputmode: "decimal",
                                                                    placeholder: "Amount",
                                                                    value: format!("{:.2}", player_data.registration.amount_paid)
                                                                }
                                                                label { class: "input-group-text management-payment-confirmed",
                                                                    input {
                                                                        class: "form-check-input mt-0",
                                                                        r#type: "checkbox",
                                                                        id: "player-paid-{player_data.registration.id}",
                                                                        aria_label: "Payment complete",
                                                                        checked: player_data.registration.paid
                                                                    }
                                                                        "Payment complete"
                                                                }
                                                                button {
                                                                    r#type: "button",
                                                                    class: "btn btn-sm btn-outline-primary",
                                                                    disabled: payment_saving().is_some(),
                                                                    onclick: move |_| {
                                                                        if payment_saving().is_some() { return; }
                                                                        payment_error.set(None);
                                                                        payment_saved.set(false);
                                                                        let u = url_save.clone();
                                                                        let rid = reg_id_save;
                                                                        payment_target.set(Some((false, rid)));
                                                                        payment_saving.set(Some((false, rid)));
                                                                        spawn(async move {
                                                                            let amount_text = web_sys::window()
                                                                                .and_then(|w| w.document())
                                                                                .and_then(|d| d.get_element_by_id(&format!("player-amount-{}", rid)))
                                                                                .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
                                                                                .map(|e: web_sys::HtmlInputElement| e.value())
                                                                                .unwrap_or_default();
                                                                            let amount = match super::manage::validated_payment_amount(&amount_text) {
                                                                                Ok(amount) => amount,
                                                                                Err(error) => {
                                                                                    payment_error.set(Some(error.to_string()));
                                                                                    payment_saving.set(None);
                                                                                    return;
                                                                                }
                                                                            };
                                                                            let paid = web_sys::window()
                                                                                .and_then(|w| w.document())
                                                                                .and_then(|d| d.get_element_by_id(&format!("player-paid-{}", rid)))
                                                                                .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
                                                                                .map(|e: web_sys::HtmlInputElement| e.checked())
                                                                                .unwrap_or(false);
                                                                            match api::mark_player_paid(&u, rid, amount, paid, "", "", "").await {
                                                                                Ok(response) if response.success => {
                                                                                    payment_saved.set(true);
                                                                                    refresh.set(refresh() + 1);
                                                                                }
                                                                                Ok(response) => payment_error.set(Some(response.error.unwrap_or_else(|| "Payment details could not be saved. Please try again.".to_string()))),
                                                                                Err(error) => payment_error.set(Some(format!("Payment details could not be saved. {error}"))),
                                                                            }
                                                                            payment_saving.set(None);
                                                                        });
                                                                    },
                                                                    if payment_saving() == Some((false, reg_id_save)) { "Saving…" } else { "Save payment" }
                                                                }
                                                                if payment_target() == Some((false, reg_id_save)) {
                                                                    if let Some(error) = payment_error() {
                                                                        div { class: "w-100 small text-danger mt-2", role: "alert", "{error}" }
                                                                    } else if payment_saved() {
                                                                        div { class: "w-100 small text-success mt-2", role: "status", "Saved" }
                                                                    }
                                                                }
                                                            }
                                                            if let Some(paid_at) = &player_data.registration.paid_at {
                                                                div { class: "form-text", "Paid " {crate::time_format::format_registration_timestamp(paid_at)} }
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

            if active_tab() == ManageTab::RegisterOnBehalf {
                div { class: "alert alert-info",
                    strong { "Note: " }
                    "Quick Register bypasses payment (registration is marked paid for $0) and skips the usual approval steps (registrations land as confirmed immediately)."
                }
                // Sub-tab pills: Players / Teams
                ul { class: "nav nav-pills mb-3",
                    li { class: "nav-item",
                        button {
                            r#type: "button",
                            class: if register_sub_tab() == RegisterSubTab::Players { "nav-link active" } else { "nav-link" },
                            onclick: move |_| {
                                register_sub_tab.set(RegisterSubTab::Players);
                                player_submit_error.set(None);
                            },
                            "Players"
                        }
                    }
                    li { class: "nav-item",
                        button {
                            r#type: "button",
                            class: if register_sub_tab() == RegisterSubTab::Teams { "nav-link active" } else { "nav-link" },
                            onclick: move |_| {
                                register_sub_tab.set(RegisterSubTab::Teams);
                                team_submit_error.set(None);
                            },
                            "Teams"
                        }
                    }
                }

                div { class: "row",
                    div { class: "col-md-8",

                        if register_sub_tab() == RegisterSubTab::Players {
                            if let Some(ref err) = player_submit_error() {
                                div { class: "alert alert-danger", "{err}" }
                            }

                            // Player search section
                            div { class: "edit-section mb-3",
                                div { class: "edit-section-heading", h5 { class: "mb-0", "Find Player" } }
                                div { class: "edit-section-body",
                                    input {
                                        r#type: "text",
                                        class: "form-control mb-2",
                                        placeholder: "Search by name or username",
                                        value: "{player_search_query()}",
                                        oninput: move |e| player_search_query.set(e.value()),
                                    }
                                    if player_searching() {
                                        p { class: "text-muted small mb-0", "Searching..." }
                                    }
                                    if !player_search_results().is_empty() {
                                        ul { class: "list-group",
                                            for p in player_search_results().iter().cloned() {
                                                li {
                                                    class: "list-group-item d-flex justify-content-between align-items-center",
                                                    div {
                                                        strong { "{p.name}" }
                                                        span { class: "text-muted ms-2", "@{p.id}" }
                                                    }
                                                    button {
                                                        class: "btn btn-sm btn-primary",
                                                        r#type: "button",
                                                        onclick: move |_| {
                                                            selected_player.set(Some(p.clone()));
                                                            player_search_query.set(String::new());
                                                            player_search_results.set(Vec::new());
                                                        },
                                                        "Select"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // Selected player + registration form
                            if let Some(player) = selected_player() {
                                {
                                    let teams = d.team_registrations.clone();
                                    let waiver_required = d.tournament.waiver_required;
                                    let waiver_url = d.tournament.waiver_filepath.clone();
                                    let url_submit = url.clone();
                                    rsx! {
                                        div { class: "edit-section mb-3",
                                            div { class: "edit-section-heading d-flex justify-content-between align-items-center",
                                                h5 { class: "mb-0", "Registering: {player.name}" }
                                                button {
                                                    class: "btn btn-sm btn-outline-secondary",
                                                    r#type: "button",
                                                    onclick: move |_| {
                                                        selected_player.set(None);
                                                        selected_team.set(String::new());
                                                        jersey_number.set(String::new());
                                                        jersey_name_input.set(String::new());
                                                        waiver_signature.set(String::new());
                                                    },
                                                    "Change"
                                                }
                                            }
                                            div { class: "edit-section-body",
                                                form {
                                                    onsubmit: move |ev| {
                                                        ev.prevent_default();
                                                        if submitting() { return; }

                                                        let player = match selected_player() {
                                                            Some(p) => p,
                                                            None => return,
                                                        };
                                                        let u = url_submit.clone();
                                                        let team_val = selected_team();
                                                        let jersey_num = jersey_number();
                                                        let jersey_nm = jersey_name_input();
                                                        let waiver_sig = waiver_signature();

                                                        player_submit_error.set(None);
                                                        submitting.set(true);

                                                        spawn(async move {
                                                            let team_opt = if team_val.is_empty() {
                                                                None
                                                            } else {
                                                                Some(team_val.as_str())
                                                            };
                                                            let result: Result<RegisterPlayerAsToResponse, String> = api::register_player_as_to(
                                                                &u,
                                                                &player.id,
                                                                team_opt,
                                                                &jersey_num,
                                                                &jersey_nm,
                                                                &waiver_sig,
                                                            )
                                                            .await;
                                                            submitting.set(false);
                                                            match result {
                                                                Ok(res) if res.success => {
                                                                    session_log.with_mut(|log| {
                                                                        log.insert(0, SessionLogEntry::Player {
                                                                            player_name: res.player_name.unwrap_or_default(),
                                                                            team: res.team,
                                                                            jersey_name: res.jersey_name.unwrap_or_else(|| "N/A".into()),
                                                                            jersey_number: res.jersey_number.unwrap_or_else(|| "0".into()),
                                                                        });
                                                                    });
                                                                    selected_player.set(None);
                                                                    selected_team.set(String::new());
                                                                    jersey_number.set(String::new());
                                                                    jersey_name_input.set(String::new());
                                                                    waiver_signature.set(String::new());
                                                                    refresh.set(refresh() + 1);
                                                                }
                                                                Ok(res) => {
                                                                    player_submit_error.set(Some(
                                                                        res.error.unwrap_or_else(|| "Registration failed.".into()),
                                                                    ));
                                                                }
                                                                Err(e) => player_submit_error.set(Some(e)),
                                                            }
                                                        });
                                                    },

                                                    div { class: "mb-3",
                                                        label { r#for: "team", class: "form-label", "Team" }
                                                        select {
                                                            class: "form-select",
                                                            id: "team",
                                                            value: "{selected_team()}",
                                                            onchange: move |e| selected_team.set(e.value()),
                                                            option { value: "", "Unaffiliated (Merc)" }
                                                            for reg in teams.iter() {
                                                                {
                                                                    let label = if reg.registration.pseudonym != reg.team.name {
                                                                        format!("{} ({})", reg.registration.pseudonym, reg.team.name)
                                                                    } else {
                                                                        reg.registration.pseudonym.clone()
                                                                    };
                                                                    let val = reg.registration.team.clone();
                                                                    rsx! {
                                                                        option { value: "{val}", "{label}" }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    div { class: "mb-3",
                                                        label { r#for: "jersey_number", class: "form-label", "Jersey Number" }
                                                        input {
                                                            r#type: "text",
                                                            class: "form-control",
                                                            id: "jersey_number",
                                                            placeholder: "0",
                                                            value: "{jersey_number()}",
                                                            oninput: move |e| jersey_number.set(e.value()),
                                                        }
                                                    }
                                                    div { class: "mb-3",
                                                        label { r#for: "jersey_name", class: "form-label", "Jersey Name" }
                                                        input {
                                                            r#type: "text",
                                                            class: "form-control",
                                                            id: "jersey_name",
                                                            placeholder: "N/A",
                                                            value: "{jersey_name_input()}",
                                                            oninput: move |e| jersey_name_input.set(e.value()),
                                                        }
                                                    }
                                                    if waiver_required {
                                                        div { class: "mb-3",
                                                            if let Some(ref wurl) = waiver_url {
                                                                p { class: "form-text mb-2",
                                                                    "Waiver file: "
                                                                    a {
                                                                        href: "{api::base_url()}{wurl}",
                                                                        target: "_blank",
                                                                        class: "text-decoration-none",
                                                                        "{api::base_url()}{wurl}"
                                                                    }
                                                                }
                                                            }
                                                            p { class: "form-text mb-2",
                                                                "Enter the player's full legal name to record waiver acceptance."
                                                            }
                                                            label { r#for: "waiver_signature", class: "form-label", "Player's Legal Name" }
                                                            input {
                                                                r#type: "text",
                                                                class: "form-control",
                                                                id: "waiver_signature",
                                                                value: "{waiver_signature()}",
                                                                oninput: move |e| waiver_signature.set(e.value()),
                                                                required: true,
                                                                placeholder: "Player's full legal name",
                                                            }
                                                        }
                                                    }
                                                    div { class: "d-grid",
                                                        button {
                                                            r#type: "submit",
                                                            class: "btn btn-primary",
                                                            disabled: submitting(),
                                                            if submitting() { "Registering..." } else { "Register Player" }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if register_sub_tab() == RegisterSubTab::Teams {
                            if let Some(ref err) = team_submit_error() {
                                div { class: "alert alert-danger", "{err}" }
                            }

                            // Team search section
                            div { class: "edit-section mb-3",
                                div { class: "edit-section-heading", h5 { class: "mb-0", "Find Team" } }
                                div { class: "edit-section-body",
                                    input {
                                        r#type: "text",
                                        class: "form-control mb-2",
                                        placeholder: "Search by team name or username",
                                        value: "{team_search_query()}",
                                        oninput: move |e| team_search_query.set(e.value()),
                                    }
                                    if team_searching() {
                                        p { class: "text-muted small mb-0", "Searching..." }
                                    }
                                    if !team_search_results().is_empty() {
                                        ul { class: "list-group",
                                            for t in team_search_results().iter().cloned() {
                                                li {
                                                    class: "list-group-item d-flex justify-content-between align-items-center",
                                                    div {
                                                        strong { "{t.name}" }
                                                        span { class: "text-muted ms-2", "@{t.id}" }
                                                    }
                                                    button {
                                                        class: "btn btn-sm btn-primary",
                                                        r#type: "button",
                                                        onclick: move |_| {
                                                            team_pseudonym_input.set(t.name.clone());
                                                            team_shortname_input.set(String::new());
                                                            selected_team_for_register.set(Some(t.clone()));
                                                            team_search_query.set(String::new());
                                                            team_search_results.set(Vec::new());
                                                        },
                                                        "Select"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // Selected team + pseudonym form
                            if let Some(team) = selected_team_for_register() {
                                {
                                    let url_team_submit = url.clone();
                                    rsx! {
                                        div { class: "edit-section mb-3",
                                            div { class: "edit-section-heading d-flex justify-content-between align-items-center",
                                                h5 { class: "mb-0", "Registering: {team.name}" }
                                                button {
                                                    class: "btn btn-sm btn-outline-secondary",
                                                    r#type: "button",
                                                    onclick: move |_| {
                                                        selected_team_for_register.set(None);
                                                        team_pseudonym_input.set(String::new());
                                                        team_shortname_input.set(String::new());
                                                    },
                                                    "Change"
                                                }
                                            }
                                            div { class: "edit-section-body",
                                                form {
                                                    onsubmit: move |ev| {
                                                        ev.prevent_default();
                                                        if submitting() { return; }

                                                        let team = match selected_team_for_register() {
                                                            Some(t) => t,
                                                            None => return,
                                                        };
                                                        let u = url_team_submit.clone();
                                                        let pseudonym = team_pseudonym_input();
                                                        let shortname_raw = team_shortname_input();
                                                        let shortname = if shortname_raw.trim().is_empty() { None } else { Some(shortname_raw) };

                                                        team_submit_error.set(None);
                                                        submitting.set(true);

                                                        spawn(async move {
                                                            let result: Result<RegisterTeamAsToResponse, String> = api::register_team_as_to(&u, &team.id, &pseudonym, shortname.as_deref()).await;
                                                            submitting.set(false);
                                                            match result {
                                                                Ok(res) if res.success => {
                                                                    session_log.with_mut(|log| {
                                                                        log.insert(0, SessionLogEntry::Team {
                                                                            team_name: res.team_name.unwrap_or_default(),
                                                                            pseudonym: res.pseudonym.unwrap_or_default(),
                                                                        });
                                                                    });
                                                                    selected_team_for_register.set(None);
                                                                    team_pseudonym_input.set(String::new());
                                                                    team_shortname_input.set(String::new());
                                                                    refresh.set(refresh() + 1);
                                                                }
                                                                Ok(res) => {
                                                                    team_submit_error.set(Some(
                                                                        res.error.unwrap_or_else(|| "Team registration failed.".into()),
                                                                    ));
                                                                }
                                                                Err(e) => team_submit_error.set(Some(e)),
                                                            }
                                                        });
                                                    },

                                                    div { class: "mb-3",
                                                        label { r#for: "team_pseudonym", class: "form-label", "Tournament Pseudonym" }
                                                        input {
                                                            r#type: "text",
                                                            class: "form-control",
                                                            id: "team_pseudonym",
                                                            value: "{team_pseudonym_input()}",
                                                            oninput: move |e| team_pseudonym_input.set(e.value()),
                                                            required: true,
                                                        }
                                                        div { class: "form-text",
                                                            "How this team's name displays for this tournament. Defaults to the team's account name."
                                                        }
                                                    }

                                                    div { class: "mb-3",
                                                        label { r#for: "team_shortname", class: "form-label", "Short name (optional)" }
                                                        input {
                                                            r#type: "text",
                                                            class: "form-control",
                                                            id: "team_shortname",
                                                            maxlength: "8",
                                                            placeholder: "e.g. UWaoW",
                                                            value: "{team_shortname_input()}",
                                                            oninput: move |e| team_shortname_input.set(e.value()),
                                                        }
                                                        div { class: "form-text",
                                                            "Used in schedules and brackets. Leave blank to auto-shorten."
                                                        }
                                                    }

                                                    div { class: "d-grid",
                                                        button {
                                                            r#type: "submit",
                                                            class: "btn btn-primary",
                                                            disabled: submitting(),
                                                            if submitting() { "Registering..." } else { "Register Team" }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Session log for this tab session
                        if !session_log().is_empty() {
                            details { id: "session-registration-history", class: "card data-section section-disclosure mb-3", open: true,
                                summary { class: "card-header", h2 { class: "h5 mb-0", "Registered this session ({session_log().len()})" } }
                                ul { class: "list-group list-group-flush",
                                    for entry in session_log().iter() {
                                        match entry {
                                            SessionLogEntry::Player { player_name, team, jersey_name, jersey_number } => rsx! {
                                                li { class: "list-group-item",
                                                    strong { "{player_name}" }
                                                    " ("
                                                    if let Some(team) = team.as_ref() {
                                                        "{team}, "
                                                    } else {
                                                        "Unaffiliated, "
                                                    }
                                                    "#{jersey_number}"
                                                    if jersey_name != "N/A" {
                                                        " "
                                                        span { class: "text-muted", "{jersey_name}" }
                                                    }
                                                    ")"
                                                }
                                            },
                                            SessionLogEntry::Team { team_name, pseudonym } => rsx! {
                                                li { class: "list-group-item",
                                                    span { class: "badge table-nowrap bg-info me-2", "Team" }
                                                    strong { "{team_name}" }
                                                    if team_name != pseudonym {
                                                        " "
                                                        span { class: "text-muted", "(pseudonym: {pseudonym})" }
                                                    }
                                                }
                                            },
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { "Loading..." }
        }
    }
}

#[cfg(test)]
mod payment_tests {
    use super::validated_payment_amount;

    #[test]
    fn payment_amount_accepts_zero_and_decimal_values() {
        assert_eq!(validated_payment_amount("0"), Ok(0.0));
        assert_eq!(validated_payment_amount(" 12.50 "), Ok(12.5));
    }

    #[test]
    fn invalid_payment_amounts_cannot_become_zero_payments() {
        for value in ["", " ", "ten", "-1", "NaN", "inf", "1e999"] {
            assert!(validated_payment_amount(value).is_err(), "{value}");
        }
    }
}
