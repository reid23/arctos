//! Non-polling scoreboard used by the backend's headless-Chromium overlay
//! renderer (`app/utils/scoreboard_render.py`). The renderer calls
//! `window.arctosRenderScoreboard(stateJson)`, which shows that state and
//! returns a sequence number; once the element with a matching
//! `data-render-sequence` exists, the frame is on screen.

use super::scoreboard::{ScoreboardDisplay, SCOREBOARD_CSS};
use crate::types::ScoreboardStateResponse;
use dioxus::prelude::*;

#[component]
pub fn ScoreboardRender() -> Element {
    let frame = use_signal(|| None::<(u32, ScoreboardStateResponse)>);
    #[cfg(target_arch = "wasm32")]
    use_hook(move || install_render_function(frame));
    rsx! {
        style { "{SCOREBOARD_CSS}" }
        // Frames are captured right after a state change, so the live page's progress-bar animation must not run.
        style { ".progress-bar {{ transition: none; }}" }
        if let Some((sequence, state)) = frame() {
            div { "data-render-sequence": "{sequence}",
                ScoreboardDisplay {
                    stones_remaining: state.stones_info.as_ref().and_then(|stones| stones.stones_remaining).unwrap_or(0),
                    state,
                }
            }
        }
    }
}

#[cfg(target_arch = "wasm32")]
fn install_render_function(mut frame: Signal<Option<(u32, ScoreboardStateResponse)>>) {
    use wasm_bindgen::{closure::Closure, JsValue};

    let Some(window) = web_sys::window() else {
        return;
    };
    let mut sequence = 0u32;
    let render =
        Closure::<dyn FnMut(String) -> Result<u32, JsValue>>::new(move |state_json: String| {
            let state: ScoreboardStateResponse =
                serde_json::from_str(&state_json).map_err(|e| JsValue::from_str(&e.to_string()))?;
            sequence += 1;
            frame.set(Some((sequence, state)));
            Ok(sequence)
        });
    let _ = js_sys::Reflect::set(
        &window,
        &JsValue::from_str("arctosRenderScoreboard"),
        render.as_ref(),
    );
    render.forget();
}
