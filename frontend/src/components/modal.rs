//! Native modal dialogs keep keyboard focus and inertness in the browser's top layer.
//! Mount to open; unmount to close. Callers own pending-write and draft state.

use dioxus::prelude::*;

const MODAL_CSS: &str = include_str!("modal.css");

#[derive(Clone, Copy, Default, PartialEq)]
pub enum ModalSize {
    #[default]
    Standard,
    Large,
}

#[derive(Clone, Copy, Default, PartialEq)]
pub enum ModalKind {
    #[default]
    Dialog,
    AlertDialog,
}

#[component]
pub fn Modal(
    id: String,
    labelled_by: String,
    #[props(default)] described_by: Option<String>,
    #[props(default)] initial_focus_id: Option<String>,
    #[props(default)] return_focus_id: Option<String>,
    #[props(default)] size: ModalSize,
    #[props(default)] kind: ModalKind,
    #[props(default = true)] dismissible: bool,
    #[props(default = true)] dismiss_on_backdrop: bool,
    on_close: EventHandler<()>,
    children: Element,
) -> Element {
    let mut backdrop_pressed = use_signal(|| false);
    #[cfg(target_arch = "wasm32")]
    let opened =
        use_hook(|| std::rc::Rc::new(std::cell::RefCell::new(None::<browser::OpenDialog>)));
    #[cfg(target_arch = "wasm32")]
    {
        let opened = opened.clone();
        use_drop(move || {
            // Explicitly drop before other event closures release their Rc references.
            opened.borrow_mut().take();
        });
    }
    let mount_id = id.clone();
    let cancel_id = id.clone();
    let pointer_id = id.clone();
    let click_id = id.clone();
    let initial_focus_id = initial_focus_id.unwrap_or_else(|| labelled_by.clone());

    rsx! {
        style { {MODAL_CSS} }
        dialog {
            id,
            class: if size == ModalSize::Large { "arctos-modal arctos-modal-large" } else { "arctos-modal" },
            role: if kind == ModalKind::AlertDialog { "alertdialog" } else { "dialog" },
            aria_modal: "true",
            aria_labelledby: labelled_by,
            aria_describedby: described_by,
            onmounted: move |_| {
                #[cfg(target_arch = "wasm32")]
                if opened.borrow().is_none() {
                    *opened.borrow_mut() = browser::open(&mount_id, &initial_focus_id, return_focus_id.clone());
                }
                #[cfg(not(target_arch = "wasm32"))]
                let _ = (&mount_id, &initial_focus_id, &return_focus_id);
            },
            oncancel: move |event| {
                // Native Escape is cancellable; only the top dialog may dismiss.
                event.prevent_default();
                event.stop_propagation();
                if dismissible && is_top_dialog(&cancel_id) { on_close.call(()); }
            },
            onkeydown: move |event| {
                // Do not let Escape reach page shortcuts or the underlying dialog.
                if event.key() == Key::Escape { event.stop_propagation(); }
            },
            onpointerdown: move |event| {
                let point = event.client_coordinates();
                backdrop_pressed.set(outside_dialog(&pointer_id, point.x, point.y));
            },
            onclick: move |event| {
                event.stop_propagation();
                let point = event.client_coordinates();
                let outside = backdrop_pressed() && outside_dialog(&click_id, point.x, point.y);
                backdrop_pressed.set(false);
                if outside && dismissible && dismiss_on_backdrop && is_top_dialog(&click_id) { on_close.call(()); }
            },
            div { class: "modal-content", {children} }
        }
    }
}

fn is_top_dialog(id: &str) -> bool {
    #[cfg(target_arch = "wasm32")]
    return browser::is_top(id);
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = id;
        true
    }
}

fn outside_dialog(id: &str, x: f64, y: f64) -> bool {
    #[cfg(target_arch = "wasm32")]
    {
        let Some(element) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id(id))
        else {
            return false;
        };
        let rect = element.get_bounding_client_rect();
        x < rect.left() || x > rect.right() || y < rect.top() || y > rect.bottom()
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (id, x, y);
        false
    }
}

#[cfg(target_arch = "wasm32")]
mod browser {
    use std::cell::RefCell;
    use wasm_bindgen::{closure::Closure, JsCast};
    use web_sys::{HtmlDialogElement, HtmlElement};

    thread_local! {
        static OPEN_DIALOGS: RefCell<Vec<String>> = const { RefCell::new(Vec::new()) };
    }

    pub(super) fn is_top(id: &str) -> bool {
        OPEN_DIALOGS.with(|dialogs| dialogs.borrow().last().is_some_and(|top| top == id))
    }

    pub(super) struct OpenDialog {
        element: HtmlDialogElement,
        opener: Option<HtmlElement>,
        return_focus_id: Option<String>,
        location: String,
    }

    pub(super) fn open(
        id: &str,
        initial_focus_id: &str,
        return_focus_id: Option<String>,
    ) -> Option<OpenDialog> {
        let window = web_sys::window()?;
        let document = window.document()?;
        let element = document
            .get_element_by_id(id)?
            .dyn_into::<HtmlDialogElement>()
            .ok()?;
        let opener = document
            .active_element()
            .and_then(|e| e.dyn_into::<HtmlElement>().ok());
        let initial = document
            .get_element_by_id(initial_focus_id)
            .filter(|e| element.contains(Some(e)));
        // Set autofocus before showModal so the browser never briefly opens a phone
        // keyboard by focusing the first text field. Callers give headings tabindex=-1.
        let had_autofocus = initial
            .as_ref()
            .is_some_and(|e| e.has_attribute("autofocus"));
        if let Some(initial) = &initial {
            let _ = initial.set_attribute("autofocus", "");
        }
        let result = element.show_modal();
        if !had_autofocus {
            if let Some(initial) = &initial {
                let _ = initial.remove_attribute("autofocus");
            }
        }
        if result.is_err() {
            return None;
        }
        OPEN_DIALOGS.with(|dialogs| dialogs.borrow_mut().push(id.to_string()));
        if let Some(initial) = initial.and_then(|e| e.dyn_into::<HtmlElement>().ok()) {
            focus(&initial);
        }
        Some(OpenDialog {
            element,
            opener,
            return_focus_id,
            location: window.location().href().unwrap_or_default(),
        })
    }

    fn focus(element: &HtmlElement) {
        let options = web_sys::FocusOptions::new();
        options.set_prevent_scroll(true);
        let _ = element.focus_with_options(&options);
    }

    impl Drop for OpenDialog {
        fn drop(&mut self) {
            let was_top = is_top(&self.element.id());
            OPEN_DIALOGS.with(|dialogs| dialogs.borrow_mut().retain(|id| id != &self.element.id()));
            self.element.close();
            if !was_top {
                return;
            }
            let Some(window) = web_sys::window() else {
                return;
            };
            let opener = self.opener.clone();
            let return_focus_id = self.return_focus_id.clone();
            let location = self.location.clone();
            // Wait until the caller's render has re-enabled the trigger or replaced
            // it after a successful save. Never move focus onto a different route.
            let callback = Closure::once_into_js(move || {
                let Some(window) = web_sys::window() else {
                    return;
                };
                if window.location().href().ok().as_deref() != Some(location.as_str()) {
                    return;
                }
                let Some(document) = window.document() else {
                    return;
                };
                let top = OPEN_DIALOGS.with(|dialogs| dialogs.borrow().last().cloned());
                let candidate = return_focus_id
                    .and_then(|id| document.get_element_by_id(&id))
                    .and_then(|e| e.dyn_into::<HtmlElement>().ok())
                    .or(opener);
                if let Some(candidate) =
                    candidate.filter(|e| e.is_connected() && !e.has_attribute("disabled"))
                {
                    let inside_top = top.as_ref().map_or(true, |id| {
                        document
                            .get_element_by_id(id)
                            .is_some_and(|dialog| dialog.contains(Some(&candidate)))
                    });
                    if inside_top {
                        focus(&candidate);
                        return;
                    }
                }
                if top.is_none() {
                    if let Some(content) = document
                        .get_element_by_id("page-content")
                        .and_then(|e| e.dyn_into::<HtmlElement>().ok())
                    {
                        focus(&content);
                    }
                }
            });
            let _ = window.request_animation_frame(callback.unchecked_ref());
        }
    }
}
