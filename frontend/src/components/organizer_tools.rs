use crate::appearance::focus_control;
use dioxus::prelude::*;

#[component]
pub fn OrganizerTools(
    children: Element,
    #[props(default = "organizer-tools".to_string())] id_prefix: String,
    #[props(default = "Organizer tools".to_string())] label: String,
) -> Element {
    let mut open = use_signal(|| false);
    let route = use_route::<crate::Route>();
    use_effect(use_reactive(&route, move |_| open.set(false)));
    let trigger_id = format!("{id_prefix}-trigger");
    let options_id = format!("{id_prefix}-options");
    let keyboard_trigger = trigger_id.clone();
    let dismiss_trigger = trigger_id.clone();
    rsx! {
        style { {include_str!("organizer_tools.css")} }
        div {
            class: if open() { "organizer-tools is-open" } else { "organizer-tools" },
            onkeydown: move |event| {
                if open() && event.key() == Key::Escape {
                    event.prevent_default();
                    event.stop_propagation();
                    open.set(false);
                    focus_control(&keyboard_trigger);
                }
            },
            button {
                id: trigger_id, class: "organizer-tools-trigger", r#type: "button",
                aria_expanded: "{open()}", aria_controls: options_id.clone(),
                onclick: move |_| open.set(!open()),
                "{label}"
                i { class: "fas fa-chevron-down", aria_hidden: "true" }
            }
            if open() {
                button {
                    class: "organizer-tools-dismiss", r#type: "button", tabindex: "-1",
                    aria_label: "Dismiss organizer tools",
                    onclick: move |_| { open.set(false); focus_control(&dismiss_trigger); },
                }
                div {
                    id: options_id.clone(), class: "organizer-tools-dropdown",
                    onclick: move |_| open.set(false),
                    {children}
                }
            }
        }
    }
}
