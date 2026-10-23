//! Display API timestamps in the browser's local timezone.

use chrono::NaiveDateTime;

/// Browser timezone offset in minutes (local = UTC + offset). Matches `schedule.rs`.
pub fn browser_tz_offset_minutes() -> i64 {
    #[cfg(target_arch = "wasm32")]
    {
        let date = js_sys::Date::new_0();
        let offset = date.get_timezone_offset();
        -offset as i64
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        0_i64
    }
}

fn parse_iso_utc_naive(s: &str) -> Option<NaiveDateTime> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(s) {
        return Some(dt.naive_utc());
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f") {
        return Some(dt);
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S") {
        return Some(dt);
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M") {
        return Some(dt);
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        return Some(dt);
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M") {
        return Some(dt);
    }
    None
}

/// `mm/dd hh:mm AM/PM` in the user's local timezone (browser on wasm).
pub fn format_match_display_local(iso: &str) -> String {
    let Some(utc_dt) = parse_iso_utc_naive(iso) else {
        return iso.to_string();
    };
    let local = utc_dt + chrono::Duration::minutes(browser_tz_offset_minutes());
    local.format("%m/%d %I:%M %p").to_string()
}

/// Readable local date and time for registration histories.
pub fn format_registration_timestamp(iso: &str) -> String {
    let Some(utc) = parse_iso_utc_naive(iso) else {
        return iso.to_string();
    };
    #[cfg(target_arch = "wasm32")]
    let offset_minutes = {
        // Historical dates may fall in a different daylight saving season.
        let date = js_sys::Date::new(&wasm_bindgen::JsValue::from_f64(
            utc.and_utc().timestamp_millis() as f64,
        ));
        -date.get_timezone_offset() as i64
    };
    #[cfg(not(target_arch = "wasm32"))]
    let offset_minutes = 0;
    format_utc_at_offset(utc, offset_minutes)
}

#[cfg(test)]
fn format_timestamp_at_offset(iso: &str, offset_minutes: i64) -> String {
    match parse_iso_utc_naive(iso) {
        Some(utc) => format_utc_at_offset(utc, offset_minutes),
        None => iso.to_string(),
    }
}

fn format_utc_at_offset(utc: NaiveDateTime, offset_minutes: i64) -> String {
    (utc + chrono::Duration::minutes(offset_minutes))
        .format("%b %-d, %Y · %-I:%M %p")
        .to_string()
}

#[cfg(test)]
mod timestamp_tests {
    use super::*;

    #[test]
    fn registration_timestamp_uses_local_date_across_midnight() {
        assert_eq!(
            format_timestamp_at_offset("2026-10-22T01:30:00.123456", -420),
            "Oct 21, 2026 · 6:30 PM"
        );
        assert_eq!(
            format_timestamp_at_offset("2026-10-22T01:30:00+02:00", 0),
            "Oct 21, 2026 · 11:30 PM"
        );
    }

    #[test]
    fn unknown_timestamp_is_preserved() {
        assert_eq!(
            format_registration_timestamp("Date pending"),
            "Date pending"
        );
        assert_eq!(
            format_timestamp_at_offset("Date pending", 60),
            "Date pending"
        );
    }

    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn native_registration_timestamp_keeps_utc_fallback() {
        assert_eq!(
            format_registration_timestamp("2026-01-15T12:30:00+02:00"),
            "Jan 15, 2026 · 10:30 AM"
        );
    }
}
