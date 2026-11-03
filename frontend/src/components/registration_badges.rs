use super::registration_status::{
    payment_status, registration_status, waiver_status, StatusPresentation,
};
use dioxus::prelude::*;

#[component]
fn StatusBadge(
    presentation: StatusPresentation,
    #[props(default)] show_icon: bool,
    #[props(default)] class: String,
) -> Element {
    rsx! {
        span { class: "badge arctos-registration-badge table-nowrap d-inline-flex align-items-center gap-1 {presentation.class} {class}",
            if show_icon { i { class: presentation.icon, aria_hidden: "true" } }
            span { "{presentation.label}" }
        }
    }
}

#[component]
pub fn RegistrationStatusBadge(status: String, #[props(default)] show_icon: bool) -> Element {
    rsx! { StatusBadge { presentation: registration_status(&status), show_icon } }
}

#[component]
pub fn PaymentStatusBadge(
    paid: bool,
    #[props(default)] show_icon: bool,
    #[props(default)] class: String,
) -> Element {
    rsx! { StatusBadge { presentation: payment_status(paid), show_icon, class } }
}

#[component]
pub fn WaiverStatusBadge(
    required: bool,
    status: Option<String>,
    #[props(default)] show_icon: bool,
    #[props(default)] show_not_required: bool,
) -> Element {
    match waiver_status(required, status.as_deref()) {
        Some(presentation) => rsx! { StatusBadge { presentation, show_icon } },
        None if show_not_required => {
            rsx! { span { class: "text-muted table-nowrap", "Not required" } }
        }
        None => rsx! {},
    }
}
