use crate::api;
use crate::components::{BannerAccountKind, BannerUpload, EventBannerPhoto, LeaguePenaltyTypesTable};
use crate::types::ToEntry;
use crate::Route;
use dioxus::prelude::*;
use std::collections::HashMap;
use wasm_bindgen::JsCast;

fn get_form_value(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

fn get_form_select_value(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlSelectElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

fn get_form_textarea(id: &str) -> String {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlTextAreaElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

fn get_form_check(id: &str) -> bool {
    let doc = web_sys::window().and_then(|w| w.document()).unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
        .map(|e| e.checked())
        .unwrap_or(false)
}

#[component]
fn LeagueToRow(
    to_entry: ToEntry,
    league_url: String,
    data: Resource<Result<crate::types::LeagueDetailResponse, String>>,
    to_error: Signal<Option<String>>,
) -> Element {
    let url_remove = league_url.clone();
    let mut data_remove = data.clone();
    rsx! {
        li { class: "list-group-item d-flex justify-content-between align-items-center",
            div {
                strong { "{to_entry.user_name}" }
                br {}
                small { class: "text-muted",
                    "{to_entry.user_type.to_uppercase()} ({to_entry.user_id})"
                    if to_entry.is_current_user {
                        span { class: "badge bg-primary ms-1", "You" }
                    } else { }
                }
            }
            if !to_entry.is_current_user {
                button {
                    r#type: "button",
                    class: "btn btn-sm btn-outline-danger",
                    onclick: move |_| {
                        let to_id = to_entry.id;
                        let to_name = to_entry.user_name.clone();
                        let url_clone = url_remove.clone();
                        let msg = format!("Are you sure you want to remove {} as a league organizer?", to_name);
                        let ok = web_sys::window()
                            .and_then(|w| w.confirm_with_message(&msg).ok())
                            .unwrap_or(false);
                        if !ok { return; }
                        to_error.set(None);
                        spawn(async move {
                            match api::remove_league_to(&url_clone, to_id).await {
                                Ok(res) => {
                                    if res.success {
                                        data_remove.restart();
                                    } else if let Some(e) = res.error {
                                        to_error.set(Some(e));
                                    }
                                }
                                Err(e) => { to_error.set(Some(e)); }
                            }
                        });
                    },
                    "Remove"
                }
            } else { }
        }
    }
}

#[component]
pub fn LeagueSettings(league_url: String) -> Element {
    let mut refresh = use_signal(|| 0u32);
    let lu = league_url.clone();
    let lu_form1_submit = league_url.clone();
    let mut data = use_resource(move || {
        let _ = refresh();
        let u = lu.clone();
        async move { api::league_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let trigger_refresh = move |_| refresh.set(refresh() + 1);
    let mut draft_values = use_signal(HashMap::<String, String>::new);
    let mut draft_checks = use_signal(HashMap::<String, bool>::new);
    let mut draft_initialized = use_signal(|| false);
    let local_banner_photo = use_signal(|| None::<String>);
    let mut banner_photo = try_use_context::<EventBannerPhoto>().map_or(local_banner_photo, |shared| shared.0);
    let banner_busy = use_signal(|| false);
    let mut banner_initialized = use_signal(|| false);
    let mut saving = use_signal(|| false);
    let mut save_error = use_signal(|| None::<String>);
    let mut save_success = use_signal(|| false);
    let mut to_error = use_signal(|| None as Option<String>);
    let navigator = use_navigator();
    let mut waiver_file_bytes = use_signal(|| None as Option<bytes::Bytes>);
    let mut waiver_file_name = use_signal(|| None as Option<String>);
    let mut waiver_reading = use_signal(|| false);
    let mut waiver_upload_error = use_signal(|| None as Option<String>);
    let mut require_waiver_signature_ui = use_signal(|| false);
    let mut waiver_toggle_initialized = use_signal(|| false);

    // Initialize waiver requirement toggle from backend state.
    use_effect(move || {
        if let Some(Ok(d)) = data.value().read().as_ref() {
            // Upload progress and organizer edits must not replace the settings draft.
            if !draft_initialized() {
                draft_values.set(HashMap::from([
                    ("about".into(), d.league.about.clone().unwrap_or_default()),
                    ("team_reg_fee".into(), d.league.team_reg_fee.unwrap_or(0.0).to_string()),
                    ("player_reg_fee".into(), d.league.player_reg_fee.unwrap_or(0.0).to_string()),
                    ("n_max_teams".into(), d.league.n_max_teams.map(|value| value.to_string()).unwrap_or_default()),
                    ("max_team_size_roster".into(), d.league.max_team_size_roster.map(|value| value.to_string()).unwrap_or_default()),
                    ("max_team_size_field".into(), d.league.max_team_size_field.map(|value| value.to_string()).unwrap_or_default()),
                ]));
                draft_checks.set(HashMap::from([
                    ("team_registration_open".into(), d.league.team_registration_open),
                    ("player_registration_open".into(), d.league.player_registration_open),
                    ("published".into(), d.league.published),
                ]));
                draft_initialized.set(true);
            }
            if !banner_initialized() {
                banner_photo.set(d.league.banner_photo.clone());
                banner_initialized.set(true);
            }
            if !waiver_toggle_initialized() {
                require_waiver_signature_ui.set(d.league.waiver_required);
                waiver_toggle_initialized.set(true);
                if !d.league.waiver_required {
                    waiver_file_bytes.set(None);
                    waiver_file_name.set(None);
                    waiver_upload_error.set(None);
                }
            }
        }
    });

    rsx! {
        style { {include_str!("../components/organizer_tools.css")} }
        if let Some(Ok(d)) = data.value().read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h2 { "Settings" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item",
                                Link { to: Route::LeagueHome { league_url: league_url.clone() }, "{d.league.name}" }
                            }
                            li { class: "breadcrumb-item active", "Settings" }
                        }
                    }
                }
            }
            if save_success() {
                div { class: "alert alert-success", "Settings saved." }
            }
            if let Some(ref err) = save_error() {
                div { class: "alert alert-danger", "{err}" }
            }
            div { class: "row organizer-settings",
                div { class: "col-12",
            form {
                onsubmit: move |ev| {
                    ev.prevent_default();
                    if saving() || banner_busy() || waiver_reading() { return; }
                    saving.set(true);
                    save_error.set(None);
                    save_success.set(false);
                    let about = get_form_textarea("about");
                    let team_reg_fee: f64 = get_form_value("team_reg_fee").parse().unwrap_or(0.0);
                    let player_reg_fee: f64 = get_form_value("player_reg_fee").parse().unwrap_or(0.0);
                    let team_registration_open = get_form_check("team_registration_open");
                    let player_registration_open = get_form_check("player_registration_open");
                    let published = get_form_check("published");
                    let require_waiver_signature = get_form_check("require_waiver_signature");
                    let mut params = HashMap::new();
                    params.insert("about".to_string(), serde_json::json!(about));
                    params.insert("team_reg_fee".to_string(), serde_json::json!(team_reg_fee));
                    params.insert("player_reg_fee".to_string(), serde_json::json!(player_reg_fee));
                    params.insert("team_registration_open".to_string(), serde_json::json!(team_registration_open));
                    params.insert("player_registration_open".to_string(), serde_json::json!(player_registration_open));
                    params.insert("published".to_string(), serde_json::json!(published));
                    params.insert(
                        "require_waiver_signature".to_string(),
                        serde_json::json!(require_waiver_signature),
                    );
                    let n_max_teams = get_form_value("n_max_teams");
                    let max_roster = get_form_value("max_team_size_roster");
                    let max_field = get_form_value("max_team_size_field");
                    params.insert("n_max_teams".to_string(), serde_json::json!(if n_max_teams.trim().is_empty() { serde_json::Value::Null } else { serde_json::json!(n_max_teams.parse::<u32>().unwrap_or(0)) }));
                    params.insert("max_team_size_roster".to_string(), serde_json::json!(if max_roster.trim().is_empty() { serde_json::Value::Null } else { serde_json::json!(max_roster.parse::<u32>().unwrap_or(0)) }));
                    params.insert("max_team_size_field".to_string(), serde_json::json!(if max_field.trim().is_empty() { serde_json::Value::Null } else { serde_json::json!(max_field.parse::<u32>().unwrap_or(0)) }));
                    let lu = lu_form1_submit.clone();
                    let nav = navigator.clone();
                    let league_url_for_nav = lu.clone();
                    let waiver_bytes_for_save = waiver_file_bytes();
                    let waiver_name_for_save = waiver_file_name();
                    spawn(async move {
                        match api::league_update_settings(&lu, &params).await {
                                    Ok(res) if res.success => {
                                        if let Some(bytes) = waiver_bytes_for_save {
                                            let filename =
                                                waiver_name_for_save.as_deref().unwrap_or("waiver");
                                            match api::league_upload_waiver(&lu, bytes.to_vec(), filename).await {
                                                Ok(_) => {}
                                                Err(e) => {
                                                    save_error.set(Some(e));
                                                    saving.set(false);
                                                    return;
                                                }
                                            }
                                        }
                                        save_success.set(true);
                                        let _ = nav.push(Route::LeagueHome { league_url: league_url_for_nav.clone() });
                                    }
                            Ok(res) => save_error.set(Some(res.error.unwrap_or_else(|| "Save failed.".to_string()))),
                            Err(e) => save_error.set(Some(e)),
                        }
                        saving.set(false);
                    });
                },
                div { class: "edit-section mb-3",
                    div { class: "edit-section-heading", h5 { class: "mb-0", "League settings" } }
                    div { class: "edit-section-body",
                        BannerUpload { kind: BannerAccountKind::League, account_id: league_url.clone(), photo: banner_photo, busy: banner_busy }
                        div { class: "mb-3",
                            label { r#for: "about", class: "form-label", "About (markdown)" }
                            textarea {
                                class: "form-control",
                                id: "about",
                                name: "about",
                                rows: "6",
                                value: draft_values.read().get("about").cloned().unwrap_or_default(),
                                oninput: move |event| { draft_values.write().insert("about".into(), event.value()); },
                            }
                        }
                        div { class: "row mb-3",
                            div { class: "col-md-6",
                                label { r#for: "team_reg_fee", class: "form-label", "Team registration fee" }
                                input {
                                    r#type: "number",
                                    step: "0.01",
                                    min: "0",
                                    class: "form-control",
                                    id: "team_reg_fee",
                                    value: draft_values.read().get("team_reg_fee").cloned().unwrap_or_default(),
                                    oninput: move |event| { draft_values.write().insert("team_reg_fee".into(), event.value()); }
                                }
                            }
                            div { class: "col-md-6",
                                label { r#for: "player_reg_fee", class: "form-label", "Player registration fee" }
                                input {
                                    r#type: "number",
                                    step: "0.01",
                                    min: "0",
                                    class: "form-control",
                                    id: "player_reg_fee",
                                    value: draft_values.read().get("player_reg_fee").cloned().unwrap_or_default(),
                                    oninput: move |event| { draft_values.write().insert("player_reg_fee".into(), event.value()); }
                                }
                            }
                        }
                        div { class: "mb-3",
                            h5 { class: "mb-2", "Waiver Upload" }
                            div { class: "form-check mb-2",
                                input {
                                    class: "form-check-input",
                                    r#type: "checkbox",
                                    id: "require_waiver_signature",
                                    checked: require_waiver_signature_ui(),
                                    onchange: move |ev| {
                                        let checked = ev.checked();
                                        require_waiver_signature_ui.set(checked);
                                        if !checked {
                                            waiver_file_bytes.set(None);
                                            waiver_file_name.set(None);
                                            waiver_upload_error.set(None);
                                        }
                                    }
                                }
                                label { class: "form-check-label", r#for: "require_waiver_signature", "Require waiver signature during registration" }
                            }

                            if require_waiver_signature_ui() {
                                if let Some(sha) = d.league.waiver_sha256.as_deref() {
                                    div { class: "form-text mb-1", "Current waiver hash (SHA-256):" }
                                    pre { class: "p-2 border rounded bg-light mb-2", style: "white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;", code { "{sha}" } }
                                } else {
                                    p { class: "form-text mb-1", "No waiver uploaded yet." }
                                }
                                if let Some(link) = d.league.waiver_filepath.as_deref() {
                                    a {
                                        href: api::static_file_url(link),
                                        class: "d-block small mb-3",
                                        target: "_blank",
                                        rel: "noreferrer",
                                        "View current waiver"
                                    }
                                }

                                input {
                                    r#type: "file",
                                    class: "form-control",
                                    accept: "*/*",
                                    disabled: waiver_reading(),
                                    onchange: move |evt| {
                                        #[cfg(target_arch = "wasm32")]
                                        {
                                            use dioxus::html::HasFileData;
                                            let files = evt.files();
                                            if let Some(file) = files.into_iter().next() {
                                                waiver_upload_error.set(None);
                                                waiver_reading.set(true);
                                                let filename = file.name();
                                                spawn(async move {
                                                    match file.read_bytes().await {
                                                        Ok(bytes) => {
                                                            waiver_file_bytes.set(Some(bytes));
                                                            waiver_file_name.set(Some(filename));
                                                        }
                                                        Err(_) => {
                                                            waiver_file_bytes.set(None);
                                                            waiver_file_name.set(None);
                                                            waiver_upload_error.set(Some("Failed to read file".to_string()));
                                                        }
                                                    }
                                                    waiver_reading.set(false);
                                                });
                                            }
                                        }
                                    }
                                }

                                if waiver_file_bytes().is_some() {
                                    p { class: "text-muted small mt-2 mb-2", "Ready to upload the selected waiver file." }
                                }

                                if let Some(ref err) = waiver_upload_error() {
                                    div { class: "alert alert-danger small py-2 mt-2", "{err}" }
                                }

                                div { class: "form-text mt-2", "Selected waiver uploads when you click Save Settings." }
                            } else {
                                div { class: "form-text text-muted mt-2", "Waiver signature will not be required." }
                            }
                        }
                        div { class: "row mb-3",
                            div { class: "col-md-4",
                                label { r#for: "n_max_teams", class: "form-label", "Max teams" }
                                input {
                                    r#type: "number",
                                    min: "1",
                                    class: "form-control",
                                    id: "n_max_teams",
                                    name: "n_max_teams",
                                    value: draft_values.read().get("n_max_teams").cloned().unwrap_or_default(),
                                    oninput: move |event| { draft_values.write().insert("n_max_teams".into(), event.value()); }
                                }
                            }
                            div { class: "col-md-4",
                                label { r#for: "max_team_size_roster", class: "form-label", "Max roster size" }
                                input {
                                    r#type: "number",
                                    min: "1",
                                    class: "form-control",
                                    id: "max_team_size_roster",
                                    name: "max_team_size_roster",
                                    value: draft_values.read().get("max_team_size_roster").cloned().unwrap_or_default(),
                                    oninput: move |event| { draft_values.write().insert("max_team_size_roster".into(), event.value()); }
                                }
                            }
                            div { class: "col-md-4",
                                label { r#for: "max_team_size_field", class: "form-label", "Max on field" }
                                input {
                                    r#type: "number",
                                    min: "1",
                                    class: "form-control",
                                    id: "max_team_size_field",
                                    name: "max_team_size_field",
                                    value: draft_values.read().get("max_team_size_field").cloned().unwrap_or_default(),
                                    oninput: move |event| { draft_values.write().insert("max_team_size_field".into(), event.value()); }
                                }
                            }
                        }
                        div { class: "mb-3",
                            div { class: "form-check",
                                input {
                                    r#type: "checkbox",
                                    class: "form-check-input",
                                    id: "team_registration_open",
                                    checked: draft_checks.read().get("team_registration_open").copied().unwrap_or(false),
                                    onchange: move |event| { draft_checks.write().insert("team_registration_open".into(), event.checked()); },
                                }
                                label { r#for: "team_registration_open", class: "form-check-label", "Team registration open" }
                            }
                            div { class: "form-check mt-1",
                                input {
                                    r#type: "checkbox",
                                    class: "form-check-input",
                                    id: "player_registration_open",
                                    checked: draft_checks.read().get("player_registration_open").copied().unwrap_or(false),
                                    onchange: move |event| { draft_checks.write().insert("player_registration_open".into(), event.checked()); },
                                }
                                label { r#for: "player_registration_open", class: "form-check-label", "Player registration open" }
                            }
                        }
                        div { class: "mb-3",
                            div { class: "form-check",
                                input {
                                    r#type: "checkbox",
                                    class: "form-check-input",
                                    id: "published",
                                    checked: draft_checks.read().get("published").copied().unwrap_or(false),
                                    onchange: move |event| { draft_checks.write().insert("published".into(), event.checked()); },
                                }
                                label { r#for: "published", class: "form-check-label", "Published (visible in Leagues)" }
                            }
                        }
                        button { r#type: "submit", class: "btn btn-primary", disabled: saving() || banner_busy() || waiver_reading(), if saving() { "Saving…" } else { "Save settings" } }
                    }
                }
            }

                }
                div { class: "col-12",
            LeaguePenaltyTypesTable {
                penalty_types: d.penalty_types.clone(),
                league_url: league_url.clone(),
                on_refresh: trigger_refresh,
            }

            details { class: "edit-section section-disclosure mb-3", open: true,
                summary { class: "edit-section-heading", h2 { class: "h5 mb-0", "League Organizers ({d.to_entries.len()})" } }
                div { class: "edit-section-body",
                    ul { class: "list-group list-group-flush mb-3",
                        for to_entry in &d.to_entries {
                            LeagueToRow {
                                to_entry: to_entry.clone(),
                                league_url: league_url.clone(),
                                data: data.clone(),
                                to_error: to_error,
                            }
                        }
                    }
                    if let Some(ref e) = to_error() {
                        div { class: "alert alert-danger mb-3", "{e}" }
                    }
                    form {
                        onsubmit: move |ev| {
                            ev.prevent_default();
                            let user_id = get_form_value("to_user_id").trim().to_string();
                            let user_type = get_form_select_value("to_user_type");
                            if user_id.is_empty() {
                                to_error.set(Some("User ID is required.".to_string()));
                                return;
                            }
                            to_error.set(None);
                            let url_add = league_url.clone();
                            let mut to_err = to_error.clone();
                            spawn(async move {
                                match api::add_league_to(&url_add, &user_type, &user_id).await {
                                    Ok(res) => {
                                        if res.success {
                                            data.restart();
                                        } else if let Some(e) = res.error {
                                            to_err.set(Some(e));
                                        }
                                    }
                                    Err(e) => { to_err.set(Some(e)); }
                                }
                            });
                        },
                        div { class: "row g-2 align-items-end",
                            div { class: "col-auto",
                                label { class: "form-label", r#for: "to_user_type", "User Type" }
                                select {
                                    class: "form-select form-select-sm",
                                    id: "to_user_type",
                                    name: "to_user_type",
                                    option { value: "player", "Player" }
                                    option { value: "team", "Team" }
                                }
                            }
                            div { class: "col-auto",
                                label { class: "form-label", r#for: "to_user_id", "User ID" }
                                input {
                                    class: "form-control form-control-sm",
                                    id: "to_user_id",
                                    name: "to_user_id",
                                    r#type: "text",
                                    placeholder: "Case-sensitive",
                                }
                            }
                            div { class: "col-auto",
                                button { r#type: "submit", class: "btn btn-primary btn-sm", "Add organizer" }
                            }
                        }
                    }
                }
            }

                }
            }
        } else if let Some(Err(e)) = data.value().read().as_ref() {
            div { class: "alert alert-danger", "{e}" }
        } else {
            p { class: "text-muted", "Loading…" }
        }
    }
}
