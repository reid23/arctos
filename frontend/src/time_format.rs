//! Display API timestamps in the browser's local timezone.

use chrono::{NaiveDate, NaiveDateTime, SecondsFormat};

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
    let local = local_datetime_at(utc_dt);
    local.format("%m/%d %I:%M %p").to_string()
}

/// Calendar dates are not instants: preserve the entered day in every timezone.
pub fn format_calendar_date(value: &str) -> String {
    let date = value.split('T').next().unwrap_or(value);
    NaiveDate::parse_from_str(date, "%Y-%m-%d")
        .map(|date| date.format("%b %-d, %Y").to_string())
        .unwrap_or_else(|_| value.to_string())
}

pub fn format_calendar_range(start: &str, end: Option<&str>) -> String {
    let start = format_calendar_date(start);
    match end.map(format_calendar_date) {
        Some(end) if end != start => format!("{start} – {end}"),
        _ => start,
    }
}

fn timestamp_offset_minutes(utc: NaiveDateTime) -> i64 {
    #[cfg(target_arch = "wasm32")]
    {
        // Historical dates may fall in a different daylight saving season.
        let date = js_sys::Date::new(&wasm_bindgen::JsValue::from_f64(
            utc.and_utc().timestamp_millis() as f64,
        ));
        -date.get_timezone_offset() as i64
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = utc;
        0
    }
}

#[derive(Debug, PartialEq)]
pub struct RecordTimestampParts {
    pub date: String,
    pub time: String,
    pub datetime: String,
    pub accessible_label: String,
}

pub fn record_timestamp_parts(iso: &str, include_seconds: bool) -> Option<RecordTimestampParts> {
    let utc = parse_iso_utc_naive(iso)?;
    timestamp_parts_at_offset(utc, timestamp_offset_minutes(utc), include_seconds)
}

fn timestamp_parts_at_offset(
    utc: NaiveDateTime,
    offset_minutes: i64,
    include_seconds: bool,
) -> Option<RecordTimestampParts> {
    let local = utc.checked_add_signed(chrono::Duration::try_minutes(offset_minutes)?)?;
    let date = local.format("%b %-d, %Y").to_string();
    let time = local
        .format(if include_seconds {
            "%-I:%M:%S %p"
        } else {
            "%-I:%M %p"
        })
        .to_string();
    let offset = format!(
        "UTC{}{:02}:{:02}",
        if offset_minutes < 0 { "−" } else { "+" },
        offset_minutes.unsigned_abs() / 60,
        offset_minutes.unsigned_abs() % 60,
    );
    Some(RecordTimestampParts {
        accessible_label: format!("{date} at {time}, {offset}"),
        date,
        time,
        datetime: utc.and_utc().to_rfc3339_opts(SecondsFormat::AutoSi, true),
    })
}

/// Readable viewer-local instant. Unknown values remain visible rather than guessed.
pub fn format_record_timestamp(iso: &str) -> String {
    match record_timestamp_parts(iso, false) {
        Some(parts) => format!("{} · {}", parts.date, parts.time),
        None => iso.to_string(),
    }
}

/// Compatibility alias for existing registration and payment tables.
pub fn format_registration_timestamp(iso: &str) -> String {
    format_record_timestamp(iso)
}

/// Native date inputs use the timezone offset at the selected date.
pub fn datetime_local_input(iso: &str) -> String {
    #[cfg(target_arch = "wasm32")]
    {
        let date = js_sys::Date::new(&wasm_bindgen::JsValue::from_str(iso));
        if date.get_time().is_finite() {
            return format!(
                "{:04}-{:02}-{:02}T{:02}:{:02}",
                date.get_full_year(),
                date.get_month() + 1,
                date.get_date(),
                date.get_hours(),
                date.get_minutes()
            );
        }
    }
    chrono::DateTime::parse_from_rfc3339(iso)
        .map(|dt| dt.naive_utc().format("%Y-%m-%dT%H:%M").to_string())
        .unwrap_or_default()
}

pub fn datetime_local_to_utc(value: &str) -> Option<String> {
    let parsed = chrono::NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M").ok()?;
    #[cfg(target_arch = "wasm32")]
    {
        let date = js_sys::Date::new(&wasm_bindgen::JsValue::from_str(value));
        if !date.get_time().is_finite() {
            return None;
        }
        let iso: String = date.to_iso_string().into();
        // Reject skipped local times at the spring daylight saving change.
        if datetime_local_input(&iso) != value {
            return None;
        }
        let _ = parsed;
        Some(iso)
    }
    #[cfg(not(target_arch = "wasm32"))]
    Some(parsed.and_utc().to_rfc3339())
}

/// Convert a UTC timestamp using the browser offset at that instant.
pub fn local_datetime_at(utc: NaiveDateTime) -> NaiveDateTime {
    utc + chrono::Duration::minutes(timestamp_offset_minutes(utc))
}

#[cfg(test)]
fn format_timestamp_at_offset(iso: &str, offset_minutes: i64) -> String {
    match parse_iso_utc_naive(iso)
        .and_then(|utc| timestamp_parts_at_offset(utc, offset_minutes, false))
    {
        Some(parts) => format!("{} · {}", parts.date, parts.time),
        None => iso.to_string(),
    }
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
        assert!(record_timestamp_parts("", false).is_none());
        assert!(record_timestamp_parts("2026-10-24", false).is_none());
        assert!(record_timestamp_parts("not a timestamp", true).is_none());
    }

    #[test]
    fn calendar_dates_and_ranges_preserve_the_entered_day() {
        for input in [
            "2026-01-01",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00+14:00",
        ] {
            assert_eq!(format_calendar_date(input), "Jan 1, 2026");
        }
        assert_eq!(format_calendar_date("Date pending"), "Date pending");
        assert_eq!(format_calendar_date("TBA"), "TBA");
        assert_eq!(format_calendar_date("Time pending"), "Time pending");
        assert_eq!(format_calendar_date("2026-02-30"), "2026-02-30");
        assert_eq!(
            format_calendar_date("2026-02-30T09:30:00Z"),
            "2026-02-30T09:30:00Z"
        );
        assert_eq!(format_calendar_range("2026-01-01", None), "Jan 1, 2026");
        assert_eq!(
            format_calendar_range("2026-01-01", Some("2026-01-01T00:00:00")),
            "Jan 1, 2026"
        );
        assert_eq!(
            format_calendar_range("2026-12-31", Some("2027-01-02")),
            "Dec 31, 2026 – Jan 2, 2027"
        );
    }

    #[test]
    fn audit_timestamp_keeps_seconds_and_exact_instant() {
        let utc = parse_iso_utc_naive("2026-10-22T01:30:09.123456+02:00").unwrap();
        let parts = timestamp_parts_at_offset(utc, 330, true).unwrap();
        assert_eq!(parts.date, "Oct 22, 2026");
        assert_eq!(parts.time, "5:00:09 AM");
        assert_eq!(parts.datetime, "2026-10-21T23:30:09.123456Z");
        assert_eq!(
            parts.accessible_label,
            "Oct 22, 2026 at 5:00:09 AM, UTC+05:30"
        );
        let west = timestamp_parts_at_offset(utc, -420, true).unwrap();
        assert_eq!(west.date, "Oct 21, 2026");
        assert_eq!(west.time, "4:30:09 PM");
        assert!(west.accessible_label.ends_with("UTC−07:00"));
    }

    #[test]
    fn local_offset_is_applied_per_instant_not_to_calendar_dates() {
        // Browser selects the historical offset; this tests the pure conversion.
        assert_eq!(
            format_timestamp_at_offset("2026-01-15T12:30:00Z", -480),
            "Jan 15, 2026 · 4:30 AM"
        );
        assert_eq!(
            format_timestamp_at_offset("2026-07-15T12:30:00Z", -420),
            "Jul 15, 2026 · 5:30 AM"
        );
        assert_eq!(
            format_timestamp_at_offset("2026-12-31T23:45:00Z", 330),
            "Jan 1, 2027 · 5:15 AM"
        );
        assert_eq!(format_calendar_date("2026-01-15"), "Jan 15, 2026");
    }

    #[test]
    fn naive_utc_and_rfc3339_represent_the_same_recorded_instant() {
        let expected = format_timestamp_at_offset("2026-10-22T01:30:00Z", -420);
        for input in [
            "2026-10-22T01:30:00",
            "2026-10-22 01:30:00",
            "2026-10-22T01:30",
            "2026-10-22T03:30:00+02:00",
        ] {
            assert_eq!(format_timestamp_at_offset(input, -420), expected);
        }
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

/// Offset for a calendar day, used when shifting that day's match schedule.
pub fn calendar_day_offset_minutes(day: &str) -> i32 {
    #[cfg(target_arch = "wasm32")]
    {
        let date = js_sys::Date::new(&wasm_bindgen::JsValue::from_str(&format!("{day}T12:00")));
        if date.get_time().is_finite() {
            return -date.get_timezone_offset() as i32;
        }
    }
    let _ = day;
    0
}
