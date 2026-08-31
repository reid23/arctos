//! Bayesian filter for client–server clock-offset estimation.
//!
//! The filter tracks a single scalar — the clock offset, in **milliseconds** —
//! with a Gaussian posterior. Two operations advance it:
//!
//! * [`BayesianOffsetFilter::predict`] advances the random-walk dynamics one
//!   tick, inflating the variance by the process noise `Q`. Call this every
//!   tick regardless of whether a network measurement was taken.
//! * [`BayesianOffsetFilter::observe`] folds in one measurement with a
//!   caller-supplied measurement variance `R` (the sync loop derives `R` from
//!   the sample's round-trip time, so a slow/asymmetric sample is trusted
//!   less). A light innovation sanity-gate rejects an offset that is absurdly
//!   far from the current estimate given the current uncertainty.
//!
//! `server_time_ms = client_time_ms + mean_ms`.

/// Process noise added to the variance on every [`predict`](BayesianOffsetFilter::predict)
/// tick (random-walk drift). Defined against the sync loop's fixed tick
/// interval; if that interval changes, scale this with it.
pub const PROCESS_NOISE_MS2_PER_TICK: f64 = 7.5;

/// Variance (ms²) below which the estimate is considered converged: tight
/// enough to stop probing (coast) and tight enough to run a stones match.
/// √100 = 10 ms per-client stdev → ~14 ms between two independent clients,
/// inside the ±15 ms goal.
pub const CONVERGED_VARIANCE_MS2: f64 = 100.0;

/// Innovation sanity-gate width. A measurement is rejected only when it lands
/// more than this many standard deviations from the current estimate — a
/// generous last-resort guard against a low-RTT-but-garbage sample, not the
/// primary outlier mechanism (RTT-scaled `R` is).
const INNOVATION_SIGMA: f64 = 6.0;

/// Initial variance: "no idea yet". Large relative to any real measurement.
const INITIAL_VARIANCE_MS2: f64 = 1e12;

#[derive(Clone, Debug)]
pub struct BayesianOffsetFilter {
    /// Posterior mean: estimated offset in milliseconds.
    pub mean_ms: f64,
    /// Posterior variance in ms².
    pub variance_ms2: f64,
    /// Process noise per predict tick (ms²).
    pub process_noise_ms2: f64,
    pub rejected_count: u32,
}

impl Default for BayesianOffsetFilter {
    fn default() -> Self {
        Self {
            mean_ms: 0.0,
            variance_ms2: INITIAL_VARIANCE_MS2,
            process_noise_ms2: PROCESS_NOISE_MS2_PER_TICK,
            rejected_count: 0,
        }
    }
}

impl BayesianOffsetFilter {
    /// Advance random-walk dynamics one tick.
    pub fn predict(&mut self) {
        self.variance_ms2 += self.process_noise_ms2;
    }

    /// Fold in one measurement (offset in ms) with its measurement variance
    /// `R` (ms²). Returns the posterior mean. Does **not** advance dynamics;
    /// call [`predict`](Self::predict) for that.
    pub fn observe(&mut self, measurement_ms: f64, measurement_variance_ms2: f64) -> f64 {
        if self.is_converged() {
            let innovation = measurement_ms - self.mean_ms;
            let innovation_std = (self.variance_ms2 + measurement_variance_ms2).sqrt();
            if innovation_std > 0.0 && innovation.abs() > INNOVATION_SIGMA * innovation_std {
                self.rejected_count += 1;
                return self.mean_ms;
            }
        }

        let prior_precision = 1.0 / self.variance_ms2;
        let likelihood_precision = 1.0 / measurement_variance_ms2;
        let posterior_precision = prior_precision + likelihood_precision;
        self.variance_ms2 = 1.0 / posterior_precision;
        self.mean_ms = (self.mean_ms * prior_precision + measurement_ms * likelihood_precision)
            / posterior_precision;
        self.mean_ms
    }

    pub fn get_mean_ms(&self) -> f64 {
        self.mean_ms
    }

    pub fn get_variance_ms2(&self) -> f64 {
        self.variance_ms2
    }

    pub fn is_converged(&self) -> bool {
        self.variance_ms2 < CONVERGED_VARIANCE_MS2
    }

    pub fn reset(&mut self) {
        self.mean_ms = 0.0;
        self.variance_ms2 = INITIAL_VARIANCE_MS2;
        self.rejected_count = 0;
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
    fn predict_inflates_variance_by_process_noise() {
        let mut filter = BayesianOffsetFilter::default();
        filter.variance_ms2 = 50.0;
        filter.process_noise_ms2 = 7.5;
        filter.predict();
        assert_close(filter.get_variance_ms2(), 57.5, 1e-9);
    }

    #[test]
    fn first_observation_snaps_mean_toward_measurement() {
        // With huge initial variance, the first measurement dominates: the
        // posterior mean lands essentially on the measurement.
        let mut filter = BayesianOffsetFilter::default();
        filter.observe(120.0, 4.0);
        assert_close(filter.get_mean_ms(), 120.0, 0.1);
    }

    #[test]
    fn observation_shrinks_variance() {
        let mut filter = BayesianOffsetFilter::default();
        let before = filter.get_variance_ms2();
        filter.observe(10.0, 4.0);
        assert!(
            filter.get_variance_ms2() < before,
            "variance should shrink after an observation"
        );
        // Posterior variance is bounded above by the measurement variance.
        assert!(filter.get_variance_ms2() <= 4.0 + 1e-6);
    }

    #[test]
    fn low_variance_measurement_moves_mean_more_than_high_variance() {
        // Two filters in the same (not-yet-converged) state, so the innovation
        // gate is inactive and this isolates the precision weighting: feed the
        // same offset with different measurement variances. The low-R (trusted)
        // sample should pull the mean further.
        let mut trusted = BayesianOffsetFilter::default();
        trusted.mean_ms = 0.0;
        trusted.variance_ms2 = 200.0;
        let mut distrusted = trusted.clone();

        trusted.observe(30.0, 2.0);
        distrusted.observe(30.0, 2000.0);

        assert!(
            trusted.get_mean_ms() > distrusted.get_mean_ms(),
            "trusted (low R) sample should pull the mean further: {} vs {}",
            trusted.get_mean_ms(),
            distrusted.get_mean_ms()
        );
    }

    #[test]
    fn innovation_gate_rejects_absurd_sample_when_converged() {
        // Converged filter (small variance). A wildly off measurement with a
        // small R should be rejected and leave the mean essentially untouched.
        let mut filter = BayesianOffsetFilter::default();
        filter.mean_ms = 5.0;
        filter.variance_ms2 = 4.0;
        let before = filter.get_mean_ms();
        filter.observe(100_000.0, 4.0);
        assert_close(filter.get_mean_ms(), before, 1e-9);
        assert_eq!(filter.rejected_count, 1);
    }

    #[test]
    fn innovation_gate_does_not_reject_during_initial_convergence() {
        // With huge initial variance the innovation band is enormous, so even
        // a large first measurement is accepted, not rejected.
        let mut filter = BayesianOffsetFilter::default();
        filter.observe(50_000.0, 4.0);
        assert_eq!(filter.rejected_count, 0);
        assert_close(filter.get_mean_ms(), 50_000.0, 1.0);
    }

    #[test]
    fn is_converged_tracks_threshold() {
        let mut filter = BayesianOffsetFilter::default();
        assert!(!filter.is_converged());
        filter.variance_ms2 = CONVERGED_VARIANCE_MS2 - 1.0;
        assert!(filter.is_converged());
        filter.variance_ms2 = CONVERGED_VARIANCE_MS2 + 1.0;
        assert!(!filter.is_converged());
    }

    #[test]
    fn reset_restores_high_uncertainty() {
        let mut filter = BayesianOffsetFilter::default();
        filter.observe(42.0, 4.0);
        filter.reset();
        assert_close(filter.get_mean_ms(), 0.0, 1e-9);
        assert!(!filter.is_converged());
        assert_eq!(filter.rejected_count, 0);
    }

    #[test]
    fn repeated_consistent_observations_converge() {
        // Feeding the same offset repeatedly (with predict between) should
        // converge the mean to it and drive the filter to the converged state.
        let mut filter = BayesianOffsetFilter::default();
        for _ in 0..50 {
            filter.predict();
            filter.observe(200.0, 4.0);
        }
        assert_close(filter.get_mean_ms(), 200.0, 1.0);
        assert!(filter.is_converged());
    }
}
