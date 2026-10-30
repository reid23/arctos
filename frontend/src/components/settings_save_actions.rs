use dioxus::prelude::*;

/// A single save destination for long settings forms, reachable from the heading.
#[component]
pub fn SettingsSaveJump() -> Element {
    rsx! {
        a { href: "#settings-save-actions", class: "tool-page-back mb-0",
            onclick: move |event| {
                event.prevent_default();
                focus_save_actions();
            },
            "Jump to save"
            i { class: "fas fa-arrow-down", aria_hidden: "true" }
        }
    }
}

fn focus_save_actions() {
    #[cfg(target_arch = "wasm32")]
    if let Some(element) = web_sys::window()
        .and_then(|window| window.document())
        .and_then(|document| document.get_element_by_id("settings-save-actions"))
    {
        element.scroll_into_view();
        crate::appearance::focus_control("settings-save-actions");
    }
}

#[component]
pub fn SettingsSaveActions(
    saving: bool,
    #[props(default)] blocked_reason: Option<String>,
    #[props(default)] error: Option<String>,
) -> Element {
    use_effect(use_reactive(&error, move |message| {
        if message.is_some() {
            focus_save_actions();
        }
    }));
    rsx! {
        div { id: "settings-save-actions", class: "settings-save-actions", tabindex: "-1",
            role: "group", aria_label: "Save settings",
            aria_describedby: if error.is_some() { Some("settings-save-error") } else { None },
            span { class: "visually-hidden", role: "status", if saving { "Saving settings…" } }
            if let Some(ref message) = error {
                p { id: "settings-save-error", class: "text-danger mb-2", role: "alert", "{message}" }
            }
            if let Some(reason) = &blocked_reason {
                p { id: "settings-save-reason", class: "form-text mb-2", role: "status", "{reason}" }
            }
            div { class: "form-actions",
                button { r#type: "submit", class: "btn btn-primary", disabled: saving || blocked_reason.is_some(),
                    aria_describedby: if blocked_reason.is_some() { Some("settings-save-reason") } else { None },
                    if saving { "Saving…" } else { "Save settings" }
                }
            }
        }
    }
}
