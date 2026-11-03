use crate::appearance::{focus_control, Appearance, ColorMode, Palette};
use crate::pages::layout::{page_banner_key, set_page_banner_hidden, use_page_banner_preferences};
use dioxus::prelude::*;

/// The Appearance form shares the More menu's anchor and dismiss control.
#[component]
pub fn AppearanceMenu(
    mut open: Signal<bool>,
    mut utility_open: Signal<bool>,
    appearance: Appearance,
) -> Element {
    let preferences = appearance.preferences();
    let banners = use_page_banner_preferences();
    let page_key = page_banner_key(&use_route::<crate::Route>());
    rsx! {
        style { {include_str!("appearance_menu.css")} }
        if open() {
            div {
                class: "appearance-panel", id: "appearance-options", role: "dialog", tabindex: "-1",
                aria_labelledby: "appearance-heading",
                onmounted: move |_| focus_control(&format!("appearance-mode-{}", preferences.mode.id())),
                div { class: "appearance-panel-header",
                    button {
                        class: "appearance-back", r#type: "button", aria_label: "Back to menu",
                        onclick: move |_| {
                            open.set(false);
                            utility_open.set(true);
                            focus_control("utility-trigger");
                        },
                        i { class: "fa-solid fa-chevron-left", aria_hidden: "true" }
                        span { "Menu" }
                    }
                    h2 { id: "appearance-heading", "Appearance" }
                    button {
                        class: "app-icon-button appearance-close", r#type: "button",
                        aria_label: "Close appearance settings",
                        onclick: move |_| {
                            open.set(false);
                            utility_open.set(false);
                            focus_control("utility-trigger");
                        },
                        i { class: "fa-solid fa-xmark", aria_hidden: "true" }
                    }
                }
                div { class: "appearance-panel-body",
                    fieldset { class: "appearance-section",
                        legend { "Display mode" }
                        div { class: "appearance-mode-options",
                            for mode in ColorMode::ALL {
                                label { class: if preferences.mode == mode { "appearance-mode-option is-selected" } else { "appearance-mode-option" },
                                    input {
                                        id: "appearance-mode-{mode.id()}", r#type: "radio",
                                        name: "arctos-color-mode", value: mode.id(),
                                        aria_label: "{mode.name()} mode", checked: preferences.mode == mode,
                                        onchange: move |_| appearance.set_mode(mode),
                                    }
                                    span { "{mode.name()}" }
                                }
                            }
                        }
                    }
                    fieldset { class: "appearance-section",
                        legend { "Theme" }
                        div { class: "appearance-theme-options",
                            for palette in Palette::ALL {
                                ThemeOption { palette, appearance }
                            }
                        }
                    }
                    if let Some(key) = page_key {
                        div { class: "appearance-section appearance-page-setting",
                            label { r#for: "page-introduction-setting",
                                input {
                                    id: "page-introduction-setting", r#type: "checkbox",
                                    checked: !banners.read().contains(&key), aria_controls: "page-banner",
                                    onchange: move |event| set_page_banner_hidden(banners, &key, !event.checked()),
                                }
                                span { "Show page introduction" }
                            }
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn ThemeOption(palette: Palette, appearance: Appearance) -> Element {
    let selected = appearance.preferences().palette == palette;
    rsx! {
        label { class: if selected { "appearance-theme-option is-selected" } else { "appearance-theme-option" },
            input {
                id: "appearance-palette-{palette.id()}", r#type: "radio", name: "arctos-palette",
                value: palette.id(), aria_label: "{palette.name()} theme", checked: selected,
                onchange: move |_| appearance.set_palette(palette),
            }
            span { "{palette.name()}" }
        }
    }
}
