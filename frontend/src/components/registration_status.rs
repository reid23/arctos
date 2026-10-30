//! Registration presentation only; these labels never determine eligibility or actions.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct StatusPresentation {
    pub label: &'static str,
    pub class: &'static str,
    pub icon: &'static str,
}

pub(super) fn registration_status(status: &str) -> StatusPresentation {
    let (label, class, icon) = match status {
        "CONFIRMED" => ("Confirmed", "arctos-badge-success", "fas fa-circle-check"),
        "PENDING_TEAM_APPROVAL" => (
            "Awaiting Team Approval",
            "arctos-badge-warning",
            "fas fa-clock",
        ),
        "REJECTED" => ("Declined", "arctos-badge-danger", "fas fa-circle-xmark"),
        "CANCELLED" => ("Cancelled", "arctos-badge-neutral", "fas fa-circle-minus"),
        // Team PENDING is a transient cap-enforcement state, not an approval queue.
        _ => (
            "Status unavailable",
            "arctos-badge-neutral",
            "fas fa-circle-question",
        ),
    };
    StatusPresentation { label, class, icon }
}

pub(super) fn payment_status(paid: bool) -> StatusPresentation {
    if paid {
        StatusPresentation {
            label: "Paid",
            class: "arctos-badge-success",
            icon: "fas fa-check",
        }
    } else {
        StatusPresentation {
            label: "Unpaid",
            class: "arctos-badge-danger",
            icon: "fas fa-receipt",
        }
    }
}

pub(super) fn waiver_status(required: bool, status: Option<&str>) -> Option<StatusPresentation> {
    if !required {
        return None;
    }
    let (label, class, icon) = match status.unwrap_or("NOT_SIGNED") {
        "VALID" => ("Waiver valid", "arctos-badge-success", "fas fa-check"),
        "OUT_OF_DATE" => (
            "Waiver needs re-signing",
            "arctos-badge-warning",
            "fas fa-file-signature",
        ),
        "NOT_SIGNED" => (
            "Waiver not signed",
            "arctos-badge-danger",
            "fas fa-file-signature",
        ),
        _ => (
            "Waiver status unknown",
            "arctos-badge-neutral",
            "fas fa-circle-question",
        ),
    };
    Some(StatusPresentation { label, class, icon })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn player_lifecycle_states_have_distinct_meanings() {
        for (value, label, class) in [
            ("CONFIRMED", "Confirmed", "arctos-badge-success"),
            (
                "PENDING_TEAM_APPROVAL",
                "Awaiting Team Approval",
                "arctos-badge-warning",
            ),
            ("REJECTED", "Declined", "arctos-badge-danger"),
            ("CANCELLED", "Cancelled", "arctos-badge-neutral"),
        ] {
            let display = registration_status(value);
            assert_eq!(display.label, label);
            assert_eq!(display.class, class);
        }
    }

    #[test]
    fn unknown_and_transient_team_state_do_not_imply_approval_or_success() {
        for value in ["PENDING", "NEW_BACKEND_STATE", "", "confirmed"] {
            let display = registration_status(value);
            assert_eq!(display.label, "Status unavailable");
            assert_eq!(display.class, "arctos-badge-neutral");
        }
    }

    #[test]
    fn payment_and_waiver_are_independent_of_confirmation() {
        assert_eq!(registration_status("CONFIRMED").label, "Confirmed");
        assert_eq!(payment_status(false).label, "Unpaid");
        assert_eq!(payment_status(false).class, "arctos-badge-danger");
        assert_eq!(payment_status(true).label, "Paid");
        assert_eq!(
            waiver_status(true, None).unwrap().label,
            "Waiver not signed"
        );
    }

    #[test]
    fn waiver_validity_distinguishes_missing_replaced_and_unknown_versions() {
        for (value, label, class) in [
            ("VALID", "Waiver valid", "arctos-badge-success"),
            (
                "OUT_OF_DATE",
                "Waiver needs re-signing",
                "arctos-badge-warning",
            ),
            ("NOT_SIGNED", "Waiver not signed", "arctos-badge-danger"),
            (
                "NEW_BACKEND_STATE",
                "Waiver status unknown",
                "arctos-badge-neutral",
            ),
        ] {
            let display = waiver_status(true, Some(value)).unwrap();
            assert_eq!(display.label, label);
            assert_eq!(display.class, class);
        }
        for value in [None, Some("VALID"), Some("OUT_OF_DATE"), Some("NOT_SIGNED")] {
            assert_eq!(waiver_status(false, value), None);
        }
    }
}
