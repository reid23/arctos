use crate::api;
use crate::components::{BannerAccountKind, BannerUpload, EventBannerPhoto};
use crate::Route;
use dioxus::prelude::*;
use std::collections::HashMap;
use wasm_bindgen::JsCast;

const PREDEFINED_COLORS: &[&str] = &[
    "FF0000", "FF8C00", "FFD700", "32CD32", "008000", "00CED1", "1E90FF", "0000FF",
    "8A2BE2", "FF00FF", "C71585", "A52A2A", "808080", "000000",
];

fn get_form_value(id: &str) -> String {
    let window = web_sys::window().unwrap();
    let doc = window.document().unwrap();
    let el = match doc.get_element_by_id(id) {
        Some(e) => e,
        None => return String::new(),
    };
    if let Ok(input) = el.clone().dyn_into::<web_sys::HtmlInputElement>() {
        return input.value();
    }
    if let Ok(select) = el.dyn_into::<web_sys::HtmlSelectElement>() {
        return select.value();
    }
    String::new()
}

fn get_form_textarea(id: &str) -> String {
    let window = web_sys::window().unwrap();
    let doc = window.document().unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlTextAreaElement>().ok())
        .map(|e| e.value())
        .unwrap_or_default()
}

fn get_form_check(id: &str) -> bool {
    let window = web_sys::window().unwrap();
    let doc = window.document().unwrap();
    doc.get_element_by_id(id)
        .and_then(|e| e.dyn_into::<web_sys::HtmlInputElement>().ok())
        .map(|e| e.checked())
        .unwrap_or(false)
}

const NAME_MAX_LEN: usize = 50;

#[component]
fn PenaltyTypesTableBody(
    penalty_types: Vec<crate::types::PenaltyType>,
    url: String,
    data: Resource<Result<crate::types::TournamentDetailResponse, String>>,
    editing_pt_id: Signal<Option<i32>>,
    add_new_penalty: Signal<bool>,
    edit_name: Signal<String>,
    edit_color: Signal<String>,
    edit_desc: Signal<String>,
    edit_error: Signal<Option<String>>,
    show_color_picker_for: Signal<Option<i32>>,
    custom_color_hex: Signal<String>,
) -> Element {
    let penalty_rows: Vec<(i32, String, String, String, String, bool, String)> = penalty_types
        .iter()
        .map(|pt| {
            let desc = pt.desc.as_deref().unwrap_or("").to_string();
            let preview = if desc.len() > 80 {
                format!("{}\u{2026}", desc.chars().take(80).collect::<String>())
            } else {
                desc.clone()
            };
            (
                pt.id,
                pt.name.clone(),
                pt.color.clone(),
                desc,
                preview,
                editing_pt_id() == Some(pt.id),
                url.clone(),
            )
        })
        .collect();
    let add_new_key = "add-new-row";
    let row_elements: Vec<Element> = penalty_rows
        .into_iter()
        .map(|row| {
            let (pt_id, name, color, desc, preview, is_editing, url) = row;
            let url_save = url.clone();
            let url_del = url.clone();
            let edit_tr = rsx! {
                    tr { key: "edit-{pt_id}",
                        td { colspan: "4", class: "penalty-edit-cell",
                            div { class: "penalty-edit-grid",
                                div { class: "penalty-edit-name",
                                    label { r#for: "penalty-type-edit-name", class: "form-label", "Name" }
                                    input {
                                        id: "penalty-type-edit-name",
                                        aria_label: "Penalty type name",
                                        r#type: "text",
                                        class: "form-control form-control-sm",
                                        maxlength: "{NAME_MAX_LEN}",
                                        placeholder: "Name (max 50)",
                                        value: "{edit_name()}",
                                        oninput: move |ev| {
                                            edit_name.set(ev.value().clone());
                                            edit_error.set(None);
                                        }
                                    }
                                    span { class: "small text-muted", "{edit_name().len()}/{NAME_MAX_LEN}" }
                                }
                                div { class: "penalty-edit-color",
                                    label { r#for: "penalty-type-edit-color", class: "form-label", "Color" }
                                    div {
                                        class: "penalty-color-control",
                                        button {
                                            id: "penalty-type-edit-color",
                                            r#type: "button",
                                            class: "penalty-color-swatch",
                                            aria_label: "Choose penalty color",
                                            aria_expanded: (show_color_picker_for() == Some(pt_id)).to_string(),
                                            style: format!("background-color: #{};", edit_color()),
                                            onclick: move |_| {
                                                if show_color_picker_for() == Some(pt_id) {
                                                    show_color_picker_for.set(None);
                                                } else {
                                                    show_color_picker_for.set(Some(pt_id));
                                                    custom_color_hex.set(edit_color());
                                                }
                                            }
                                        }
                                        if show_color_picker_for() == Some(pt_id) {
                                            div { class: "penalty-color-options",
                                                div { class: "penalty-color-presets",
                                                    for c in PREDEFINED_COLORS.iter() {
                                                        button {
                                                            r#type: "button",
                                                            class: "penalty-color-swatch",
                                                            aria_label: format!("Use color #{}", c),
                                                            style: format!("background-color: #{};", *c),
                                                            onclick: move |_| {
                                                                edit_color.set(c.to_string());
                                                                custom_color_hex.set(c.to_string());
                                                            }
                                                        }
                                                    }
                                                }
                                                div { class: "input-group input-group-sm",
                                                    span { class: "input-group-text", "#" }
                                                    input {
                                                        r#type: "text",
                                                        class: "form-control",
                                                        aria_label: "Hex color",
                                                        value: "{custom_color_hex()}",
                                                        oninput: move |ev| custom_color_hex.set(ev.value().clone())
                                                    }
                                                    button {
                                                        class: "btn btn-outline-primary btn-sm",
                                                        r#type: "button",
                                                        onclick: move |_| {
                                                            let c = custom_color_hex().trim().trim_start_matches('#').to_string();
                                                            if c.len() == 6 {
                                                                edit_color.set(c);
                                                                show_color_picker_for.set(None);
                                                            }
                                                        },
                                                        "Apply"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                div { class: "penalty-edit-description",
                                    label { r#for: "penalty-type-edit-description", class: "form-label", "Description (optional)" }
                                    textarea {
                                        id: "penalty-type-edit-description",
                                        class: "form-control form-control-sm",
                                        rows: "3",
                                        placeholder: "Description (optional)",
                                        value: "{edit_desc()}",
                                        oninput: move |ev| edit_desc.set(ev.value().clone())
                                    }
                                }
                                div { class: "penalty-edit-actions",
                                    button {
                                        class: "btn btn-sm btn-primary me-1",
                                        r#type: "button",
                                        onclick: move |_| {
                                            let u = url_save.clone();
                                            let name_trim = edit_name().trim().to_string();
                                            let name_len = name_trim.len();
                                            if name_trim.is_empty() {
                                                edit_error.set(Some("Name is required.".to_string()));
                                                return;
                                            }
                                            if name_len > NAME_MAX_LEN {
                                                edit_error.set(Some(format!("Name must be at most {} characters.", NAME_MAX_LEN)));
                                                return;
                                            }
                                            let color_val = edit_color().trim_start_matches('#').to_string();
                                            let desc_val = edit_desc().trim().to_string();
                                            let desc_opt = if desc_val.is_empty() { None } else { Some(desc_val) };
                                            editing_pt_id.set(None);
                                            edit_error.set(None);
                                            let mut data = data.clone();
                                            spawn(async move {
                                                let _ = api::update_penalty_type(&u, pt_id, Some(&name_trim), Some(&color_val), desc_opt.as_deref()).await;
                                                data.restart();
                                            });
                                        },
                                        "Save"
                                    }
                                    button {
                                        class: "btn btn-sm btn-secondary",
                                        r#type: "button",
                                        onclick: move |_| {
                                            editing_pt_id.set(None);
                                            edit_error.set(None);
                                        },
                                        "Cancel"
                                    }
                                }
                            }
                        }
                    }
                };
            let view_tr = rsx! {
                                                tr { key: "{pt_id}",
                                                    td { class: "table-copy", "{name}" }
                                                    td {
                                                        div {
                                                            class: "rounded border d-inline-block",
                                                            style: format!("width: 20px; height: 20px; background-color: #{};", color)
                                                        }
                                                    }
                                                    td { class: "table-copy small text-muted", "{preview}" }
                                                    td {
                                                        button {
                                                            class: "btn btn-sm btn-outline-primary me-1",
                                                            r#type: "button",
                                                            disabled: add_new_penalty() || editing_pt_id().is_some(),
                                                            onclick: move |_| {
                                                                editing_pt_id.set(Some(pt_id));
                                                                edit_name.set(name.clone());
                                                                edit_color.set(color.clone());
                                                                edit_desc.set(desc.clone());
                                                                edit_error.set(None);
                                                            },
                                                            "Edit"
                                                        }
                                                        button {
                                                            class: "btn btn-sm btn-outline-danger",
                                                            r#type: "button",
                                                            onclick: move |_| {
                                                                let u = url_del.clone();
                                                                let row_id = pt_id;
                                                                let mut data = data.clone();
                                                                spawn(async move {
                                                                    let _ = api::delete_penalty_type(&u, row_id).await;
                                                                    data.restart();
                                                                });
                                                            },
                                                            "Delete"
                                                        }
                                                    }
                                                }
                };
            if is_editing { edit_tr } else { view_tr }
        })
        .collect();
    rsx! {
        for el in row_elements.iter() {
            {el}
        }
        if add_new_penalty() {
            tr { key: "{add_new_key}",
                td { colspan: "4", class: "penalty-edit-cell",
                    div { class: "penalty-edit-grid",
                        div { class: "penalty-edit-name",
                            label { r#for: "penalty-type-new-name", class: "form-label", "Name" }
                            input {
                                id: "penalty-type-new-name",
                                aria_label: "Penalty type name",
                                onmounted: move |_| crate::appearance::focus_control("penalty-type-new-name"),
                                r#type: "text",
                                class: "form-control form-control-sm",
                                maxlength: "{NAME_MAX_LEN}",
                                placeholder: "Name (max 50)",
                                value: "{edit_name()}",
                                oninput: move |ev| {
                                    edit_name.set(ev.value().clone());
                                    edit_error.set(None);
                                }
                            }
                            span { class: "small text-muted", "{edit_name().len()}/{NAME_MAX_LEN}" }
                            if let Some(ref err) = edit_error() {
                                span { class: "small text-danger d-block", "{err}" }
                            }
                        }
                        div { class: "penalty-edit-color",
                            label { r#for: "penalty-type-new-color", class: "form-label", "Color" }
                            div {
                                class: "penalty-color-control",
                                button {
                                    id: "penalty-type-new-color",
                                    r#type: "button",
                                    class: "penalty-color-swatch",
                                    aria_label: "Choose penalty color",
                                    aria_expanded: (show_color_picker_for() == Some(-1)).to_string(),
                                    style: format!("background-color: #{};", edit_color()),
                                    onclick: move |_| {
                                        if show_color_picker_for() == Some(-1) {
                                            show_color_picker_for.set(None);
                                        } else {
                                            show_color_picker_for.set(Some(-1));
                                            custom_color_hex.set(edit_color());
                                        }
                                    }
                                }
                                if show_color_picker_for() == Some(-1) {
                                    div { class: "penalty-color-options",
                                        div { class: "penalty-color-presets",
                                            for c in PREDEFINED_COLORS.iter() {
                                                button {
                                                    r#type: "button",
                                                    class: "penalty-color-swatch",
                                                    aria_label: format!("Use color #{}", c),
                                                    style: format!("background-color: #{};", *c),
                                                    onclick: move |_| {
                                                        edit_color.set(c.to_string());
                                                        custom_color_hex.set(c.to_string());
                                                    }
                                                }
                                            }
                                        }
                                        div { class: "input-group input-group-sm",
                                            span { class: "input-group-text", "#" }
                                            input {
                                                r#type: "text",
                                                class: "form-control",
                                                aria_label: "Hex color",
                                                value: "{custom_color_hex()}",
                                                oninput: move |ev| custom_color_hex.set(ev.value().clone())
                                            }
                                            button {
                                                class: "btn btn-outline-primary btn-sm",
                                                r#type: "button",
                                                onclick: move |_| {
                                                    let c = custom_color_hex().trim().trim_start_matches('#').to_string();
                                                    if c.len() == 6 {
                                                        edit_color.set(c);
                                                        show_color_picker_for.set(None);
                                                    }
                                                },
                                                "Apply"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        div { class: "penalty-edit-description",
                            label { r#for: "penalty-type-new-description", class: "form-label", "Description (optional)" }
                            textarea {
                                id: "penalty-type-new-description",
                                class: "form-control form-control-sm",
                                rows: "3",
                                placeholder: "Description (optional)",
                                value: "{edit_desc()}",
                                oninput: move |ev| edit_desc.set(ev.value().clone())
                            }
                        }
                        div { class: "penalty-edit-actions",
                            button {
                                class: "btn btn-sm btn-primary me-1",
                                r#type: "button",
                                onclick: move |_| {
                                    let u = url.clone();
                                    let name_trim = edit_name().trim().to_string();
                                    if name_trim.is_empty() {
                                        edit_error.set(Some("Name is required.".to_string()));
                                        return;
                                    }
                                    if name_trim.len() > NAME_MAX_LEN {
                                        edit_error.set(Some(format!("Name must be at most {} characters.", NAME_MAX_LEN)));
                                        return;
                                    }
                                    let color_val = edit_color().trim_start_matches('#').to_string();
                                    let desc_val = edit_desc().trim().to_string();
                                    let color_opt = if color_val.len() == 6 { Some(color_val) } else { None };
                                    let desc_opt = if desc_val.is_empty() { None } else { Some(desc_val) };
                                    add_new_penalty.set(false);
                                    edit_error.set(None);
                                    let mut data = data.clone();
                                    spawn(async move {
                                        let _ = api::create_penalty_type(&u, &name_trim, color_opt.as_deref(), desc_opt.as_deref()).await;
                                        data.restart();
                                    });
                                },
                                "Save"
                            }
                            button {
                                class: "btn btn-sm btn-secondary",
                                r#type: "button",
                                onclick: move |_| {
                                    add_new_penalty.set(false);
                                    edit_name.set(String::new());
                                    edit_color.set("808080".to_string());
                                    edit_desc.set(String::new());
                                    edit_error.set(None);
                                    show_color_picker_for.set(None);
                                },
                                "Cancel"
                            }
                        }
                    }
                }
            }
        }

    }
}

#[component]
fn ToRow(
    to_entry: crate::types::ToEntry,
    url: String,
    data: Resource<Result<crate::types::TournamentDetailResponse, String>>,
    to_error: Signal<Option<String>>,
) -> Element {
    let url_remove = url.clone();
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
                        let msg = format!("Are you sure you want to remove {} as a TO?", to_name);
                        let ok = web_sys::window()
                            .and_then(|w| w.confirm_with_message(&msg).ok())
                            .unwrap_or(false);
                        if !ok { return; }
                        to_error.set(None);
                        spawn(async move {
                            match api::remove_tournament_to(&url_clone, to_id).await {
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
pub fn TournamentSettings(url: String) -> Element {
    let navigator = use_navigator();
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        async move { api::tournament_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let mut draft_values = use_signal(HashMap::<String, String>::new);
    let mut draft_checks = use_signal(HashMap::<String, bool>::new);
    let mut draft_initialized = use_signal(|| false);
    let local_banner_photo = use_signal(|| None::<String>);
    let mut banner_photo = try_use_context::<EventBannerPhoto>().map_or(local_banner_photo, |shared| shared.0);
    let banner_busy = use_signal(|| false);
    let mut banner_initialized = use_signal(|| false);
    let mut saving = use_signal(|| false);
    let mut save_error = use_signal(|| None::<String>);
    let mut editing_pt_id = use_signal(|| None as Option<i32>);
    let mut add_new_penalty = use_signal(|| false);
    let mut penalties_open = use_signal(|| false);
    let mut edit_name = use_signal(|| String::new());
    let mut edit_color = use_signal(|| "808080".to_string());
    let mut edit_desc = use_signal(|| String::new());
    let mut edit_error = use_signal(|| None as Option<String>);
    let mut show_color_picker_for = use_signal(|| None as Option<i32>);
    let mut custom_color_hex = use_signal(|| String::new());
    let mut to_error = use_signal(|| None as Option<String>);
    let mut waiver_file_bytes = use_signal(|| None as Option<bytes::Bytes>);
    let mut waiver_file_name = use_signal(|| None as Option<String>);
    let mut waiver_reading = use_signal(|| false);
    let mut waiver_upload_error = use_signal(|| None as Option<String>);
    let mut require_waiver_signature_ui = use_signal(|| false);
    let mut waiver_toggle_initialized = use_signal(|| false);
    let val = data.value();
    let url_form = url.clone();
    let url_form_submit = url_form.clone();

    use_effect(move || {
        if let Some(Ok(d)) = val.read().as_ref() {
            // Keep the form draft independent of upload status and detail refreshes.
            if !draft_initialized() {
                draft_values.set(HashMap::from([
                    ("name".into(), d.tournament.name.clone()),
                    ("location".into(), d.tournament.location.clone().unwrap_or_default()),
                    ("start_date".into(), d.tournament.start_date.split('T').next().unwrap_or(&d.tournament.start_date).to_string()),
                    ("end_date".into(), d.tournament.end_date.as_deref().map(|value| value.split('T').next().unwrap_or(value)).unwrap_or_default().to_string()),
                    ("n_max_teams".into(), d.tournament.n_max_teams.map(|value| value.to_string()).unwrap_or_default()),
                    ("max_team_size_roster".into(), d.tournament.max_team_size_roster.map(|value| value.to_string()).unwrap_or_default()),
                    ("max_team_size_field".into(), d.tournament.max_team_size_field.map(|value| value.to_string()).unwrap_or_default()),
                    ("team_reg_fee".into(), d.tournament.team_reg_fee.unwrap_or(0.0).to_string()),
                    ("player_reg_fee".into(), d.tournament.player_reg_fee.unwrap_or(0.0).to_string()),
                    ("about".into(), d.tournament.about.clone().unwrap_or_default()),
                    ("head_refs_allowed_list".into(), d.tournament.head_refs_allowed_list.clone().unwrap_or_default()),
                ]));
                draft_checks.set(HashMap::from([
                    ("head_refs_allow_anyone".into(), d.tournament.head_refs_allow_anyone),
                    ("head_refs_allow_reffing_teams".into(), d.tournament.head_refs_allow_reffing_teams),
                    ("published".into(), d.tournament.published),
                    ("schedule_published".into(), d.tournament.schedule_published),
                    ("team_registration_open".into(), d.tournament.team_registration_open),
                    ("player_registration_open".into(), d.tournament.player_registration_open),
                ]));
                draft_initialized.set(true);
            }
            if !banner_initialized() {
                banner_photo.set(d.tournament.banner_photo.clone());
                banner_initialized.set(true);
            }
            // Only initialize once so user toggles aren't overwritten by rerenders.
            if !waiver_toggle_initialized() {
                require_waiver_signature_ui.set(d.tournament.waiver_required);
                waiver_toggle_initialized.set(true);
                if !d.tournament.waiver_required {
                    waiver_file_bytes.set(None);
                    waiver_file_name.set(None);
                    waiver_upload_error.set(None);
                }
            }
        }
    });
    rsx! {
        style { {include_str!("../components/organizer_tools.css")} }
        if let Some(Ok(d)) = val.read().as_ref() {
            div { class: "penalty-settings-wrap organizer-settings",
                div { class: "row",
                div { class: "col-12",
                    h2 { "Settings" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::TournamentHome { url: url.clone() }, "{d.tournament.name}" } }
                            li { class: "breadcrumb-item active", "Settings" }
                        }
                    }
                }
            }

            div { class: "row",
                div { class: "col-12",
                    div { class: "edit-section",
                        div { class: "edit-section-heading",
                            h5 { class: "mb-0", "Tournament Information" }
                        }
                        div { class: "edit-section-body",
                            BannerUpload { kind: BannerAccountKind::Tournament, account_id: url.clone(), photo: banner_photo, busy: banner_busy }
                            form {
                                id: "tournament-settings-form",
                                onsubmit: move |ev| {
                                    ev.prevent_default();
                                    if saving() || banner_busy() || waiver_reading() { return; }
                                    saving.set(true);
                                    save_error.set(None);
                                    let mut params: Vec<(String, String)> = vec![
                                        ("name".into(), get_form_value("name")),
                                        ("location".into(), get_form_value("location")),
                                        ("start_date".into(), get_form_value("start_date")),
                                        ("end_date".into(), get_form_value("end_date")),
                                        ("n_max_teams".into(), get_form_value("n_max_teams")),
                                        ("max_team_size_roster".into(), get_form_value("max_team_size_roster")),
                                        ("max_team_size_field".into(), get_form_value("max_team_size_field")),
                                        ("about".into(), get_form_textarea("about")),
                                        ("head_refs_allowed_list".into(), get_form_value("head_refs_allowed_list")),
                                        ("team_reg_fee".into(), get_form_value("team_reg_fee")),
                                        ("player_reg_fee".into(), get_form_value("player_reg_fee")),
                                    ];
                                    if get_form_check("head_refs_allow_anyone") {
                                        params.push(("head_refs_allow_anyone".into(), "on".to_string()));
                                    }
                                    if get_form_check("head_refs_allow_reffing_teams") {
                                        params.push(("head_refs_allow_reffing_teams".into(), "on".to_string()));
                                    }
                                    if get_form_check("published") {
                                        params.push(("published".into(), "on".to_string()));
                                    }
                                    if get_form_check("schedule_published") {
                                        params.push(("schedule_published".into(), "on".to_string()));
                                    }
                                    if get_form_check("team_registration_open") {
                                        params.push(("team_registration_open".into(), "on".to_string()));
                                    }
                                    if get_form_check("player_registration_open") {
                                        params.push(("player_registration_open".into(), "on".to_string()));
                                    }
                                    if get_form_check("require_waiver_signature") {
                                        params.push((
                                            "require_waiver_signature".into(),
                                            "on".to_string(),
                                        ));
                                    }
                                    let nav = navigator.clone();
                                    let url_submit = url_form_submit.clone();
                                    let waiver_bytes_for_save = waiver_file_bytes();
                                    let waiver_name_for_save = waiver_file_name();
                                    spawn(async move {
                                        match api::update_tournament_settings(&url_submit, &params).await {
                                            Ok(res) => {
                                                if res.success {
                                                    if let Some(bytes) = waiver_bytes_for_save {
                                                        let filename =
                                                            waiver_name_for_save.as_deref().unwrap_or("waiver");
                                                        match api::upload_waiver(&url_submit, bytes.to_vec(), filename).await {
                                                            Ok(_) => {}
                                                            Err(e) => {
                                                                waiver_upload_error.set(Some(e));
                                                                saving.set(false);
                                                                return;
                                                            }
                                                        }
                                                    }
                                                    nav.push(Route::TournamentHome { url: url_submit });
                                                } else {
                                                    save_error.set(Some(res.error.unwrap_or_else(|| "Could not save settings. Try again.".into())));
                                                }
                                            }
                                            Err(error) => save_error.set(Some(error)),
                                        }
                                        saving.set(false);
                                    });
                                },
                                div { class: "row",
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "name", class: "form-label", "Tournament Name" }
                                            input { r#type: "text", class: "form-control", id: "name", name: "name", value: draft_values.read().get("name").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("name".into(), event.value()); }, required: true }
                                        }
                                    }
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "location", class: "form-label", "Location" }
                                            input { r#type: "text", class: "form-control", id: "location", name: "location", value: draft_values.read().get("location").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("location".into(), event.value()); } }
                                        }
                                    }
                                }

                                div { class: "row",
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "start_date", class: "form-label", "Start Date" }
                                            input { r#type: "date", class: "form-control", id: "start_date", name: "start_date", value: draft_values.read().get("start_date").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("start_date".into(), event.value()); }, required: true }
                                        }
                                    }
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "end_date", class: "form-label", "End Date" }
                                            input { r#type: "date", class: "form-control", id: "end_date", name: "end_date", value: draft_values.read().get("end_date").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("end_date".into(), event.value()); }, required: true }
                                        }
                                    }
                                }

                                if d.tournament.league.is_none() {
                                div { class: "row",
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "n_max_teams", class: "form-label", "Max Teams" }
                                            input { r#type: "number", class: "form-control", id: "n_max_teams", name: "n_max_teams", value: draft_values.read().get("n_max_teams").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("n_max_teams".into(), event.value()); }, min: "1" }
                                        }
                                    }
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "max_team_size_roster", class: "form-label", "Max Team Size (Roster)" }
                                            input { r#type: "number", class: "form-control", id: "max_team_size_roster", name: "max_team_size_roster", value: draft_values.read().get("max_team_size_roster").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("max_team_size_roster".into(), event.value()); }, min: "1" }
                                            div { class: "form-text", "Maximum players on team roster" }
                                        }
                                    }
                                }

                                div { class: "row",
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "max_team_size_field", class: "form-label", "Max Team Size (Field)" }
                                            input { r#type: "number", class: "form-control", id: "max_team_size_field", name: "max_team_size_field", value: draft_values.read().get("max_team_size_field").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("max_team_size_field".into(), event.value()); }, min: "1" }
                                            div { class: "form-text", "Maximum players on field at once" }
                                        }
                                    }
                                }
                                }

                                if d.tournament.league.is_none() {
                                div { class: "row",
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "team_reg_fee", class: "form-label", "Team Registration Fee" }
                                            div { class: "input-group",
                                                span { class: "input-group-text", "$" }
                                                input { r#type: "number", class: "form-control", id: "team_reg_fee", name: "team_reg_fee", value: draft_values.read().get("team_reg_fee").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("team_reg_fee".into(), event.value()); }, step: "0.01", min: "0" }
                                            }
                                        }
                                    }
                                    div { class: "col-md-6",
                                        div { class: "mb-3",
                                            label { r#for: "player_reg_fee", class: "form-label", "Player Registration Fee" }
                                            div { class: "input-group",
                                                span { class: "input-group-text", "$" }
                                                input { r#type: "number", class: "form-control", id: "player_reg_fee", name: "player_reg_fee", value: draft_values.read().get("player_reg_fee").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("player_reg_fee".into(), event.value()); }, step: "0.01", min: "0" }
                                            }
                                        }
                                    }
                                }
                                }

                                div { class: "mb-3",
                                    label { r#for: "about", class: "form-label", "About" }
                                    textarea { class: "form-control", id: "about", name: "about", rows: "4", value: draft_values.read().get("about").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("about".into(), event.value()); } }
                                    div { class: "form-text",
                                        "supports "
                                        a { href: "https://www.markdownguide.org/basic-syntax/", "markdown" }
                                        ", including most of the "
                                        a { href: "https://www.markdownguide.org/extended-syntax/", "extended syntax" }
                                        ". Images can be inserted with "
                                        code { "![alt text](https://image_url)" }
                                        ", and links with "
                                        code { "[text](link)" }
                                        "."
                                    }
                                }

                                if d.tournament.league.is_none() {
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
                                            if let Some(sha) = d.tournament.waiver_sha256.as_deref() {
                                                div { class: "form-text mb-1", "Current waiver hash (SHA-256):" }
                                                pre { class: "p-2 border rounded bg-light mb-2", style: "white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;", code { "{sha}" } }
                                            } else {
                                                p { class: "form-text mb-1", "No waiver uploaded yet." }
                                            }
                                            if let Some(link) = d.tournament.waiver_filepath.as_deref() {
                                                a { href: api::static_file_url(link), class: "d-block small mb-3", target: "_blank", rel: "noreferrer", "View current waiver" }
                                            }

                                            div { class: "mb-2",
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
                                            }

                                            if waiver_file_bytes().is_some() {
                                                p { class: "text-muted small mb-2", "Ready to upload the selected waiver file." }
                                            }

                                            if let Some(ref err) = waiver_upload_error() {
                                                div { class: "alert alert-danger small py-2", "{err}" }
                                            }

                                            div { class: "form-text", "Selected waiver uploads when you click Save Settings." }
                                        } else {
                                            div { class: "form-text text-muted", "Waiver signature will not be required." }
                                        }
                                    }
                                }

                                details { class: "settings-advanced", id: "head-ref-options",
                                    summary { "Advanced options" small {
                                        "Head referee permissions · "
                                        if draft_checks.read().get("head_refs_allow_anyone").copied().unwrap_or(false) { "registered players" }
                                        else if draft_checks.read().get("head_refs_allow_reffing_teams").copied().unwrap_or(false) { "assigned referee teams and named players" }
                                        else { "named players" }
                                    } }
                                    div { class: "settings-advanced-body",
                                    h3 { class: "h5", "Head referee permissions" }
                                    p { class: "text-muted", "Choose which player accounts can run matches. Listed players can referee without registering; other players must be registered. Team accounts cannot head referee." }
                                    p { class: "form-text", "Tell referees how these permissions work before play. A started match cannot be unstarted." }
                                div { class: "mb-3",
                                    label { r#for: "head_refs_allowed_list", class: "form-label", "Explicit List of Allowed Usernames" }
                                    input { r#type: "text", class: "form-control", id: "head_refs_allowed_list", name: "head_refs_allowed_list", value: draft_values.read().get("head_refs_allowed_list").cloned().unwrap_or_default(), oninput: move |event| { draft_values.write().insert("head_refs_allowed_list".into(), event.value()); }, placeholder: "player1,player2,player3" }
                                    div { class: "form-text", "Comma-separated list of player IDs who can ref matches" }
                                }
                                div { class: "mb-3",
                                    div { class: "form-check",
                                        input { class: "form-check-input", r#type: "checkbox", id: "head_refs_allow_anyone", name: "head_refs_allow_anyone", checked: draft_checks.read().get("head_refs_allow_anyone").copied().unwrap_or(false), onchange: move |event| { draft_checks.write().insert("head_refs_allow_anyone".into(), event.checked()); } }
                                        label { class: "form-check-label", r#for: "head_refs_allow_anyone", "Allow all registered players to run matches" }
                                        div { class: "form-text", "When enabled, players who are registered for the tournament can head ref all matches." }
                                    }
                                }
                                div { id: "head_ref_specific_options",
                                    div { class: "mb-3",
                                        div { class: "form-check",
                                            input { class: "form-check-input", r#type: "checkbox", id: "head_refs_allow_reffing_teams", name: "head_refs_allow_reffing_teams", checked: draft_checks.read().get("head_refs_allow_reffing_teams").copied().unwrap_or(false), onchange: move |event| { draft_checks.write().insert("head_refs_allow_reffing_teams".into(), event.checked()); } }
                                            label { class: "form-check-label", r#for: "head_refs_allow_reffing_teams", "Allow reffing teams to head ref" }
                                            div { class: "form-text", "When enabled, players on teams assigned to ref a match can head ref that match." }
                                        }
                                    }
                                }

                                }
                                }

                                h3 { "Publication and registration" }
                                div { class: "mb-3",
                                    div { class: "form-check",
                                        input { class: "form-check-input", r#type: "checkbox", id: "published", name: "published", checked: draft_checks.read().get("published").copied().unwrap_or(false), onchange: move |event| { draft_checks.write().insert("published".into(), event.checked()); } }
                                        label { class: "form-check-label", r#for: "published", "Published" }
                                        div { class: "form-text", "Show this tournament in Events." }
                                    }
                                }
                                div { class: "mb-3",
                                    div { class: "form-check",
                                        input { class: "form-check-input", r#type: "checkbox", id: "schedule_published", name: "schedule_published", checked: draft_checks.read().get("schedule_published").copied().unwrap_or(false), onchange: move |event| { draft_checks.write().insert("schedule_published".into(), event.checked()); } }
                                        label { class: "form-check-label", r#for: "schedule_published", "Schedule Published (schedule visible to public)" }
                                        div { class: "form-text", "Make the schedule public. Organizers and head referees can still view an unpublished schedule." }
                                    }
                                }
                                if d.tournament.league.is_none() {
                                    div { class: "mb-3",
                                        div { class: "form-check",
                                            input { class: "form-check-input", r#type: "checkbox", id: "team_registration_open", name: "team_registration_open", checked: draft_checks.read().get("team_registration_open").copied().unwrap_or(false), onchange: move |event| { draft_checks.write().insert("team_registration_open".into(), event.checked()); } }
                                            label { class: "form-check-label", r#for: "team_registration_open", "Team registration open" }
                                        }
                                        div { class: "form-check mt-1",
                                            input { class: "form-check-input", r#type: "checkbox", id: "player_registration_open", name: "player_registration_open", checked: draft_checks.read().get("player_registration_open").copied().unwrap_or(false), onchange: move |event| { draft_checks.write().insert("player_registration_open".into(), event.checked()); } }
                                            label { class: "form-check-label", r#for: "player_registration_open", "Player registration open" }
                                        }
                                    }
                                }

                                div { class: "d-grid",
                                    if let Some(message) = save_error() { p { class: "text-danger", role: "alert", "{message}" } }
                                    button { r#type: "submit", class: "btn btn-primary", disabled: saving() || banner_busy() || waiver_reading(), if saving() { "Saving…" } else { "Save Settings" } }
                                }
                            }
                        }
                    }
                }

                div { class: "col-12 mt-4",
                    if d.tournament.league.is_none() {
                    section { class: "edit-section penalty-types-section",
                        div { class: "edit-section-heading penalty-types-heading",
                            h2 { class: "h5 mb-0",
                                button { id: "penalty-types-toggle", class: "penalty-types-toggle", r#type: "button",
                                    aria_expanded: "{penalties_open()}", aria_controls: "penalty-types-content",
                                    onclick: move |_| penalties_open.set(!penalties_open()),
                                    i { class: if penalties_open() { "fas fa-chevron-down" } else { "fas fa-chevron-right" }, aria_hidden: "true" }
                                    "Penalty types ({d.penalty_types.len()})"
                                }
                            }
                            button { id: "penalty-types-add", class: "btn btn-sm btn-outline-secondary penalty-types-add", r#type: "button",
                                aria_label: "Add penalty type",
                                disabled: add_new_penalty() || editing_pt_id().is_some(),
                                onclick: move |_| {
                                    if add_new_penalty() || editing_pt_id().is_some() { return; }
                                    edit_name.set(String::new());
                                    edit_color.set("808080".to_string());
                                    edit_desc.set(String::new());
                                    edit_error.set(None);
                                    show_color_picker_for.set(None);
                                    penalties_open.set(true);
                                    add_new_penalty.set(true);
                                },
                                i { class: "fas fa-plus me-1", aria_hidden: "true" }
                                "Add"
                            }
                        }
                        div { id: "penalty-types-content", class: "edit-section-body", hidden: !penalties_open(),
                            div { class: "table-responsive",
                                table { class: "table table-sm",
                                    thead {
                                        tr {
                                            th { class: "table-nowrap", "Name" }
                                            th { class: "table-nowrap", "Color" }
                                            th { class: "table-nowrap", "Description" }
                                            th { class: "table-nowrap", style: "width: 1%;", "Actions" }
                                        }
                                    }
                                    tbody {
                                        PenaltyTypesTableBody {
                                            penalty_types: d.penalty_types.clone(),
                                            url: url.clone(),
                                            data: data.clone(),
                                            editing_pt_id: editing_pt_id,
                                            add_new_penalty: add_new_penalty,
                                            edit_name: edit_name,
                                            edit_color: edit_color,
                                            edit_desc: edit_desc,
                                            edit_error: edit_error,
                                            show_color_picker_for: show_color_picker_for,
                                            custom_color_hex: custom_color_hex,
                                        }
                                    }
                                }
                            }
                        }
                    }
                    }

                    if d.tournament.league.is_none() {
                    details { class: "edit-section section-disclosure mt-4", open: true,
                        summary { class: "edit-section-heading",
                            h2 { class: "h5 mb-0", "Tournament Organizers ({d.to_entries.len()})" }
                        }
                        div { class: "edit-section-body",
                            if !d.to_entries.is_empty() {
                                ul { class: "list-group list-group-flush mb-3",
                                    for to_entry in d.to_entries.iter() {
                                        ToRow {
                                            to_entry: to_entry.clone(),
                                            url: url.clone(),
                                            data: data.clone(),
                                            to_error: to_error,
                                        }
                                    }
                                }
                            } else {
                                p { class: "text-muted", "No TOs found" }
                            }
                            hr {}
                            h6 { class: "mb-3", "Add New TO" }
                            if let Some(ref e) = to_error() {
                                div { class: "alert alert-danger mb-3", "{e}" }
                            }
                            form {
                                class: "row g-2 align-items-end",
                                onsubmit: move |ev| {
                                    ev.prevent_default();
                                    let user_type = get_form_value("to_user_type");
                                    let user_id = get_form_value("to_user_id").trim().to_string();
                                    if user_id.is_empty() {
                                        to_error.set(Some("User ID is required.".to_string()));
                                        return;
                                    }
                                    to_error.set(None);
                                    let url_add = url.clone();
                                    let mut data_add = data.clone();
                                    let mut to_err = to_error.clone();
                                    spawn(async move {
                                        match api::add_tournament_to(&url_add, &user_type, &user_id).await {
                                            Ok(res) => {
                                                if res.success {
                                                    data_add.restart();
                                                } else if let Some(e) = res.error {
                                                    to_err.set(Some(e));
                                                }
                                            }
                                            Err(e) => { to_err.set(Some(e)); }
                                        }
                                    });
                                },
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
                                    button { r#type: "submit", class: "btn btn-primary btn-sm", "Add TO" }
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
