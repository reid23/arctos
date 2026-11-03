//! Field feedback for custom validation, alongside native HTML constraints.

#[derive(Clone, Debug, PartialEq)]
pub struct FieldError {
    pub field_id: String,
    pub message: String,
}

impl FieldError {
    pub fn new(field_id: impl Into<String>, message: impl Into<String>) -> Self {
        Self { field_id: field_id.into(), message: message.into() }
    }
}

pub fn field_message(error: &Option<FieldError>, id: &str) -> Option<String> {
    error.as_ref().filter(|error| error.field_id == id).map(|error| error.message.clone())
}

pub fn described_by(error: &Option<FieldError>, id: &str, help_id: &str) -> Option<String> {
    let mut ids = Vec::new();
    if !help_id.is_empty() { ids.push(help_id.to_string()); }
    if field_message(error, id).is_some() { ids.push(format!("{id}-error")); }
    (!ids.is_empty()).then(|| ids.join(" "))
}

/// Wait for conditional errors/hidden field panels to render before focusing.
pub fn focus_after_render(id: impl Into<String>) {
    let id = id.into();
    #[cfg(target_arch = "wasm32")]
    dioxus::prelude::spawn(async move {
        gloo_timers::future::TimeoutFuture::new(0).await;
        crate::appearance::focus_control(&id);
    });
    #[cfg(not(target_arch = "wasm32"))]
    let _ = id;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn feedback_only_describes_its_field_and_keeps_existing_help() {
        let error = Some(FieldError::new("email", "Enter an email address."));
        assert_eq!(described_by(&error, "email", "email-help").as_deref(), Some("email-help email-error"));
        assert_eq!(described_by(&error, "name", "name-help").as_deref(), Some("name-help"));
        assert_eq!(field_message(&error, "name"), None);
        assert_eq!(described_by(&None, "email", ""), None);
        assert_eq!(described_by(&error, "email", "").as_deref(), Some("email-error"));
    }
}
