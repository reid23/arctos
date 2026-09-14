//! Display helpers shared across Dioxus pages and components.
//!
//! Currently only `short_or_truncate`, which picks a layout-friendly
//! label for team registrations: their optional shortname when present,
//! falling back to a 12-character truncation of the full name with an
//! ellipsis when the name overflows.

/// Return a team label suitable for layout-constrained UI.
///
/// * If `shortname` is `Some` with non-whitespace contents, the trimmed
///   value is returned.
/// * Otherwise the full name is returned verbatim if it is at most 8
///   Unicode codepoints long, or truncated to the first 7 codepoints
///   plus `"..."`.
pub fn short_or_truncate(full: &str, shortname: Option<&str>) -> String {
    if let Some(s) = shortname {
        let trimmed = s.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    let count = full.chars().count();
    if count <= 8 {
        full.to_string()
    } else {
        let prefix: String = full.chars().take(7).collect();
        format!("{prefix}...")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn returns_shortname_when_present() {
        assert_eq!(
            short_or_truncate("Boston Common Stones", Some("BCS")),
            "BCS"
        );
    }

    #[test]
    fn trims_shortname_whitespace() {
        assert_eq!(short_or_truncate("Boston", Some("  BCS  ")), "BCS");
    }

    #[test]
    fn ignores_empty_shortname() {
        assert_eq!(short_or_truncate("Boston", Some("")), "Boston");
        assert_eq!(short_or_truncate("Boston", Some("   ")), "Boston");
    }

    #[test]
    fn returns_full_name_when_short_enough() {
        assert_eq!(short_or_truncate("Short", None), "Short");
        assert_eq!(short_or_truncate("Exactly8", None), "Exactly8");
    }

    #[test]
    fn truncates_long_name_with_ellipsis() {
        assert_eq!(
            short_or_truncate("Boston Common Stones", None),
            "Boston ..."
        );
    }

    #[test]
    fn counts_unicode_codepoints_not_bytes() {
        let full = "日本語チームXXXXXXXXXX";
        let out = short_or_truncate(full, None);
        let prefix: String = full.chars().take(7).collect();
        assert_eq!(out, format!("{prefix}..."));
    }
}
