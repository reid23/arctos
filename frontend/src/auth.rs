use crate::api::AccountError;
use crate::types::User;
use dioxus::prelude::*;

/// One account check shared by navigation and task gates. Retrying it updates
/// every consumer without navigating away from the current page.
#[derive(Clone, Copy)]
pub struct AccountContext(pub Resource<Result<User, AccountError>>);

pub fn use_account() -> Resource<Result<User, AccountError>> {
    use_context::<AccountContext>().0
}

#[component]
pub fn AccountCheckNotice() -> Element {
    let mut account = use_account();
    let unavailable = matches!(account.read().as_ref(), Some(Err(AccountError::Unavailable)));
    let pending = *account.state().read() == UseResourceState::Pending;
    if !unavailable {
        return rsx! {};
    }
    rsx! {
        div { class: "alert alert-warning account-check-notice", id: "account-check-notice",
            p { class: "mb-2", role: "status",
                if pending { "Checking your account…" }
                else { "Your account could not be checked. You can keep browsing and try again." }
            }
            button { r#type: "button", class: "btn btn-outline-secondary", disabled: pending,
                onclick: move |_| { if account.finished() { account.restart(); } },
                if pending { "Checking…" } else { "Retry account check" }
            }
        }
    }
}
