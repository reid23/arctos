use crate::api;
use crate::Route;
use dioxus::prelude::*;
use wasm_bindgen::JsCast;

#[component]
pub fn LeagueManage(league_url: String) -> Element {
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
    let url_for_data = league_url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        let s = submitted_search().clone();
        let t = submitted_type().clone();
        let _r = refresh();
        async move {
            api::league_manage(&u, &s, &t)
                .await
                .map_err(|e| e.to_string())
        }
    });
    let val = data.value();
    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h2 { "Registration management" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::LeagueHome { league_url: league_url.clone() }, "{d.tournament.name}" } }
                            li { class: "breadcrumb-item active", "Registration Management" }
                        }
                    }
                }
            }

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
                                        for (url_dereg, url_save, team_id_dereg, reg_id_save, team_data) in d.team_registrations.iter().map(|t| (league_url.clone(), league_url.clone(), t.registration.team.clone(), t.registration.id, t)) {
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
                                                                    match api::league_deregister_any_team(&u, &tid).await {
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
                                                                        match api::league_mark_team_paid(&u, rid, amount, paid, "", "", "").await {
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
                                        for (url_dereg, url_save, player_id_dereg, reg_id_save, player_data) in d.player_registrations.iter().map(|p| (league_url.clone(), league_url.clone(), p.registration.player.clone(), p.registration.id, p)) {
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
                                                                    match api::league_deregister_any_player(&u, &pid).await {
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
                                                                        match api::league_mark_player_paid(&u, rid, amount, paid, "", "", "").await {
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
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { "Loading…" }
        }
    }
}
