use crate::api;
use dioxus::prelude::*;

#[component]
pub fn Markdown(page: String) -> Element {
    let mut data = use_resource(use_reactive(&page, move |page| async move {
        let result = api::markdown_page(&page).await.map_err(|e| e.to_string());
        (page, result)
    }));
    let val = data.value();
    let current = val
        .read()
        .as_ref()
        .filter(|(loaded_page, _)| loaded_page == &page)
        .map(|(_, result)| result.clone());

    let url = dioxus::router::router().full_route_string();
    let initial_fragment = use_hook(|| {
        thread_local! { static CONSUMED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) }; }
        if CONSUMED.replace(true) {
            None
        } else {
            crate::initial_fragment(url.split(['?', '#']).next().unwrap_or(&url))
        }
    });
    let mut initial_fragment = use_signal(|| initial_fragment);
    let navigator = use_navigator();
    #[cfg(target_arch = "wasm32")]
    let interaction = use_hook(browser::InteractionGuard::new);
    let loaded = current.as_ref().is_some_and(Result::is_ok);
    use_effect(use_reactive(&(url, loaded), move |(url, loaded)| {
        if let Some(fragment) = initial_fragment.write().take() {
            navigator.replace(format!(
                "{}{fragment}",
                url.split('#').next().unwrap_or(&url)
            ));
            return;
        }
        if loaded {
            #[cfg(target_arch = "wasm32")]
            browser::reveal_fragment(url, interaction.clone());
        }
    }));

    rsx! {
        if let Some(Ok(d)) = current.as_ref() {
            div { class: "row",
                div { class: "col-lg-6 mx-auto",
                    div { dangerous_inner_html: "{d.html}" }
                }
            }
        } else if let Some(Err(error)) = current.as_ref() {
            div { class: "arctos-empty-state", role: "alert",
                h1 { "This page could not be loaded" }
                p { "{error}" }
                button { r#type: "button", class: "btn btn-primary", onclick: move |_| data.restart(), "Try again" }
            }
        } else {
            p { role: "status", "Loading…" }
        }
    }
}

#[cfg(target_arch = "wasm32")]
mod browser {
    use std::{cell::Cell, rc::Rc};
    use wasm_bindgen::{closure::Closure, JsCast};

    pub(super) struct InteractionGuard {
        window: Option<web_sys::Window>,
        cancelled: Rc<Cell<bool>>,
        listeners: Vec<(&'static str, Closure<dyn FnMut(web_sys::Event)>)>,
    }
    impl InteractionGuard {
        pub(super) fn new() -> Rc<Self> {
            let window = web_sys::window();
            let cancelled = Rc::new(Cell::new(false));
            let mut listeners = Vec::new();
            if let Some(window) = &window {
                for name in ["pointerdown", "wheel", "keydown"] {
                    let flag = cancelled.clone();
                    let callback =
                        Closure::<dyn FnMut(web_sys::Event)>::new(move |_| flag.set(true));
                    let _ = window
                        .add_event_listener_with_callback(name, callback.as_ref().unchecked_ref());
                    listeners.push((name, callback));
                }
            }
            Rc::new(Self {
                window,
                cancelled,
                listeners,
            })
        }
    }
    impl Drop for InteractionGuard {
        fn drop(&mut self) {
            self.cancelled.set(true);
            if let Some(window) = &self.window {
                for (name, callback) in &self.listeners {
                    let _ = window.remove_event_listener_with_callback(
                        name,
                        callback.as_ref().unchecked_ref(),
                    );
                }
            }
        }
    }

    pub(super) fn reveal_fragment(url: String, interaction: Rc<InteractionGuard>) {
        let Some((_, fragment)) = url.split_once('#') else {
            return;
        };
        let id = urlencoding::decode(fragment)
            .map(|id| id.into_owned())
            .unwrap_or_else(|_| fragment.to_string());
        if id.is_empty() || interaction.cancelled.get() {
            return;
        }
        dioxus::prelude::spawn(async move {
            let Some(window) = web_sys::window() else {
                return;
            };
            let Some(document) = window.document() else {
                return;
            };
            if let Ok(ready) = document.fonts().ready() {
                let _ = wasm_bindgen_futures::JsFuture::from(ready).await;
            }
            let callback = Closure::once_into_js(move || {
                let location = document.location();
                let current = location.map(|location| {
                    format!(
                        "{}{}{}",
                        location.pathname().unwrap_or_default(),
                        location.search().unwrap_or_default(),
                        location.hash().unwrap_or_default()
                    )
                });
                if !interaction.cancelled.get() && current.as_deref() == Some(url.as_str()) {
                    if let Some(target) = document.get_element_by_id(&id) {
                        target.scroll_into_view();
                    }
                }
            });
            let _ = window.request_animation_frame(callback.unchecked_ref());
        });
    }
}
