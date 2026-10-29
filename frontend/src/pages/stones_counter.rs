//! A local stone count driven by the synchronized server beat grid.

const BEAT_SECONDS: f64 = 1.5;
const BLOCK_STONES: u64 = 100;

#[derive(Clone, Copy, Debug)]
struct Cursor {
    server_secs: f64,
    offset_ms: f64,
}

impl Cursor {
    fn new(server_secs: f64, offset_ms: f64) -> Option<Self> {
        (server_secs.is_finite() && offset_ms.is_finite()).then_some(Self {
            server_secs,
            offset_ms,
        })
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct StoneCounter {
    count: u64,
    cursor: Option<Cursor>,
}

impl StoneCounter {
    /// Start at the current grid position; resuming never counts paused beats.
    pub fn start(&mut self, server_now_secs: f64, offset_ms: f64) {
        if self.cursor.is_some() {
            self.advance(server_now_secs, offset_ms);
        } else {
            self.cursor = Cursor::new(server_now_secs, offset_ms);
        }
    }

    /// Count crossed server-grid boundaries, catching up after delayed UI ticks.
    /// Rebase the previous sample by any clock-offset change before comparing:
    /// calibration changes the grid phase without inventing elapsed stones.
    pub fn advance(&mut self, server_now_secs: f64, offset_ms: f64) {
        let (Some(previous), Some(current)) =
            (self.cursor, Cursor::new(server_now_secs, offset_ms))
        else {
            return;
        };
        let previous_on_current_clock =
            previous.server_secs + (current.offset_ms - previous.offset_ms) / 1000.0;
        if !previous_on_current_clock.is_finite() {
            return;
        }
        // A backwards device-clock step must not recount already observed time.
        let now = current.server_secs.max(previous_on_current_clock);
        let crossed = ((now / BEAT_SECONDS).floor()
            - (previous_on_current_clock / BEAT_SECONDS).floor())
        .max(0.0) as u64;
        self.count = self.count.saturating_add(crossed);
        self.cursor = Some(Cursor {
            server_secs: now,
            offset_ms: current.offset_ms,
        });
    }

    /// Include any last crossed boundary, then freeze until the next start.
    pub fn pause(&mut self, server_now_secs: f64, offset_ms: f64) {
        self.advance(server_now_secs, offset_ms);
        self.cursor = None;
    }

    /// Stop and zero this local count without touching audio or clock calibration.
    pub fn reset(&mut self) {
        *self = Self::default();
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    /// Count time, not wall time: one stone represents exactly 1.5 seconds.
    pub fn elapsed_label(&self) -> String {
        let seconds = u128::from(self.count) * 3 / 2;
        format!("{:02}:{:02}", seconds / 60, seconds % 60)
    }

    /// Keep a completed block visible until the first stone of the next block.
    pub fn block_progress(&self) -> (u64, u64) {
        let start = self.count.saturating_sub(1) / BLOCK_STONES * BLOCK_STONES;
        (start, self.count - start)
    }
}

#[cfg(test)]
mod tests {
    use super::StoneCounter;

    #[test]
    fn counts_server_boundaries_not_time_since_start() {
        let mut counter = StoneCounter::default();
        counter.start(100.0, 0.0);
        counter.advance(100.49, 0.0);
        assert_eq!(counter.count(), 0);
        counter.advance(100.5, 0.0);
        assert_eq!(counter.count(), 1);
        counter.advance(100.5, 0.0);
        assert_eq!(counter.count(), 1);
        counter.advance(102.0, 0.0);
        assert_eq!(counter.count(), 2);
        assert_eq!(counter.elapsed_label(), "00:03");
    }

    #[test]
    fn starting_exactly_on_boundary_waits_for_next_stone() {
        let mut counter = StoneCounter::default();
        counter.start(150.0, 0.0);
        counter.advance(150.0, 0.0);
        assert_eq!(counter.count(), 0);
        counter.advance(151.5, 0.0);
        assert_eq!(counter.count(), 1);
        assert_eq!(counter.elapsed_label(), "00:01");
    }

    #[test]
    fn catches_up_after_throttling_and_repeated_start() {
        let mut counter = StoneCounter::default();
        counter.start(0.2, 0.0);
        counter.start(3.2, 0.0);
        counter.advance(150.2, 0.0);
        assert_eq!(counter.count(), 100);
        assert_eq!(counter.elapsed_label(), "02:30");
    }

    #[test]
    fn pause_samples_last_boundary_and_skips_paused_beats() {
        let mut counter = StoneCounter::default();
        counter.start(0.0, 0.0);
        counter.pause(3.0, 0.0);
        assert_eq!(counter.count(), 2);
        counter.advance(30.0, 0.0);
        counter.pause(60.0, 0.0);
        assert_eq!(counter.count(), 2);
        counter.start(100.0, 0.0);
        counter.advance(100.5, 0.0);
        assert_eq!(counter.count(), 3);
    }

    #[test]
    fn reset_stops_and_zeros_count() {
        let mut counter = StoneCounter::default();
        counter.start(0.0, 100.0);
        counter.advance(3.0, 100.0);
        counter.reset();
        counter.advance(30.0, 100.0);
        assert_eq!(counter.count(), 0);
        assert_eq!(counter.elapsed_label(), "00:00");
        assert_eq!(counter.block_progress(), (0, 0));
        counter.start(30.0, 100.0);
        counter.advance(31.5, 100.0);
        assert_eq!(counter.count(), 1);
    }

    #[test]
    fn hundred_stone_blocks_keep_completed_progress() {
        let mut counter = StoneCounter::default();
        counter.start(0.0, 0.0);
        for (count, expected) in [
            (99, (0, 99)),
            (100, (0, 100)),
            (101, (100, 1)),
            (200, (100, 100)),
        ] {
            counter.advance(count as f64 * 1.5, 0.0);
            assert_eq!(counter.block_progress(), expected);
        }
    }

    #[test]
    fn clock_corrections_rebase_without_inventing_stones() {
        let mut counter = StoneCounter::default();
        counter.start(1.49, 0.0);
        counter.advance(1.51, 20.0); // Same instant, corrected across a boundary.
        assert_eq!(counter.count(), 0);
        counter.advance(4.51, 3020.0); // Larger forward calibration change.
        assert_eq!(counter.count(), 0);
        counter.advance(1.49, 0.0); // Reset/revert calibration at that instant.
        assert_eq!(counter.count(), 0);
        counter.advance(1.5, 0.0);
        assert_eq!(counter.count(), 1);
    }

    #[test]
    fn offset_change_preserves_actual_intervening_beats() {
        let mut counter = StoneCounter::default();
        counter.start(0.0, 0.0);
        counter.advance(3.6, 600.0); // Three real seconds, current grid shifted .6 s.
        assert_eq!(counter.count(), 2);
        counter.advance(6.0, 0.0); // Three more seconds, filter returns to zero.
        assert_eq!(counter.count(), 4);
    }

    #[test]
    fn backward_device_time_does_not_recount_old_boundaries() {
        let mut counter = StoneCounter::default();
        counter.start(0.0, 0.0);
        counter.advance(3.0, 0.0);
        counter.advance(0.0, 0.0);
        counter.advance(3.0, 0.0);
        assert_eq!(counter.count(), 2);
        counter.advance(4.5, 0.0);
        assert_eq!(counter.count(), 3);
    }

    #[test]
    fn invalid_samples_do_not_poison_the_counter() {
        let mut counter = StoneCounter::default();
        counter.start(f64::NAN, 0.0);
        counter.advance(3.0, 0.0);
        assert_eq!(counter.count(), 0);
        counter.start(0.0, 0.0);
        counter.advance(f64::INFINITY, 0.0);
        counter.advance(1.5, f64::NAN);
        counter.advance(3.0, 0.0);
        assert_eq!(counter.count(), 2);
    }
}
