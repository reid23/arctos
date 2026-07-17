//! Shared client–server time synchronization for the stones grid.
//!
//! One module owns everything the stones player, the run-match view, and the
//! scoreboard need to agree on the server's clock:
//!
//! * the [`BayesianOffsetFilter`](crate::stones_filter::BayesianOffsetFilter)
//!   estimate of the clock offset,
//! * a best-of-N, RTT-weighted probe loop that coasts when confident,
//! * two persisted manual corrections — a **clock offset** (shifts this
//!   device's whole notion of server time, affecting playback *and* the live
//!   stone counters) and an **audio delay** (shifts only when sound leaves the
//!   speaker), shared across tabs via `localStorage`,
//! * a corrected `server_now()` and a `quality()` readout for the UI and the
//!   run-match staleness gate.
//!
//! The pure, `cfg`-agnostic core (measurement model, sample selection, coast
//! gate, calibration math, staleness predicate) is unit-tested natively; the
//! wasm-only I/O (network probe, `localStorage`, `storage` events) is verified
//! in the browser.

use crate::stones_filter::{BayesianOffsetFilter, CONVERGED_VARIANCE_MS2};

/// How many parallel probes to fire per measurement round; the lowest-RTT
/// sample is kept (least queuing delay → least-biased one-way estimate).
pub const PROBES_PER_ROUND: usize = 3;

/// localStorage keys (namespaced, following the `record.rs` convention).
pub const CLOCK_OFFSET_KEY: &str = "arctos_stones_clock_offset_ms";
pub const AUDIO_DELAY_KEY: &str = "arctos_stones_audio_delay_ms";

/// One round-trip probe result. Times are unix seconds; `server_time` is the
/// server's clock at the moment it replied.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Probe {
    pub client_send_secs: f64,
    pub client_receive_secs: f64,
    pub server_time_secs: f64,
}

impl Probe {
    pub fn rtt_secs(&self) -> f64 {
        self.client_receive_secs - self.client_send_secs
    }

    /// Cristian's estimate of `server − client` at receive, in milliseconds.
    pub fn offset_ms(&self) -> f64 {
        (self.server_time_secs - self.client_receive_secs + self.rtt_secs() / 2.0) * 1000.0
    }
}

/// Measurement variance `R` (ms²) for a sample with the given round-trip time.
/// `R = exp(rtt_ms · 0.01)²  = exp(rtt_ms · 0.02)`, so trust collapses
/// super-linearly as RTT grows and a slow/asymmetric sample barely moves the
/// estimate.
pub fn measurement_variance_ms2(rtt_ms: f64) -> f64 {
    (rtt_ms * 0.02).exp()
}

/// The next beat-grid boundary at or after `now_secs`, in the same time base
/// as `now_secs`. Beats fall on integer multiples of `beat_secs` (unix time),
/// so all clients that agree on the time also agree on the grid.
pub fn next_beat_boundary(now_secs: f64, beat_secs: f64) -> f64 {
    (now_secs / beat_secs).ceil() * beat_secs
}

/// Pick the sample with the lowest round-trip time.
pub fn pick_lowest_rtt(samples: &[Probe]) -> Option<Probe> {
    samples
        .iter()
        .copied()
        .min_by(|a, b| a.rtt_secs().total_cmp(&b.rtt_secs()))
}

/// Whether to issue a network probe this tick, or coast on the model. Coast
/// once the estimate is converged (variance below the shared threshold).
pub fn should_probe(variance_ms2: f64) -> bool {
    variance_ms2 >= CONVERGED_VARIANCE_MS2
}

/// The two persisted manual corrections. Milliseconds.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Calibration {
    pub clock_offset_ms: f64,
    pub audio_delay_ms: f64,
}

impl Default for Calibration {
    fn default() -> Self {
        Self {
            clock_offset_ms: 0.0,
            audio_delay_ms: 0.0,
        }
    }
}

impl Calibration {
    /// Corrected server time in seconds, given the raw device clock (seconds)
    /// and the filter's estimated offset (ms). Folds in the manual clock
    /// offset — so this shifts both playback and the live stone counters.
    pub fn server_now_secs(&self, raw_now_secs: f64, estimated_offset_ms: f64) -> f64 {
        raw_now_secs + (estimated_offset_ms + self.clock_offset_ms) / 1000.0
    }

    /// Manual audio output delay in seconds (playback scheduling only; does
    /// not affect `server_now_secs`).
    pub fn audio_delay_secs(&self) -> f64 {
        self.audio_delay_ms / 1000.0
    }
}

/// Sync-quality snapshot for the stats UI and the run-match gate.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SyncQuality {
    /// Raw filter estimate of the client→server offset (ms). Display-only; the
    /// manual clock-offset knob is intentionally excluded.
    pub offset_ms: f64,
    pub variance_ms2: f64,
    pub rtt_ms: Option<f64>,
    pub converged: bool,
}

// ---------------------------------------------------------------------------
// Reactive handle + wasm-only I/O (network probe, localStorage, cross-tab).
// ---------------------------------------------------------------------------

use dioxus::prelude::*;

/// Fixed tick period for the sync loop. Every tick advances the filter's
/// random-walk dynamics; a network probe is issued only when not yet
/// converged. `PROCESS_NOISE_MS2_PER_TICK` is defined against this period.
pub const TICK_INTERVAL_MS: u32 = 1000;

/// Reactive time-sync handle shared by the stones player, run-match view, and
/// scoreboard. `Copy`, so it threads through closures and child components
/// without cloning ceremony.
#[derive(Clone, Copy)]
pub struct TimeSync {
    filter: Signal<BayesianOffsetFilter>,
    calibration: Signal<Calibration>,
    last_rtt_ms: Signal<Option<f64>>,
}

impl TimeSync {
    /// Corrected server time, in unix seconds. Use this anywhere the old code
    /// did `Date::now()/1000 + filter.mean`.
    pub fn server_now_secs(&self) -> f64 {
        let raw = raw_now_secs();
        self.calibration
            .read()
            .server_now_secs(raw, self.filter.read().get_mean_ms())
    }

    /// Manual audio output delay in seconds (playback scheduling only).
    pub fn audio_delay_secs(&self) -> f64 {
        self.calibration.read().audio_delay_secs()
    }

    pub fn quality(&self) -> SyncQuality {
        let filter = self.filter.read();
        SyncQuality {
            offset_ms: filter.get_mean_ms(),
            variance_ms2: filter.get_variance_ms2(),
            rtt_ms: *self.last_rtt_ms.read(),
            converged: filter.is_converged(),
        }
    }

    pub fn calibration(&self) -> Calibration {
        *self.calibration.read()
    }

    /// Update the clock-offset knob and persist it (and the calibration
    /// metadata) so other tabs pick it up.
    pub fn set_clock_offset_ms(&mut self, value: f64) {
        self.calibration.write().clock_offset_ms = value;
        self.persist();
    }

    /// Update the audio-delay knob and persist it.
    pub fn set_audio_delay_ms(&mut self, value: f64) {
        self.calibration.write().audio_delay_ms = value;
        self.persist();
    }

    /// Reset the estimator and force an immediate probe next tick (the
    /// re-ping button).
    pub fn reping(&mut self) {
        self.filter.write().reset();
    }

    #[cfg(target_arch = "wasm32")]
    fn persist(&self) {
        let cal = *self.calibration.read();
        write_calibration(&cal);
    }

    #[cfg(not(target_arch = "wasm32"))]
    fn persist(&self) {}
}

/// Raw device clock in unix seconds.
#[cfg(target_arch = "wasm32")]
fn raw_now_secs() -> f64 {
    js_sys::Date::now() / 1000.0
}

#[cfg(not(target_arch = "wasm32"))]
fn raw_now_secs() -> f64 {
    chrono::Utc::now().timestamp_millis() as f64 / 1000.0
}

/// Set up the shared sync signals and, on wasm, spawn the probe loop and the
/// cross-tab `storage` listener. Safe to call from each page that needs time;
/// each gets its own estimator that converges to the same server clock.
pub fn use_time_sync() -> TimeSync {
    let filter = use_signal(BayesianOffsetFilter::default);
    let calibration = use_signal(load_calibration);
    let last_rtt_ms = use_signal(|| Option::<f64>::None);
    let handle = TimeSync {
        filter,
        calibration,
        last_rtt_ms,
    };

    #[cfg(target_arch = "wasm32")]
    {
        let mut filter = filter;
        let mut last_rtt_ms = last_rtt_ms;
        use_effect(move || {
            spawn(async move {
                loop {
                    filter.write().predict();
                    if should_probe(filter.read().get_variance_ms2()) {
                        if let Some(best) = probe_lowest_rtt().await {
                            let rtt_ms = best.rtt_secs() * 1000.0;
                            let variance = measurement_variance_ms2(rtt_ms);
                            filter.write().observe(best.offset_ms(), variance);
                            last_rtt_ms.set(Some(rtt_ms));
                        }
                    }
                    gloo_timers::future::TimeoutFuture::new(TICK_INTERVAL_MS).await;
                }
            });
        });

        let calibration = calibration;
        use_effect(move || {
            install_storage_listener(calibration);
        });
    }

    handle
}

/// Fire `PROBES_PER_ROUND` server-time requests in parallel and return the
/// lowest-RTT sample (the one with the least queuing delay).
#[cfg(target_arch = "wasm32")]
async fn probe_lowest_rtt() -> Option<Probe> {
    let probes = (0..PROBES_PER_ROUND).map(|_| single_probe());
    let samples: Vec<Probe> = futures::future::join_all(probes)
        .await
        .into_iter()
        .flatten()
        .collect();
    pick_lowest_rtt(&samples)
}

#[cfg(target_arch = "wasm32")]
async fn single_probe() -> Option<Probe> {
    let client_send_secs = raw_now_secs();
    let res = crate::api::server_time().await.ok()?;
    let client_receive_secs = raw_now_secs();
    Some(Probe {
        client_send_secs,
        client_receive_secs,
        server_time_secs: res.server_time,
    })
}

// --- localStorage-backed calibration ---------------------------------------

#[cfg(target_arch = "wasm32")]
fn local_storage() -> Option<web_sys::Storage> {
    web_sys::window().and_then(|w| w.local_storage().ok().flatten())
}

#[cfg(target_arch = "wasm32")]
fn read_ms(storage: &web_sys::Storage, key: &str) -> Option<f64> {
    storage
        .get_item(key)
        .ok()
        .flatten()
        .and_then(|s| s.parse::<f64>().ok())
}

#[cfg(target_arch = "wasm32")]
fn load_calibration() -> Calibration {
    let Some(storage) = local_storage() else {
        return Calibration::default();
    };
    Calibration {
        clock_offset_ms: read_ms(&storage, CLOCK_OFFSET_KEY).unwrap_or(0.0),
        audio_delay_ms: read_ms(&storage, AUDIO_DELAY_KEY).unwrap_or(0.0),
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn load_calibration() -> Calibration {
    Calibration::default()
}

/// Persist both knobs so other tabs can pick up the change.
#[cfg(target_arch = "wasm32")]
fn write_calibration(cal: &Calibration) {
    if let Some(storage) = local_storage() {
        let _ = storage.set_item(CLOCK_OFFSET_KEY, &cal.clock_offset_ms.to_string());
        let _ = storage.set_item(AUDIO_DELAY_KEY, &cal.audio_delay_ms.to_string());
    }
}

/// Reload the calibration signal whenever another tab writes one of our
/// calibration keys (cross-tab consistency).
#[cfg(target_arch = "wasm32")]
fn install_storage_listener(mut calibration: Signal<Calibration>) {
    use wasm_bindgen::closure::Closure;
    use wasm_bindgen::JsCast;

    let Some(window) = web_sys::window() else {
        return;
    };
    let closure = Closure::wrap(Box::new(move |event: web_sys::StorageEvent| {
        let key = event.key();
        let touches_calibration = match key.as_deref() {
            Some(k) => k == CLOCK_OFFSET_KEY || k == AUDIO_DELAY_KEY,
            None => true,
        };
        if touches_calibration {
            calibration.set(load_calibration());
        }
    }) as Box<dyn FnMut(web_sys::StorageEvent)>);
    let _ = window.add_event_listener_with_callback("storage", closure.as_ref().unchecked_ref());
    closure.forget();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_close(actual: f64, expected: f64, tol: f64) {
        assert!(
            (actual - expected).abs() <= tol,
            "expected {expected} ± {tol}, got {actual}"
        );
    }

    #[test]
    fn measurement_variance_matches_spec_table() {
        // stdev = exp(rtt_ms * 0.01); R = stdev^2 = exp(rtt_ms * 0.02).
        assert_close(measurement_variance_ms2(5.0), 1.05_f64.powi(2), 0.02);
        assert_close(measurement_variance_ms2(50.0), 1.6487_f64.powi(2), 0.05);
        assert_close(measurement_variance_ms2(500.0), 148.41_f64.powi(2), 50.0);
        assert!(measurement_variance_ms2(1000.0) > 1e8);
    }

    #[test]
    fn measurement_variance_is_monotonic_in_rtt() {
        assert!(measurement_variance_ms2(10.0) < measurement_variance_ms2(100.0));
        assert!(measurement_variance_ms2(100.0) < measurement_variance_ms2(1000.0));
    }

    #[test]
    fn pick_lowest_rtt_selects_least_queued_sample() {
        let samples = vec![
            Probe { client_send_secs: 0.0, client_receive_secs: 0.30, server_time_secs: 100.0 },
            Probe { client_send_secs: 0.0, client_receive_secs: 0.05, server_time_secs: 100.0 },
            Probe { client_send_secs: 0.0, client_receive_secs: 0.20, server_time_secs: 100.0 },
        ];
        let best = pick_lowest_rtt(&samples).expect("some sample");
        assert_close(best.rtt_secs(), 0.05, 1e-9);
    }

    #[test]
    fn pick_lowest_rtt_none_when_empty() {
        assert!(pick_lowest_rtt(&[]).is_none());
    }

    #[test]
    fn offset_ms_uses_cristian_formula() {
        // send at 10.0, receive at 10.2 (rtt 0.2s), server said 100.0s.
        // offset = (100.0 - 10.2 + 0.1) * 1000 = 89_900 ms.
        let probe = Probe {
            client_send_secs: 10.0,
            client_receive_secs: 10.2,
            server_time_secs: 100.0,
        };
        assert_close(probe.offset_ms(), 89_900.0, 1e-6);
    }

    #[test]
    fn next_beat_boundary_rounds_up_to_grid() {
        // 1.5s grid: 10.0 -> 10.5, 10.4 -> 10.5, 10.6 -> 12.0.
        assert_close(next_beat_boundary(10.0, 1.5), 10.5, 1e-9);
        assert_close(next_beat_boundary(10.4, 1.5), 10.5, 1e-9);
        assert_close(next_beat_boundary(10.6, 1.5), 12.0, 1e-9);
    }

    #[test]
    fn next_beat_boundary_on_exact_boundary_returns_same() {
        // Already exactly on a boundary: return it, not the next one.
        assert_close(next_beat_boundary(9.0, 1.5), 9.0, 1e-9);
        assert_close(next_beat_boundary(0.0, 1.5), 0.0, 1e-9);
    }

    #[test]
    fn next_beat_boundary_is_always_at_or_after_now() {
        for &now in &[0.1, 3.7, 100.25, 1_700_000_000.3] {
            let b = next_beat_boundary(now, 1.5);
            assert!(b >= now - 1e-9, "boundary {b} should be >= now {now}");
            assert!(b - now < 1.5 + 1e-9, "boundary should be within one beat");
        }
    }

    #[test]
    fn should_probe_gates_on_converged_threshold() {
        assert!(should_probe(CONVERGED_VARIANCE_MS2 + 1.0));
        assert!(!should_probe(CONVERGED_VARIANCE_MS2 - 1.0));
    }

    #[test]
    fn server_now_folds_in_clock_offset_only() {
        let cal = Calibration { clock_offset_ms: 40.0, audio_delay_ms: 250.0 };
        // raw 1000.0s, estimated offset 60ms, clock offset 40ms → +100ms.
        assert_close(cal.server_now_secs(1000.0, 60.0), 1000.1, 1e-9);
        // audio delay must not leak into server_now.
        let cal_no_audio = Calibration { clock_offset_ms: 40.0, audio_delay_ms: 0.0 };
        assert_close(
            cal.server_now_secs(1000.0, 60.0),
            cal_no_audio.server_now_secs(1000.0, 60.0),
            1e-12,
        );
    }

    #[test]
    fn audio_delay_secs_converts_ms() {
        let cal = Calibration { clock_offset_ms: 0.0, audio_delay_ms: 250.0 };
        assert_close(cal.audio_delay_secs(), 0.25, 1e-12);
    }

    #[test]
    fn filter_default_is_not_converged() {
        // Sanity tie to the shared threshold constant.
        let filter = BayesianOffsetFilter::default();
        assert!(!filter.is_converged());
    }
}
