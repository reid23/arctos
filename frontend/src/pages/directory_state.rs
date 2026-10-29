//! URL state for the four public directories. Draft input stays local until committed.
#[cfg(test)]
use crate::Route;
use dioxus::prelude::*;
use dioxus::router::{router, Navigator};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Directory {
    Events,
    Leagues,
    Players,
    Teams,
}

impl Directory {
    fn path(self) -> &'static str {
        match self {
            Self::Events => "/",
            Self::Leagues => "/leagues",
            Self::Players => "/players",
            Self::Teams => "/teams",
        }
    }

    fn owns_url(self, url: &str) -> bool {
        let path = url.split(['?', '#']).next().unwrap_or(url);
        let path = if path == "/" {
            path
        } else {
            path.strip_suffix('/').unwrap_or(path)
        };
        path == self.path()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct DirectoryState {
    pub search: String,
    pub past: bool,
    pub page: u32,
}

impl DirectoryState {
    pub fn new(search: &str, view: &str, page: &str) -> Self {
        Self {
            search: search.trim().to_string(),
            past: view == "past",
            page: page.parse::<u32>().ok().filter(|p| *p > 0).unwrap_or(1),
        }
    }

    pub fn url(&self, directory: Directory) -> String {
        let path = directory.path();
        let mut params = Vec::new();
        if !self.search.is_empty() {
            params.push(format!("q={}", urlencoding::encode(&self.search)));
        }
        if matches!(directory, Directory::Events | Directory::Leagues) && self.past {
            params.push("view=past".to_string());
        }
        if directory == Directory::Players && self.page > 1 {
            params.push(format!("page={}", self.page));
        }
        if params.is_empty() {
            path.to_string()
        } else {
            format!("{path}?{}", params.join("&"))
        }
    }
}

// Dioxus 0.7 decodes the entire query before splitting its named arguments.
// Read the raw history URL instead so encoded ampersands stay inside search text.
fn url_state(url: &str, directory: Directory) -> DirectoryState {
    let before_fragment = url.split('#').next().unwrap_or(url);
    let query = before_fragment
        .split_once('?')
        .map(|(_, query)| query)
        .unwrap_or("");
    let mut search = String::new();
    let mut view = String::new();
    let mut page = String::new();
    for pair in query.split('&') {
        let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
        let decode = |value: &str| {
            let form_value = value.replace('+', " ");
            urlencoding::decode(&form_value)
                .map(|value| value.into_owned())
                .unwrap_or(form_value)
        };
        let value = decode(value);
        match decode(key).as_str() {
            "q" => search = value,
            "view" if matches!(directory, Directory::Events | Directory::Leagues) => view = value,
            "page" if directory == Directory::Players => page = value,
            _ => {}
        }
    }
    DirectoryState::new(&search, &view, &page)
}

/// A departing directory can still receive blur or deferred effects. It must
/// never interpret the destination's query as its own state and navigate back.
fn owned_url_state(url: &str, directory: Directory) -> Option<DirectoryState> {
    directory.owns_url(url).then(|| url_state(url, directory))
}

fn live_flush_is_current(url: &str, directory: Directory, committed: &DirectoryState) -> bool {
    owned_url_state(url, directory).as_ref() == Some(committed)
}

#[derive(Clone, Copy)]
pub(super) struct DirectoryControls {
    pub search: Signal<String>,
    pub state: Signal<DirectoryState>,
    directory: Directory,
    navigator: Navigator,
}

impl DirectoryControls {
    pub fn commit(mut self, next: DirectoryState, push: bool) {
        let Some(current) = owned_url_state(&router().full_route_string(), self.directory) else {
            return;
        };
        if *self.state.peek() == next && current == next {
            return;
        }
        let url = next.url(self.directory);
        self.state.set(next.clone());
        if current == next {
            return;
        }
        if push {
            self.navigator.push(url);
        } else {
            // Router replacements scroll to the top by default. Live local filtering
            // must not move the reader (or change focus) while they type.
            #[cfg(target_arch = "wasm32")]
            let position = web_sys::window()
                .map(|w| (w.scroll_x().unwrap_or(0.0), w.scroll_y().unwrap_or(0.0)));
            self.navigator.replace(url);
            #[cfg(target_arch = "wasm32")]
            if let (Some(w), Some((x, y))) = (web_sys::window(), position) {
                w.scroll_to_with_x_and_y(x, y);
            }
        }
    }

    pub fn submit(mut self) {
        let next = DirectoryState::new(&self.search.peek(), "", "");
        self.search.set(next.search.clone());
        self.commit(next, true);
    }

    pub fn clear(mut self) {
        let mut next = self.state.peek().clone();
        next.search.clear();
        next.page = 1;
        self.search.set(String::new());
        self.commit(next, true);
    }

    pub fn filter(self, past: bool) {
        let next = DirectoryState::new(&self.search.peek(), if past { "past" } else { "" }, "");
        self.commit(next, true);
    }

    pub fn page(self, page: u32, push: bool) {
        let mut next = self.state.peek().clone();
        next.page = page.max(1);
        self.commit(next, push);
    }

    pub fn flush(self) {
        let committed = self.state.peek().clone();
        // A Back/Forward navigation within this directory may precede its sync
        // effect. Automatic blur/debounce writes must not undo that navigation.
        if !live_flush_is_current(&router().full_route_string(), self.directory, &committed) {
            return;
        }
        let next = DirectoryState::new(
            &self.search.peek(),
            if committed.past { "past" } else { "" },
            "",
        );
        self.commit(next, false);
    }
}

fn initial_directory_url(directory: Directory) -> Option<String> {
    thread_local! { static CONSUMED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) }; }
    if CONSUMED.replace(true) {
        return None;
    }
    let path = DirectoryState::new("", "", "").url(directory);
    crate::initial_query(&path).map(|query| format!("{path}?{query}"))
}

pub(super) fn use_directory(directory: Directory, live: bool) -> DirectoryControls {
    let url = router().full_route_string();
    let startup = use_hook(move || initial_directory_url(directory));
    let initial = url_state(startup.as_deref().unwrap_or(&url), directory);
    let mut startup_repair = use_signal(|| startup);
    let mut state = use_signal(|| initial.clone());
    let mut search = use_signal(|| initial.search.clone());
    let controls = DirectoryControls {
        search,
        state,
        directory,
        navigator: use_navigator(),
    };
    use_effect(use_reactive(&url, move |_| {
        let Some(next) = owned_url_state(&router().full_route_string(), directory) else {
            return;
        };
        if let Some(original) = startup_repair.write().take() {
            // Router construction rewrites typed query arguments before child
            // hooks run. Recover the pre-Router raw query once, never on Back.
            controls
                .navigator
                .replace(url_state(&original, directory).url(directory));
            return;
        }
        // Own commits have already updated state. Do not trim/replace the input
        // under a typist; external Back/Forward does restore the committed query.
        if *state.peek() != next {
            search.set(next.search.clone());
            state.set(next);
        }
    }));
    let _commit = use_resource(move || {
        let draft = search();
        let committed = state();
        async move {
            if !live || draft.trim() == committed.search {
                return;
            }
            #[cfg(target_arch = "wasm32")]
            gloo_timers::future::TimeoutFuture::new(350).await;
            controls.flush();
        }
    });
    use_hook(install_scroll_observer);
    controls
}

/// The router restores scroll before async directory data arrives, when the
/// loading page may be too short. Repeat only a pending Back/Forward restoration
/// after this directory's matching response has rendered. Never focus an input.
pub(super) fn use_directory_scroll(controls: DirectoryControls, ready: bool) {
    let url = router().full_route_string();
    let ready = ready
        && owned_url_state(&url, controls.directory)
            .is_some_and(|current| *controls.state.read() == current);
    use_effect(use_reactive(&(url, ready), move |(url, ready)| {
        if ready {
            restore_pending_scroll(url.split('#').next().unwrap_or(&url).to_string());
        }
    }));
}

#[cfg(not(target_arch = "wasm32"))]
fn install_scroll_observer() {}
#[cfg(not(target_arch = "wasm32"))]
fn restore_pending_scroll(_expected_url: String) {}

#[cfg(target_arch = "wasm32")]
mod scroll {
    use std::cell::RefCell;
    pub(super) struct Pending {
        pub url: String,
        pub x: f64,
        pub y: f64,
    }
    thread_local! {
        pub(super) static INSTALLED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
        pub(super) static PENDING: RefCell<Option<Pending>> = const { RefCell::new(None) };
    }
    pub(super) fn url() -> Option<String> {
        let l = web_sys::window()?.location();
        Some(format!("{}{}", l.pathname().ok()?, l.search().ok()?))
    }
}

#[cfg(target_arch = "wasm32")]
fn install_scroll_observer() {
    use wasm_bindgen::{closure::Closure, JsCast};
    if scroll::INSTALLED.replace(true) {
        return;
    }
    let Some(window) = web_sys::window() else {
        return;
    };
    let listener = Closure::<dyn FnMut(web_sys::Event)>::new(move |_| {
        let position = web_sys::window()
            .and_then(|w| w.history().ok())
            .and_then(|h| h.state().ok());
        let pending = position.filter(js_sys::Array::is_array).and_then(|value| {
            let values = js_sys::Array::from(&value);
            let (x, y) = (values.get(0).as_f64()?, values.get(1).as_f64()?);
            (x.is_finite() && y.is_finite()).then(|| scroll::Pending {
                url: scroll::url().unwrap_or_default(),
                x,
                y,
            })
        });
        scroll::PENDING.set(pending);
    });
    let _ = window.add_event_listener_with_callback("popstate", listener.as_ref().unchecked_ref());
    listener.forget();
    // A slow request must not later pull the page away after the user interacts.
    for event in ["pointerdown", "wheel", "keydown"] {
        let listener =
            Closure::<dyn FnMut(web_sys::Event)>::new(move |_| scroll::PENDING.set(None));
        let _ = window.add_event_listener_with_callback(event, listener.as_ref().unchecked_ref());
        listener.forget();
    }
}

#[cfg(target_arch = "wasm32")]
fn restore_pending_scroll(expected_url: String) {
    use wasm_bindgen::{closure::Closure, JsCast};
    let matches = scroll::PENDING.with_borrow(|p| {
        p.as_ref()
            .is_some_and(|p| p.url == expected_url && Some(p.url.clone()) == scroll::url())
    });
    if !matches {
        return;
    }
    spawn(async move {
        let Some(window) = web_sys::window() else {
            return;
        };
        if let Some(document) = window.document() {
            if let Ok(ready) = document.fonts().ready() {
                let _ = wasm_bindgen_futures::JsFuture::from(ready).await;
            }
        }
        let callback = Closure::once_into_js(move || {
            scroll::PENDING.with_borrow_mut(|pending| {
                if pending
                    .as_ref()
                    .is_some_and(|p| p.url == expected_url && Some(p.url.clone()) == scroll::url())
                {
                    if let Some(p) = pending.take() {
                        if let Some(window) = web_sys::window() {
                            window.scroll_to_with_x_and_y(p.x, p.y);
                        }
                    }
                }
            });
        });
        let _ = window.request_animation_frame(callback.unchecked_ref());
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn delayed_updates_cannot_take_over_a_destination_route() {
        for (directory, list, detail) in [
            (Directory::Events, "/?q=Open&view=past", "/demo-open?q=Open"),
            (
                Directory::Leagues,
                "/leagues?q=Coast",
                "/leagues/demo-coast-league?q=Coast",
            ),
            (
                Directory::Players,
                "/players?q=Lee&page=2",
                "/players/lee?tab=history",
            ),
            (
                Directory::Teams,
                "/teams?q=Bears",
                "/teams/ursae?tab=history",
            ),
        ] {
            assert!(owned_url_state(list, directory).is_some());
            assert!(owned_url_state(detail, directory).is_none(), "{detail}");
            assert!(owned_url_state("/login?next=/leagues", directory).is_none());
            for other in [
                Directory::Events,
                Directory::Leagues,
                Directory::Players,
                Directory::Teams,
            ] {
                if other != directory {
                    assert!(owned_url_state(other.path(), directory).is_none());
                }
            }
        }
        assert!(owned_url_state("/leagues/?q=Coast#results", Directory::Leagues).is_some());
        assert!(owned_url_state("/leagues-archive?q=Coast", Directory::Leagues).is_none());
        assert!(owned_url_state("/leagues//?q=Coast", Directory::Leagues).is_none());
    }

    #[test]
    fn delayed_live_search_does_not_undo_back_to_another_query() {
        let committed = DirectoryState::new("Coast", "past", "");
        assert!(live_flush_is_current(
            "/leagues?q=Coast&view=past",
            Directory::Leagues,
            &committed
        ));
        assert!(!live_flush_is_current(
            "/leagues?q=North&view=past",
            Directory::Leagues,
            &committed
        ));
        assert!(!live_flush_is_current(
            "/leagues/demo-coast-league?q=Coast&view=past",
            Directory::Leagues,
            &committed
        ));
        assert!(!live_flush_is_current(
            "/?q=Coast&view=past",
            Directory::Leagues,
            &committed
        ));
    }

    #[test]
    fn malformed_filters_and_pages_are_safe() {
        for page in ["", "0", "-1", "no", "999999999999999999999"] {
            assert_eq!(
                DirectoryState::new(" q ", "unknown", page),
                DirectoryState {
                    search: "q".into(),
                    past: false,
                    page: 1
                }
            );
        }
        assert_eq!(DirectoryState::new("", "past", "42").page, 42);
    }
    #[test]
    fn queries_encode_delimiters_and_unicode_without_changing_meaning() {
        let state = DirectoryState::new("A&B + # / café", "past", "2");
        assert_eq!(
            state.url(Directory::Events),
            "/?q=A%26B%20%2B%20%23%20%2F%20caf%C3%A9&view=past"
        );
        assert_eq!(
            state.url(Directory::Players),
            "/players?q=A%26B%20%2B%20%23%20%2F%20caf%C3%A9&page=2"
        );
        assert!(matches!(
            state.url(Directory::Players).parse::<Route>().unwrap(),
            Route::PlayersListWithQuery { .. }
        ));
        assert_eq!(
            url_state(&state.url(Directory::Players), Directory::Players),
            DirectoryState::new(&state.search, "", "2")
        );
    }
    #[test]
    fn malformed_encoding_duplicates_and_fragments_are_deterministic() {
        assert_eq!(
            url_state(
                "/players?q=old&q=A%26B%2BC+Team&page=2#notes",
                Directory::Players
            ),
            DirectoryState::new("A&B+C Team", "", "2")
        );
        assert_eq!(
            url_state("/?q=%FF%ZZ&view=unknown&page=99", Directory::Events),
            DirectoryState::new("%FF%ZZ", "", "")
        );
        assert_eq!(
            url_state("/teams?q=%23rank%3D1&view=past&page=9", Directory::Teams),
            DirectoryState::new("#rank=1", "", "")
        );
    }
    #[test]
    fn defaults_and_directory_specific_fields_stay_canonical() {
        let state = DirectoryState::new("", "", "1");
        assert_eq!(state.url(Directory::Events), "/");
        assert_eq!(state.url(Directory::Leagues), "/leagues");
        assert_eq!(state.url(Directory::Players), "/players");
        assert_eq!(state.url(Directory::Teams), "/teams");
        for (url, directory) in [
            ("/", Directory::Events),
            ("/leagues", Directory::Leagues),
            ("/players", Directory::Players),
            ("/teams", Directory::Teams),
        ] {
            assert_eq!(url_state(url, directory), state);
            assert!(url.parse::<Route>().is_ok());
        }
    }
    #[test]
    fn browser_history_routes_restore_independent_directory_states() {
        for (url, directory, expected) in [
            (
                "/?q=Open&view=past",
                Directory::Events,
                DirectoryState::new("Open", "past", ""),
            ),
            (
                "/leagues?q=Coast&view=past",
                Directory::Leagues,
                DirectoryState::new("Coast", "past", ""),
            ),
            (
                "/players?q=Lee&page=3",
                Directory::Players,
                DirectoryState::new("Lee", "", "3"),
            ),
            (
                "/teams?q=Otters",
                Directory::Teams,
                DirectoryState::new("Otters", "", ""),
            ),
            (
                "/players",
                Directory::Players,
                DirectoryState::new("", "", ""),
            ),
        ] {
            assert_eq!(url_state(url, directory), expected, "{url}");
        }
    }
}
