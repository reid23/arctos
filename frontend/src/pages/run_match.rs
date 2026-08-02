use crate::Route;
use crate::api;
use crate::components::PenaltyDisplay;
use crate::stones_filter::BayesianOffsetFilter;
use dioxus::prelude::*;
#[cfg(target_arch = "wasm32")]
use gloo_timers::callback::Interval;
use serde_json::Value;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast;

/// Parse ISO timestamp to epoch seconds (for stones elapsed).
/// Handles RFC3339 (with Z or offset), and naive ISO from Python (e.g. "2025-02-16T19:34:56.123456").
fn parse_iso_epoch(s: &str) -> Option<i64> {
    let s = s.trim();
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.timestamp())
        .or_else(|| {
            let with_z = if s.ends_with('Z') || s.contains('+') || (s.contains('-') && s.len() > 10)
            {
                s.to_string()
            } else {
                format!("{}Z", s.trim_end_matches('z').trim_end_matches('Z'))
            };
            chrono::DateTime::parse_from_rfc3339(&with_z)
                .ok()
                .map(|dt| dt.timestamp())
        })
        .or_else(|| {
            // Naive ISO from Python isoformat() (no Z), possibly with fractional seconds
            let without_tz = s.trim_end_matches('z').trim_end_matches('Z').trim();
            let base = if let Some(dot) = without_tz.find('.') {
                &without_tz[..dot]
            } else if let Some(plus) = without_tz.find('+') {
                &without_tz[..plus]
            } else {
                without_tz
            };
            chrono::NaiveDateTime::parse_from_str(base, "%Y-%m-%dT%H:%M:%S")
                .ok()
                .map(|t| t.and_utc().timestamp())
        })
}

#[cfg(target_arch = "wasm32")]
fn now_epoch_secs() -> f64 {
    js_sys::Date::new_0().get_time() / 1000.0
}

#[cfg(not(target_arch = "wasm32"))]
fn now_epoch_secs() -> f64 {
    chrono::Utc::now().timestamp() as f64
}

/// Long-lived Web Audio context for run-match tones.
///
/// Browsers only allow an AudioContext to leave the ``suspended`` state inside a
/// user gesture. Creating a fresh context when a timer hits zero (seconds later)
/// leaves it suspended, and dropping it immediately cancels scheduled beeps.
/// Keep one context for the page lifetime and unlock it on the first gesture.
#[cfg(target_arch = "wasm32")]
fn run_match_audio_ctx() -> Option<web_sys::AudioContext> {
    thread_local! {
        static CTX: std::cell::RefCell<Option<web_sys::AudioContext>> =
            const { std::cell::RefCell::new(None) };
    }
    CTX.with(|cell| {
        let mut slot = cell.borrow_mut();
        if slot.is_none() {
            match web_sys::AudioContext::new() {
                Ok(ctx) => {
                    let _ = ctx.resume();
                    *slot = Some(ctx);
                }
                Err(_) => return None,
            }
        } else if let Some(ctx) = slot.as_ref() {
            if ctx.state() != web_sys::AudioContextState::Running {
                let _ = ctx.resume();
            }
        }
        slot.clone()
    })
}

/// Unlock/resume audio during a user gesture (pointer down, button click, etc.).
///
/// ``AudioContext.resume()`` is asynchronous. Calling it alone on pointerdown
/// often leaves the context still ``suspended`` by the time the promise runs
/// (outside the gesture). Starting a near-silent oscillator on this stack is
/// what actually primes the graph — same reason Start/End Point beeps work
/// on first click.
#[cfg(target_arch = "wasm32")]
fn unlock_run_match_audio() {
    let Some(ctx) = run_match_audio_ctx() else {
        return;
    };
    let _ = ctx.resume();
    if let (Ok(osc), Ok(gain)) = (ctx.create_oscillator(), ctx.create_gain()) {
        // Inaudible prime so the gesture counts as "audio started".
        gain.gain().set_value(0.0001);
        osc.set_type(web_sys::OscillatorType::Sine);
        osc.frequency().set_value(440.0);
        let _ = osc.connect_with_audio_node(&gain);
        let _ = gain.connect_with_audio_node(&ctx.destination());
        let now = ctx.current_time();
        let _ = osc.start_with_when(now);
        let _ = osc.stop_with_when(now + 0.05);
    }
}

/// Play sound and trigger vibration for start/end point button. Different sound and pattern for start vs end.
#[cfg(target_arch = "wasm32")]
fn point_button_feedback(is_end_point: bool) {
    use wasm_bindgen::JsCast;
    if let Some(window) = web_sys::window() {
        // Vibration (optional: not supported on many desktop browsers)
        let nav = window.navigator();
        if let Ok(vibrate_fn) = js_sys::Reflect::get(&nav, &"vibrate".into()) {
            if let Some(f) = vibrate_fn.dyn_ref::<js_sys::Function>() {
                let args = if is_end_point {
                    js_sys::Array::of3(&120.into(), &70.into(), &120.into())
                } else {
                    js_sys::Array::of1(&200.into())
                };
                let _ = f.apply(&nav, &args);
            }
        }
        // Sound via Web Audio (shared context unlocked by this click gesture).
        if let Some(ctx) = run_match_audio_ctx() {
            if let (Ok(osc), Ok(gain)) = (ctx.create_oscillator(), ctx.create_gain()) {
                gain.gain().set_value(0.4);
                let (freq, duration) = if is_end_point {
                    (440.0, 0.2)
                } else {
                    (880.0, 0.25)
                };
                osc.set_type(web_sys::OscillatorType::Sine);
                osc.frequency().set_value(freq);
                let _ = osc.connect_with_audio_node(&gain);
                let _ = gain.connect_with_audio_node(&ctx.destination());
                let _ = osc.start();
                let _ = osc.stop_with_when(ctx.current_time() + duration);
            }
        }
    }
}

/// Play five fast beeps to warn that 15 stones remain in a stones match.
#[cfg(target_arch = "wasm32")]
fn play_stones_warning_beeps() {
    let Some(ctx) = run_match_audio_ctx() else {
        return;
    };
    let start = ctx.current_time();
    let beep = 0.08;
    let gap = 0.12;
    for i in 0..5 {
        if let (Ok(osc), Ok(gain)) = (ctx.create_oscillator(), ctx.create_gain()) {
            gain.gain().set_value(0.4);
            osc.set_type(web_sys::OscillatorType::Square);
            osc.frequency().set_value(1000.0);
            let _ = osc.connect_with_audio_node(&gain);
            let _ = gain.connect_with_audio_node(&ctx.destination());
            let at = start + (i as f64) * gap;
            let _ = osc.start_with_when(at);
            let _ = osc.stop_with_when(at + beep);
        }
    }
}

/// Play five beeps spread across ~2 seconds to signal a quick timer reaching zero.
#[cfg(target_arch = "wasm32")]
fn play_timer_end_beeps() {
    let Some(ctx) = run_match_audio_ctx() else {
        return;
    };
    // Resume in case the browser suspended the context while the tab was backgrounded.
    let _ = ctx.resume();
    let start = ctx.current_time();
    let beep = 0.12;
    let gap = 0.5; // 5 beeps at 0.0, 0.5, 1.0, 1.5, 2.0s.
    for i in 0..5 {
        if let (Ok(osc), Ok(gain)) = (ctx.create_oscillator(), ctx.create_gain()) {
            gain.gain().set_value(0.4);
            osc.set_type(web_sys::OscillatorType::Square);
            osc.frequency().set_value(660.0);
            let _ = osc.connect_with_audio_node(&gain);
            let _ = gain.connect_with_audio_node(&ctx.destination());
            let at = start + (i as f64) * gap;
            let _ = osc.start_with_when(at);
            let _ = osc.stop_with_when(at + beep);
        }
    }
}

/// Capture the given pointer on the quick-timer element so drag move/up events
/// keep arriving even after the cursor leaves the small element's bounds.
#[cfg(target_arch = "wasm32")]
fn capture_quick_timer_pointer(pointer_id: i32) {
    if let Some(el) = web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id("quick-timer"))
    {
        let _ = el.set_pointer_capture(pointer_id);
    }
}

/// State of the ref-only quick timer beside the stone counter.
#[derive(Clone, PartialEq)]
enum QuickTimer {
    /// Showing the ⏱ icon.
    Idle,
    /// Finger down; previewing a value derived from drag magnitude.
    Dragging { start_stones: u32 },
    /// Counting down from start_stones since start_time (server unix seconds).
    Running { start_time: f64, start_stones: u32 },
}

/// Compute scores_by_set from points array (client-authoritative; same logic as server).
fn scores_by_set_from_points(points: &[&Value]) -> Vec<(String, u64, u64)> {
    let mut by_set: std::collections::BTreeMap<u64, (u64, u64)> = std::collections::BTreeMap::new();
    for pt in points {
        let set_num = pt.get("set_number").and_then(|n| n.as_u64()).unwrap_or(1);
        let winner = pt.get("winner").and_then(|w| w.as_str()).unwrap_or("none");
        let rerolled = pt
            .get("rerolled")
            .and_then(|r| r.as_bool())
            .unwrap_or(false);
        if rerolled {
            continue;
        }
        let entry = by_set.entry(set_num).or_insert((0, 0));
        if winner == "TEAM1" {
            entry.0 += 1;
        } else if winner == "TEAM2" {
            entry.1 += 1;
        }
    }
    by_set
        .into_iter()
        .map(|(k, (t1, t2))| (k.to_string(), t1, t2))
        .collect()
}


/// Read a point's set_number from match-state JSON (handles null / int forms).
fn set_number_from_state(state: &Option<Result<Value, String>>, point_id: &str) -> u32 {
    let Some(Ok(v)) = state.as_ref() else {
        return 1;
    };
    let Some(points) = v.get("points").and_then(|p| p.as_array()) else {
        return 1;
    };
    for p in points {
        if p.get("uuid").and_then(|u| u.as_str()) == Some(point_id) {
            return p
                .get("set_number")
                .and_then(|n| n.as_u64().or_else(|| n.as_i64().map(|i| i.max(0) as u64)))
                .unwrap_or(1) as u32;
        }
    }
    1
}

/// Optimistically write set_number for a point in match-state JSON. Returns true if found.
fn set_number_in_state(state: &mut Value, point_id: &str, new_set: u32) -> bool {
    let Some(points) = state.get_mut("points").and_then(|p| p.as_array_mut()) else {
        return false;
    };
    for p in points.iter_mut() {
        if p.get("uuid").and_then(|u| u.as_str()) == Some(point_id) {
            p["set_number"] = serde_json::json!(new_set);
            return true;
        }
    }
    false
}



/// Highlight a point row once, then clear so live re-renders do not restart the animation.
#[cfg(target_arch = "wasm32")]
fn trigger_point_row_flash(mut flash_point_id: Signal<Option<String>>, point_id: String) {
    flash_point_id.set(Some(point_id));
    spawn(async move {
        gloo_timers::future::TimeoutFuture::new(1200).await;
        flash_point_id.set(None);
    });
}

#[cfg(not(target_arch = "wasm32"))]
fn trigger_point_row_flash(mut flash_point_id: Signal<Option<String>>, point_id: String) {
    flash_point_id.set(Some(point_id));
}

/// Set +/- controls for one point. Own component so it re-renders from
/// ``state_signal`` when set_number changes, independent of how the parent
/// table rows are reconciled (prebuilt VNodes were showing a stale number).
#[component]
fn SetNumberControls(
    point_id: String,
    url: String,
    mut state_signal: Signal<Option<Result<Value, String>>>,
    mut action_error: Signal<Option<String>>,
) -> Element {
    // Subscribe to state so this cell always shows the live value for point_id.
    let set_num = set_number_from_state(&state_signal(), point_id.as_str());
    let pid_inc = point_id.clone();
    let pid_dec = point_id.clone();
    let u_inc = url.clone();
    let u_dec = url.clone();
    rsx! {
        div { class: "set-number-controls",
            button {
                class: "set-step-btn",
                r#type: "button",
                onclick: move |_| {
                    let prev = state_signal().clone();
                    let current = set_number_from_state(&prev, pid_inc.as_str());
                    let new_set = current.saturating_add(1).max(1);
                    if let Some(Ok(ref state)) = prev.clone() {
                        let mut state = state.clone();
                        if set_number_in_state(&mut state, pid_inc.as_str(), new_set) {
                            state_signal.set(Some(Ok(state)));
                        }
                    }
                    let u = u_inc.clone();
                    let p = pid_inc.clone();
                    let mut state_signal = state_signal;
                    let mut action_error = action_error;
                    spawn(async move {
                        let body = serde_json::json!({ "point_id": p, "set_number": new_set });
                        match api::update_point(&u, &p, &body).await {
                            Ok(_) => { action_error.set(None); }
                            Err(e) => {
                                action_error.set(Some(e));
                                state_signal.set(prev);
                            }
                        }
                    });
                },
                "+"
            }
            span { class: "set-number-display", "{set_num}" }
            button {
                class: "set-step-btn",
                r#type: "button",
                onclick: move |_| {
                    let prev = state_signal().clone();
                    let current = set_number_from_state(&prev, pid_dec.as_str());
                    let new_set = current.saturating_sub(1).max(1);
                    if new_set == current {
                        return;
                    }
                    if let Some(Ok(ref state)) = prev.clone() {
                        let mut state = state.clone();
                        if set_number_in_state(&mut state, pid_dec.as_str(), new_set) {
                            state_signal.set(Some(Ok(state)));
                        }
                    }
                    let u = u_dec.clone();
                    let p = pid_dec.clone();
                    let mut state_signal = state_signal;
                    let mut action_error = action_error;
                    spawn(async move {
                        let body = serde_json::json!({ "point_id": p, "set_number": new_set });
                        match api::update_point(&u, &p, &body).await {
                            Ok(_) => { action_error.set(None); }
                            Err(e) => {
                                action_error.set(Some(e));
                                state_signal.set(prev);
                            }
                        }
                    });
                },
                "−"
            }
        }
    }
}

/// Stones elapsed = number of global 1.5s beat boundaries crossed (so counters tick in sync with global clock).
fn stones_elapsed_beats(stamp_opt: Option<&str>, end_opt: Option<&str>) -> u32 {
    const BEAT: f64 = 1.5;
    let start = stamp_opt.and_then(parse_iso_epoch).unwrap_or(0) as f64;
    let end = end_opt
        .and_then(parse_iso_epoch)
        .map(|t| t as f64)
        .unwrap_or_else(now_epoch_secs);
    let start_beat = (start / BEAT).floor() as i64;
    let end_beat = (end / BEAT).floor() as i64;
    (end_beat - start_beat).max(0) as u32
}

/// Stones elapsed with millisecond precision (global beat boundaries, same as table).
fn stones_elapsed_beats_ms(stamp_opt: Option<&str>, end_opt: Option<&str>) -> u32 {
    const BEAT: f64 = 1.5;
    let start = stamp_opt
        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
        .map(|dt| dt.timestamp_millis() as f64 / 1000.0)
        .unwrap_or(0.0);
    let end = end_opt
        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
        .map(|dt| dt.timestamp_millis() as f64 / 1000.0)
        .unwrap_or_else(now_epoch_secs);
    let start_beat = (start / BEAT).floor() as i64;
    let end_beat = (end / BEAT).floor() as i64;
    (end_beat - start_beat).max(0) as u32
}

/// Px of drag magnitude per 5 stones when arming the quick timer.
const PX_PER_5: f64 = 30.0;
/// Global beat length (seconds) for the quick timer countdown.
const QUICK_TIMER_BEAT: f64 = 1.5;

/// Radial drag magnitude (px) -> stones, snapped to the nearest multiple of 5.
fn drag_magnitude_to_stones(dx: f64, dy: f64) -> u32 {
    let magnitude = (dx * dx + dy * dy).sqrt();
    let steps = (magnitude / PX_PER_5).round();
    (steps.max(0.0) as u32) * 5
}

/// Stones remaining for a running quick timer: start_stones minus the number of
/// global 1.5s beat boundaries crossed between start_time and now_server (both
/// server-synced unix seconds). Can go negative (-1 signals "return to idle").
fn quick_timer_current(start_time: f64, start_stones: u32, now_server: f64) -> i64 {
    let start_beat = (start_time / QUICK_TIMER_BEAT).floor() as i64;
    let now_beat = (now_server / QUICK_TIMER_BEAT).floor() as i64;
    start_stones as i64 - (now_beat - start_beat).max(0)
}

/// Stones elapsed = global 1.5s beat boundaries crossed. Uses Bayesian filter for server time when end is None (ongoing point).
#[cfg(target_arch = "wasm32")]
fn stones_elapsed_with_filter(
    stamp_opt: Option<&str>,
    end_opt: Option<&str>,
    filter: &Signal<BayesianOffsetFilter>,
) -> u32 {
    const BEAT: f64 = 1.5;
    let start = match stamp_opt.and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok()) {
        Some(parsed) => parsed.timestamp_millis() as f64 / 1000.0,
        None => return 0,
    };
    let end = if let Some(e) = end_opt {
        if let Ok(parsed) = chrono::DateTime::parse_from_rfc3339(e) {
            parsed.timestamp_millis() as f64 / 1000.0
        } else {
            let client_time = js_sys::Date::now() / 1000.0;
            client_time + filter.read().get_mean()
        }
    } else {
        let client_time = js_sys::Date::now() / 1000.0;
        client_time + filter.read().get_mean()
    };
    let start_beat = (start / BEAT).floor() as i64;
    let end_beat = (end / BEAT).floor() as i64;
    (end_beat - start_beat).max(0) as u32
}

/// Max length of a team shortname (mirrors SHORTNAME_LEN in app/models/constants.py).
const SHORTNAME_LEN: usize = 8;

/// Team label for the winner buttons: the shortname if set, otherwise the name truncated to
/// SHORTNAME_LEN-1 chars ending in "…" (never the full name).
fn team_short_label(shortname: Option<&str>, name: &str) -> String {
    if let Some(s) = shortname.filter(|s| !s.is_empty()) {
        return s.to_string();
    }
    let max_len = SHORTNAME_LEN - 1;
    if name.chars().count() <= max_len {
        name.to_string()
    } else {
        let head: String = name.chars().take(max_len - 1).collect();
        format!("{head}…")
    }
}

#[component]
pub fn RunMatch(url: String, match_id: String) -> Element {
    let navigator = use_navigator();
    let url_for_detail = url.clone();
    let id_for_detail = match_id.clone();
    let detail = use_resource(move || {
        let u = url_for_detail.clone();
        let id = id_for_detail.clone();
        async move {
            api::match_detail(&u, Some(&id), None)
                .await
                .map_err(|e| e.to_string())
        }
    });

    let state_signal = use_signal(|| None as Option<Result<Value, String>>);
    let state_loaded = use_signal(|| false);
    let url_for_state = url.clone();
    let id_for_state = match_id.clone();
    use_effect(move || {
        if state_loaded() {
            return;
        }
        let u = url_for_state.clone();
        let id = id_for_state.clone();
        let mut state_signal = state_signal;
        let mut state_loaded = state_loaded;
        state_loaded.set(true);
        spawn(async move {
            match api::match_state(&u, &id).await {
                Ok(v) => state_signal.set(Some(Ok(v))),
                Err(e) => state_signal.set(Some(Err(e))),
            }
        });
    });

    // In-progress point: when Some, button shows "End Point" and we tick stones.
    let mut current_point = use_signal(|| None as Option<String>);
    // Sync current_point from state: if latest point has no end_stamp, we're in End Point mode.
    let state_for_sync = state_signal();
    if let Some(Ok(ref v)) = state_for_sync {
        let points_arr = v.get("points").and_then(|p| p.as_array());
        let points: Vec<&Value> = points_arr.map(|a| a.iter().collect()).unwrap_or_default();
        if let Some(last) = points.last() {
            let end_stamp = last.get("end_stamp").and_then(|e| e.as_str());
            if end_stamp.is_none() || end_stamp == Some("") {
                let uuid = last.get("uuid").and_then(|u| u.as_str()).unwrap_or("");
                if !uuid.is_empty() && current_point().as_deref() != Some(uuid) {
                    current_point.set(Some(uuid.to_string()));
                }
            } else if current_point().as_deref() == last.get("uuid").and_then(|u| u.as_str()) {
                current_point.set(None);
            }
        }
    }

    // Ref-only quick timer beside the stone counter (local, not persisted).
    // Declared before the server-time sync effect so that effect can react to it.
    let mut quick_timer = use_signal(|| QuickTimer::Idle);
    // Page coordinates captured at pointerdown, for computing drag magnitude.
    #[cfg(target_arch = "wasm32")]
    let mut quick_drag_start = use_signal(|| None as Option<(f64, f64)>);
    // Edge latch so the end tone fires once when the countdown reaches zero.
    #[cfg(target_arch = "wasm32")]
    let mut quick_timer_fired_zero = use_signal(|| false);

    // Bayesian filter for server time sync (stones elapsed during ongoing point).
    #[cfg(target_arch = "wasm32")]
    let time_filter = use_signal(|| BayesianOffsetFilter::default());
    #[cfg(target_arch = "wasm32")]
    let server_time_loop_started = use_signal(|| false);
    #[cfg(target_arch = "wasm32")]
    {
        let binding = detail.value();
        let mut time_filter = time_filter;
        let mut server_time_loop_started = server_time_loop_started;
        use_effect(move || {
            if server_time_loop_started() {
                return;
            }
            let guard = binding.try_read();
            let Ok(ref g) = guard else { return };
            let Some(Ok(d)) = g.as_ref() else { return };
            let quick_timer_running = matches!(quick_timer(), QuickTimer::Running { .. });
            if quick_timer_running
                || (d.match_data.set_type.as_deref() == Some("STONES")
                    && d.match_data.status == "IN_PROGRESS")
            {
                server_time_loop_started.set(true);
                let mut filter = time_filter;
                spawn(async move {
                    loop {
                        let client_send = js_sys::Date::now() / 1000.0;
                        if let Ok(res) = api::server_time().await {
                            let client_receive = js_sys::Date::now() / 1000.0;
                            let rtt = client_receive - client_send;
                            let offset = res.server_time - client_receive + (rtt / 2.0);
                            filter.write().update(offset);
                        }
                        gloo_timers::future::TimeoutFuture::new(997).await;
                    }
                });
            }
        });
    }

    // Screen wake lock: keep the device awake while the match page is open (wasm only).
    #[cfg(target_arch = "wasm32")]
    {
        let wake_lock_sentinel = use_signal(|| None::<wasm_bindgen::JsValue>);
        use_effect(move || {
            let mut sentinel_sig = wake_lock_sentinel.to_owned();
            wasm_bindgen_futures::spawn_local(async move {
                let window = match web_sys::window() {
                    Some(w) => w,
                    None => return,
                };
                let navigator = window.navigator();

                let wake_lock_js = match js_sys::Reflect::get(
                    &navigator,
                    &wasm_bindgen::JsValue::from_str("wakeLock"),
                ) {
                    Ok(v) if !v.is_undefined() && !v.is_null() => v,
                    _ => return,
                };
                let request_js = match js_sys::Reflect::get(
                    &wake_lock_js,
                    &wasm_bindgen::JsValue::from_str("request"),
                ) {
                    Ok(v) if v.is_function() => v,
                    _ => return,
                };

                let request_fn = match request_js.dyn_ref::<js_sys::Function>() {
                    Some(f) => f,
                    None => return,
                };

                let type_arg = wasm_bindgen::JsValue::from_str("screen");
                let promise = request_fn
                    .call1(&wake_lock_js, &type_arg)
                    .ok()
                    .and_then(|v| v.dyn_into::<js_sys::Promise>().ok());

                let promise = match promise {
                    Some(p) => p,
                    None => return,
                };

                let result = wasm_bindgen_futures::JsFuture::from(promise).await;
                if let Ok(sentinel) = result {
                    sentinel_sig.set(Some(sentinel));
                }
            });
        });

        let wake_lock_for_drop = wake_lock_sentinel.to_owned();
        use_drop(move || {
            if let Some(sentinel) = wake_lock_for_drop() {
                let release_js =
                    js_sys::Reflect::get(&sentinel, &wasm_bindgen::JsValue::from_str("release"))
                        .ok();
                if let Some(release_fn) =
                    release_js.and_then(|f| f.dyn_ref::<js_sys::Function>().map(|f| f.clone()))
                {
                    let _ = release_fn.call0(&sentinel);
                }
            }
        });
    }

    // Tick for live stones elapsed and time elapsed (every 100ms for accuracy).
    let live_tick = use_signal(|| 0u32);
    #[cfg(target_arch = "wasm32")]
    let live_tick_interval = use_signal(|| None as Option<Interval>);
    #[cfg(target_arch = "wasm32")]
    {
        let mut live_tick = live_tick;
        let mut live_tick_interval = live_tick_interval;
        use_effect(move || {
            let _ = current_point();
            if live_tick_interval.read().is_some() {
                return;
            }
            let handle = Interval::new(100, move || {
                live_tick.set(live_tick() + 1);
            });
            live_tick_interval.set(Some(handle));
        });
    }

    let action_error = use_signal(|| Option::<String>::None);
    let mut notes_modal_point_id = use_signal(|| None as Option<String>);
    let mut notes_modal_notes = use_signal(|| None as Option<Result<Vec<Value>, String>>);
    let mut notes_modal_new_text = use_signal(|| String::new());
    let mut notes_modal_target = use_signal(|| "match".to_string());
    let mut notes_modal_player_id = use_signal(|| None as Option<String>);
    let mut notes_modal_player_query = use_signal(|| String::new());
    // Point row from which the user opened the Penalties modal; delete is only allowed for that point's penalties.
    let mut penalties_modal_point_id = use_signal(|| None as Option<String>);
    let mut penalties_step = use_signal(|| 1);
    let mut penalties_selected_player_id = use_signal(|| None as Option<String>);
    /// Single selection: Some(pt_id) or None; "Other" is separate (penalties_other_selected).
    let mut penalties_selected_type = use_signal(|| None as Option<i32>);
    let mut penalties_other_text = use_signal(|| String::new());
    let mut penalties_other_selected = use_signal(|| false);
    /// (player_id, penalties) after fetching for selected player.
    let mut penalty_history_signal =
        use_signal(|| None as Option<(String, Vec<crate::types::PlayerPenaltyHistoryItem>)>);
    /// Display name of the player currently selected in the penalties modal (step 2).
    let mut penalties_selected_player_display = use_signal(|| String::new());
    let mut point_notes_map_signal =
        use_signal(|| std::collections::HashMap::<String, Vec<Value>>::new());
    let mut point_notes_seeded = use_signal(|| false);
    let mut penalty_desc_modal = use_signal(|| None::<String>);
    // Local stones remaining (for STONES set type); synced from match/state when not ticking.
    // None means the count is not yet known (neither stones_remaining nor stones_per_set is set).
    let mut stones_remaining = use_signal(|| None as Option<u32>);
    // uuid of most recently added point for one-shot row highlight (auto-cleared).
    let mut flash_point_id = use_signal(|| None as Option<String>);
    // When true, stones input shows stones_edit_value so we don't overwrite typing with display_stones.
    let mut stones_input_focused = use_signal(|| false);
    let mut stones_edit_value = use_signal(|| String::new());
    // Previous live stone count, for detecting a tick-down to the 15-stone warning threshold.
    #[cfg(target_arch = "wasm32")]
    let mut prev_stones_for_beep = use_signal(|| None as Option<u32>);
    // Time elapsed (seconds) from match confirmed start (UTC); correct after reload.
    let mut time_elapsed_secs = use_signal(|| 0u64);

    use_effect(move || {
        if point_notes_seeded() {
            return;
        }
        let binding = detail.value();
        let Ok(guard) = binding.try_read() else {
            return;
        };
        let Some(Ok(ref d)) = guard.as_ref() else {
            return;
        };
        point_notes_seeded.set(true);
        let mut map = point_notes_map_signal();
        for (pid, notes) in &d.point_notes_map {
            let v: Vec<Value> = notes
                .iter()
                .filter_map(|n| serde_json::to_value(n).ok())
                .collect();
            map.insert(pid.clone(), v);
        }
        point_notes_map_signal.set(map);
    });

    let detail_view = match detail.value().try_read() {
        Ok(guard) => match guard.as_ref() {
            Some(Ok(d)) => {
                let m = &d.match_data;
                let set_type_stones = m.set_type.as_deref() == Some("STONES");
                // Initialize the local count from the match's stored value the first time it
                // becomes known. None stays None until there's a real value, so user edits and
                // tick-downs are never clobbered, and an unconfigured count surfaces as unknown.
                if stones_remaining().is_none() {
                    if let Some(known) = m.stones_remaining.or(m.stones_per_set) {
                        stones_remaining.set(Some(known));
                    }
                }
                let team1 = m.team1_name.as_str();
                let team2 = m.team2_name.as_str();
                let team1_short = team_short_label(m.team1_shortname.as_deref(), team1);
                let team2_short = team_short_label(m.team2_shortname.as_deref(), team2);
                let team1_photo = m.team1_photo.clone();
                let team2_photo = m.team2_photo.clone();
                let base_url = api::base_url();
                // Refs by pseudonym (refs_display), same as team1_name/team2_name
                let refs_display = m
                    .refs_display
                    .as_deref()
                    .or(m.r#refs_initial.as_deref())
                    .or(m.r#refs.as_deref())
                    .unwrap_or("-");
                let field_display = m.field.as_deref().unwrap_or("TBA");
                let length_display = m.nominal_length.map(|n| format!("{} min", n));
                // Elapsed since match confirmed start (UTC). Difference with current time is
                // timezone-independent; correct after page reload.
                let start_iso = m.confirmed_start_time.as_deref();
                let _ = live_tick();
                let now_secs = now_epoch_secs() as u64;
                let time_elapsed_str = match start_iso.and_then(parse_iso_epoch) {
                    Some(start_secs) => {
                        let start_u = start_secs.max(0) as u64;
                        let elapsed = now_secs.saturating_sub(start_u);
                        time_elapsed_secs.set(elapsed);
                        format!("{:02}:{:02}", elapsed / 60, elapsed % 60)
                    }
                    None => {
                        time_elapsed_secs.set(0);
                        "—".to_string()
                    }
                };

                let state_opt = state_signal();
                let state_value: Option<&Value> = state_opt.as_ref().and_then(|r| r.as_ref().ok());
                let points: Vec<&Value> = state_value
                    .and_then(|v| v.get("points").and_then(|p| p.as_array()))
                    .map(|a| a.iter().collect())
                    .unwrap_or_default();
                let score_rows: Vec<(String, u64, u64)> = scores_by_set_from_points(&points);
                let finalized = state_value
                    .and_then(|v| v.get("finalized_at").and_then(|f| f.as_str()))
                    .is_some();
                let show_notes_modal = notes_modal_point_id().is_some();
                let show_penalties_modal = penalties_modal_point_id().is_some();
                let notes_modal_pid = notes_modal_point_id().clone().unwrap_or_default();
                let penalties_modal_pid = penalties_modal_point_id().clone().unwrap_or_default();
                let url_notes = url.clone();
                let id_notes = match_id.clone();
                let url_notes_submit = url_notes.clone();
                let id_notes_submit = id_notes.clone();
                let team1_notes = team1.to_string();
                let team2_notes = team2.to_string();

                let default_set = points
                    .last()
                    .and_then(|p| p.get("set_number").and_then(|n| n.as_u64()))
                    .unwrap_or(1) as u32;

                struct PointRow {
                    index: usize,
                    point_id: String,
                    winner: String,
                    rerolled: bool,
                    elapsed: u32,
                }
                let mut point_rows: Vec<PointRow> = points
                    .iter()
                    .enumerate()
                    .map(|(index, pt)| {
                        let point_id = pt
                            .get("uuid")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string();
                        let winner = pt
                            .get("winner")
                            .and_then(|v| v.as_str())
                            .unwrap_or("none")
                            .to_string();
                        let rerolled = pt
                            .get("rerolled")
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false);
                        let stamp = pt.get("stamp").and_then(|s| s.as_str());
                        let end_stamp = pt.get("end_stamp").and_then(|e| e.as_str());
                        #[cfg(target_arch = "wasm32")]
                        let elapsed = stones_elapsed_with_filter(stamp, end_stamp, &time_filter);
                        #[cfg(not(target_arch = "wasm32"))]
                        let elapsed = stones_elapsed_beats(stamp, end_stamp);
                        PointRow {
                            index: index + 1,
                            point_id,
                            winner,
                            rerolled,
                            elapsed,
                        }
                    })
                    .collect();
                // Newest first in the table; reverse data (not cloned VNodes) so each
                // row keeps a stable key tied to point id during reconciliation.
                point_rows.reverse();

                // Stones display: during an active point show stones_at_start - elapsed (ticks down); otherwise show stones_remaining.
                let display_stones: Option<u32> = if set_type_stones {
                    if let Some(ref cp) = current_point() {
                        points
                            .iter()
                            .find(|p| p.get("uuid").and_then(|u| u.as_str()) == Some(cp.as_str()))
                            .and_then(|pt| {
                                let at_start =
                                    pt.get("stones_at_start").and_then(|s| s.as_u64())?;
                                let stamp = pt.get("stamp").and_then(|s| s.as_str());
                                #[cfg(target_arch = "wasm32")]
                                let elapsed =
                                    stones_elapsed_with_filter(stamp, None, &time_filter) as u64;
                                #[cfg(not(target_arch = "wasm32"))]
                                let elapsed = stones_elapsed_beats(stamp, None) as u64;
                                Some((at_start.saturating_sub(elapsed)) as u32)
                            })
                            .or(stones_remaining())
                    } else {
                        stones_remaining()
                    }
                } else {
                    stones_remaining()
                };
                // Warn with five fast beeps when the live countdown ticks down to 15 stones.
                // Only during an active point (the countdown path) and not while the operator is
                // editing the count, so manually typing 15 doesn't beep; re-arms above 15.
                #[cfg(target_arch = "wasm32")]
                {
                    let is_counting_down =
                        set_type_stones && current_point().is_some() && !stones_input_focused();
                    let prev = prev_stones_for_beep();
                    if is_counting_down {
                        if prev.map(|p| p > 15).unwrap_or(false) && display_stones == Some(15) {
                            spawn(async move {
                                gloo_timers::future::TimeoutFuture::new(0).await;
                                play_stones_warning_beeps();
                            });
                        }
                        if prev != display_stones {
                            prev_stones_for_beep.set(display_stones);
                        }
                    } else if prev.is_some() {
                        prev_stones_for_beep.set(None);
                    }
                }
                let stones_input_value = if stones_input_focused() {
                    stones_edit_value().clone()
                } else {
                    display_stones.map(|n| n.to_string()).unwrap_or_default()
                };
                let stones_known = display_stones.is_some();
                let url_stones_blur = url.clone();
                let id_stones_blur = match_id.clone();

                let url_start = url.clone();
                let id_start = match_id.clone();
                let mut current_point = current_point;
                let mut stones_remaining = stones_remaining;
                let mut state_signal_start = state_signal;
                let on_start_end_point = move |_| {
                    let u = url_start.clone();
                    let id = id_start.clone();
                    let point_opt = current_point();
                    #[cfg(target_arch = "wasm32")]
                    {
                        let is_end = point_opt.is_some();
                        spawn(async move {
                            // Defer feedback to next tick so we don't trigger re-entrant Dioxus updates (RefCell borrow) from Web Audio/vibration callbacks.
                            gloo_timers::future::TimeoutFuture::new(0).await;
                            point_button_feedback(is_end);
                        });
                    }
                    let mut err_out = action_error;
                    let mut current_point = current_point;
                    let mut stones_remaining = stones_remaining;
                    let mut state_signal = state_signal_start;
                    let set_type_stones = set_type_stones;
                    let default_set = default_set;
                    let stones_val = stones_remaining();
                    if let Some(point_id) = point_opt {
                        // End current point — defer optimistic update and API to next tick to avoid RefCell re-entrancy in Dioxus.
                        let end_iso = chrono::Utc::now().to_rfc3339();
                        let prev = state_signal().clone();
                        let point_id = point_id.clone();
                        let id_for_stones = id.clone();
                        let u_for_stones = u.clone();
                        spawn(async move {
                            gloo_timers::future::TimeoutFuture::new(0).await;
                            let mut final_stones_for_api: Option<u32> = None;
                            if let Some(Ok(ref state)) = prev.clone() {
                                let mut state = state.clone();
                                if let Some(points) =
                                    state.get_mut("points").and_then(|p| p.as_array_mut())
                                {
                                    for p in points.iter_mut() {
                                        if p.get("uuid").and_then(|v| v.as_str())
                                            == Some(point_id.as_str())
                                        {
                                            p["end_stamp"] = serde_json::json!(end_iso);
                                            if set_type_stones {
                                                let at_start = p
                                                    .get("stones_at_start")
                                                    .and_then(|s| s.as_u64())
                                                    .unwrap_or(0);
                                                let stamp = p.get("stamp").and_then(|s| s.as_str());
                                                let elapsed = stones_elapsed_beats_ms(
                                                    stamp,
                                                    Some(end_iso.as_str()),
                                                )
                                                    as u64;
                                                let remaining =
                                                    (at_start.saturating_sub(elapsed)) as u32;
                                                stones_remaining.set(Some(remaining));
                                                final_stones_for_api = Some(remaining);
                                            }
                                            break;
                                        }
                                    }
                                }
                                state_signal.set(Some(Ok(state)));
                            }
                            current_point.set(None);
                            err_out.set(None);
                            let body =
                                serde_json::json!({ "point_id": point_id, "end_stamp": end_iso });
                            match api::update_point(&u_for_stones, &point_id, &body).await {
                                Ok(_) => {
                                    err_out.set(None);
                                    if let Some(n) = final_stones_for_api {
                                        let _ =
                                            api::update_stones(&u_for_stones, &id_for_stones, n)
                                                .await;
                                    }
                                }
                                Err(e) => {
                                    err_out.set(Some(e));
                                    state_signal.set(prev);
                                    current_point.set(Some(point_id));
                                }
                            }
                        });
                    } else {
                        // Start new point — optimistic: add pending point, set current_point
                        let pending_id =
                            format!("pending-{}", chrono::Utc::now().timestamp_millis());
                        let stamp_iso = chrono::Utc::now().to_rfc3339();
                        let new_point = serde_json::json!({
                            "uuid": pending_id,
                            "set_number": default_set,
                            "winner": "none",
                            "rerolled": false,
                            "stamp": stamp_iso,
                            "end_stamp": serde_json::Value::Null,
                            "stones_at_start": if set_type_stones { serde_json::json!(stones_val) } else { serde_json::Value::Null },
                        });
                        let prev = state_signal().clone();
                        if let Some(Ok(ref state)) = prev.clone() {
                            let mut state = state.clone();
                            if let Some(points) =
                                state.get_mut("points").and_then(|p| p.as_array_mut())
                            {
                                points.push(new_point);
                            }
                            state_signal.set(Some(Ok(state)));
                        }
                        current_point.set(Some(pending_id.clone()));
                        trigger_point_row_flash(flash_point_id, pending_id.clone());
                        let stones_at_start = if set_type_stones { stones_val } else { None };
                        spawn(async move {
                            err_out.set(None);
                            match api::add_point(
                                &u,
                                &id,
                                default_set,
                                Some(chrono::Utc::now().timestamp_millis() as u64),
                                stones_at_start,
                            )
                            .await
                            {
                                Ok(resp) => {
                                    let real_id = resp
                                        .get("point_id")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("")
                                        .to_string();
                                    if !real_id.is_empty() {
                                        if let Some(Ok(ref state)) = state_signal() {
                                            let mut state = state.clone();
                                            if let Some(points) = state
                                                .get_mut("points")
                                                .and_then(|p| p.as_array_mut())
                                            {
                                                for p in points.iter_mut() {
                                                    if p.get("uuid").and_then(|v| v.as_str())
                                                        == Some(pending_id.as_str())
                                                    {
                                                        p["uuid"] = serde_json::json!(real_id);
                                                        break;
                                                    }
                                                }
                                            }
                                            state_signal.set(Some(Ok(state)));
                                        }
                                        if flash_point_id().as_deref() == Some(pending_id.as_str())
                                        {
                                            flash_point_id.set(Some(real_id.clone()));
                                        }
                                        current_point.set(Some(real_id));
                                    }
                                    err_out.set(None);
                                }
                                Err(e) => {
                                    err_out.set(Some(e));
                                    state_signal.set(prev);
                                    current_point.set(None);
                                }
                            }
                        });
                    }
                };

                let url_mobile = url.clone();
                let id_mobile = match_id.clone();
                let mut current_point_mobile = current_point;
                let mut stones_remaining_mobile = stones_remaining;
                let mut state_signal_mobile = state_signal;
                let on_start_end_point_mobile = move |_| {
                    let u = url_mobile.clone();
                    let id = id_mobile.clone();
                    let point_opt = current_point_mobile();
                    #[cfg(target_arch = "wasm32")]
                    {
                        let is_end = point_opt.is_some();
                        spawn(async move {
                            gloo_timers::future::TimeoutFuture::new(0).await;
                            point_button_feedback(is_end);
                        });
                    }
                    let mut err_out = action_error;
                    let mut current_point = current_point_mobile;
                    let mut stones_remaining = stones_remaining_mobile;
                    let mut state_signal = state_signal_mobile;
                    let set_type_stones = set_type_stones;
                    let default_set = default_set;
                    let stones_val = stones_remaining();
                    if let Some(point_id) = point_opt {
                        let end_iso = chrono::Utc::now().to_rfc3339();
                        let prev = state_signal().clone();
                        let point_id = point_id.clone();
                        let id_for_stones = id.clone();
                        let u_for_stones = u.clone();
                        spawn(async move {
                            gloo_timers::future::TimeoutFuture::new(0).await;
                            let mut final_stones_for_api: Option<u32> = None;
                            if let Some(Ok(ref state)) = prev.clone() {
                                let mut state = state.clone();
                                if let Some(points) =
                                    state.get_mut("points").and_then(|p| p.as_array_mut())
                                {
                                    for p in points.iter_mut() {
                                        if p.get("uuid").and_then(|v| v.as_str())
                                            == Some(point_id.as_str())
                                        {
                                            p["end_stamp"] = serde_json::json!(end_iso);
                                            if set_type_stones {
                                                let at_start = p
                                                    .get("stones_at_start")
                                                    .and_then(|s| s.as_u64())
                                                    .unwrap_or(0);
                                                let stamp = p.get("stamp").and_then(|s| s.as_str());
                                                let elapsed = stones_elapsed_beats_ms(
                                                    stamp,
                                                    Some(end_iso.as_str()),
                                                )
                                                    as u64;
                                                let remaining =
                                                    (at_start.saturating_sub(elapsed)) as u32;
                                                stones_remaining.set(Some(remaining));
                                                final_stones_for_api = Some(remaining);
                                            }
                                            break;
                                        }
                                    }
                                }
                                state_signal.set(Some(Ok(state)));
                            }
                            current_point.set(None);
                            err_out.set(None);
                            let body =
                                serde_json::json!({ "point_id": point_id, "end_stamp": end_iso });
                            match api::update_point(&u_for_stones, &point_id, &body).await {
                                Ok(_) => {
                                    err_out.set(None);
                                    if let Some(n) = final_stones_for_api {
                                        let _ =
                                            api::update_stones(&u_for_stones, &id_for_stones, n)
                                                .await;
                                    }
                                }
                                Err(e) => {
                                    err_out.set(Some(e));
                                    state_signal.set(prev);
                                    current_point.set(Some(point_id));
                                }
                            }
                        });
                    } else {
                        let pending_id =
                            format!("pending-{}", chrono::Utc::now().timestamp_millis());
                        let stamp_iso = chrono::Utc::now().to_rfc3339();
                        let new_point = serde_json::json!({
                            "uuid": pending_id,
                            "set_number": default_set,
                            "winner": "none",
                            "rerolled": false,
                            "stamp": stamp_iso,
                            "end_stamp": serde_json::Value::Null,
                            "stones_at_start": if set_type_stones { serde_json::json!(stones_val) } else { serde_json::Value::Null },
                        });
                        let prev = state_signal().clone();
                        if let Some(Ok(ref state)) = prev.clone() {
                            let mut state = state.clone();
                            if let Some(points) =
                                state.get_mut("points").and_then(|p| p.as_array_mut())
                            {
                                points.push(new_point);
                            }
                            state_signal.set(Some(Ok(state)));
                        }
                        current_point.set(Some(pending_id.clone()));
                        trigger_point_row_flash(flash_point_id, pending_id.clone());
                        let stones_at_start = if set_type_stones { stones_val } else { None };
                        spawn(async move {
                            err_out.set(None);
                            match api::add_point(
                                &u,
                                &id,
                                default_set,
                                Some(chrono::Utc::now().timestamp_millis() as u64),
                                stones_at_start,
                            )
                            .await
                            {
                                Ok(resp) => {
                                    let real_id = resp
                                        .get("point_id")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("")
                                        .to_string();
                                    if !real_id.is_empty() {
                                        if let Some(Ok(ref state)) = state_signal() {
                                            let mut state = state.clone();
                                            if let Some(points) = state
                                                .get_mut("points")
                                                .and_then(|p| p.as_array_mut())
                                            {
                                                for p in points.iter_mut() {
                                                    if p.get("uuid").and_then(|v| v.as_str())
                                                        == Some(pending_id.as_str())
                                                    {
                                                        p["uuid"] = serde_json::json!(real_id);
                                                        break;
                                                    }
                                                }
                                            }
                                            state_signal.set(Some(Ok(state)));
                                        }
                                        if flash_point_id().as_deref() == Some(pending_id.as_str())
                                        {
                                            flash_point_id.set(Some(real_id.clone()));
                                        }
                                        current_point.set(Some(real_id));
                                    }
                                    err_out.set(None);
                                }
                                Err(e) => {
                                    err_out.set(Some(e));
                                    state_signal.set(prev);
                                    current_point.set(None);
                                }
                            }
                        });
                    }
                };

                let url_finalize_btn = url.clone();
                let id_finalize_btn = match_id.clone();
                let url_finalize_mobile = url.clone();
                let id_finalize_mobile = match_id.clone();

                let has_in_progress = current_point().is_some();
                let point_button_text = if has_in_progress {
                    "End Point"
                } else {
                    "Start Point"
                };
                let point_button_class = if has_in_progress {
                    "btn btn-danger btn-lg w-100 mobile-sticky-button"
                } else {
                    "btn btn-success btn-lg w-100 mobile-sticky-button"
                };
                // Block starting a point until the stone count is known (can't record stones_at_start otherwise).
                let needs_stone_count = set_type_stones && !stones_known && !has_in_progress;
                let point_button_disabled = finalized || needs_stone_count;
                let point_button_text = if needs_stone_count {
                    "Set stone count to start"
                } else {
                    point_button_text
                };

                let run_match_css = ".set-number-controls{display:flex;flex-direction:column;align-items:center;gap:0;width:40px}\
                    .set-number-controls .set-number-display{font-size:1rem;font-weight:600;text-align:center;min-width:30px;padding:0}\
                    .set-number-controls button.set-step-btn{width:32px;height:26px;padding:0;font-size:13px;line-height:1;border:none;background:transparent;color:#6c757d;cursor:pointer;touch-action:manipulation}\
                    .set-number-controls button.set-step-btn:hover{color:#212529}\
                    .delete-point-btn{background:#dc3545;color:white;border:none;width:28px;height:28px;padding:0;border-radius:3px;display:flex;align-items:center;justify-content:center;font-size:16px;line-height:1;cursor:pointer}\
                    .delete-point-btn:hover{background:#c82333}\
                    .winner-cell{display:flex;flex-wrap:wrap;align-items:center;min-width:0}\
                    .winner-options-group{display:flex;flex-wrap:wrap;align-items:center;gap:6px}\
                    .winner-option{display:inline-flex;align-items:center;gap:5px;padding:5px 10px;border-radius:4px;border:1px solid #dee2e6;background:#fff;cursor:pointer;font-size:0.95rem;line-height:1.3;min-width:0}\
                    .winner-option:hover{background:#e9ecef}\
                    .winner-option.active{background:#0d6efd;color:#fff;border-color:#0d6efd}\
                    .winner-option .winner-avatar{width:26px;height:26px;border-radius:50%;object-fit:cover;flex-shrink:0}\
                    .winner-option .winner-name{min-width:3ch;max-width:12em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
                    #quick-timer{position:relative;flex:0 0 auto;min-width:3.5rem;padding:0 0.75rem;font-size:1.75rem;line-height:1;cursor:pointer;touch-action:none;border:1px solid #dee2e6;border-radius:0.375rem;background:#fff;color:#212529}\
                    #quick-timer:hover{background:#f8f9fa}\
                    #quick-timer.qt-dragging{z-index:2001;background:#fff;box-shadow:0 0 0 4px rgba(13,110,253,0.5)}\
                    #quick-timer-scrim{position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,0.6);pointer-events:none;display:flex;align-items:flex-start;justify-content:center}\
                    #quick-timer-scrim .qt-scrim-text{margin-top:20vh;color:#fff;text-align:center;font-size:1.5rem;line-height:1.5}\
                    #quick-timer-scrim .qt-scrim-title{font-size:2rem;font-weight:600}\
                    @keyframes point-row-flash-kf{0%{background-color:#adb5bd}100%{background-color:transparent}}\
                    #points-table tbody tr.point-row-flash>td{animation:point-row-flash-kf 1.1s ease-out both}\
                    #point-button,.mobile-button-wrapper .btn{animation:none!important}\
                    #points-table tr.point-row-no-border>td{border-bottom-width:0}\
                    @media (max-width:768px){.mobile-button-wrapper{position:fixed;bottom:0;left:0;right:0;z-index:1000;background:white;padding:0;margin:0;box-shadow:0 -2px 10px rgba(0,0,0,0.1);display:flex;flex-direction:row}\
                    #quick-timer{position:fixed;top:64px;right:8px;z-index:1030;min-width:3rem;height:3rem;padding:0 0.5rem;font-size:1.5rem;box-shadow:0 2px 6px rgba(0,0,0,0.2)}\
                    .mobile-button-wrapper .btn{border-radius:0;margin:0;flex:1;min-height:96px}\
                    .container .mobile-sticky-button,.container #finalize-match-btn{display:none}\
                    #score-stones-card .card-body{padding:0.5rem}\
                    #score-stones-card h4{font-size:1rem;margin-bottom:0.1rem!important}\
                    #score-stones-card #stones-remaining{font-size:2rem!important;height:40px!important;line-height:1!important}\
                    #score-stones-card .col-md-6.mb-4{margin-bottom:0.5rem!important}\
                    #score-by-set .row{margin-bottom:0!important}\
                    #score-by-set h4{font-size:1rem}\
                    #score-by-set strong,#score-by-set #time-elapsed{font-size:0.95rem}\
                    #score-by-set small{font-size:0.75rem}}";
                let mut state_signal = state_signal;
                let mut action_error = action_error;
                let set_type_stones_reroll = set_type_stones;
                let mut stones_remaining_reroll = stones_remaining;
                let point_table_rows: Vec<_> = point_rows
                    .iter()
                    .map(|r| {
                        let pt_id = r.point_id.as_str();
                        let point_index = r.index;
                        let is_flash = flash_point_id().as_deref() == Some(pt_id);
                        let winner_val = r.winner.as_str();
                        let rerolled = r.rerolled;
                        let elapsed = r.elapsed;
                        let u_winner = url.clone();
                        let u_reroll = url.clone();
                        let u_del = url.clone();
                        let u_notes = url.clone();
                        let id_notes = match_id.clone();
                        let id_reroll_match = match_id.clone();
                        let pid = r.point_id.clone();
                        let pid_winner = pid.clone();
                        let pid_reroll = pid.clone();
                        let pid_del = pid.clone();
                        let pid_list = pid.clone();
                        let team1_short = team1_short.to_string();
                        let team2_short = team2_short.to_string();
                        let team1_photo = team1_photo.clone();
                        let team2_photo = team2_photo.clone();
                        let base_url_winner = base_url.clone();
                        let pid_t1 = pid_winner.clone();
                        let u_t1 = u_winner.clone();
                        let pid_t2 = pid_winner.clone();
                        let u_t2 = u_winner.clone();
                        let winner_is_t1 = winner_val == "TEAM1";
                        let winner_is_t2 = winner_val == "TEAM2";
                        let notes_for_point = point_notes_map_signal().get(&pid_list).cloned().unwrap_or_default();
                        let has_notes = !notes_for_point.is_empty();
                        // When a point has penalties, drop the main row's bottom border so the grey
                        // separator falls below the penalties (which belong to this point), not above them.
                        let main_row_class = match (is_flash, has_notes) {
                            (true, true) => "point-row-flash point-row-no-border",
                            (true, false) => "point-row-flash",
                            (false, true) => "point-row-no-border",
                            (false, false) => "",
                        };
                        rsx! {
                            tr {
                                key: "{pt_id}",
                                id: "point-row-{pt_id}",
                                class: "{main_row_class}",
                                td { class: "text-muted text-center align-middle", "{point_index}" }
                                td {
                                    SetNumberControls {
                                        key: "set-{pt_id}",
                                        point_id: pid.clone(),
                                        url: url.clone(),
                                        state_signal: state_signal,
                                        action_error: action_error,
                                    }
                                }
                                td { class: "align-middle", id: "stones-{pt_id}", "{elapsed}" }
                                td {
                                    div { class: "winner-cell",
                                        div { class: "winner-options-group",
                                        button {
                                            class: format!("winner-option{}", if winner_val == "TEAM1" { " active" } else { "" }),
                                            r#type: "button",
                                            onclick: move |_| {
                                                let val = if winner_is_t1 { "none" } else { "TEAM1" };
                                                let prev = state_signal().clone();
                                                if let Some(Ok(ref state)) = prev.clone() {
                                                    let mut state = state.clone();
                                                    if let Some(points) = state.get_mut("points").and_then(|p| p.as_array_mut()) {
                                                        for p in points.iter_mut() {
                                                            if p.get("uuid").and_then(|v| v.as_str()) == Some(pid_t1.as_str()) {
                                                                p["winner"] = serde_json::json!(val);
                                                                break;
                                                            }
                                                        }
                                                    }
                                                    state_signal.set(Some(Ok(state)));
                                                }
                                                let u = u_t1.clone();
                                                let p = pid_t1.clone();
                                                let mut state_signal = state_signal;
                                                let mut action_error = action_error;
                                                spawn(async move {
                                                    let body = serde_json::json!({ "point_id": p, "winner": val });
                                                    match api::update_point(&u, &p, &body).await {
                                                        Ok(_) => { action_error.set(None); }
                                                        Err(e) => { action_error.set(Some(e)); state_signal.set(prev); }
                                                    }
                                                });
                                            },
                                            if let Some(ref ph) = team1_photo {
                                                img { class: "winner-avatar", src: "{base_url_winner}/static/{ph}", alt: "" }
                                            }
                                            span { class: "winner-name", "{team1_short}" }
                                        }
                                        button {
                                            class: format!("winner-option{}", if winner_val == "TEAM2" { " active" } else { "" }),
                                            r#type: "button",
                                            onclick: move |_| {
                                                let val = if winner_is_t2 { "none" } else { "TEAM2" };
                                                let prev = state_signal().clone();
                                                if let Some(Ok(ref state)) = prev.clone() {
                                                    let mut state = state.clone();
                                                    if let Some(points) = state.get_mut("points").and_then(|p| p.as_array_mut()) {
                                                        for p in points.iter_mut() {
                                                            if p.get("uuid").and_then(|v| v.as_str()) == Some(pid_t2.as_str()) {
                                                                p["winner"] = serde_json::json!(val);
                                                                break;
                                                            }
                                                        }
                                                    }
                                                    state_signal.set(Some(Ok(state)));
                                                }
                                                let u = u_t2.clone();
                                                let p = pid_t2.clone();
                                                let mut state_signal = state_signal;
                                                let mut action_error = action_error;
                                                spawn(async move {
                                                    let body = serde_json::json!({ "point_id": p, "winner": val });
                                                    match api::update_point(&u, &p, &body).await {
                                                        Ok(_) => { action_error.set(None); }
                                                        Err(e) => { action_error.set(Some(e)); state_signal.set(prev); }
                                                    }
                                                });
                                            },
                                            if let Some(ref ph) = team2_photo {
                                                img { class: "winner-avatar", src: "{base_url_winner}/static/{ph}", alt: "" }
                                            }
                                            span { class: "winner-name", "{team2_short}" }
                                        }
                                        }
                                    }
                                }
                                td { class: "align-middle",
                                    div { class: "form-check",
                                        input {
                                            class: "form-check-input",
                                            r#type: "checkbox",
                                            checked: rerolled,
                                            onchange: move |ev| {
                                                let checked = ev.checked();
                                                let prev = state_signal().clone();
                                                if let Some(Ok(ref state)) = prev.clone() {
                                                    let mut state = state.clone();
                                                    if let Some(points) = state.get_mut("points").and_then(|p| p.as_array_mut()) {
                                                        for p in points.iter_mut() {
                                                            if p.get("uuid").and_then(|v| v.as_str()) == Some(pid_reroll.as_str()) {
                                                                p["rerolled"] = serde_json::json!(checked);
                                                                break;
                                                            }
                                                        }
                                                    }
                                                    state_signal.set(Some(Ok(state)));
                                                }
                                                // Adjust stone counter: rerun = add this point's stones back, un-rerun = subtract.
                                                // Only when the count is known; otherwise just toggle the rerolled flag.
                                                let prev_stones = stones_remaining_reroll();
                                                let (stones_to_send, stones_prev) = match (set_type_stones_reroll, prev_stones) {
                                                    (true, Some(prev)) => {
                                                        let new_val = if checked {
                                                            prev + elapsed
                                                        } else {
                                                            prev.saturating_sub(elapsed)
                                                        };
                                                        stones_remaining_reroll.set(Some(new_val));
                                                        (Some(new_val), prev_stones)
                                                    }
                                                    _ => (None, prev_stones),
                                                };
                                                let u = u_reroll.clone();
                                                let id_reroll = id_reroll_match.clone();
                                                let p = pid_reroll.clone();
                                                let mut state_signal = state_signal;
                                                let mut action_error = action_error;
                                                let mut stones_remaining_reroll = stones_remaining_reroll;
                                                spawn(async move {
                                                    let body = serde_json::json!({ "point_id": p, "rerolled": checked });
                                                    match api::update_point(&u, &p, &body).await {
                                                        Ok(_) => {
                                                            action_error.set(None);
                                                            if let Some(n) = stones_to_send {
                                                                let _ = api::update_stones(&u, &id_reroll, n).await;
                                                            }
                                                        }
                                                        Err(e) => {
                                                            action_error.set(Some(e));
                                                            state_signal.set(prev);
                                                            if stones_to_send.is_some() {
                                                                stones_remaining_reroll.set(stones_prev);
                                                            }
                                                        }
                                                    }
                                                });
                                            },
                                        }
                                    }
                                }
                                td { class: "align-middle",
                                    div { class: "d-flex flex-column gap-1",
                                        {
                                            let pid_notes = pid.clone();
                                            let pid_penalties = pid.clone();
                                            let u_notes_btn = u_notes.clone();
                                            let id_notes_btn = id_notes.clone();
                                            rsx! {
                                                button {
                                                    class: "btn btn-sm btn-outline-secondary",
                                                    title: "Point Note",
                                                    onclick: move |_| {
                                                        notes_modal_point_id.set(Some(pid_notes.clone()));
                                                        notes_modal_notes.set(None);
                                                        notes_modal_new_text.set(String::new());
                                                        let u = u_notes_btn.clone();
                                                        let id = id_notes_btn.clone();
                                                        let pid_fetch = pid_notes.clone();
                                                        let mut notes_modal_notes = notes_modal_notes;
                                                        let mut notes_modal_new_text = notes_modal_new_text;
                                                        let mut point_notes_map_signal = point_notes_map_signal;
                                                        spawn(async move {
                                                            match api::get_point_notes(&u, &id, &pid_fetch).await {
                                                                Ok(v) => {
                                                                    let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                                    let point_note_text = notes.iter()
                                                                        .find(|n| n.get("target").and_then(|t| t.as_str()) == Some("match"))
                                                                        .and_then(|n| n.get("text").and_then(|t| t.as_str()))
                                                                        .unwrap_or("")
                                                                        .to_string();
                                                                    notes_modal_new_text.set(point_note_text);
                                                                    notes_modal_notes.set(Some(Ok(notes.clone())));
                                                                    let mut m = point_notes_map_signal();
                                                                    m.insert(pid_fetch.clone(), notes);
                                                                    point_notes_map_signal.set(m);
                                                                }
                                                                Err(e) => notes_modal_notes.set(Some(Err(e))),
                                                            }
                                                        });
                                                    },
                                                    "📝"
                                                }
                                                button {
                                                    class: "btn btn-sm btn-outline-danger",
                                                    title: "Penalties",
                                                    onclick: move |_| {
                                                        penalties_modal_point_id.set(Some(pid_penalties.clone()));
                                                        penalties_step.set(1);
                                                        penalties_selected_player_id.set(None);
                                                        penalties_selected_type.set(None);
                                                        penalties_other_text.set(String::new());
                                                        penalties_other_selected.set(false);
                                                        penalty_history_signal.set(None);
                                                        notes_modal_notes.set(None);
                                                        let u = u_notes.clone();
                                                        let id = id_notes.clone();
                                                        let pid_fetch = pid_penalties.clone();
                                                        let mut notes_modal_notes = notes_modal_notes;
                                                        let mut point_notes_map_signal = point_notes_map_signal;
                                                        spawn(async move {
                                                            match api::get_point_notes(&u, &id, &pid_fetch).await {
                                                                Ok(v) => {
                                                                    let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                                    notes_modal_notes.set(Some(Ok(notes.clone())));
                                                                    let mut m = point_notes_map_signal();
                                                                    m.insert(pid_fetch.clone(), notes);
                                                                    point_notes_map_signal.set(m);
                                                                }
                                                                Err(e) => notes_modal_notes.set(Some(Err(e))),
                                                            }
                                                        });
                                                    },
                                                    "🚩"
                                                }
                                            }
                                        }
                                    }
                                }
                                td { class: "align-middle",
                                    button {
                                        class: "delete-point-btn",
                                        title: "Delete",
                                        onclick: move |_| {
                                            #[cfg(target_arch = "wasm32")]
                                            {
                                                let confirmed = web_sys::window()
                                                    .and_then(|w| w.confirm_with_message("Delete this point?").ok())
                                                    .unwrap_or(false);
                                                if !confirmed {
                                                    return;
                                                }
                                            }
                                            let prev = state_signal().clone();
                                            if let Some(Ok(ref state)) = prev.clone() {
                                                let mut state = state.clone();
                                                if let Some(points) = state.get_mut("points").and_then(|p| p.as_array_mut()) {
                                                    points.retain(|p| p.get("uuid").and_then(|v| v.as_str()) != Some(pid_del.as_str()));
                                                }
                                                state_signal.set(Some(Ok(state)));
                                            }
                                            let u = u_del.clone();
                                            let p = pid_del.clone();
                                            let mut state_signal = state_signal;
                                            let mut action_error = action_error;
                                            spawn(async move {
                                                match api::delete_point(&u, &p).await {
                                                    Ok(_) => { action_error.set(None); }
                                                    Err(e) => {
                                                        action_error.set(Some(e));
                                                        state_signal.set(prev);
                                                    }
                                                }
                                            });
                                        },
                                        "×"
                                    }
                                }
                            }
                            if has_notes {
                                tr { key: "{pt_id}-notes",
                                    td { colspan: "7", class: "pt-0 pb-2",
                                        div { class: "d-flex flex-wrap gap-1",
                                            for note_val in notes_for_point.iter() {
                                                {
                                                    let note_text = note_val.get("text").and_then(|t| t.as_str()).unwrap_or("");
                                                    let target = note_val.get("target").and_then(|t| t.as_str()).unwrap_or("match");
                                                    let target_display = note_val.get("player_display").and_then(|p| p.as_str())
                                                        .or_else(|| note_val.get("player_name").and_then(|p| p.as_str()))
                                                        .unwrap_or(if target == "match" { "Point" } else { target })
                                                        .to_string();
                                                    let target_profile_id = note_val.get("player_id").and_then(|v| v.as_str()).map(String::from);
                                                    let pt_id = note_val.get("penalty_type_id").and_then(|v| v.as_i64()).map(|v| v as i32);
                                                    let penalty_info = if let Some(id) = pt_id {
                                                        d.penalty_types.iter().find(|t| t.id == id)
                                                    } else {
                                                        None
                                                    };
                                                    let (border_color, display_text) = if let Some(pt) = penalty_info {
                                                        (pt.color.clone(), pt.name.clone())
                                                    } else {
                                                        ("808080".to_string(), if note_text.is_empty() { "Other".to_string() } else { note_text.to_string() })
                                                    };
                                                    let description = penalty_info
                                                        .and_then(|pt| pt.desc.clone())
                                                        .filter(|s| !s.is_empty());

                                                    rsx! {
                                                        PenaltyDisplay {
                                                            border_color,
                                                            display_text,
                                                            description,
                                                            target_display: Some(target_display),
                                                            target_profile_id,
                                                            on_description_click: move |d: Option<String>| penalty_desc_modal.set(d),
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    })
                    .collect();

                // Quick timer: derive display + drive end-tone/transitions each tick.
                let _ = live_tick();
                let quick_timer_label: Option<String> = match quick_timer() {
                    QuickTimer::Idle => None,
                    QuickTimer::Dragging { start_stones } => Some(start_stones.to_string()),
                    QuickTimer::Running {
                        start_time,
                        start_stones,
                    } => {
                        #[cfg(target_arch = "wasm32")]
                        let now_server =
                            js_sys::Date::now() / 1000.0 + time_filter.read().get_mean();
                        #[cfg(not(target_arch = "wasm32"))]
                        let now_server = now_epoch_secs();
                        let current = quick_timer_current(start_time, start_stones, now_server);
                        if current <= -1 {
                            quick_timer.set(QuickTimer::Idle);
                            #[cfg(target_arch = "wasm32")]
                            quick_timer_fired_zero.set(false);
                            None
                        } else {
                            #[cfg(target_arch = "wasm32")]
                            {
                                if current == 0 && !quick_timer_fired_zero() {
                                    quick_timer_fired_zero.set(true);
                                    spawn(async move {
                                        gloo_timers::future::TimeoutFuture::new(0).await;
                                        play_timer_end_beeps();
                                    });
                                }
                            }
                            Some(current.max(0).to_string())
                        }
                    }
                };
                let quick_timer_is_idle = quick_timer_label.is_none();
                let quick_timer_dragging = matches!(quick_timer(), QuickTimer::Dragging { .. });
                // Single quick-timer element; CSS positions it (right of the Start Point button on
                // wide screens, fixed top-right corner on narrow). Rendered for all match types.
                // While dragging it gets `qt-dragging` to lift above the full-screen scrim.
                #[cfg(target_arch = "wasm32")]
                let quick_timer_el = rsx! {
                    div {
                        id: "quick-timer",
                        class: if quick_timer_dragging { "d-flex align-items-center justify-content-center user-select-none qt-dragging" } else { "d-flex align-items-center justify-content-center user-select-none" },
                        title: "Quick timer: drag to set",
                        onpointerdown: move |ev: Event<PointerData>| {
                            // Unlock Web Audio during this gesture so the end-of-timer beeps
                            // (which fire seconds later) can use a running AudioContext.
                            unlock_run_match_audio();
                            // Capture the pointer so move/up events keep arriving after the cursor
                            // leaves this small element; otherwise the drag stalls and never releases.
                            capture_quick_timer_pointer(ev.pointer_id());
                            let p = ev.page_coordinates();
                            quick_drag_start.set(Some((p.x, p.y)));
                            quick_timer.set(QuickTimer::Dragging { start_stones: 0 });
                        },
                        onpointermove: move |ev: Event<PointerData>| {
                            if let Some((sx, sy)) = quick_drag_start() {
                                let p = ev.page_coordinates();
                                let stones = drag_magnitude_to_stones(p.x - sx, p.y - sy);
                                quick_timer.set(QuickTimer::Dragging { start_stones: stones });
                            }
                        },
                        onpointerup: move |_| {
                            if quick_drag_start().is_some() {
                                quick_drag_start.set(None);
                                if let QuickTimer::Dragging { start_stones } = quick_timer() {
                                    if start_stones == 0 {
                                        quick_timer.set(QuickTimer::Idle);
                                    } else {
                                        // Re-prime audio on the release gesture that arms the timer.
                                        unlock_run_match_audio();
                                        let now_server = js_sys::Date::now() / 1000.0 + time_filter.read().get_mean();
                                        quick_timer_fired_zero.set(false);
                                        quick_timer.set(QuickTimer::Running { start_time: now_server, start_stones });
                                    }
                                }
                            }
                        },
                        onpointercancel: move |_| {
                            // Browser cancelled the gesture (e.g. scroll): abandon the in-progress
                            // drag without arming a timer, and clear the stale start point.
                            if quick_drag_start().is_some() {
                                quick_drag_start.set(None);
                                if matches!(quick_timer(), QuickTimer::Dragging { .. }) {
                                    quick_timer.set(QuickTimer::Idle);
                                }
                            }
                        },
                        if quick_timer_is_idle {
                            "⏱"
                        } else {
                            "{quick_timer_label.clone().unwrap_or_default()}"
                        }
                    }
                };
                #[cfg(not(target_arch = "wasm32"))]
                let quick_timer_el = rsx! {
                    div { id: "quick-timer", "⏱" }
                };

                rsx! {
                    div { class: "container mt-4",
                        style { "{run_match_css}" }
                        if quick_timer_dragging {
                            div { id: "quick-timer-scrim",
                                div { class: "qt-scrim-text",
                                    div { class: "qt-scrim-title", "Stone Timer Utility" }
                                    div { "drag to set time" }
                                }
                            }
                        }
                        div { class: "row",
                            div { class: "col-md-12",
                                h2 { "Run Match: {m.name}" }
                                div { class: "row d-none d-md-flex",
                                    div { class: "col-md-4",
                                        p { class: "mb-0",
                                            strong { "Teams: " }
                                            "{team1} vs {team2}"
                                        }
                                    }
                                    div { class: "col-md-4",
                                        p { class: "mb-0",
                                            strong { "Refs: " }
                                            "{refs_display}"
                                        }
                                    }
                                    div { class: "col-md-2",
                                        if let Some(len) = length_display {
                                            p { class: "mb-0",
                                                strong { "Length: " }
                                                "{len}"
                                            }
                                        }
                                    }
                                    div { class: "col-md-2",
                                        p { class: "mb-0",
                                            strong { "Field: " }
                                            "{field_display}"
                                        }
                                    }
                                }
                                div { class: "row d-md-none",
                                    div { class: "col-12",
                                        p { class: "small mb-0",
                                            "Refs: {refs_display}"
                                            if let Some(len) = m.nominal_length {
                                                " | {len}m"
                                            }
                                            " | Field: {field_display}"
                                        }
                                    }
                                }
                            }
                        }

                        div { class: "row mb-4",
                            div { class: "col-md-12",
                                div { class: "card", id: "score-stones-card",
                                    div { class: "card-body",
                                        div { class: "row",
                                            div { class: "col-md-6 mb-4 mb-md-0",
                                                div { id: "score-by-set",
                                                    div { class: "row mb-1 align-items-center",
                                                        div { class: "col-5 text-center",
                                                            small { class: "text-muted", "{team1}" }
                                                        }
                                                        div { class: "col-2 text-center",
                                                            span { id: "time-elapsed", "{time_elapsed_str}" }
                                                        }
                                                        div { class: "col-5 text-center",
                                                            small { class: "text-muted", "{team2}" }
                                                        }
                                                    }
                                                    if score_rows.is_empty() {
                                                        div { class: "text-muted text-center", "No points yet" }
                                                    } else {
                                                        for (set_num, t1, t2) in score_rows.iter() {
                                                            div { class: "row mb-0", key: "{set_num}",
                                                                div { class: "col-5 text-center",
                                                                    strong { id: "team1-set-{set_num}-score", "{t1}" }
                                                                }
                                                                div { class: "col-2 text-center",
                                                                    small { class: "text-muted", "Set {set_num}" }
                                                                }
                                                                div { class: "col-5 text-center",
                                                                    strong { id: "team2-set-{set_num}-score", "{t2}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                            div { class: "col-md-6",
                                                if set_type_stones {
                                                    div { class: "d-flex align-items-center justify-content-center gap-3",
                                                        div { class: "text-start",
                                                            h4 { class: "mb-0", "Stone Count" }
                                                            p { class: "small text-muted mb-0", "(click to edit)" }
                                                        }
                                                        input {
                                                            r#type: "number",
                                                            class: "form-control-plaintext text-center display-4 m-0 p-0",
                                                            id: "stones-remaining",
                                                            style: "width: 6ch; line-height: 1; font-size: 3rem; height: 64px; min-width: 6ch;",
                                                            value: "{stones_input_value}",
                                                            onfocus: move |_| {
                                                                stones_input_focused.set(true);
                                                                stones_edit_value.set(display_stones.map(|n| n.to_string()).unwrap_or_default());
                                                            },
                                                            onblur: move |_| {
                                                                let s = stones_edit_value();
                                                                if let Ok(n) = s.parse::<u32>() {
                                                                    stones_remaining.set(Some(n));
                                                                    let u = url_stones_blur.clone();
                                                                    let id = id_stones_blur.clone();
                                                                    spawn(async move {
                                                                        let _ = api::update_stones(&u, &id, n).await;
                                                                    });
                                                                }
                                                                stones_input_focused.set(false);
                                                            },
                                                            oninput: move |ev| {
                                                                let s = ev.value();
                                                                stones_edit_value.set(s.clone());
                                                                if let Ok(n) = s.parse::<u32>() {
                                                                    stones_remaining.set(Some(n));
                                                                    let u = url.clone();
                                                                    let id = match_id.clone();
                                                                    spawn(async move {
                                                                        let _ = api::update_stones(&u, &id, n).await;
                                                                    });
                                                                }
                                                            },
                                                        }
                                                    }
                                                }
                                                div { class: "row mt-3",
                                                    div { class: "col-12 d-flex align-items-stretch gap-2",
                                                        div { class: "flex-grow-1",
                                                            button {
                                                                id: "point-button",
                                                                class: "{point_button_class}",
                                                                onclick: on_start_end_point,
                                                                disabled: point_button_disabled,
                                                                "{point_button_text}"
                                                            }
                                                        }
                                                        {quick_timer_el}
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if let Some(e) = action_error() {
                            div { class: "alert alert-danger mt-2", "{e}" }
                        }

                        div { class: "row",
                            div { class: "col-md-12",
                                div { class: "card",
                                    div { class: "card-header",
                                        h5 { class: "mb-0", "Points" }
                                    }
                                    div { class: "card-body",
                                        div { class: "table-responsive",
                                            table { class: "table table-sm", id: "points-table",
                                                thead {
                                                    tr {
                                                        th { "#" }
                                                        th { "Set" }
                                                        th { "🪨" }
                                                        th { "Winner" }
                                                        th { "Rerun" }
                                                        th { "Notes" }
                                                        th { "" }
                                                    }
                                                }
                                                tbody { id: "points-table-body",
                                                    for node in point_table_rows.into_iter() {
                                                        {node}
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        div { class: "row mt-4",
                            div { class: "col-12",
                                button {
                                    id: "finalize-match-btn",
                                    class: "btn btn-warning btn-lg w-100",
                                    onclick: move |_| {
                                        navigator.push(Route::FinalizeMatch {
                                            url: url_finalize_btn.clone(),
                                            match_id: id_finalize_btn.clone(),
                                        });
                                    },
                                    disabled: has_in_progress,
                                    if has_in_progress {
                                        "Finalize Match (Point in Progress)"
                                    } else {
                                        "Finalize Match"
                                    }
                                }
                            }
                        }
                    }

                    div { class: "mobile-button-wrapper d-md-none",
                        if !has_in_progress {
                            button {
                                class: "btn btn-warning btn-lg",
                                onclick: move |_| {
                                    navigator.push(Route::FinalizeMatch {
                                        url: url_finalize_mobile.clone(),
                                        match_id: id_finalize_mobile.clone(),
                                    });
                                },
                                "Finalize Match"
                            }
                        }
                        button {
                            class: "{point_button_class}",
                            onclick: on_start_end_point_mobile,
                            disabled: point_button_disabled,
                            "{point_button_text}"
                        }
                    }

                    if show_notes_modal {
                        div {
                            class: "modal show",
                            style: "display: block; background: rgba(0,0,0,0.5);",
                            role: "dialog",
                            tabindex: "-1",
                            onclick: move |_| {
                                notes_modal_point_id.set(None);
                            },
                            div {
                                class: "modal-dialog modal-lg",
                                onclick: move |ev| { ev.stop_propagation(); },
                                div { class: "modal-content",
                                    div { class: "modal-header",
                                        h5 { class: "modal-title", "Notes for Point" }
                                        button {
                                            r#type: "button",
                                            class: "btn-close",
                                            aria_label: "Close",
                                            onclick: move |_| notes_modal_point_id.set(None),
                                        }
                                    }
                                    div { class: "modal-body",
                                        match notes_modal_notes().as_ref() {
                                            None => rsx! {
                                                div { class: "text-muted", "Loading…" }
                                            },
                                            Some(Err(e)) => rsx! {
                                                div { class: "text-danger", "{e}" }
                                            },
                                            Some(Ok(_)) => rsx! {
                                                label { class: "form-label", "Note for this point" }
                                                textarea {
                                                    class: "form-control",
                                                    rows: "3",
                                                    placeholder: "Enter note (optional)",
                                                    value: "{notes_modal_new_text()}",
                                                    oninput: move |ev| notes_modal_new_text.set(ev.value().clone()),
                                                }
                                            },
                                        }
                                    }
                                    div { class: "modal-footer",
                                        button {
                                            r#type: "button",
                                            class: "btn btn-secondary",
                                            onclick: move |_| notes_modal_point_id.set(None),
                                            "Close"
                                        }
                                        button {
                                            r#type: "button",
                                            class: "btn btn-primary",
                                            disabled: !notes_modal_notes().as_ref().is_some_and(|r| r.is_ok()),
                                            onclick: move |_| {
                                                let text = notes_modal_new_text().trim().to_string();
                                                let u = url_notes_submit.clone();
                                                let id = id_notes_submit.clone();
                                                let pid = notes_modal_pid.clone();
                                                let mut point_notes_map_signal = point_notes_map_signal;
                                                notes_modal_point_id.set(None);
                                                spawn(async move {
                                                    if api::set_point_note(&u, &id, &pid, &text).await.is_ok() {
                                                        if let Ok(v) = api::get_point_notes(&u, &id, &pid).await {
                                                            let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                            let mut m = point_notes_map_signal();
                                                            m.insert(pid, notes);
                                                            point_notes_map_signal.set(m);
                                                        }
                                                    }
                                                });
                                            },
                                            "Save"
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if show_penalties_modal {
                        {
                            let pid_close1 = penalties_modal_pid.clone();
                            let u_close1 = url_notes.clone();
                            let mid_close1 = id_notes.clone();
                            let pid_close2 = penalties_modal_pid.clone();
                            let u_close2 = url_notes.clone();
                            let mid_close2 = id_notes.clone();
                            let pid_close3 = penalties_modal_pid.clone();
                            let u_close3 = url_notes.clone();
                            let mid_close3 = id_notes.clone();
                        rsx! {
                        div {
                            class: "modal show",
                            style: "display: block; background: rgba(0,0,0,0.5);",
                            role: "dialog",
                            tabindex: "-1",
                            onclick: move |_| {
                                let pid = pid_close1.clone();
                                let u = u_close1.clone();
                                let mid = mid_close1.clone();
                                let mut pn_sig = point_notes_map_signal.clone();
                                penalties_modal_point_id.set(None);
                                spawn(async move {
                                    if let Ok(v) = api::get_point_notes(&u, &mid, &pid).await {
                                        let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                        let mut m = pn_sig();
                                        m.insert(pid, notes);
                                        pn_sig.set(m);
                                    }
                                });
                            },
                            div {
                                class: "modal-dialog modal-lg",
                                onclick: move |ev| { ev.stop_propagation(); },
                                div { class: "modal-content",
                                    div { class: "modal-header",
                                        h5 { class: "modal-title", "Penalties" }
                                        button {
                                            r#type: "button",
                                            class: "btn-close",
                                            aria_label: "Close",
                                            onclick: move |_| {
                                                let pid = pid_close2.clone();
                                                let u = u_close2.clone();
                                                let mid = mid_close2.clone();
                                                let mut pn_sig = point_notes_map_signal.clone();
                                                penalties_modal_point_id.set(None);
                                                spawn(async move {
                                                    if let Ok(v) = api::get_point_notes(&u, &mid, &pid).await {
                                                        let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                        let mut m = pn_sig();
                                                        m.insert(pid, notes);
                                                        pn_sig.set(m);
                                                    }
                                                });
                                            },
                                        }
                                    }
                                    div { class: "modal-body",
                                        if penalties_step() == 1 {
                                            {
                                                let pid_fetch_team1 = penalties_modal_pid.clone();
                                                let pid_fetch_team2 = penalties_modal_pid.clone();
                                                rsx! {
                                            div { class: "row",
                                                div { class: "col-6",
                                                    h6 { class: "mb-2 text-center", "{team1_notes}" }
                                                    div { class: "list-group", style: "max-height: 400px; overflow-y: auto;",
                                                        for p in d.match_players.iter().filter(|p| p.team_side.as_deref() == Some("team1")) {
                                                            {
                                                                let pid = p.player_id.clone();
                                                                let display = p.display.clone();
                                                                let base_url = api::base_url();
                                                                let u_hist = url_notes.clone();
                                                                let mid_hist = id_notes.clone();
                                                                let point_id_hist = pid_fetch_team1.clone();
                                                                rsx! {
                                                                    button {
                                                                        class: "list-group-item list-group-item-action d-flex align-items-center gap-2",
                                                                        onclick: move |_| {
                                                                            penalties_selected_player_id.set(Some(pid.clone()));
                                                                            penalties_selected_player_display.set(display.clone());
                                                                            penalties_step.set(2);
                                                                            let pid_fetch = pid.clone();
                                                                            let u = u_hist.clone();
                                                                            let mid = mid_hist.clone();
                                                                            let point_id_for_spawn = point_id_hist.clone();
                                                                            spawn(async move {
                                                                                if let Ok(res) = api::get_player_penalty_history(&u, &pid_fetch, &mid, &point_id_for_spawn).await {
                                                                                    penalty_history_signal.set(Some((pid_fetch, res.penalties)));
                                                                                } else {
                                                                                    penalty_history_signal.set(Some((pid_fetch, vec![])));
                                                                                }
                                                                            });
                                                                        },
                                                                        if let Some(ph) = &p.profile_photo {
                                                                            img { src: "{base_url}/static/{ph}", class: "rounded-circle", style: "width: 32px; height: 32px; object-fit: cover;" }
                                                                        }
                                                                        div {
                                                                            div { "{p.display}" }
                                                                            small { class: "text-muted", "{p.name}" }
                                                                        }
                                                                        if p.in_this_match {
                                                                            span { class: "badge bg-primary ms-auto", "Playing" }
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                                div { class: "col-6",
                                                    h6 { class: "mb-2 text-center", "{team2_notes}" }
                                                    div { class: "list-group", style: "max-height: 400px; overflow-y: auto;",
                                                        for p in d.match_players.iter().filter(|p| p.team_side.as_deref() == Some("team2")) {
                                                            {
                                                                let pid = p.player_id.clone();
                                                                let display2 = p.display.clone();
                                                                let base_url = api::base_url();
                                                                let u_hist2 = url_notes.clone();
                                                                let mid_hist2 = id_notes.clone();
                                                                let point_id_hist2 = pid_fetch_team2.clone();
                                                                rsx! {
                                                                    button {
                                                                        class: "list-group-item list-group-item-action d-flex align-items-center gap-2",
                                                                        onclick: move |_| {
                                                                            penalties_selected_player_id.set(Some(pid.clone()));
                                                                            penalties_selected_player_display.set(display2.clone());
                                                                            penalties_step.set(2);
                                                                            let pid_fetch = pid.clone();
                                                                            let u = u_hist2.clone();
                                                                            let mid = mid_hist2.clone();
                                                                            let point_id_for_spawn2 = point_id_hist2.clone();
                                                                            spawn(async move {
                                                                                if let Ok(res) = api::get_player_penalty_history(&u, &pid_fetch, &mid, &point_id_for_spawn2).await {
                                                                                    penalty_history_signal.set(Some((pid_fetch, res.penalties)));
                                                                                } else {
                                                                                    penalty_history_signal.set(Some((pid_fetch, vec![])));
                                                                                }
                                                                            });
                                                                        },
                                                                        if let Some(ph) = &p.profile_photo {
                                                                            img { src: "{base_url}/static/{ph}", class: "rounded-circle", style: "width: 32px; height: 32px; object-fit: cover;" }
                                                                        }
                                                                        div {
                                                                            div { "{p.display}" }
                                                                            small { class: "text-muted", "{p.name}" }
                                                                        }
                                                                        if p.in_this_match {
                                                                            span { class: "badge bg-primary ms-auto", "Playing" }
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                                }
                                            }
                                        } else if let Some(player) = d.match_players.iter().find(|p| Some(&p.player_id) == penalties_selected_player_id().as_ref()) {
                                            {
                                                let history_for_player: Option<Vec<_>> = penalty_history_signal().as_ref().and_then(|(pid, list)| if pid == &player.player_id { Some(list.clone()) } else { None });
                                                let penalty_color_by_name: std::collections::HashMap<String, String> = d.penalty_types.iter().map(|t| (t.name.clone(), t.color.clone())).collect();
                                                rsx! {
                                            div {
                                                div { class: "d-flex align-items-center gap-3 mb-3 p-3 border rounded bg-light",
                                                    if let Some(ph) = &player.profile_photo {
                                                        img { src: "{api::base_url()}/static/{ph}", class: "rounded-circle", style: "width: 64px; height: 64px; object-fit: cover;" }
                                                    }
                                                    div {
                                                        h5 { class: "mb-0", "{player.display}" }
                                                        div { class: "text-muted", "{player.name}" }
                                                    }
                                                }
                                                h6 { class: "mb-2", "Penalty history this tournament" }
                                                div { class: "table-responsive mb-3",
                                                    table { class: "table table-sm table-striped mb-0",
                                                        thead { class: "table-light",
                                                            tr {
                                                                th { "Penalty type" }
                                                                th { "Match" }
                                                                th { "Date" }
                                                                th { "" }
                                                                th { class: "text-end", style: "min-width: 2.5rem;", "" }
                                                            }
                                                        }
                                                        tbody {
                                                            if let Some(list) = history_for_player {
                                                                for item in list.iter() {
                                                                    {
                                                                        let row_color = penalty_color_by_name.get(&item.penalty_type_name).cloned().unwrap_or_else(|| "808080".to_string());
                                                                        // is_current_point: penalty belongs to the point row from which Penalties was clicked
                                                                        let can_delete = item.is_current_point && item.note_uuid.is_some();
                                                                        let note_uuid_owned = item.note_uuid.clone().unwrap_or_default();
                                                                        let u_del = url_notes.clone();
                                                                        let mid_del = id_notes.clone();
                                                                        let pid_del = penalties_modal_pid.clone();
                                                                        let pl_id_del = player.player_id.clone();
                                                                        let mut ph_sig = penalty_history_signal.clone();
                                                                        let mut pn_sig = point_notes_map_signal.clone();
                                                                        rsx! {
                                                                            tr {
                                                                                td { style: format!("border-left: 8px solid #{}; background-color: #{}22;", row_color, row_color), "{item.penalty_type_name}" }
                                                                                td { "{item.match_name}" }
                                                                                td { "{item.date}" }
                                                                                td {
                                                                                    if item.is_current_match {
                                                                                        span { class: "badge bg-info", "current match" }
                                                                                    }
                                                                                }
                                                                                td { class: "text-end align-middle",
                                                                                    if can_delete && !note_uuid_owned.is_empty() {
                                                                                        button {
                                                                                            r#type: "button",
                                                                                            class: "btn btn-sm btn-outline-danger border-danger",
                                                                                            style: "min-width: 2rem; font-size: 1.1rem; line-height: 1;",
                                                                                            title: "Remove penalty from this point",
                                                                                            onclick: move |_| {
                                                                                                let uuid = note_uuid_owned.clone();
                                                                                                let u = u_del.clone();
                                                                                                let mid = mid_del.clone();
                                                                                                let pid = pid_del.clone();
                                                                                                let pl_id = pl_id_del.clone();
                                                                                                spawn(async move {
                                                                                                    if api::delete_point_note(&u, &uuid).await.is_ok() {
                                                                                                        if let Ok(v) = api::get_point_notes(&u, &mid, &pid).await {
                                                                                                            let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                                                                            let mut m = pn_sig();
                                                                                                            m.insert(pid.clone(), notes.clone());
                                                                                                            pn_sig.set(m);
                                                                                                        }
                                                                                                        if let Ok(res) = api::get_player_penalty_history(&u, &pl_id, &mid, &pid).await {
                                                                                                            ph_sig.set(Some((pl_id, res.penalties)));
                                                                                                        }
                                                                                                    }
                                                                                                });
                                                                                            },
                                                                                            "×"
                                                                                        }
                                                                                    } else {
                                                                                        span { class: "text-muted", "—" }
                                                                                    }
                                                                                }
                                                                            }
                                                                        }
                                                                    }
                                                                }
                                                            } else {
                                                                tr {
                                                                    td { colspan: "5", class: "text-muted text-center", "Loading…" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                                h6 { class: "mb-2", "Select penalty type" }
                                                div { class: "d-flex flex-wrap gap-2 mb-3",
                                                    for pt in d.penalty_types.iter() {
                                                        {
                                                            let pt_id = pt.id;
                                                            let selected = penalties_selected_type() == Some(pt_id);
                                                            let pt_desc = pt.desc.clone().filter(|s| !s.is_empty());
                                                            let show_pt_help = pt_desc.is_some();
                                                            rsx! {
                                                                button {
                                                                    class: if selected { "btn btn-dark" } else { "btn btn-outline-dark" },
                                                                    style: if selected { format!("background-color: #{}; border-color: #{};", pt.color, pt.color) } else { format!("color: black; border-color: #{}; background-color: white;", pt.color) },
                                                                    onclick: move |_| {
                                                                        if penalties_selected_type() == Some(pt_id) {
                                                                            penalties_selected_type.set(None);
                                                                        } else {
                                                                            penalties_selected_type.set(Some(pt_id));
                                                                            penalties_other_selected.set(false);
                                                                        }
                                                                    },
                                                                    "{pt.name}"
                                                                    if show_pt_help {
                                                                        span {
                                                                            class: "ms-1 cursor-pointer d-inline-flex align-items-center",
                                                                            title: "Description",
                                                                            onclick: move |ev| {
                                                                                ev.stop_propagation();
                                                                                penalty_desc_modal.set(pt_desc.clone());
                                                                            },
                                                                            img {
                                                                                src: "/static/question-mark.svg",
                                                                                alt: "?",
                                                                                style: "width: 14px; height: 14px; filter: invert(27%) sepia(51%) saturate(2878%) hue-rotate(224deg); vertical-align: middle;",
                                                                            }
                                                                        }
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    {
                                                        let selected = penalties_other_selected();
                                                        rsx! {
                                                            button {
                                                                class: if selected { "btn btn-secondary" } else { "btn btn-outline-secondary" },
                                                                onclick: move |_| {
                                                                    if selected {
                                                                        penalties_other_selected.set(false);
                                                                    } else {
                                                                        penalties_other_selected.set(true);
                                                                        penalties_selected_type.set(None);
                                                                    }
                                                                },
                                                                "Other"
                                                            }
                                                        }
                                                    }
                                                }
                                                if penalties_other_selected() {
                                                    div { class: "mb-3",
                                                        label { "Other penalty description" }
                                                        input {
                                                            class: "form-control",
                                                            value: "{penalties_other_text()}",
                                                            oninput: move |ev| penalties_other_text.set(ev.value().clone()),
                                                            placeholder: "Describe penalty..."
                                                        }
                                                    }
                                                }
                                            }
                                                }
                                            }
                                        }
                                    }
                                    {
                                        let pid_for_add = penalties_modal_pid.clone();
                                        rsx! {
                                    div { class: "modal-footer",
                                        if penalties_step() == 2 {
                                            button {
                                                class: "btn btn-outline-secondary me-auto",
                                                onclick: move |_| {
                                                    penalties_step.set(1);
                                                    penalties_selected_type.set(None);
                                                    penalties_other_selected.set(false);
                                                    penalties_other_text.set(String::new());
                                                },
                                                "Back"
                                            }
                                            button {
                                                class: "btn btn-primary",
                                                onclick: move |_| {
                                                    let selected_pt = penalties_selected_type();
                                                    let other = penalties_other_selected();
                                                    let other_text = penalties_other_text();
                                                    let pid = pid_for_add.clone();
                                                    let mid = id_notes.clone();
                                                    let pl_id = penalties_selected_player_id().clone();
                                                    let u = url_notes.clone();
                                                    if selected_pt.is_none() && !other { return; }
                                                    if other && other_text.trim().is_empty() { return; }
                                                    if let Some(player_id) = pl_id {
                                                        let u2 = u.clone();
                                                        let mid2 = mid.clone();
                                                        let pid2 = pid.clone();
                                                        let mut point_notes_map_signal = point_notes_map_signal;
                                                        let mut notes_modal_notes = notes_modal_notes;
                                                        let mut penalty_history_signal = penalty_history_signal;
                                                        spawn(async move {
                                                            let ok = if let Some(pt_id) = selected_pt {
                                                                api::add_point_note(&u2, &mid2, &pid2, "", "player", Some(&player_id), Some(pt_id)).await.is_ok()
                                                            } else {
                                                                api::add_point_note(&u2, &mid2, &pid2, &other_text, "player", Some(&player_id), None).await.is_ok()
                                                            };
                                                            if ok {
                                                                if let Ok(v) = api::get_point_notes(&u2, &mid2, &pid2).await {
                                                                    let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                                    let mut m = point_notes_map_signal();
                                                                    m.insert(pid2.clone(), notes.clone());
                                                                    point_notes_map_signal.set(m);
                                                                    notes_modal_notes.set(Some(Ok(notes)));
                                                                }
                                                                if let Ok(res) = api::get_player_penalty_history(&u2, &player_id, &mid2, &pid2).await {
                                                                    penalty_history_signal.set(Some((player_id.clone(), res.penalties)));
                                                                }
                                                            }
                                                        });
                                                        penalties_selected_type.set(None);
                                                        penalties_other_selected.set(false);
                                                        penalties_other_text.set(String::new());
                                                    }
                                                },
                                                "Add penalty"
                                            }
                                        }
                                        button {
                                            r#type: "button",
                                            class: "btn btn-secondary",
                                            onclick: move |_| {
                                                let pid = pid_close3.clone();
                                                let u = u_close3.clone();
                                                let mid = mid_close3.clone();
                                                let mut pn_sig = point_notes_map_signal.clone();
                                                penalties_modal_point_id.set(None);
                                                spawn(async move {
                                                    if let Ok(v) = api::get_point_notes(&u, &mid, &pid).await {
                                                        let notes = v.get("notes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                                                        let mut m = pn_sig();
                                                        m.insert(pid, notes);
                                                        pn_sig.set(m);
                                                    }
                                                });
                                            },
                                            "Close"
                                        }
                                        }
                                    }
                                        }
                                    }
                                }
                            }
                        }
                        }
                    }
                    if penalty_desc_modal().is_some() {
                        div { class: "modal show", style: "display: block;",
                            div { class: "modal-dialog modal-dialog-centered",
                                div { class: "modal-content",
                                    div { class: "modal-header",
                                        h5 { class: "modal-title", "Penalty description" }
                                        button { r#type: "button", class: "btn-close", onclick: move |_| penalty_desc_modal.set(None) }
                                    }
                                    div { class: "modal-body", "{penalty_desc_modal().as_ref().unwrap_or(&String::new())}" }
                                    div { class: "modal-footer",
                                        button { r#type: "button", class: "btn btn-secondary", onclick: move |_| penalty_desc_modal.set(None), "Close" }
                                    }
                                }
                            }
                        }
                        div { class: "modal-backdrop show" }
                    }
                }
            }
            Some(Err(e)) => rsx! { p { class: "text-danger", "{e}" } },
            None => rsx! { p { "Loading…" } },
        },
        Err(_) => rsx! { p { "Loading…" } },
    };
    rsx! { {detail_view} }
}

#[cfg(test)]
mod quick_timer_tests {
    use super::{drag_magnitude_to_stones, quick_timer_current};

    #[test]
    fn magnitude_snaps_to_multiples_of_five() {
        assert_eq!(drag_magnitude_to_stones(0.0, 0.0), 0);
        // 12px per 5 stones; ~6px (half step) rounds to 5.
        assert_eq!(drag_magnitude_to_stones(6.5, 0.0), 5);
        // 24px -> 10, direction-independent (use y, and negatives).
        assert_eq!(drag_magnitude_to_stones(0.0, -24.0), 10);
        // 3-4-5 triangle: magnitude 60px -> 25 stones.
        assert_eq!(drag_magnitude_to_stones(36.0, 48.0), 25);
        // Small jitter below half a step stays 0 (tap cancel).
        assert_eq!(drag_magnitude_to_stones(3.0, 0.0), 0);
    }

    #[test]
    fn current_counts_down_on_beat_boundaries() {
        // start at t=0.0 with 20 stones.
        assert_eq!(quick_timer_current(0.0, 20, 0.0), 20);
        // just before first beat boundary (1.5s) -> still 20.
        assert_eq!(quick_timer_current(0.0, 20, 1.49), 20);
        // crossed one boundary -> 19.
        assert_eq!(quick_timer_current(0.0, 20, 1.5), 19);
        // 30s -> 20 boundaries -> 0.
        assert_eq!(quick_timer_current(0.0, 20, 30.0), 0);
        // one more beat past zero -> -1.
        assert_eq!(quick_timer_current(0.0, 20, 31.5), -1);
    }

    #[test]
    fn current_uses_floor_of_both_endpoints() {
        // start mid-beat: flooring both endpoints means elapsed counts
        // global boundaries crossed, not raw elapsed / 1.5.
        assert_eq!(quick_timer_current(0.4, 5, 1.6), 4);
    }
}
