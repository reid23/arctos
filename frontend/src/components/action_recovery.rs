//! UI state for writes whose response may be lost after the server commits.
#[derive(Clone, Debug, Default, PartialEq)]
pub enum ActionRecovery {
    #[default]
    Idle,
    Pending,
    Checking,
    NeedsCheck(String),
    Retryable(String),
}

impl ActionRecovery {
    pub fn busy(&self) -> bool {
        matches!(self, Self::Pending | Self::Checking)
    }

    pub fn blocks_write(&self) -> bool {
        self.busy() || matches!(self, Self::NeedsCheck(_))
    }

    pub fn message(&self) -> Option<&str> {
        match self {
            Self::NeedsCheck(message) | Self::Retryable(message) => Some(message),
            _ => None,
        }
    }

    /// Only a successful authoritative read may enable another write.
    pub fn after_check(still_present: Result<bool, String>, original_error: &str) -> Self {
        match still_present {
            Ok(true) => Self::Retryable(format!("{original_error} The item is still present. You can try again.")),
            Ok(false) => Self::Idle,
            Err(error) => Self::NeedsCheck(format!("{original_error} Could not check the current state: {error}. Check status before trying again.")),
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct RequestEpoch(u64);

impl RequestEpoch {
    pub fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(1);
        self.0
    }

    pub fn is_current(&self, request: u64) -> bool {
        self.0 == request
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum RegistrationIntent {
    Register(String),
    Update(String),
    Remove,
}

impl RegistrationIntent {
    pub fn applied(&self, registered: bool, weapon: Option<&str>) -> bool {
        match self {
            Self::Register(expected) | Self::Update(expected) => registered && weapon == Some(expected.as_str()),
            Self::Remove => !registered,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uncertain_write_requires_successful_read_before_retry() {
        let uncertain = ActionRecovery::after_check(Err("offline".into()), "Delete was not confirmed.");
        assert!(uncertain.blocks_write());
        assert!(!uncertain.busy());
        assert!(uncertain.message().unwrap().contains("Check status"));
        let present = ActionRecovery::after_check(Ok(true), "Delete was not confirmed.");
        assert!(!present.blocks_write());
        assert!(present.message().is_some());
        assert_eq!(ActionRecovery::after_check(Ok(false), "Delete was not confirmed."), ActionRecovery::Idle);
    }

    #[test]
    fn pending_and_checking_block_duplicate_writes() {
        for state in [ActionRecovery::Pending, ActionRecovery::Checking] {
            assert!(state.busy());
            assert!(state.blocks_write());
        }
        assert!(!ActionRecovery::Idle.blocks_write());
    }

    #[test]
    fn newer_query_or_clear_invalidates_old_response() {
        let mut epoch = RequestEpoch::default();
        let old = epoch.next();
        let new = epoch.next();
        assert!(!epoch.is_current(old));
        assert!(epoch.is_current(new));
        epoch.next(); // Clearing the search also invalidates the outstanding GET.
        assert!(!epoch.is_current(new));
    }

    #[test]
    fn registration_reconciliation_checks_role_and_presence() {
        let intent = RegistrationIntent::Register("LONG".into());
        assert!(intent.applied(true, Some("LONG")));
        assert!(!intent.applied(true, Some("CHAIN")));
        assert!(!intent.applied(false, Some("LONG")));
        assert!(!intent.applied(true, None));
        assert!(RegistrationIntent::Update("STAFF".into()).applied(true, Some("STAFF")));
        assert!(RegistrationIntent::Remove.applied(false, None));
        assert!(!RegistrationIntent::Remove.applied(true, None));
    }
}
