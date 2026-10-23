//! Browser-local appearance preferences. No global document styles are changed.

use dioxus::prelude::*;
use serde::Serialize;

pub const STORAGE_KEY: &str = "arctos.appearance.v1";

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Palette {
    #[default]
    California,
    Field,
    Neutral,
}

impl Palette {
    pub const ALL: [Self; 3] = [Self::California, Self::Field, Self::Neutral];

    pub fn id(self) -> &'static str {
        match self {
            Self::California => "california",
            Self::Field => "field",
            Self::Neutral => "neutral",
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::California => "California Jugger",
            Self::Field => "Field",
            Self::Neutral => "Neutral",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        // Preserve this earlier public storage ID; removed and unknown choices
        // fall back to the default through Preferences::from_storage.
        match value {
            "field-chalk" => Some(Self::Field),
            _ => Self::ALL.into_iter().find(|palette| palette.id() == value),
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ColorMode {
    Light,
    Dark,
    #[default]
    System,
}

impl ColorMode {
    pub const ALL: [Self; 3] = [Self::Light, Self::Dark, Self::System];

    pub fn id(self) -> &'static str {
        match self {
            Self::Light => "light",
            Self::Dark => "dark",
            Self::System => "system",
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Light => "Light",
            Self::Dark => "Dark",
            Self::System => "System",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|mode| mode.id() == value)
    }

    pub fn resolve(self, system_dark: bool) -> &'static str {
        match self {
            Self::Dark => "dark",
            Self::Light => "light",
            Self::System if system_dark => "dark",
            Self::System => "light",
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Preferences {
    pub palette: Palette,
    pub mode: ColorMode,
}

impl Preferences {
    fn from_storage(raw: Option<&str>) -> Self {
        let value = raw.and_then(|raw| serde_json::from_str::<serde_json::Value>(raw).ok());
        let palette = value
            .as_ref()
            .and_then(|value| value.get("palette"))
            .and_then(|value| value.as_str())
            .and_then(Palette::parse)
            .unwrap_or_default();
        let mode = value
            .as_ref()
            .and_then(|value| value.get("mode"))
            .and_then(|value| value.as_str())
            .and_then(ColorMode::parse)
            .unwrap_or_default();
        Self { palette, mode }
    }
}

#[derive(Clone, Copy, PartialEq)]
pub struct Appearance {
    preferences: Signal<Preferences>,
    system_dark: Signal<bool>,
}

impl Appearance {
    pub fn preferences(self) -> Preferences {
        (self.preferences)()
    }

    pub fn scheme(self) -> &'static str {
        self.preferences().mode.resolve((self.system_dark)())
    }

    pub fn set_palette(self, palette: Palette) {
        self.update(Preferences {
            palette,
            ..self.preferences()
        });
    }

    pub fn set_mode(self, mode: ColorMode) {
        self.update(Preferences {
            mode,
            ..self.preferences()
        });
    }

    fn update(mut self, preferences: Preferences) {
        self.preferences.set(preferences);
        let _ = store_preferences(preferences);
    }
}

pub fn use_appearance() -> Appearance {
    let initial = use_hook(load_preferences);
    let preferences = use_signal(move || initial);
    let system_dark = use_signal(system_prefers_dark);
    #[cfg(target_arch = "wasm32")]
    let _listeners = use_hook(move || {
        // The hook owns the last Rc after rendering. Drop removes both browser
        // listeners when Layout unmounts, before their closures are released.
        std::rc::Rc::new(BrowserListeners::install(preferences, system_dark))
    });
    Appearance {
        preferences,
        system_dark,
    }
}

fn load_preferences() -> Preferences {
    #[cfg(target_arch = "wasm32")]
    if let Some(storage) =
        web_sys::window().and_then(|window| window.local_storage().ok().flatten())
    {
        if let Ok(raw) = storage.get_item(STORAGE_KEY) {
            return Preferences::from_storage(raw.as_deref());
        }
    }
    Preferences::default()
}

fn store_preferences(preferences: Preferences) -> bool {
    #[cfg(target_arch = "wasm32")]
    if let Some(storage) =
        web_sys::window().and_then(|window| window.local_storage().ok().flatten())
    {
        return serde_json::to_string(&preferences)
            .ok()
            .is_some_and(|value| storage.set_item(STORAGE_KEY, &value).is_ok());
    }
    let _ = preferences;
    false
}

fn system_prefers_dark() -> bool {
    #[cfg(target_arch = "wasm32")]
    if let Some(query) = web_sys::window().and_then(|window| {
        window
            .match_media("(prefers-color-scheme: dark)")
            .ok()
            .flatten()
    }) {
        return query.matches();
    }
    false
}

pub fn focus_control(id: &str) {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        if let Some(element) = web_sys::window()
            .and_then(|window| window.document())
            .and_then(|document| document.get_element_by_id(id))
            .and_then(|element| element.dyn_into::<web_sys::HtmlElement>().ok())
        {
            let _ = element.focus();
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = id;
}

#[cfg(target_arch = "wasm32")]
struct BrowserListeners {
    window: web_sys::Window,
    query: Option<web_sys::MediaQueryList>,
    media: Option<wasm_bindgen::closure::Closure<dyn FnMut(web_sys::MediaQueryListEvent)>>,
    storage: wasm_bindgen::closure::Closure<dyn FnMut(web_sys::StorageEvent)>,
}

#[cfg(target_arch = "wasm32")]
impl BrowserListeners {
    fn install(
        mut preferences: Signal<Preferences>,
        mut system_dark: Signal<bool>,
    ) -> Option<Self> {
        use wasm_bindgen::{closure::Closure, JsCast};
        let window = web_sys::window()?;
        let query = window
            .match_media("(prefers-color-scheme: dark)")
            .ok()
            .flatten();
        let media = query.as_ref().and_then(|query| {
            let callback = Closure::<dyn FnMut(web_sys::MediaQueryListEvent)>::new(
                move |event: web_sys::MediaQueryListEvent| {
                    system_dark.set(event.matches());
                },
            );
            query
                .add_event_listener_with_callback("change", callback.as_ref().unchecked_ref())
                .ok()?;
            Some(callback)
        });
        let storage_window = window.clone();
        let storage = Closure::<dyn FnMut(web_sys::StorageEvent)>::new(
            move |event: web_sys::StorageEvent| {
                let local = storage_window.local_storage().ok().flatten();
                if local.is_none() || event.storage_area() != local {
                    return;
                }
                if event.key().as_deref().is_some_and(|key| key != STORAGE_KEY) {
                    return;
                }
                preferences.set(Preferences::from_storage(event.new_value().as_deref()));
            },
        );
        let _ =
            window.add_event_listener_with_callback("storage", storage.as_ref().unchecked_ref());
        Some(Self {
            window,
            query,
            media,
            storage,
        })
    }
}

#[cfg(target_arch = "wasm32")]
impl Drop for BrowserListeners {
    fn drop(&mut self) {
        use wasm_bindgen::JsCast;
        if let (Some(query), Some(callback)) = (&self.query, &self.media) {
            let _ = query
                .remove_event_listener_with_callback("change", callback.as_ref().unchecked_ref());
        }
        let _ = self
            .window
            .remove_event_listener_with_callback("storage", self.storage.as_ref().unchecked_ref());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_or_malformed_preferences_use_defaults() {
        assert_eq!(
            Preferences::default(),
            Preferences {
                palette: Palette::California,
                mode: ColorMode::System
            }
        );
        for raw in [
            None,
            Some(""),
            Some("not JSON"),
            Some("[]"),
            Some("null"),
            Some(r#"{"palette":42,"mode":"unknown"}"#),
        ] {
            assert_eq!(Preferences::from_storage(raw), Preferences::default());
        }
    }

    #[test]
    fn stored_choices_round_trip_and_unknown_fields_fall_back_independently() {
        for palette in Palette::ALL {
            for mode in ColorMode::ALL {
                let preferences = Preferences { palette, mode };
                let raw = serde_json::to_string(&preferences).unwrap();
                assert_eq!(Preferences::from_storage(Some(&raw)), preferences);
            }
        }
        assert_eq!(
            Preferences::from_storage(Some(r#"{"palette":"old-palette","mode":"dark"}"#)),
            Preferences {
                palette: Palette::California,
                mode: ColorMode::Dark
            }
        );
        assert_eq!(
            Preferences::from_storage(Some(r#"{"palette":"field"}"#)),
            Preferences {
                palette: Palette::Field,
                mode: ColorMode::System
            }
        );
    }

    #[test]
    fn legacy_field_id_migrates_and_removed_palettes_use_california() {
        let field = Preferences {
            palette: Palette::Field,
            mode: ColorMode::Light,
        };
        assert_eq!(
            Preferences::from_storage(Some(r#"{"palette":"field-chalk","mode":"light"}"#)),
            field
        );
        assert_eq!(
            serde_json::to_string(&field).unwrap(),
            r#"{"palette":"field","mode":"light"}"#
        );
        for palette in ["forest", "ocean", "plum", "ember"] {
            let raw = format!(r#"{{"palette":"{palette}","mode":"dark"}}"#);
            assert_eq!(
                Preferences::from_storage(Some(&raw)),
                Preferences {
                    palette: Palette::California,
                    mode: ColorMode::Dark
                }
            );
        }
    }

    #[test]
    fn explicit_modes_override_system_and_system_follows_it() {
        for system_dark in [false, true] {
            assert_eq!(ColorMode::Light.resolve(system_dark), "light");
            assert_eq!(ColorMode::Dark.resolve(system_dark), "dark");
        }
        assert_eq!(ColorMode::System.resolve(false), "light");
        assert_eq!(ColorMode::System.resolve(true), "dark");
    }
}
