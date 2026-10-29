//! Keep the initialized application alive across public and interactive routes.
//!
//! Flask remains the first document response. A public document may start Wasm
//! without mounting its Router; the visible HTML remains authoritative until a
//! visitor navigates. The cancelable navigation bridge then transfers ownership
//! to the existing History instance, preserving raw queries and browser history.

use crate::Route;
use dioxus::history::{history, provide_history_context, History};
use dioxus::prelude::*;
use std::{
    cell::{Cell, RefCell},
    rc::Rc,
    str::FromStr,
    sync::Arc,
};

fn app_route(target: &str) -> bool {
    if !target.starts_with('/')
        || target.starts_with("//")
        || target.contains('\\')
        || target.chars().any(char::is_control)
    {
        return false;
    }
    !matches!(
        Route::from_str(target.split('#').next().unwrap_or(target)),
        Err(_)
            | Ok(Route::NotFound { .. }
                | Route::Scoreboard { .. }
                | Route::ScoreboardRender { .. }
                | Route::SideCompEnterResults { .. })
    )
}

fn bare_route(target: &str) -> bool {
    matches!(
        Route::from_str(target.split('#').next().unwrap_or(target)),
        Ok(Route::Scoreboard { .. }
            | Route::ScoreboardRender { .. }
            | Route::SideCompEnterResults { .. })
    )
}

pub(crate) fn without_prefix<'a>(target: &'a str, prefix: &str) -> Option<&'a str> {
    let prefix = prefix.trim_end_matches('/');
    if prefix.is_empty() {
        return Some(target);
    }
    let route = target.strip_prefix(prefix)?;
    if route.is_empty() {
        Some("/")
    } else if route.starts_with('/') {
        Some(route)
    } else {
        None
    }
}

pub(crate) fn document_prefix() -> String {
    #[cfg(target_arch = "wasm32")]
    return web_sys::window()
        .and_then(|window| window.document())
        .and_then(|document| document.document_element())
        .and_then(|root| root.get_attribute("data-public-prefix"))
        .unwrap_or_default()
        .trim_end_matches('/')
        .to_string();
    #[cfg(not(target_arch = "wasm32"))]
    String::new()
}

fn background_document() -> bool {
    #[cfg(target_arch = "wasm32")]
    return web_sys::window()
        .and_then(|window| window.document())
        .and_then(|document| document.document_element())
        .and_then(|root| root.get_attribute("data-public-background"))
        .as_deref()
        == Some("true");
    #[cfg(not(target_arch = "wasm32"))]
    false
}

/// Return from authentication without discarding the running application.
/// Callers retain native navigation for external services and bare documents.
pub(crate) fn navigate_within_application(target: &str) -> bool {
    let prefix = history().current_prefix().unwrap_or_else(document_prefix);
    let Some(route) = without_prefix(target, &prefix).filter(|route| app_route(route)) else {
        return false;
    };
    dioxus::router::navigator().push(route.to_string());
    true
}

struct PersistentHistory {
    inner: Rc<dyn History>,
    normalized: Cell<bool>,
    update: RefCell<Option<Arc<dyn Fn() + Send + Sync>>>,
}

impl PersistentHistory {
    fn new(inner: Rc<dyn History>) -> Self {
        Self {
            inner,
            normalized: Cell::new(false),
            update: RefCell::new(None),
        }
    }

    fn notify(&self) {
        if let Some(update) = self.update.borrow().clone() {
            update();
        }
    }

    fn navigate_bare(&self, target: &str, replace: bool) -> bool {
        if !bare_route(target) {
            return false;
        }
        #[cfg(target_arch = "wasm32")]
        if let Some(window) = web_sys::window() {
            let target = format!(
                "{}{target}",
                self.current_prefix().unwrap_or_else(document_prefix)
            );
            return if replace {
                window.location().replace(&target)
            } else {
                window.location().assign(&target)
            }
            .is_ok();
        }
        let _ = replace;
        false
    }
}

impl History for PersistentHistory {
    fn current_route(&self) -> String {
        self.inner.current_route()
    }
    fn current_prefix(&self) -> Option<String> {
        self.inner.current_prefix()
    }
    fn can_go_back(&self) -> bool {
        self.inner.can_go_back()
    }
    fn can_go_forward(&self) -> bool {
        self.inner.can_go_forward()
    }
    fn go_back(&self) {
        self.inner.go_back();
    }
    fn go_forward(&self) {
        self.inner.go_forward();
    }
    fn push(&self, target: String) {
        if !self.navigate_bare(&target, false) {
            self.inner.push(target);
        }
    }
    fn replace(&self, target: String) {
        if !self.normalized.replace(true)
            && Route::from_str(&self.inner.current_route())
                .is_ok_and(|route| route.to_string() == target)
        {
            // Router initialization formats typed arguments and would otherwise
            // discard undeclared next/app keys, raw encodings, and fragments.
            return;
        }
        if !self.navigate_bare(&target, true) {
            self.inner.replace(target);
        }
    }
    fn external(&self, target: String) -> bool {
        self.inner.external(target)
    }
    fn updater(&self, callback: Arc<dyn Fn() + Send + Sync>) {
        *self.update.borrow_mut() = Some(callback.clone());
        self.inner.updater(callback);
    }
    fn include_prevent_default(&self) -> bool {
        self.inner.include_prevent_default()
    }
}

/// Install once above Router. `false` means Wasm is warm but the server document
/// still owns the screen. Readiness here means a navigation can be accepted; it
/// deliberately makes no claim that route data or interactive controls are ready.
pub fn use_application_runtime() -> bool {
    let active = use_signal(|| !background_document());
    let boundary = use_hook(|| {
        let boundary = Rc::new(PersistentHistory::new(history()));
        provide_history_context(boundary.clone());
        boundary
    });
    #[cfg(target_arch = "wasm32")]
    {
        let bridge = use_hook(|| Rc::new(RefCell::new(None::<BrowserBridge>)));
        use_effect(move || {
            if bridge.borrow().is_none() {
                *bridge.borrow_mut() = BrowserBridge::install(boundary.clone(), active);
            }
        });
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = boundary;
    active()
}

#[cfg(target_arch = "wasm32")]
struct BrowserBridge {
    window: web_sys::Window,
    listener: wasm_bindgen::closure::Closure<dyn FnMut(web_sys::CustomEvent)>,
}

#[cfg(target_arch = "wasm32")]
impl BrowserBridge {
    fn install(boundary: Rc<PersistentHistory>, mut active: Signal<bool>) -> Option<Self> {
        use wasm_bindgen::JsCast;
        let window = web_sys::window()?;
        let source = window.clone();
        let listener =
            wasm_bindgen::closure::Closure::wrap(Box::new(move |event: web_sys::CustomEvent| {
                if !event.cancelable() {
                    return;
                }
                let Some(href) = event.detail().as_string() else {
                    return;
                };
                let Ok(url) = web_sys::Url::new_with_base(
                    &href,
                    &source.location().href().unwrap_or_default(),
                ) else {
                    return;
                };
                if Some(url.origin()) != source.location().origin().ok() {
                    return;
                }
                let raw = format!("{}{}{}", url.pathname(), url.search(), url.hash());
                let prefix = boundary.current_prefix().unwrap_or_else(document_prefix);
                let Some(target) = without_prefix(&raw, &prefix).filter(|target| app_route(target))
                else {
                    return;
                };
                // First activation pushes before Router construction, so its initial
                // route is the requested destination rather than the old HTML page.
                boundary.push(target.to_string());
                active.set(true);
                boundary.notify();
                event.prevent_default();
            })
                as Box<dyn FnMut(web_sys::CustomEvent)>);
        window
            .add_event_listener_with_callback("arctos:navigate", listener.as_ref().unchecked_ref())
            .ok()?;
        if let Some(root) = window
            .document()
            .and_then(|document| document.document_element())
        {
            let _ = root.set_attribute("data-arctos-router-ready", "true");
        }
        if let Ok(event) = web_sys::Event::new("arctos:router-ready") {
            let _ = window.dispatch_event(&event);
        }
        Some(Self { window, listener })
    }
}

#[cfg(target_arch = "wasm32")]
impl Drop for BrowserBridge {
    fn drop(&mut self) {
        use wasm_bindgen::JsCast;
        let _ = self.window.remove_event_listener_with_callback(
            "arctos:navigate",
            self.listener.as_ref().unchecked_ref(),
        );
        if let Some(root) = self
            .window
            .document()
            .and_then(|document| document.document_element())
        {
            let _ = root.remove_attribute("data-arctos-router-ready");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use dioxus::history::MemoryHistory;

    #[test]
    fn public_and_interactive_reading_stay_in_the_running_application() {
        for target in [
            "/",
            "/?q=A%26B&view=past&page=2#events",
            "/cup?tab=info",
            "/leagues",
            "/players?page=2",
            "/login?next=%2Fcup%2Fregister",
            "/?app=1",
        ] {
            assert!(app_route(target), "{target}");
        }
        for target in [
            "https://other.test/",
            "//other.test/",
            "/\\other.test/",
            "/cup/waiver",
            "/scoreboard-render",
            "/cup/scoreboard",
            "/cup/sidecomps/1/enter-results",
        ] {
            assert!(!app_route(target), "{target}");
        }
    }

    #[test]
    fn navigation_and_back_forward_keep_one_history_with_raw_queries() {
        let history = PersistentHistory::new(Rc::new(MemoryHistory::with_initial_path(
            "/?q=Rock%26Roll&page=2#events",
        )));
        history.replace(
            Route::from_str(&history.current_route())
                .unwrap()
                .to_string(),
        );
        assert_eq!(history.current_route(), "/?q=Rock%26Roll&page=2#events");
        history.push("/leagues?q=A%26B".into());
        history.push("/cup?tab=info#registration".into());
        history.go_back();
        assert_eq!(history.current_route(), "/leagues?q=A%26B");
        history.go_back();
        assert_eq!(history.current_route(), "/?q=Rock%26Roll&page=2#events");
        history.go_forward();
        assert_eq!(history.current_route(), "/leagues?q=A%26B");
    }

    #[test]
    fn first_activation_preserves_destination_query_instead_of_old_public_query() {
        let history =
            PersistentHistory::new(Rc::new(MemoryHistory::with_initial_path("/?q=old&page=2")));
        history.push("/login?next=%2Fcup%2Fregister#form".into());
        history.replace(
            Route::from_str(&history.current_route())
                .unwrap()
                .to_string(),
        );
        assert_eq!(
            history.current_route(),
            "/login?next=%2Fcup%2Fregister#form"
        );
    }

    #[test]
    fn same_url_navigation_keeps_history_and_raw_query() {
        let initial = "/?q=Rock%26Roll&view=past&page=2&app=1#events";
        let history = PersistentHistory::new(Rc::new(MemoryHistory::with_initial_path(initial)));
        // Revisiting the current URL preserves its raw query and does not add
        // another entry to browser history.
        history.push(initial.into());
        history.replace(
            Route::from_str(&history.current_route())
                .unwrap()
                .to_string(),
        );
        assert_eq!(history.current_route(), initial);
        assert!(!history.can_go_back());

        history.push("/leagues?q=Summer%20League#leagues".into());
        history.go_back();
        history.push(initial.into());
        assert!(!history.can_go_back());
        assert!(history.can_go_forward());
        history.go_forward();
        assert_eq!(
            history.current_route(),
            "/leagues?q=Summer%20League#leagues"
        );
    }

    #[test]
    fn prefix_matching_requires_a_path_boundary_and_preserves_encoding() {
        assert_eq!(
            without_prefix("/arctos/?q=A%26B&page=2#events", "/arctos"),
            Some("/?q=A%26B&page=2#events")
        );
        assert_eq!(without_prefix("/arctos-other/leagues", "/arctos"), None);
        assert_eq!(without_prefix("/leagues", "/arctos"), None);
        assert_eq!(
            without_prefix("/arctos/players", "/arctos/"),
            Some("/players")
        );
    }
}
