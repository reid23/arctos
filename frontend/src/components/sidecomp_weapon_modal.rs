//! Pompfen / weapon role helpers and registration modal for side competitions.

use crate::api;
use crate::components::Modal;
use dioxus::prelude::*;

/// All pompfen options in display order.
pub const ALL_POMPFEN: &[PompfenOption] = &[
    PompfenOption {
        id: "CHAIN",
        label: "Chain",
        icon: "pompfen_symbols/chain.png",
    },
    PompfenOption {
        id: "LONG",
        label: "Long",
        icon: "pompfen_symbols/long.png",
    },
    PompfenOption {
        id: "QTIP",
        label: "Q-Tip",
        icon: "pompfen_symbols/qtip.png",
    },
    PompfenOption {
        id: "STAFF",
        label: "Staff",
        icon: "pompfen_symbols/staff.png",
    },
    PompfenOption {
        id: "BOARD",
        label: "Board",
        icon: "pompfen_symbols/board.png",
    },
    PompfenOption {
        id: "FLORENTINE",
        label: "Florentine",
        icon: "pompfen_symbols/flo.png",
    },
    PompfenOption {
        id: "SKULL",
        label: "Skull",
        icon: "pompfen_symbols/skull.png",
    },
    PompfenOption {
        id: "UNARMED",
        label: "Unarmed",
        icon: "pompfen_symbols/unarmed.png",
    },
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PompfenOption {
    pub id: &'static str,
    pub label: &'static str,
    pub icon: &'static str,
}

pub fn pompfen_by_id(id: &str) -> Option<&'static PompfenOption> {
    ALL_POMPFEN.iter().find(|p| p.id == id)
}

pub fn pompfen_icon_src(id: &str) -> Option<String> {
    pompfen_by_id(id).map(|p| format!("{}/static/{}", api::base_url(), p.icon))
}

pub fn default_allowed_weapons() -> Vec<String> {
    ALL_POMPFEN.iter().map(|p| p.id.to_string()).collect()
}

#[component]
pub fn PompfenIcon(
    weapon: String,
    #[props(default = "1.5em".to_string())] size: String,
) -> Element {
    let src = pompfen_icon_src(&weapon);
    match src {
        Some(src) => rsx! {
            img {
                src: "{src}",
                alt: "{weapon}",
                title: "{weapon}",
                style: "width: {size}; height: {size}; object-fit: contain; vertical-align: middle;",
            }
        },
        None => rsx! {},
    }
}

/// Checkbox list of all pompfen options for TO create/edit settings.
#[component]
pub fn AllowedWeaponsCheckboxes(
    selected: Signal<Vec<String>>,
    #[props(default)] disabled_warning_count: Option<i32>,
) -> Element {
    rsx! {
        div { class: "mb-3",
            label { class: "form-label", "Allowed weapons" }
            div { class: "d-flex flex-column gap-1",
                for opt in ALL_POMPFEN.iter() {
                    {
                        let id = opt.id.to_string();
                        let id_for_check = id.clone();
                        let checked = selected().iter().any(|s| s == &id);
                        rsx! {
                            div { class: "form-check d-flex align-items-center gap-2",
                                input {
                                    class: "form-check-input",
                                    r#type: "checkbox",
                                    id: "weapon-{opt.id}",
                                    checked: checked,
                                    onchange: move |evt| {
                                        let mut cur = selected();
                                        if evt.checked() {
                                            if !cur.iter().any(|s| s == &id) {
                                                cur.push(id.clone());
                                            }
                                        } else {
                                            cur.retain(|s| s != &id);
                                        }
                                        // Keep stable order matching ALL_POMPFEN.
                                        cur.sort_by_key(|s| {
                                            ALL_POMPFEN.iter().position(|p| p.id == s).unwrap_or(usize::MAX)
                                        });
                                        selected.set(cur);
                                    },
                                }
                                label {
                                    class: "form-check-label d-flex align-items-center gap-2",
                                    r#for: "weapon-{opt.id}",
                                    PompfenIcon { weapon: id_for_check, size: "1.25em".to_string() }
                                    "{opt.label}"
                                }
                            }
                        }
                    }
                }
            }
            div { class: "form-text", "Players may only register with weapons you enable here." }
            if let Some(n) = disabled_warning_count {
                if n > 0 {
                    div { class: "text-danger small mt-1",
                        "there are currently {n} players registered under a disabled weapon. saving these settings will deregister them from this side comp"
                    }
                }
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SideCompRegistrationMode {
    Register,
    Edit,
}

/// Modal for selecting (or changing) a pompfen when registering for a side comp.
#[component]
pub fn SideCompWeaponModal(
    title: String,
    mode: SideCompRegistrationMode,
    allowed_weapons: Vec<String>,
    #[props(default)] initial_weapon: Option<String>,
    #[props(default)] show_deregister: bool,
    on_close: EventHandler<()>,
    on_submit: EventHandler<String>,
    #[props(default)] on_deregister: EventHandler<()>,
    #[props(default)] busy: bool,
    #[props(default)] error: Option<String>,
    #[props(default)] needs_check: bool,
    #[props(default)] on_check: EventHandler<()>,
) -> Element {
    let options: Vec<&PompfenOption> = ALL_POMPFEN
        .iter()
        .filter(|p| allowed_weapons.iter().any(|a| a == p.id))
        .collect();
    let initial = initial_weapon
        .clone()
        .filter(|w| allowed_weapons.iter().any(|a| a == w))
        .or_else(|| options.first().map(|o| o.id.to_string()))
        .unwrap_or_default();
    let mut selected = use_signal(|| initial);
    let mut validation_error = use_signal(|| None::<String>);

    let submit_label = match mode {
        SideCompRegistrationMode::Register => "Register",
        SideCompRegistrationMode::Edit => "Save",
    };

    rsx! {
        Modal {
            id: "sidecomp-registration-dialog", labelled_by: "sidecomp-registration-title",
            initial_focus_id: "sidecomp-registration-title", dismissible: !busy,
            on_close: move |_| { if !busy { on_close.call(()); } },
            div { class: "modal-header",
                h2 { class: "modal-title h5", id: "sidecomp-registration-title", tabindex: "-1", "{title}" }
                button { r#type: "button", class: "btn-close", aria_label: "Close", disabled: busy,
                    onclick: move |_| { if !busy { on_close.call(()); } } }
            }
            div { class: "modal-body",
                p { id: "sidecomp-weapon-label", "What will you be playing?" }
                if options.is_empty() { p { class: "text-muted", "No weapons are enabled for this side competition." } }
                else {
                    fieldset { disabled: busy || needs_check, aria_labelledby: "sidecomp-weapon-label", class: "border-0 p-0 m-0",
                        div { class: "d-flex flex-column gap-2",
                            for opt in options.iter() {
                                {
                                    let id = opt.id.to_string();
                                    rsx! {
                                        div { class: "form-check d-flex align-items-center gap-2",
                                            input { class: "form-check-input", r#type: "radio", name: "sidecomp-weapon", id: "reg-weapon-{opt.id}",
                                                checked: selected() == id, onchange: move |_| selected.set(id.clone()) }
                                            label { class: "form-check-label d-flex align-items-center gap-2", r#for: "reg-weapon-{opt.id}",
                                                PompfenIcon { weapon: opt.id.to_string(), size: "1.5em".to_string() } "{opt.label}" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if let Some(message) = error.or_else(|| validation_error()) { p { class: "text-danger mt-3 mb-0", role: "alert", "{message}" } }
                if busy { p { role: "status", class: "small text-muted", "Saving and checking registration…" } }
                if needs_check { button { r#type: "button", class: "btn btn-outline-secondary mt-2", disabled: busy, onclick: move |_| on_check.call(()), "Check registration status" } }
            }
            div { class: "modal-footer",
                if show_deregister {
                    button { r#type: "button", class: "btn btn-outline-danger me-auto", disabled: busy || needs_check,
                        onclick: move |_| { if !busy && !needs_check { on_deregister.call(()); } }, "Deregister" }
                }
                button { r#type: "button", class: "btn btn-outline-secondary", disabled: busy,
                    onclick: move |_| { if !busy { on_close.call(()); } }, "Cancel" }
                button { r#type: "button", class: "btn btn-primary", disabled: busy || needs_check || selected().is_empty(),
                    onclick: move |_| {
                        if busy || needs_check { return; }
                        let weapon = selected();
                        if weapon.is_empty() { validation_error.set(Some("Please select a weapon.".into())); return; }
                        validation_error.set(None);
                        on_submit.call(weapon);
                    },
                    if busy { "Saving…" } else { "{submit_label}" }
                }
            }
        }
    }
}
