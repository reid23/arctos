//! Clock of a `TIMED` match's current set.
//!
//! A set lasts `set_minutes` of wall time, counted from the start of its first
//! point; the clock keeps running between points. The current set is the
//! highest set number among the started points. Mirrors `_set_clock_at` in
//! `app/services/scoreboard_service.py`.

/// Seconds left in the current set at `now_secs`, never negative.
///
/// `points` holds `(set_number, start_epoch_secs)` for each started point.
/// Before any point, the full set length remains.
pub fn seconds_remaining(points: &[(u32, f64)], set_minutes: u32, now_secs: f64) -> u32 {
    let set_seconds = set_minutes * 60;
    let current_set = points.iter().map(|(set_number, _)| *set_number).max();
    let current_set_start = current_set.and_then(|current_set| {
        points
            .iter()
            .filter(|(set_number, _)| *set_number == current_set)
            .map(|(_, start)| *start)
            .reduce(f64::min)
    });
    match current_set_start {
        Some(start) => set_seconds.saturating_sub((now_secs - start).max(0.0) as u32),
        None => set_seconds,
    }
}

/// `m:ss` display of a clock value in seconds.
pub fn format_clock(seconds: u32) -> String {
    format!("{}:{:02}", seconds / 60, seconds % 60)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_length_before_any_point() {
        assert_eq!(seconds_remaining(&[], 12, 1_000.0), 720);
    }

    #[test]
    fn runs_on_wall_time_from_the_first_point_of_the_current_set() {
        let points = [(1, 100.0), (1, 160.0), (2, 1_000.0), (2, 1_050.0)];
        assert_eq!(seconds_remaining(&points, 12, 1_100.0), 620);
    }

    #[test]
    fn stops_at_zero() {
        assert_eq!(seconds_remaining(&[(1, 0.0)], 1, 500.0), 0);
    }

    #[test]
    fn formats_minutes_and_seconds() {
        assert_eq!(format_clock(720), "12:00");
        assert_eq!(format_clock(65), "1:05");
    }
}
