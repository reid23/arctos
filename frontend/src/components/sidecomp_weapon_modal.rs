//! Pompfen / weapon role helpers and registration modal for side competitions.

use crate::api;
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
        id: "FLOURENTINE",
        label: "Flourentine",
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
pub fn PompfenIcon(weapon: String, #[props(default = "1.5em".to_string())] size: String) -> Element {
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
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);

    let submit_label = match mode {
        SideCompRegistrationMode::Register => "Submit",
        SideCompRegistrationMode::Edit => "Save",
    };

    rsx! {
        div {
            class: "modal show d-block",
            style: "background: rgba(0,0,0,0.5);",
            tabindex: "-1",
            role: "dialog",
            onclick: move |_| on_close.call(()),
            div {
                class: "modal-dialog modal-dialog-centered",
                onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                div { class: "modal-content",
                    div { class: "modal-header",
                        h5 { class: "modal-title", "{title}" }
                        button {
                            r#type: "button",
                            class: "btn-close",
                            aria_label: "Close",
                            onclick: move |_| on_close.call(()),
                        }
                    }
                    div { class: "modal-body",
                        p { "What will you be playing?" }
                        if options.is_empty() {
                            p { class: "text-muted", "No weapons are enabled for this side competition." }
                        } else {
                            div { class: "d-flex flex-column gap-2",
                                for opt in options.iter() {
                                    {
                                        let id = opt.id.to_string();
                                        let id_label = opt.id.to_string();
                                        rsx! {
                                            div { class: "form-check d-flex align-items-center gap-2",
                                                input {
                                                    class: "form-check-input",
                                                    r#type: "radio",
                                                    name: "sidecomp-weapon",
                                                    id: "reg-weapon-{opt.id}",
                                                    checked: selected() == id,
                                                    onchange: move |_| selected.set(id.clone()),
                                                }
                                                label {
                                                    class: "form-check-label d-flex align-items-center gap-2",
                                                    r#for: "reg-weapon-{opt.id}",
                                                    PompfenIcon { weapon: id_label, size: "1.5em".to_string() }
                                                    "{opt.label}"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        if let Some(err) = error() {
                            div { class: "alert alert-danger mt-3 mb-0", "{err}" }
                        }
                    }
                    div { class: "modal-footer d-flex justify-content-between",
                        div {
                            if show_deregister {
                                button {
                                    r#type: "button",
                                    class: "btn btn-outline-danger",
                                    disabled: busy(),
                                    onclick: move |_| {
                                        busy.set(true);
                                        error.set(None);
                                        on_deregister.call(());
                                    },
                                    "Deregister"
                                }
                            }
                        }
                        div { class: "d-flex gap-2",
                            button {
                                r#type: "button",
                                class: "btn btn-secondary",
                                disabled: busy(),
                                onclick: move |_| on_close.call(()),
                                "Cancel"
                            }
                            button {
                                r#type: "button",
                                class: "btn btn-primary",
                                disabled: busy() || selected().is_empty(),
                                onclick: move |_| {
                                    let w = selected();
                                    if w.is_empty() {
                                        error.set(Some("Please select a weapon.".to_string()));
                                        return;
                                    }
                                    busy.set(true);
                                    error.set(None);
                                    on_submit.call(w);
                                },
                                "{submit_label}"
                            }
                        }
                    }
                }
            }
        }
    }
}
