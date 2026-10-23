//! Stones Player: globally synchronized stones using a Bayesian filter for time offset.

use crate::api;
use crate::components::PageIntro;
use crate::types::StonesResponse;
use dioxus::prelude::*;

#[path = "stones_counter.rs"]
mod counter;

#[cfg(target_arch = "wasm32")]
use std::cell::RefCell;
#[cfg(target_arch = "wasm32")]
use std::collections::{HashMap, HashSet};
#[cfg(target_arch = "wasm32")]
use std::rc::Rc;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast;

const BEAT_INTERVAL: f64 = 1.5;
// Kept short so that after a calibration change clears the queue, the loop
// re-queues quickly and the new timing lands on the next stone.
const SCHEDULE_INTERVAL_MS: u32 = 200;
const SCHEDULE_AHEAD_SEC: f64 = 7.0;
const MIN_GAP_SEC: f64 = 1.0;
// Visual metronome: how long the flash stays lit, and a floor gap afterward so
// we never busy-loop if the next boundary computes as essentially now.
const BEAT_FLASH_MS: u32 = 120;
const BEAT_MIN_GAP_MS: u32 = 50;
// Step 2 of calibration: after resetting the estimate, poll this often and wait
// at most this long for it to settle before letting the user tune the offset.
const STEP2_POLL_MS: u32 = 100;
const STEP2_MAX_SYNC_MS: u32 = 3000;

#[component]
pub fn Stones() -> Element {
    let stones_data =
        use_resource(move || async move { api::stones_list().await.map_err(|e| e.to_string()) });
    let stones_val = stones_data.value();

    #[cfg(target_arch = "wasm32")]
    {
        rsx! {
            StonesPlayerWasm {
                stones_val: stones_val.clone(),
            }
        }
    }

    #[cfg(not(target_arch = "wasm32"))]
    {
        rsx! {
            div { class: "container mt-4 stones-page",
                h1 { "Stones Player" }
                p { "Use the web (WASM) build for the synchronized Stones player." }
            }
        }
    }
}

#[cfg(target_arch = "wasm32")]
#[component]
fn StonesPlayerWasm(stones_val: ReadSignal<Option<Result<StonesResponse, String>>>) -> Element {
    let mut is_playing = use_signal(|| false);
    let mut starting = use_signal(|| false);
    let mut previewing = use_signal(|| false);
    let mut sound_loading = use_signal(|| false);
    let mut is_muted = use_signal(|| false);
    let mut online = use_signal(|| {
        web_sys::window()
            .map(|w| w.navigator().on_line())
            .unwrap_or(false)
    });
    let mut custom_name = use_signal(|| Option::<String>::None);
    let mut request_generation = use_signal(|| 0u64);
    let mut stone_counter = use_signal(counter::StoneCounter::default);
    let mut selected_index = use_signal(|| 0usize);
    let mut time_sync = crate::time_sync::use_time_sync();
    let mut custom_status = use_signal(|| Option::<String>::None);
    // Pulses true (briefly) on each server beat, driving the visual metronome
    // shown inside the calibration walkthrough.
    let beat_flash = use_signal(|| false);
    // Calibration walkthrough: 0 = closed, 1 = audio-delay step, 2 = clock-offset step.
    let mut calibration_step = use_signal(|| 0u8);
    let mut calibration_snapshot = use_signal(|| crate::time_sync::SyncSnapshot {
        calibration: crate::time_sync::Calibration::default(),
        mean_ms: 0.0,
        variance_ms2: 0.0,
    });
    let step2_syncing = use_signal(|| false);

    let audio_ctx = use_signal(|| Option::<web_sys::AudioContext>::None);
    let mut audio_buffer = use_signal(|| Option::<web_sys::AudioBuffer>::None);
    let mut custom_buffer = use_signal(|| Option::<web_sys::AudioBuffer>::None);
    let ctx_start_time = use_signal(|| Option::<f64>::None);
    let schedule_state = use_signal(|| {
        Rc::new(RefCell::new(ScheduleState {
            times: HashSet::new(),
            sources: HashMap::new(),
            media_dest: None,
        }))
    });
    // Bytes and decoded buffers for every built-in stone. Filled once when the
    // file list arrives, then play / pause / sound switches use this copy.
    let stone_library = use_signal(|| Rc::new(RefCell::new(StoneLibrary::default())));

    // Download and decode every built-in stone as soon as the list is known,
    // so later play and sound changes do not touch the network.
    use_effect(move || {
        let filenames = {
            let guard = stones_val.read();
            let Some(Ok(data)) = guard.as_ref() else {
                return;
            };
            data.stones
                .iter()
                .map(|stone| stone.filename_encoded.clone())
                .collect::<Vec<_>>()
        };
        let library = stone_library.read().clone();
        spawn(async move {
            let ctx = match ensure_audio_context(audio_ctx, ctx_start_time, &library) {
                Ok(ctx) => ctx,
                Err(_) => return,
            };
            for name in filenames {
                let _ = cached_audio_buffer(&ctx, &name, &library).await;
            }
        });
    });

    // One scheduler lives for this component's lifetime. Pause/resume never
    // starts another loop while the previous one is still waking up.
    use_effect(move || {
        spawn(async move {
            let schedule_rc = schedule_state.read().clone();
            // Only clear on manual calibration edits — not on every filter mean
            // update from probing (that re-churned the MediaStream and pitch-bent).
            let mut last_audio = time_sync.calibration().audio_delay_ms;
            let mut last_locked = time_sync.calibration().locked_offset_ms;
            let mut last_lock = time_sync.calibration().offset_locked;
            loop {
                let ctx = audio_ctx.read().clone();
                let buf = audio_buffer.read().clone();
                if let (true, Some(ref ctx), Some(ref buf)) = (is_playing(), ctx, buf) {
                    let cal = time_sync.calibration();
                    if cal.audio_delay_ms != last_audio
                        || cal.locked_offset_ms != last_locked
                        || cal.offset_locked != last_lock
                    {
                        clear_future_scheduled(schedule_rc.clone(), ctx);
                        last_audio = cal.audio_delay_ms;
                        last_locked = cal.locked_offset_ms;
                        last_lock = cal.offset_locked;
                    }
                    let audio_now = ctx.current_time();
                    let server_now = time_sync.server_now_secs();
                    let audio_delay = time_sync.audio_delay_secs();
                    let mut next_server_beat =
                        crate::time_sync::next_beat_boundary(server_now, BEAT_INTERVAL);
                    let mut scheduled = 0u32;
                    while next_server_beat - server_now <= SCHEDULE_AHEAD_SEC && scheduled < 5 {
                        if !is_playing() {
                            break;
                        }
                        let delay = next_server_beat - server_now;
                        if delay > 0.05 && delay < 60.0 {
                            // Signed audio offset: positive schedules later, negative earlier.
                            let audio_time = audio_now + delay + audio_delay;
                            if schedule_sound_at(
                                ctx.clone(),
                                buf.clone(),
                                audio_time,
                                schedule_rc.clone(),
                            ) {
                                scheduled += 1;
                            }
                        }
                        next_server_beat += BEAT_INTERVAL;
                    }
                }
                gloo_timers::future::TimeoutFuture::new(SCHEDULE_INTERVAL_MS).await;
            }
        });
    });

    // Visual beat loop: pulse `beat_flash` on every server beat (clock-offset
    // adjusted grid, so it moves with the offset slider). Drives the metronome
    // dot shown inside the calibration walkthrough.
    use_effect(move || {
        let mut beat_flash = beat_flash;
        spawn(async move {
            loop {
                let server_now = time_sync.server_now_secs();
                let next_beat = crate::time_sync::next_beat_boundary(server_now, BEAT_INTERVAL);
                let wait_ms = ((next_beat - server_now) * 1000.0).max(0.0);
                gloo_timers::future::TimeoutFuture::new(wait_ms.ceil() as u32 + 1).await;
                online.set(
                    web_sys::window()
                        .map(|w| w.navigator().on_line())
                        .unwrap_or(false),
                );
                if is_playing() {
                    stone_counter
                        .write()
                        .advance(time_sync.server_now_secs(), time_sync.quality().offset_ms);
                }
                beat_flash.set(true);
                gloo_timers::future::TimeoutFuture::new(BEAT_FLASH_MS).await;
                beat_flash.set(false);
                gloo_timers::future::TimeoutFuture::new(BEAT_MIN_GAP_MS).await;
            }
        });
    });

    use_effect(move || {
        if calibration_step() != 2 {
            return;
        }
        let mut time_sync = time_sync;
        let mut step2_syncing = step2_syncing;
        spawn(async move {
            time_sync.begin_clock_calibration();
            step2_syncing.set(true);
            let mut waited = 0u32;
            while waited < STEP2_MAX_SYNC_MS {
                if time_sync.quality().converged {
                    break;
                }
                gloo_timers::future::TimeoutFuture::new(STEP2_POLL_MS).await;
                waited += STEP2_POLL_MS;
            }
            step2_syncing.set(false);
        });
    });

    let on_stop = use_callback(move |_: ()| {
        request_generation += 1;
        starting.set(false);
        previewing.set(false);
        sound_loading.set(false);
        is_playing.set(false);
        stone_counter
            .write()
            .pause(time_sync.server_now_secs(), time_sync.quality().offset_ms);
        stop_all_scheduled(schedule_state.read().clone());
        pause_audio_stream_element();
        set_media_session_playing(false);
    });

    let on_start = use_callback(move |preview: bool| {
        if starting() || previewing() || sound_loading() || is_playing() {
            return;
        }
        custom_status.set(None);
        let stones_opt = stones_val
            .read()
            .as_ref()
            .and_then(|r| r.as_ref().ok())
            .cloned();
        let idx = selected_index();
        let stones_len = stones_opt.as_ref().map(|s| s.stones.len()).unwrap_or(0);
        if idx >= stones_len && custom_buffer.read().is_none() {
            custom_status.set(Some("Upload a custom MP3 first.".into()));
            return;
        }
        let library = stone_library.read().clone();
        let ctx =
            match prepare_playback_context(audio_ctx, ctx_start_time, schedule_state, &library) {
                Ok(ctx) => ctx,
                Err(msg) => {
                    custom_status.set(Some(msg));
                    return;
                }
            };
        set_audio_stream_muted(is_muted());
        let Some(play_promise) = play_audio_stream_element() else {
            custom_status.set(Some("Could not start audio. Try again.".into()));
            return;
        };
        // Attach rejection handling immediately, before any other await.
        let playback = wasm_bindgen_futures::JsFuture::from(play_promise);
        request_generation += 1;
        let request_id = request_generation();
        if preview {
            previewing.set(true);
        } else {
            starting.set(true);
        }
        spawn(async move {
            let result = load_playback_audio(
                &ctx,
                custom_buffer,
                stones_opt,
                idx,
                schedule_state,
                library,
                request_generation,
                request_id,
                playback,
            )
            .await;
            if request_generation() != request_id {
                return;
            }
            match result {
                Ok(buffer) => {
                    audio_buffer.set(Some(buffer.clone()));
                    custom_status.set(None);
                    if preview {
                        stop_all_scheduled(schedule_state.read().clone());
                        if !schedule_sound_at(
                            ctx.clone(),
                            buffer.clone(),
                            ctx.current_time() + 0.02,
                            schedule_state.read().clone(),
                        ) {
                            custom_status
                                .set(Some("Could not preview this sound. Try again.".into()));
                        } else {
                            gloo_timers::future::TimeoutFuture::new(
                                (buffer.duration() * 1000.0) as u32 + 80,
                            )
                            .await;
                        }
                        if request_generation() == request_id {
                            previewing.set(false);
                            pause_audio_stream_element();
                        }
                    } else {
                        stone_counter
                            .write()
                            .start(time_sync.server_now_secs(), time_sync.quality().offset_ms);
                        starting.set(false);
                        is_playing.set(true);
                        set_media_session_playing(true);
                    }
                }
                Err(message) => {
                    custom_status.set(Some(message));
                    starting.set(false);
                    previewing.set(false);
                    pause_audio_stream_element();
                    set_media_session_playing(false);
                }
            }
        });
    });

    let on_play_pause = use_callback(move |_: ()| {
        if is_playing() {
            on_stop.call(());
        } else {
            on_start.call(false);
        }
    });

    let on_reset = move |_| {
        on_stop.call(());
        stone_counter.write().reset();
        custom_status.set(None);
    };

    let on_mute = move |_| {
        let muted = !is_muted();
        is_muted.set(muted);
        set_audio_stream_muted(muted);
    };

    // Stop already queued WebAudio nodes as well as the stream when navigating
    // away. Dioxus cancels this component's pending async tasks on unmount.
    let cleanup_schedule = schedule_state.read().clone();
    let cleanup_library = stone_library.read().clone();
    use_drop(move || {
        stop_all_scheduled(cleanup_schedule);
        pause_audio_stream_element();
        set_media_session_playing(false);
        if let Some(ctx) = cleanup_library.borrow().context.as_ref() {
            let _ = ctx.close();
        }
    });

    let on_reset_sync = move |_| {
        time_sync.reset_sync();
    };

    let on_open_calibration = move |_| {
        calibration_snapshot.set(time_sync.snapshot());
        calibration_step.set(1);
    };

    let on_calibration_next = move |_| {
        let step = calibration_step();
        if step == 1 {
            calibration_step.set(2);
        } else if step >= 2 {
            time_sync.set_offset_ms(time_sync.quality().offset_ms);
            calibration_step.set(0);
        }
    };

    let on_calibration_cancel = move |_| {
        let snap = *calibration_snapshot.read();
        time_sync.restore_snapshot(snap);
        calibration_step.set(0);
    };

    let mut nudge_audio_delay = move |delta_ms: f64| {
        let cur = time_sync.calibration().audio_delay_ms;
        let next = (cur + delta_ms).clamp(-750.0, 750.0);
        time_sync.set_audio_delay_ms(next);
        set_range_input_value("stones-audio-offset", -next);
    };

    let mut nudge_clock_offset = move |delta_ms: f64| {
        let cur = time_sync.quality().offset_ms;
        let next = (cur + delta_ms).clamp(-750.0, 750.0);
        time_sync.set_offset_ms(next);
        set_range_input_value("stones-clock-offset", next);
    };

    let stones_ok = stones_val
        .read()
        .as_ref()
        .and_then(|r| r.as_ref().ok())
        .cloned();
    let stones_len = stones_ok.as_ref().map(|s| s.stones.len()).unwrap_or(0);
    let quality = time_sync.quality();
    let calibration = time_sync.calibration();
    let offset_str = format!("{:.1} ms", quality.offset_ms);
    let offset_locked = calibration.offset_locked;
    let var_str = format!("{:.1} ms\u{00B2}", quality.variance_ms2);
    let converged_str = if quality.converged { "yes" } else { "not yet" };
    let rtt_display = match quality.rtt_ms {
        Some(ms) => format!("{:.1} ms", ms),
        None => "-".to_string(),
    };
    let clock_offset_val = format!("{:.0}", quality.offset_ms);
    let audio_delay_val = format!("{:.0}", calibration.audio_delay_ms);
    // Mirrored so the range track matches endpoint labels (left=later, right=earlier).
    let audio_slider_val = format!("{:.0}", -calibration.audio_delay_ms);

    let current_stone = stone_counter.read().count();
    let count_time = stone_counter.read().elapsed_label();
    let (block_start, block_count) = stone_counter.read().block_progress();
    let block_end = block_start.saturating_add(100);
    let sound_value = if selected_index() >= stones_len {
        "custom".to_string()
    } else {
        selected_index().to_string()
    };
    let sound_options = stones_ok
        .as_ref()
        .map(|s| s.stones.clone())
        .unwrap_or_default();
    let sync_label = if !online() {
        "Offline · local timing"
    } else if offset_locked {
        "Manual clock offset"
    } else if quality.converged && quality.rtt_ms.is_some() {
        "Synced with server"
    } else {
        "Synchronizing…"
    };
    let sync_ready = online() && !offset_locked && quality.converged && quality.rtt_ms.is_some();

    rsx! {
        div { class: "container stones-page",
            div { class: "row",
                div { class: "col-12",
                    PageIntro { page_key: "stones", title: "Stones", focus_target: "stones-start-pause",
                        "Stones mark time during a jugger match, with a beat every 1.5 seconds."
                    }
                    div { class: "card stones-player-card",
                        div { class: "card-body",
                            div { class: "stones-counter-panel",
                                h2 { class: "h5", "Current stone" }
                                p { class: if sync_ready { "stones-sync-status is-ready" } else { "stones-sync-status" }, role: "status",
                                    i { class: if sync_ready { "fas fa-circle-check" } else if !online() { "fas fa-cloud-arrow-down" } else { "fas fa-clock" }, aria_hidden: "true" }
                                    "{sync_label}"
                                }
                                div { id: "stones-count", class: "stones-count", "{current_stone}" }
                                p { class: "stones-count-time",
                                    span { id: "stones-count-time", "{count_time}" }
                                    span { class: "stones-count-time-label", "Count time" }
                                }
                                div { class: "stones-progress-wrap",
                                    progress { id: "stones-progress", class: "stones-progress", max: "100", value: "{block_count}", aria_label: "Progress through this 100-stone block", "{block_count} / 100" }
                                    div { class: "stones-progress-labels",
                                        span { "{block_start}" }
                                        span { "{block_end} stones" }
                                    }
                                }
                                div { class: "stones-playback",
                                    button {
                                        id: "stones-start-pause", r#type: "button",
                                        class: if is_playing() { "btn btn-warning" } else { "btn btn-primary" },
                                        disabled: !is_playing() && (starting() || previewing() || sound_loading()),
                                        onclick: move |_| on_play_pause.call(()),
                                        i { class: if is_playing() { "fas fa-pause" } else { "fas fa-play" }, aria_hidden: "true" }
                                        if starting() { "Starting…" } else if is_playing() { "Pause" } else { "Start" }
                                    }
                                    button {
                                        id: "stones-reset", r#type: "button", class: "btn btn-outline-secondary",
                                        onclick: on_reset,
                                        i { class: "fas fa-rotate-left", aria_hidden: "true" }
                                        "Reset"
                                    }
                                    button {
                                        id: "stones-mute", r#type: "button", class: "btn btn-outline-secondary stones-mute",
                                        aria_label: "Mute sound", aria_pressed: is_muted(),
                                        title: if is_muted() { "Unmute sound" } else { "Mute sound" },
                                        onclick: on_mute,
                                        i { class: if is_muted() { "fas fa-volume-xmark" } else { "fas fa-volume-high" }, aria_hidden: "true" }
                                    }
                                }
                                p { class: "stones-count-help", "1 stone = 1.5 seconds. The count continues past 100." }
                            }
                            div { class: "stones-sound-picker",
                                label { class: "form-label", r#for: "stones-sound", "Sound" }
                                div { class: "stones-sound-controls",
                                    select {
                                        id: "stones-sound", class: "form-select", value: "{sound_value}",
                                        disabled: starting() || previewing() || sound_loading(),
                                        onchange: move |evt| {
                                            let idx = evt.value().parse::<usize>().unwrap_or(stones_len);
                                            request_generation += 1;
                                            let request_id = request_generation();
                                            custom_status.set(None);
                                            selected_index.set(idx);
                                            if let Some(ctx) = audio_ctx.read().clone() {
                                                clear_future_scheduled(schedule_state.read().clone(), &ctx);
                                            }
                                            if idx >= stones_len {
                                                audio_buffer.set(custom_buffer.read().clone());
                                                if custom_buffer.read().is_none() { on_stop.call(()); }
                                                return;
                                            }
                                            let filename = stones_val.read().as_ref().and_then(|r| r.as_ref().ok()).and_then(|s| s.stones.get(idx)).map(|s| s.filename_encoded.clone());
                                            let Some(filename) = filename else { return; };
                                            let library = stone_library.read().clone();
                                            let ctx = match ensure_audio_context(audio_ctx, ctx_start_time, &library) {
                                                Ok(ctx) => ctx,
                                                Err(message) => {
                                                    on_stop.call(());
                                                    custom_status.set(Some(message));
                                                    return;
                                                }
                                            };
                                            audio_buffer.set(None);
                                            sound_loading.set(true);
                                            spawn(async move {
                                                let result = cached_audio_buffer(&ctx, &filename, &library).await;
                                                if request_generation() != request_id { return; }
                                                sound_loading.set(false);
                                                match result {
                                                    Ok(buffer) => audio_buffer.set(Some(buffer)),
                                                    Err(message) => {
                                                        on_stop.call(());
                                                        custom_status.set(Some(format!("Could not load this sound: {message}")));
                                                    }
                                                }
                                            });
                                        },
                                        for (idx, sound) in sound_options.iter().enumerate() {
                                            option { value: "{idx}", "{sound.display_name}" }
                                        }
                                        option { value: "custom", "Custom MP3" }
                                    }
                                    button {
                                        id: "stones-preview", r#type: "button", class: "btn btn-outline-secondary",
                                        disabled: is_playing() || starting() || previewing() || sound_loading(),
                                        title: if is_playing() { "Pause the stones to preview a sound" } else { "Preview one sound" },
                                        onclick: move |_| on_start.call(true),
                                        i { class: "fas fa-play", aria_hidden: "true" }
                                        if previewing() { "Previewing…" } else { "Preview" }
                                    }
                                }
                                if sound_loading() { p { class: "form-text", role: "status", "Loading sound…" } }
                                if selected_index() >= stones_len {
                                    div { class: "mt-3",
                                        label { class: "form-label", r#for: "stones-custom-file", "Custom MP3" }
                                        input {
                                            id: "stones-custom-file", r#type: "file", class: "form-control",
                                            accept: "audio/mpeg,audio/mp3,.mp3", disabled: starting() || previewing() || sound_loading(),
                                            onchange: move |_| {
                                                let file = web_sys::window().and_then(|w| w.document())
                                                    .and_then(|d| d.get_element_by_id("stones-custom-file"))
                                                    .and_then(|el| el.dyn_into::<web_sys::HtmlInputElement>().ok())
                                                    .and_then(|input| input.files()).and_then(|files| files.get(0));
                                                let Some(file) = file else { return; };
                                                let library = stone_library.read().clone();
                                                let ctx = match ensure_audio_context(audio_ctx, ctx_start_time, &library) {
                                                    Ok(ctx) => ctx,
                                                    Err(message) => { custom_status.set(Some(message)); return; }
                                                };
                                                request_generation += 1;
                                                let request_id = request_generation();
                                                let name = file.name();
                                                sound_loading.set(true);
                                                custom_status.set(None);
                                                spawn(async move {
                                                    let result = decode_custom_file(file, &ctx).await;
                                                    if request_generation() != request_id { return; }
                                                    sound_loading.set(false);
                                                    match result {
                                                        Ok(buffer) => {
                                                            clear_future_scheduled(schedule_state.read().clone(), &ctx);
                                                            custom_name.set(Some(format!("{} ({:.2} s)", name, buffer.duration())));
                                                            custom_buffer.set(Some(buffer.clone()));
                                                            audio_buffer.set(Some(buffer));
                                                        }
                                                        Err(message) => custom_status.set(Some(message)),
                                                    }
                                                });
                                            }
                                        }
                                        p { class: "form-text", "Use a file under 1.5 seconds with no silence at the start." }
                                        if let Some(name) = custom_name() { p { class: "form-text", "Loaded: {name}" } }
                                    }
                                }
                                if let Some(message) = custom_status() { p { class: "text-danger form-text", role: "alert", "{message}" } }
                            }

                            div { class: "mb-3",
                                p { class: "form-text mb-1", "Something doesn't look or sound right?" }
                                button {
                                    class: "btn btn-outline-secondary",
                                    onclick: on_open_calibration,
                                    "Calibrate manually"
                                }
                            }

                            details { class: "stones-details mb-3",
                                summary { "Tips for speakers and Bluetooth" }
                                ul { class: "mt-3 mb-0",
                                    li { "Stand an equal distance from speakers when comparing the beat. Sound takes about 110 ms to cross a 40 m field." }
                                    li { "Allow 3–5 stones for devices to synchronize." }
                                    li { "Bluetooth can delay sound by up to about 250 ms. Use ‘Calibrate manually’ to compensate." }
                                    li { "For a custom sound, use an MP3 under 1.5 seconds with no silence at the start." }
                                }
                            }

                        audio {
                            id: "audio-stream",
                            autoplay: true,
                            preload: "auto",
                            muted: is_muted(),
                            style: "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:-9999px;bottom:0;",
                        }

                            details { class: "stones-details mb-3",
                                summary { "Advanced synchronization details" }
                                p { class: "form-text mt-2", "These diagnostics help troubleshoot audio timing. Most players can leave the automatic settings on." }
                                button { id: "stones-resync", r#type: "button", class: "btn btn-outline-secondary mb-3", onclick: on_reset_sync, "Resync" }
                                p { class: "form-text", "Resync clears manual audio and clock offsets." }
                                p { "Offset (x\u{0302}): {offset_str}" }
                                div { class: "form-check mb-2",
                                    input {
                                        id: "stones-offset-lock",
                                        class: "form-check-input",
                                        r#type: "checkbox",
                                        checked: offset_locked,
                                        onchange: move |evt| {
                                            time_sync.set_offset_locked(evt.checked());
                                        },
                                    }
                                    label {
                                        class: "form-check-label",
                                        r#for: "stones-offset-lock",
                                        if offset_locked { "Offset locked (filter paused)" }
                                        else { "Lock offset (stop auto-adjust)" }
                                    }
                                }
                                p { "Variance (P): {var_str}" }
                                p { "Converged: {converged_str}" }
                                p { "Round trip time: {rtt_display}" }
                            }
                        }
                    }
                }
            }
        }

        if let Some(Err(e)) = stones_val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        }

        if calibration_step() != 0 {
            div { class: "modal show", style: "display: block;", role: "dialog", aria_modal: "true", aria_label: "Calibrate stone timing",
                div { class: "modal-dialog modal-dialog-centered",
                    div { class: "modal-content",
                        div { class: "modal-header",
                            h5 { class: "modal-title",
                                if calibration_step() == 1 { "Calibrate — Step 1 of 2: Audio offset" }
                                else { "Calibrate — Step 2 of 2: Clock offset" }
                            }
                            button { r#type: "button", class: "btn-close", aria_label: "Close calibration", onclick: on_calibration_cancel }
                        }
                        div { class: "modal-body",
                            div { class: "d-flex flex-column align-items-center mb-3",
                                div {
                                    class: if beat_flash() { "stones-beat-dot stones-beat-dot-on" } else { "stones-beat-dot" },
                                }
                                span { class: "form-text mt-2 text-center",
                       "this should light up when " strong {
                       if calibration_step()==1 { "this device plays" }
                       else { "other devices play" }
                       } " a stone"
                }
                            }

                            if calibration_step() == 1 {
                                p {
                                    "Press Start, then drag the slider until the stones you hear from "
                                    strong { "this device" }
                                    " line up with the flashing indicator above."
                                }
                                p { class: "form-text",
                                    "If you're not playing stones from this device, you can skip this step — just press Next."
                                }
                                div { class: "mb-3",
                                    button {
                                        r#type: "button",
                                        class: if is_playing() { "btn btn-warning" } else { "btn btn-primary" },
                                        onclick: move |_| on_play_pause.call(()),
                                        disabled: !is_playing() && (starting() || previewing() || sound_loading()),
                                        if starting() { "Starting…" } else if is_playing() { "Pause" } else { "Start" }
                                    }
                                }
                                label { class: "form-label d-flex justify-content-between",
                                    span { "Audio offset" }
                                    strong { class: "ms-2", "{audio_delay_val} ms" }
                                }
                                // Slider is mirrored vs stored ms so left = later, right = earlier.
                                input {
                                    id: "stones-audio-offset",
                                    r#type: "range",
                                    class: "form-range",
                                    min: "-750",
                                    max: "750",
                                    step: "10",
                                    value: "{audio_slider_val}",
                                    onmounted: move |evt: Event<MountedData>| {
                                        set_input_value_on_mount(
                                            &evt.data(),
                                            -time_sync.calibration().audio_delay_ms,
                                        );
                                    },
                                    oninput: move |evt| {
                                        if let Ok(v) = evt.value().parse::<f64>() {
                                            time_sync.set_audio_delay_ms(-v);
                                        }
                                    },
                                }
                                div { class: "d-flex justify-content-between align-items-center mt-1",
                                    button {
                                        r#type: "button",
                                        class: "btn btn-sm btn-outline-secondary",
                                        onclick: move |_| nudge_audio_delay(10.0),
                                        "+10 ms"
                                    }
                                    span { class: "form-text", "play later ← → play earlier" }
                                    button {
                                        r#type: "button",
                                        class: "btn btn-sm btn-outline-secondary",
                                        onclick: move |_| nudge_audio_delay(-10.0),
                                        "-10 ms"
                                    }
                                }
                            } else {
                                if step2_syncing() {
                                    div { class: "d-flex align-items-center gap-2 mb-2",
                                        div { class: "spinner-border spinner-border-sm", role: "status" }
                                        span { "Re-syncing the clock… (up to 3 seconds)" }
                                    }
                                } else {
                                    p {
                                        "Drag the slider until the flashing indicator lines up with the stones playing from "
                                        strong { "another already-synced device" }
                                        " nearby."
                                    }
                                    p { class: "form-text",
                                        "This adjusts this device's whole sense of server time, so it also fixes the live stone counter when you run a match."
                                    }
                                    div { class: "mb-3",
                                        button {
                                            r#type: "button",
                                            class: if is_playing() { "btn btn-warning" } else { "btn btn-primary" },
                                            onclick: move |_| on_play_pause.call(()),
                                            disabled: !is_playing() && (starting() || previewing() || sound_loading()),
                                            if starting() { "Starting…" } else if is_playing() { "Pause" } else { "Start" }
                                        }
                                    }
                                    label { class: "form-label d-flex justify-content-between",
                                        span { "Clock offset" }
                                        strong { class: "ms-2", "{clock_offset_val} ms" }
                                    }
                                    input {
                                        id: "stones-clock-offset",
                                        r#type: "range",
                                        class: "form-range",
                                        min: "-750",
                                        max: "750",
                                        step: "10",
                                        value: "{clock_offset_val}",
                                        onmounted: move |evt: Event<MountedData>| {
                                            set_input_value_on_mount(&evt.data(), time_sync.quality().offset_ms);
                                        },
                                        oninput: move |evt| {
                                            if let Ok(v) = evt.value().parse::<f64>() {
                                                time_sync.set_offset_ms(v);
                                            }
                                        },
                                    }
                                    div { class: "d-flex justify-content-between align-items-center mt-1",
                                        button {
                                            r#type: "button",
                                            class: "btn btn-sm btn-outline-secondary",
                                            onclick: move |_| nudge_clock_offset(-10.0),
                                            "-10 ms"
                                        }
                                        span { class: "form-text", "earlier ← → later" }
                                        button {
                                            r#type: "button",
                                            class: "btn btn-sm btn-outline-secondary",
                                            onclick: move |_| nudge_clock_offset(10.0),
                                            "+10 ms"
                                        }
                                    }
                                }
                            }
                        }
                        div { class: "modal-footer",
                            button { r#type: "button", class: "btn btn-secondary", onclick: on_calibration_cancel, "Cancel" }
                            button {
                                r#type: "button",
                                class: "btn btn-primary",
                                disabled: calibration_step() == 2 && step2_syncing(),
                                onclick: on_calibration_next,
                                if calibration_step() == 1 { "Next" } else { "Done" }
                            }
                        }
                    }
                }
            }
            div { class: "modal-backdrop show" }
        }
    }
}

#[cfg(target_arch = "wasm32")]
struct ScheduleState {
    times: HashSet<i64>,
    sources: HashMap<i64, web_sys::AudioBufferSourceNode>,
    media_dest: Option<web_sys::MediaStreamAudioDestinationNode>,
}

/// Built-in stone files kept in memory after the first download.
#[cfg(target_arch = "wasm32")]
struct StoneLibrary {
    context: Option<web_sys::AudioContext>,
    bytes: HashMap<String, Vec<u8>>,
    buffers: HashMap<String, web_sys::AudioBuffer>,
    /// Filenames whose download or decode is already running.
    inflight: HashSet<String>,
}

#[cfg(target_arch = "wasm32")]
impl Default for StoneLibrary {
    fn default() -> Self {
        Self {
            context: None,
            bytes: HashMap::new(),
            buffers: HashMap::new(),
            inflight: HashSet::new(),
        }
    }
}

#[cfg(target_arch = "wasm32")]
fn ensure_audio_context(
    mut audio_ctx: Signal<Option<web_sys::AudioContext>>,
    mut ctx_start_time: Signal<Option<f64>>,
    library: &Rc<RefCell<StoneLibrary>>,
) -> Result<web_sys::AudioContext, String> {
    let existing = audio_ctx.read().clone();
    let needs_new = match &existing {
        None => true,
        Some(ctx) => ctx.state() == web_sys::AudioContextState::Closed,
    };
    if !needs_new {
        return Ok(existing.unwrap());
    }
    // Decoded buffers belong to the context that created them.
    library.borrow_mut().buffers.clear();
    let opts = web_sys::AudioContextOptions::new();
    let ctx = web_sys::AudioContext::new_with_context_options(&opts)
        .map_err(|_| "Could not create audio context. Try again.".to_string())?;
    ctx_start_time.set(Some(js_sys::Date::now() / 1000.0));
    audio_ctx.set(Some(ctx.clone()));
    library.borrow_mut().context = Some(ctx.clone());
    Ok(ctx)
}

#[cfg(target_arch = "wasm32")]
fn prepare_playback_context(
    audio_ctx: Signal<Option<web_sys::AudioContext>>,
    ctx_start_time: Signal<Option<f64>>,
    schedule_state: Signal<Rc<RefCell<ScheduleState>>>,
    library: &Rc<RefCell<StoneLibrary>>,
) -> Result<web_sys::AudioContext, String> {
    let existing = audio_ctx.read().clone();
    let needs_new = match &existing {
        None => true,
        Some(ctx) => ctx.state() == web_sys::AudioContextState::Closed,
    };
    if needs_new {
        schedule_state.read().borrow_mut().media_dest = None;
    }
    let ctx = ensure_audio_context(audio_ctx, ctx_start_time, library)?;
    if ctx.state() != web_sys::AudioContextState::Running {
        let _ = ctx.resume();
    }
    ensure_media_destination(&ctx, schedule_state, needs_new);
    if schedule_state.read().borrow().media_dest.is_none() {
        return Err("Could not connect audio output. Try again.".into());
    }
    Ok(ctx)
}

#[cfg(target_arch = "wasm32")]
fn ensure_media_destination(
    ctx: &web_sys::AudioContext,
    mut schedule_state: Signal<Rc<RefCell<ScheduleState>>>,
    force_rebind: bool,
) {
    use wasm_bindgen::JsCast;
    use wasm_bindgen::JsValue;

    let stream_ok = schedule_state
        .read()
        .borrow()
        .media_dest
        .as_ref()
        .and_then(|d| {
            js_sys::Reflect::get(&JsValue::from(d.clone()), &JsValue::from_str("stream")).ok()
        })
        .and_then(|s| s.dyn_into::<web_sys::MediaStream>().ok())
        .map(|s| s.active())
        .unwrap_or(false);

    if schedule_state.read().borrow().media_dest.is_some() && stream_ok && !force_rebind {
        return;
    }

    let dest = match ctx.create_media_stream_destination() {
        Ok(d) => d,
        Err(_) => return,
    };

    if let Some(window) = web_sys::window() {
        if let Some(doc) = window.document() {
            if let Some(el) = doc.get_element_by_id("audio-stream") {
                if let Ok(audio) = el.dyn_into::<web_sys::HtmlAudioElement>() {
                    if let Ok(stream) = js_sys::Reflect::get(
                        &JsValue::from(dest.clone()),
                        &JsValue::from_str("stream"),
                    ) {
                        let _ =
                            js_sys::Reflect::set(&audio, &JsValue::from_str("srcObject"), &stream);
                    }
                    audio.set_autoplay(true);
                    let _ = js_sys::Reflect::set(
                        &audio,
                        &JsValue::from_str("playsInline"),
                        &JsValue::TRUE,
                    );
                }
            }
        }
    }

    schedule_state.write().borrow_mut().media_dest = Some(dest);
}

#[cfg(target_arch = "wasm32")]
fn pause_audio_stream_element() {
    if let Some(window) = web_sys::window() {
        if let Some(doc) = window.document() {
            if let Some(el) = doc.get_element_by_id("audio-stream") {
                if let Ok(audio) = el.dyn_into::<web_sys::HtmlAudioElement>() {
                    let _ = audio.pause();
                }
            }
        }
    }
}

#[cfg(target_arch = "wasm32")]
fn play_audio_stream_element() -> Option<js_sys::Promise> {
    web_sys::window()?
        .document()?
        .get_element_by_id("audio-stream")?
        .dyn_into::<web_sys::HtmlAudioElement>()
        .ok()?
        .play()
        .ok()
}

#[cfg(target_arch = "wasm32")]
fn set_media_session_playing(playing: bool) {
    use wasm_bindgen::JsCast;
    use wasm_bindgen::JsValue;

    let Some(window) = web_sys::window() else {
        return;
    };
    let nav = window.navigator();
    let Ok(session) = js_sys::Reflect::get(&nav, &JsValue::from_str("mediaSession")) else {
        return;
    };
    if session.is_undefined() || session.is_null() {
        return;
    }
    if playing {
        if let Ok(ctor) = js_sys::Reflect::get(&window, &JsValue::from_str("MediaMetadata")) {
            if let Ok(ctor_fn) = ctor.dyn_into::<js_sys::Function>() {
                let init = js_sys::Object::new();
                let _ = js_sys::Reflect::set(
                    &init,
                    &JsValue::from_str("title"),
                    &JsValue::from_str("Stones"),
                );
                let _ = js_sys::Reflect::set(
                    &init,
                    &JsValue::from_str("artist"),
                    &JsValue::from_str("Arctos"),
                );
                let _ = js_sys::Reflect::set(
                    &init,
                    &JsValue::from_str("album"),
                    &JsValue::from_str("Synchronized Stones"),
                );
                if let Ok(meta) = js_sys::Reflect::construct(&ctor_fn, &js_sys::Array::of1(&init)) {
                    let _ = js_sys::Reflect::set(&session, &JsValue::from_str("metadata"), &meta);
                }
            }
        }
        let _ = js_sys::Reflect::set(
            &session,
            &JsValue::from_str("playbackState"),
            &JsValue::from_str("playing"),
        );
    } else {
        let _ = js_sys::Reflect::set(
            &session,
            &JsValue::from_str("playbackState"),
            &JsValue::from_str("paused"),
        );
    }
}

#[cfg(target_arch = "wasm32")]
fn schedule_sound_at(
    ctx: web_sys::AudioContext,
    buf: web_sys::AudioBuffer,
    audio_time: f64,
    state_rc: Rc<RefCell<ScheduleState>>,
) -> bool {
    let rounded_ms = (audio_time * 1000.0).round() as i64;
    let min_gap_ms = (MIN_GAP_SEC * 1000.0) as i64;
    {
        let state = state_rc.borrow();
        for &t_ms in state.times.iter() {
            if (t_ms - rounded_ms).abs() < min_gap_ms {
                return false;
            }
        }
    }
    let current = ctx.current_time();
    let delay = audio_time - current;
    if delay <= 0.0 || delay >= 60.0 {
        return false;
    }
    let source = match ctx.create_buffer_source() {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = source.set_buffer(Some(&buf));
    {
        let state = state_rc.borrow();
        if let Some(ref media_dest) = state.media_dest {
            if source.connect_with_audio_node(media_dest).is_err() {
                return false;
            }
        } else {
            // All audible output must pass through the same muteable element.
            return false;
        }
    }
    if source.start_with_when(audio_time).is_err() {
        let _ = source.disconnect();
        return false;
    }
    let state_rc2 = state_rc.clone();
    let rounded2 = rounded_ms;
    let ended_source = source.clone();
    let closure = wasm_bindgen::closure::Closure::once(Box::new(move || {
        let mut state = state_rc2.borrow_mut();
        // A stopped source can finish after a replacement was queued at the
        // same beat. Its callback must not remove that replacement's guard.
        if state.sources.get(&rounded2) == Some(&ended_source) {
            state.sources.remove(&rounded2);
            state.times.remove(&rounded2);
        }
    }) as Box<dyn FnOnce()>);
    #[allow(deprecated)]
    let _ = source.set_onended(Some(closure.as_ref().unchecked_ref()));
    closure.forget();
    state_rc.borrow_mut().times.insert(rounded_ms);
    state_rc.borrow_mut().sources.insert(rounded_ms, source);
    true
}

/// Force a range/number input's value on mount. Range inputs clamp an
/// un-applied value to the track midpoint, so a stored calibration would show
/// as 0 without this; setting the DOM value directly reflects the real value.
#[cfg(target_arch = "wasm32")]
fn set_input_value_on_mount(event: &MountedData, value: f64) {
    use dioxus_web::WebEventExt;
    if let Some(el) = event.try_as_web_event() {
        if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
            input.set_value(&format!("{value:.0}"));
        }
    }
}

#[cfg(target_arch = "wasm32")]
fn set_range_input_value(id: &str, value: f64) {
    if let Some(window) = web_sys::window() {
        if let Some(doc) = window.document() {
            if let Some(el) = doc.get_element_by_id(id) {
                if let Ok(input) = el.dyn_into::<web_sys::HtmlInputElement>() {
                    input.set_value(&format!("{value:.0}"));
                }
            }
        }
    }
}

/// Cancel only sources whose scheduled start is still in the future, leaving
/// any currently-sounding stone to finish. Cutting the source that is actively
/// feeding the live MediaStream starves it, and the browser time-stretches the
/// gap and drops the pitch — so sound changes and calibration tweaks clear
/// only future sources. Pause/Reset stop output immediately instead.
#[cfg(target_arch = "wasm32")]
fn clear_future_scheduled(state_rc: Rc<RefCell<ScheduleState>>, ctx: &web_sys::AudioContext) {
    // A source is "playing" (do not touch) if it started at or before now; a
    // small margin keeps one that is about to start from being cut mid-attack.
    let cutoff_ms = ((ctx.current_time() + 0.05) * 1000.0) as i64;
    let to_stop: Vec<web_sys::AudioBufferSourceNode> = {
        let mut state = state_rc.borrow_mut();
        let future: Vec<i64> = state
            .times
            .iter()
            .copied()
            .filter(|&t_ms| t_ms > cutoff_ms)
            .collect();
        for t_ms in &future {
            state.times.remove(t_ms);
        }
        future
            .iter()
            .filter_map(|t_ms| state.sources.remove(t_ms))
            .collect()
    };
    for source in to_stop {
        #[allow(deprecated)]
        let _ = source.stop();
        let _ = source.disconnect();
    }
}

#[cfg(target_arch = "wasm32")]
fn stop_all_scheduled(state_rc: Rc<RefCell<ScheduleState>>) {
    let sources = {
        let mut state = state_rc.borrow_mut();
        state.times.clear();
        state
            .sources
            .drain()
            .map(|(_, source)| source)
            .collect::<Vec<_>>()
    };
    for source in sources {
        #[allow(deprecated)]
        let _ = source.stop();
        let _ = source.disconnect();
    }
}

#[cfg(target_arch = "wasm32")]
fn set_audio_stream_muted(muted: bool) {
    if let Some(audio) = web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id("audio-stream"))
        .and_then(|el| el.dyn_into::<web_sys::HtmlAudioElement>().ok())
    {
        audio.set_muted(muted);
    }
}

#[cfg(target_arch = "wasm32")]
async fn load_playback_audio(
    ctx: &web_sys::AudioContext,
    custom_buffer: Signal<Option<web_sys::AudioBuffer>>,
    stones: Option<StonesResponse>,
    selected_index: usize,
    schedule_state: Signal<Rc<RefCell<ScheduleState>>>,
    library: Rc<RefCell<StoneLibrary>>,
    request_generation: Signal<u64>,
    request_id: u64,
    playback: wasm_bindgen_futures::JsFuture,
) -> Result<web_sys::AudioBuffer, String> {
    playback
        .await
        .map_err(|_| "Could not start audio. Try again.")?;
    if request_generation() != request_id {
        return Err("Playback cancelled.".into());
    }
    if ctx.state() != web_sys::AudioContextState::Running {
        let promise = ctx.resume().map_err(|_| "Could not resume audio.")?;
        wasm_bindgen_futures::JsFuture::from(promise)
            .await
            .map_err(|_| "Could not start audio.")?;
    }
    if request_generation() != request_id {
        return Err("Playback cancelled.".into());
    }
    ensure_media_destination(ctx, schedule_state, false);
    let stones_len = stones.as_ref().map(|s| s.stones.len()).unwrap_or(0);
    if selected_index >= stones_len {
        custom_buffer
            .read()
            .clone()
            .ok_or_else(|| "Upload a custom MP3 first.".into())
    } else {
        let stone = stones
            .as_ref()
            .and_then(|s| s.stones.get(selected_index))
            .ok_or("No sound selected.")?;
        cached_audio_buffer(ctx, &stone.filename_encoded, &library)
            .await
            .map_err(|message| format!("Could not load this sound: {message}"))
    }
}

#[cfg(target_arch = "wasm32")]
enum CacheStep {
    Ready(web_sys::AudioBuffer),
    Wait,
    Decode(Vec<u8>),
    Fetch,
}

/// Return the decoded buffer for a built-in stone, downloading its bytes at most once.
#[cfg(target_arch = "wasm32")]
async fn cached_audio_buffer(
    ctx: &web_sys::AudioContext,
    filename: &str,
    library: &Rc<RefCell<StoneLibrary>>,
) -> Result<web_sys::AudioBuffer, String> {
    loop {
        let step = {
            let mut lib = library.borrow_mut();
            if let Some(buf) = lib.buffers.get(filename).cloned() {
                CacheStep::Ready(buf)
            } else if lib.inflight.contains(filename) {
                CacheStep::Wait
            } else {
                let bytes = lib.bytes.get(filename).cloned();
                lib.inflight.insert(filename.to_string());
                match bytes {
                    Some(bytes) => CacheStep::Decode(bytes),
                    None => CacheStep::Fetch,
                }
            }
        };
        match step {
            CacheStep::Ready(buf) => return Ok(buf),
            CacheStep::Wait => {
                gloo_timers::future::TimeoutFuture::new(40).await;
            }
            CacheStep::Decode(bytes) => {
                let decoded = decode_audio_bytes(ctx, &bytes).await;
                let mut lib = library.borrow_mut();
                lib.inflight.remove(filename);
                match decoded {
                    Ok(buf) => {
                        lib.buffers.insert(filename.to_string(), buf.clone());
                        return Ok(buf);
                    }
                    Err(e) => return Err(e),
                }
            }
            CacheStep::Fetch => {
                let url = format!("{}/static/stones/{}", api::base_url(), filename);
                let fetched = api::fetch_bytes(&url).await;
                let mut lib = library.borrow_mut();
                lib.inflight.remove(filename);
                match fetched {
                    Ok(bytes) => {
                        lib.bytes.insert(filename.to_string(), bytes);
                    }
                    Err(e) => return Err(e),
                }
            }
        }
    }
}

#[cfg(target_arch = "wasm32")]
async fn decode_audio_bytes(
    ctx: &web_sys::AudioContext,
    bytes: &[u8],
) -> Result<web_sys::AudioBuffer, String> {
    use js_sys::Uint8Array;
    use wasm_bindgen::JsCast;
    let arr = Uint8Array::new_with_length(bytes.len() as u32);
    arr.copy_from(bytes);
    let array_buffer = arr
        .buffer()
        .dyn_into::<js_sys::ArrayBuffer>()
        .map_err(|_| "not ArrayBuffer")?;
    let promise = ctx
        .decode_audio_data(&array_buffer)
        .map_err(|_| "decode_audio_data failed".to_string())?;
    let result = wasm_bindgen_futures::JsFuture::from(promise)
        .await
        .map_err(|_| "decode_audio_data failed".to_string())?;
    result
        .dyn_into::<web_sys::AudioBuffer>()
        .map_err(|_| "not AudioBuffer".to_string())
}

#[cfg(target_arch = "wasm32")]
async fn decode_custom_file(
    file: web_sys::File,
    ctx: &web_sys::AudioContext,
) -> Result<web_sys::AudioBuffer, String> {
    if !file.name().to_ascii_lowercase().ends_with(".mp3") {
        return Err("Choose an MP3 file.".into());
    }
    let array_buffer = wasm_bindgen_futures::JsFuture::from(file.array_buffer())
        .await
        .map_err(|_| "Could not read this file.")?
        .dyn_into::<js_sys::ArrayBuffer>()
        .map_err(|_| "Could not read this file.")?;
    let promise = ctx
        .decode_audio_data(&array_buffer)
        .map_err(|_| "Could not decode audio. Try a different MP3.")?;
    let buffer = wasm_bindgen_futures::JsFuture::from(promise)
        .await
        .map_err(|_| "Could not decode audio. Try a different MP3.")?
        .dyn_into::<web_sys::AudioBuffer>()
        .map_err(|_| "Could not decode audio.")?;
    let duration = buffer.duration();
    if duration >= BEAT_INTERVAL {
        return Err(format!("File is {:.2} s. Must be under 1.5 s.", duration));
    }
    Ok(buffer)
}
