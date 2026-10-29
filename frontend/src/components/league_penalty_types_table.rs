//! Penalty types table for league settings.

use crate::api;
use dioxus::prelude::*;

const PREDEFINED_COLORS: &[&str] = &[
    "FF0000", "FF8C00", "FFD700", "32CD32", "008000", "00CED1", "1E90FF", "0000FF",
    "8A2BE2", "FF00FF", "C71585", "A52A2A", "808080", "000000",
];
const NAME_MAX_LEN: usize = 50;

#[component]
pub fn LeaguePenaltyTypesTable(
    penalty_types: Vec<crate::types::PenaltyType>,
    league_url: String,
    on_refresh: EventHandler<()>,
    mutation_pending: Signal<bool>,
    settings_saving: Signal<bool>,
) -> Element {
    let mut editing_pt_id = use_signal(|| None as Option<i32>);
    let mut add_new_penalty = use_signal(|| false);
    let mut penalties_open = use_signal(|| false);
    let mut edit_name = use_signal(|| String::new());
    let mut edit_color = use_signal(|| "808080".to_string());
    let mut edit_desc = use_signal(|| String::new());
    let mut edit_error = use_signal(|| None as Option<String>);
    let mut creation_needs_check = use_signal(|| false);
    let mut creation_known_ids = use_signal(Vec::<i32>::new);
    let mut show_color_picker_for = use_signal(|| None as Option<i32>);
    let mut custom_color_hex = use_signal(|| String::new());

    use_effect(move || {
        if !mutation_pending() && edit_error().is_some() {
            crate::appearance::focus_control("penalty-save-error");
        }
    });
    let existing_ids: Vec<i32> = penalty_types.iter().map(|penalty| penalty.id).collect();
    let penalty_rows: Vec<_> = penalty_types
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
            )
        })
        .collect();

    let row_elements: Vec<Element> = penalty_rows
        .into_iter()
        .map(|(pt_id, name, color, desc, preview, is_editing)| {
            let url_save = league_url.clone();
            let url_del = league_url.clone();
            let on_refresh = on_refresh.clone();
            let edit_tr = rsx! {
                tr { key: "edit-{pt_id}",
                    td { colspan: "4", class: "penalty-edit-cell",
                        div { class: "penalty-edit-grid",
                            div { class: "penalty-edit-name",
                                label { r#for: "penalty-type-edit-name", class: "form-label", "Name" }
                                input {
                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                span { class: "small text-muted", "{edit_name().chars().count()}/{NAME_MAX_LEN}" }
                            }
                            div { class: "penalty-edit-color",
                                label { r#for: "penalty-type-edit-color", class: "form-label", "Color" }
                                div {
                                    class: "penalty-color-control",
                                    button {
                                        id: "penalty-type-edit-color",
                                        r#type: "button",
                                        disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                                        disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
                                                    r#type: "text",
                                                    class: "form-control",
                                                    aria_label: "Hex color",
                                                    value: "{custom_color_hex()}",
                                                    oninput: move |ev| custom_color_hex.set(ev.value().clone())
                                                }
                                                button {
                                                    class: "btn btn-outline-primary btn-sm",
                                                    r#type: "button",
                                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
                                                    onclick: move |_| {
                                                        let c = custom_color_hex().trim().trim_start_matches('#').to_string();
                                                        if c.len() == 6 && c.chars().all(|value| value.is_ascii_hexdigit()) {
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
                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                    disabled: settings_saving() || mutation_pending(),
                                    id: "penalty-type-save",
                                    onclick: move |_| {
                                        if settings_saving() || mutation_pending() { return; }
                                        let name_trim = edit_name().trim().to_string();
                                        if name_trim.is_empty() {
                                            edit_error.set(Some("Name is required.".to_string()));
                                            return;
                                        }
                                        if name_trim.chars().count() > NAME_MAX_LEN {
                                            edit_error.set(Some(format!("Name must be at most {} characters.", NAME_MAX_LEN)));
                                            return;
                                        }
                                        let color_val = edit_color().trim_start_matches('#').to_string();
                                    if color_val.len() != 6 || !color_val.chars().all(|value| value.is_ascii_hexdigit()) {
                                        edit_error.set(Some("Choose a color using six hexadecimal digits.".into()));
                                        return;
                                    }
                                        let desc_val = edit_desc().trim().to_string();
                                        edit_error.set(None);
                                        mutation_pending.set(true);
                                        let u = url_save.clone();
                                        spawn(async move {
                                            match api::update_league_penalty_type(&u, pt_id, Some(&name_trim), Some(&color_val), Some(&desc_val)).await {
                                                Ok(_) => {
                                                    editing_pt_id.set(None);
                                                    show_color_picker_for.set(None);
                                                    on_refresh.call(());
                                                }
                                                Err(error) => edit_error.set(Some(error)),
                                            }
                                            mutation_pending.set(false);
                                        });
                                    },
                                    if mutation_pending() { "Saving…" } else { "Save" }
                                }
                                button {
                                    class: "btn btn-sm btn-secondary",
                                    r#type: "button",
                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                            disabled: settings_saving() || mutation_pending() || add_new_penalty() || editing_pt_id().is_some(),
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
                            disabled: settings_saving() || mutation_pending() || add_new_penalty() || editing_pt_id().is_some(),
                            onclick: move |_| {
                                if settings_saving() || mutation_pending() { return; }
                                let u = url_del.clone();
                                let row_id = pt_id;
                                edit_error.set(None);
                                mutation_pending.set(true);
                                spawn(async move {
                                    match api::delete_league_penalty_type(&u, row_id).await {
                                        Ok(_) => { on_refresh.call(()); }
                                        Err(error) => edit_error.set(Some(error)),
                                    }
                                    mutation_pending.set(false);
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

    let rows = rsx! {
        for el in row_elements.iter() {
            {el}
        }
        if add_new_penalty() {
            tr { key: "add-new-row-{league_url}",
                td { colspan: "4", class: "penalty-edit-cell",
                    div { class: "penalty-edit-grid",
                        div { class: "penalty-edit-name",
                            label { r#for: "penalty-type-new-name", class: "form-label", "Name" }
                            input {
                                disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                            span { class: "small text-muted", "{edit_name().chars().count()}/{NAME_MAX_LEN}" }
                        }
                        div { class: "penalty-edit-color",
                            label { r#for: "penalty-type-new-color", class: "form-label", "Color" }
                            div {
                                class: "penalty-color-control",
                                button {
                                    id: "penalty-type-new-color",
                                    r#type: "button",
                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                                    disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                                disabled: settings_saving() || mutation_pending() || creation_needs_check(),
                                                r#type: "text",
                                                class: "form-control",
                                                aria_label: "Hex color",
                                                value: "{custom_color_hex()}",
                                                oninput: move |ev| custom_color_hex.set(ev.value().clone())
                                            }
                                            button {
                                                class: "btn btn-outline-primary btn-sm",
                                                r#type: "button",
                                                disabled: settings_saving() || mutation_pending() || creation_needs_check(),
                                                onclick: move |_| {
                                                    let c = custom_color_hex().trim().trim_start_matches('#').to_string();
                                                    if c.len() == 6 && c.chars().all(|value| value.is_ascii_hexdigit()) {
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
                                disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
                                    disabled: settings_saving() || mutation_pending(),
                                    id: "penalty-type-save",
                                onclick: move |_| {
                                    if settings_saving() || mutation_pending() { return; }
                                    let u = league_url.clone();
                                    let name_trim = edit_name().trim().to_string();
                                    if name_trim.is_empty() {
                                        edit_error.set(Some("Name is required.".to_string()));
                                        return;
                                    }
                                    if name_trim.chars().count() > NAME_MAX_LEN {
                                        edit_error.set(Some(format!("Name must be at most {} characters.", NAME_MAX_LEN)));
                                        return;
                                    }
                                    let color_val = edit_color().trim_start_matches('#').to_string();
                                    if color_val.len() != 6 || !color_val.chars().all(|value| value.is_ascii_hexdigit()) {
                                        edit_error.set(Some("Choose a color using six hexadecimal digits.".into()));
                                        return;
                                    }
                                    let desc_val = edit_desc().trim().to_string();
                                    let color_opt = if color_val.len() == 6 { Some(color_val) } else { None };
                                    let desc_opt = if desc_val.is_empty() { None } else { Some(desc_val) };
                                    edit_error.set(None);
                                    mutation_pending.set(true);
                                    let check_only = creation_needs_check();
                                    if !check_only { creation_known_ids.set(existing_ids.clone()); }
                                    let known_ids = creation_known_ids();

                                    spawn(async move {
                                        if check_only {
                                            match api::check_penalty_creation(&u, true, &known_ids, &name_trim, color_opt.as_deref().unwrap_or("808080"), desc_opt.as_deref().unwrap_or("")).await {
                                                Ok(true) => {
                                                    creation_needs_check.set(false);
                                                    add_new_penalty.set(false);
                                                    show_color_picker_for.set(None);
                                                    on_refresh.call(());
                                                }
                                                Ok(false) => {
                                                    creation_needs_check.set(false);
                                                    edit_error.set(Some("No matching saved penalty was found. Review the draft, then choose Save to try again.".into()));
                                                }
                                                Err(error) => edit_error.set(Some(format!("Could not check the saved penalty types. Your draft is unchanged. {error}"))),
                                            }
                                        } else {
                                            match api::create_league_penalty_type(&u, &name_trim, color_opt.as_deref(), desc_opt.as_deref()).await {
                                                Ok(result) if result.success => {
                                                    add_new_penalty.set(false);
                                                    show_color_picker_for.set(None);
                                                    on_refresh.call(());
                                                }
                                                result => {
                                                    creation_needs_check.set(true);
                                                    let reason = result.err().unwrap_or_else(|| "The server did not confirm the save.".into());
                                                    edit_error.set(Some(format!("Save was not confirmed. Choose Check save status before trying again, to avoid creating a duplicate. {reason}")));
                                                }
                                            }
                                        }
                                        mutation_pending.set(false);
                                    });
                                },
                                if mutation_pending() { "Saving…" } else if creation_needs_check() { "Check save status" } else { "Save" }
                            }
                            button {
                                class: "btn btn-sm btn-secondary",
                                r#type: "button",
                                disabled: settings_saving() || mutation_pending() || creation_needs_check(),
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
    };

    rsx! {
        section { class: "edit-section penalty-types-section mb-3",
            div { class: "edit-section-heading penalty-types-heading",
                h3 { class: "h5 mb-0",
                    button { id: "penalty-types-toggle", class: "penalty-types-toggle", r#type: "button",
                        disabled: settings_saving() || mutation_pending(),
                        aria_expanded: "{penalties_open()}", aria_controls: "penalty-types-content",
                        onclick: move |_| penalties_open.set(!penalties_open()),
                        i { class: if penalties_open() { "fas fa-chevron-down" } else { "fas fa-chevron-right" }, aria_hidden: "true" }
                        "Penalty types ({penalty_types.len()})"
                    }
                }
                button { id: "penalty-types-add", class: "btn btn-sm btn-outline-secondary penalty-types-add", r#type: "button",
                    disabled: settings_saving() || mutation_pending() || add_new_penalty() || editing_pt_id().is_some(),
                    aria_label: "Add penalty type",
                    onclick: move |_| {
                        if settings_saving() || mutation_pending() || add_new_penalty() || editing_pt_id().is_some() { return; }
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
                if let Some(error) = edit_error() { p { id: "penalty-save-error", tabindex: "-1", class: "text-danger", role: "alert", "{error}" } }
                div { class: "table-responsive",
                    table { class: "table table-sm",
                        thead { tr { th { class: "table-nowrap", "Name" } th { class: "table-nowrap", "Color" } th { class: "table-nowrap", "Description" } th { class: "table-nowrap", style: "width: 1%;", "Actions" } } }
                        tbody {
                            {rows}
                        }
                    }
                }
            }
        }
    }
}
