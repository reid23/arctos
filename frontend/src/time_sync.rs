//! Shared client–server time synchronization for the stones grid.
//!
//! One module owns everything the stones player, the run-match view, and the
//! scoreboard need to agree on the server's clock:
//!
//! * the [`BayesianOffsetFilter`](crate::stones_filter::BayesianOffsetFilter)
//!   estimate of the clock offset (the single source of truth for
//!   `server_now`),
//! * a best-of-N, RTT-weighted probe loop that coasts when confident,
//! * a persisted **audio offset** (signed timing correction for playback
//!   scheduling only) and an optional **manual lock** on the filter offset
//!   (set by the calibration slider; cleared by Reset Sync),
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
pub const OFFSET_LOCKED_KEY: &str = "arctos_stones_offset_locked";

/// Variance pinned while the offset is manually locked (below the coast gate).
const LOCKED_VARIANCE_MS2: f64 = CONVERGED_VARIANCE_MS2 * 0.5;

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

/// Persisted calibration knobs. The clock offset itself lives in the filter;
/// when `offset_locked`, probing stops and `locked_offset_ms` is restored into
/// the filter on load / cross-tab sync.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Calibration {
    pub audio_delay_ms: f64,
    pub offset_locked: bool,
    pub locked_offset_ms: f64,
}

impl Default for Calibration {
    fn default() -> Self {
        Self {
            audio_delay_ms: 0.0,
            offset_locked: false,
            locked_offset_ms: 0.0,
        }
    }
}

impl Calibration {
    /// Corrected server time in seconds from the device clock and filter offset.
    pub fn server_now_secs(&self, raw_now_secs: f64, estimated_offset_ms: f64) -> f64 {
        raw_now_secs + estimated_offset_ms / 1000.0
    }

    /// Manual audio offset in seconds (playback scheduling only; does not
    /// affect `server_now_secs`). Positive schedules later; negative earlier
    /// (`audio_time = … + audio_delay_secs()`).
    pub fn audio_delay_secs(&self) -> f64 {
        self.audio_delay_ms / 1000.0
    }
}

/// Sync-quality snapshot for the stats UI and the run-match gate.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SyncQuality {
    /// Filter offset (ms) — same value the clock-offset slider edits.
    pub offset_ms: f64,
    pub variance_ms2: f64,
    pub rtt_ms: Option<f64>,
    pub converged: bool,
}

/// Snapshot for rolling back a cancelled calibration walkthrough.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SyncSnapshot {
    pub calibration: Calibration,
    pub mean_ms: f64,
    pub variance_ms2: f64,
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

    /// Manual audio offset in seconds (playback scheduling only). Positive
    /// schedules later; negative earlier — callers add this to the scheduled
    /// audio time.
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

    pub fn snapshot(&self) -> SyncSnapshot {
        let filter = self.filter.read();
        SyncSnapshot {
            calibration: *self.calibration.read(),
            mean_ms: filter.get_mean_ms(),
            variance_ms2: filter.get_variance_ms2(),
        }
    }

    pub fn restore_snapshot(&mut self, snap: SyncSnapshot) {
        *self.calibration.write() = snap.calibration;
        {
            let mut filter = self.filter.write();
            filter.mean_ms = snap.mean_ms;
            filter.variance_ms2 = snap.variance_ms2;
        }
        self.persist();
    }

    /// Set the filter offset and lock it (default after manual calibration).
    pub fn set_offset_ms(&mut self, value: f64) {
        {
            let mut filter = self.filter.write();
            filter.mean_ms = value;
            filter.variance_ms2 = LOCKED_VARIANCE_MS2;
        }
        {
            let mut cal = self.calibration.write();
            cal.offset_locked = true;
            cal.locked_offset_ms = value;
        }
        self.persist();
    }

    /// Lock or unlock the offset. Unlocking resumes probing from the current mean.
    pub fn set_offset_locked(&mut self, locked: bool) {
        if locked {
            let mean = self.filter.read().get_mean_ms();
            self.filter.write().variance_ms2 = LOCKED_VARIANCE_MS2;
            {
                let mut cal = self.calibration.write();
                cal.offset_locked = true;
                cal.locked_offset_ms = mean;
            }
        } else {
            self.calibration.write().offset_locked = false;
            if self.filter.read().get_variance_ms2() < CONVERGED_VARIANCE_MS2 {
                self.filter.write().variance_ms2 = CONVERGED_VARIANCE_MS2;
            }
        }
        self.persist();
    }

    /// Update the audio-delay knob and persist it.
    pub fn set_audio_delay_ms(&mut self, value: f64) {
        self.calibration.write().audio_delay_ms = value;
        self.persist();
    }

    /// Unlock, zero both knobs, and reset the estimator (Reset Sync).
    pub fn reset_sync(&mut self) {
        {
            let mut cal = self.calibration.write();
            cal.offset_locked = false;
            cal.locked_offset_ms = 0.0;
            cal.audio_delay_ms = 0.0;
        }
        self.filter.write().reset();
        self.persist();
    }

    /// Unlock and reset the estimator for step-2 re-measurement (keeps audio delay).
    pub fn begin_clock_calibration(&mut self) {
        {
            let mut cal = self.calibration.write();
            cal.offset_locked = false;
        }
        self.filter.write().reset();
        self.persist();
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
    let filter = use_signal(|| {
        let cal = load_calibration();
        let mut f = BayesianOffsetFilter::default();
        if cal.offset_locked {
            apply_locked_offset(&mut f, cal.locked_offset_ms);
        }
        f
    });
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
        let calibration = calibration;
        use_effect(move || {
            spawn(async move {
                loop {
                    let locked = calibration.read().offset_locked;
                    if !locked {
                        filter.write().predict();
                        if should_probe(filter.read().get_variance_ms2()) {
                            if let Some(best) = probe_lowest_rtt().await {
                                let rtt_ms = best.rtt_secs() * 1000.0;
                                let variance = measurement_variance_ms2(rtt_ms);
                                filter.write().observe(best.offset_ms(), variance);
                                last_rtt_ms.set(Some(rtt_ms));
                            }
                        }
                    }
                    gloo_timers::future::TimeoutFuture::new(TICK_INTERVAL_MS).await;
                }
            });
        });

        let _storage_listener = use_hook(|| {
            std::rc::Rc::new(std::cell::RefCell::new(install_storage_listener(
                calibration,
                filter,
            )))
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

fn apply_locked_offset(filter: &mut BayesianOffsetFilter, locked_offset_ms: f64) {
    filter.mean_ms = locked_offset_ms;
    filter.variance_ms2 = LOCKED_VARIANCE_MS2;
}

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
fn read_locked(storage: &web_sys::Storage) -> bool {
    storage
        .get_item(OFFSET_LOCKED_KEY)
        .ok()
        .flatten()
        .map(|s| s == "1" || s.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
}

#[cfg(target_arch = "wasm32")]
fn load_calibration() -> Calibration {
    let Some(storage) = local_storage() else {
        return Calibration::default();
    };
    let offset_locked = read_locked(&storage);
    Calibration {
        audio_delay_ms: read_ms(&storage, AUDIO_DELAY_KEY).unwrap_or(0.0),
        offset_locked,
        locked_offset_ms: if offset_locked {
            read_ms(&storage, CLOCK_OFFSET_KEY).unwrap_or(0.0)
        } else {
            0.0
        },
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn load_calibration() -> Calibration {
    Calibration::default()
}

/// Persist knobs so other tabs can pick up the change.
#[cfg(target_arch = "wasm32")]
fn write_calibration(cal: &Calibration) {
    if let Some(storage) = local_storage() {
        let _ = storage.set_item(AUDIO_DELAY_KEY, &cal.audio_delay_ms.to_string());
        let _ = storage.set_item(OFFSET_LOCKED_KEY, if cal.offset_locked { "1" } else { "0" });
        let _ = storage.set_item(CLOCK_OFFSET_KEY, &cal.locked_offset_ms.to_string());
    }
}

/// Reload calibration (and locked filter state) when another tab writes our keys.
#[cfg(target_arch = "wasm32")]
fn install_storage_listener(
    mut calibration: Signal<Calibration>,
    mut filter: Signal<BayesianOffsetFilter>,
) -> Option<StorageListenerGuard> {
    use wasm_bindgen::closure::Closure;
    use wasm_bindgen::JsCast;

    let window = web_sys::window()?;
    let closure = Closure::wrap(Box::new(move |event: web_sys::StorageEvent| {
        let key = event.key();
        let touches_calibration = match key.as_deref() {
            Some(k) => {
                k == CLOCK_OFFSET_KEY || k == AUDIO_DELAY_KEY || k == OFFSET_LOCKED_KEY
            }
            None => true,
        };
        if !touches_calibration {
            return;
        }
        let was_locked = calibration.read().offset_locked;
        let cal = load_calibration();
        calibration.set(cal);
        if cal.offset_locked {
            apply_locked_offset(&mut filter.write(), cal.locked_offset_ms);
        } else if was_locked {
            filter.write().reset();
        }
    }) as Box<dyn FnMut(web_sys::StorageEvent)>);
    window
        .add_event_listener_with_callback("storage", closure.as_ref().unchecked_ref())
        .ok()?;
    Some(StorageListenerGuard { closure })
}

/// Owns the `storage` event Closure and removes it on Drop.
#[cfg(target_arch = "wasm32")]
struct StorageListenerGuard {
    closure: wasm_bindgen::closure::Closure<dyn FnMut(web_sys::StorageEvent)>,
}

#[cfg(target_arch = "wasm32")]
impl Drop for StorageListenerGuard {
    fn drop(&mut self) {
        use wasm_bindgen::JsCast;
        if let Some(window) = web_sys::window() {
            let _ = window.remove_event_listener_with_callback(
                "storage",
                self.closure.as_ref().unchecked_ref(),
            );
        }
    }
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
    fn server_now_uses_estimated_offset_only() {
        let cal = Calibration {
            audio_delay_ms: 250.0,
            offset_locked: false,
            locked_offset_ms: 40.0,
        };
        // raw 1000.0s, estimated offset 100ms → +100ms. Locked field ignored here.
        assert_close(cal.server_now_secs(1000.0, 100.0), 1000.1, 1e-9);
        let cal_no_audio = Calibration {
            audio_delay_ms: 0.0,
            offset_locked: false,
            locked_offset_ms: 0.0,
        };
        assert_close(
            cal.server_now_secs(1000.0, 100.0),
            cal_no_audio.server_now_secs(1000.0, 100.0),
            1e-12,
        );
    }

    #[test]
    fn audio_delay_secs_converts_ms() {
        let cal = Calibration {
            audio_delay_ms: 250.0,
            offset_locked: false,
            locked_offset_ms: 0.0,
        };
        assert_close(cal.audio_delay_secs(), 0.25, 1e-12);
    }

    #[test]
    fn filter_default_is_not_converged() {
        // Sanity tie to the shared threshold constant.
        let filter = BayesianOffsetFilter::default();
        assert!(!filter.is_converged());
    }
}
