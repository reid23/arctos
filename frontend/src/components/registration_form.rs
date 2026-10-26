use crate::types::RegistrationFormConfig;
use dioxus::prelude::*;

#[component]
pub fn RegistrationFormNotice(config: RegistrationFormConfig, personal_url: Option<String>) -> Element {
    if !config.registration_form_required {
        return rsx! {};
    }

    rsx! {
        div { class: "alert alert-info mb-3",
            h3 { class: "h6", "Registration form required" }
            p { class: "mb-2",
                if personal_url.is_some() {
                    "Open your personal form link below. Your registration code is filled in automatically; leave it unchanged. Submit the form, then return here to register."
                } else {
                    "Ask the player to open their personal form link on the registration page for this event or league and submit the form."
                }
                " Arctos checks for a response before confirming registration."
            }
            if let Some(url) = personal_url {
                a { href: url, target: "_blank", rel: "noopener noreferrer",
                    "Open my registration form (opens in a new tab)"
                }
            }
        }
    }
}

#[component]
pub fn RegistrationFormSettings(
    mut draft: Signal<RegistrationFormConfig>,
    disabled: bool,
) -> Element {
    let config = draft.read().clone();
    rsx! {
        fieldset { class: "mb-4", disabled,
            legend { class: "h5", "Registration form" }
            div { class: "form-check mb-2",
                input {
                    class: "form-check-input", r#type: "checkbox",
                    id: "registration_form_required",
                    checked: config.registration_form_required,
                    aria_describedby: "registration-form-help",
                    onchange: move |event| draft.write().registration_form_required = event.checked(),
                }
                label { class: "form-check-label", r#for: "registration_form_required",
                    "Require a Google Form response for player registration"
                }
            }
            p { id: "registration-form-help", class: "form-text",
                "Players receive a personal form link with their registration code filled in. Existing confirmed registrations remain valid."
            }
            if config.registration_form_required {
                if !config.registration_form_available {
                    p { class: "alert alert-warning",
                        "Form verification is unavailable. Contact the site administrator to configure Google Sheets access."
                    }
                }
                ol { class: "form-text",
                    li { "Add a required short-answer question named “Arctos registration code” to your Google Form. Explain that Arctos fills it automatically and players should leave it unchanged." }
                    li { "In Google Forms, choose Pre-fill form, enter ARCTOS_CODE for that question, then choose Get link and copy it." }
                    li { "Publish the form for its intended players, link it to a response spreadsheet, and enter its details below." }
                }
                div { class: "mb-3",
                    label { class: "form-label", r#for: "registration_form_url", "Prefilled Google Form link" }
                    input {
                        class: "form-control", r#type: "url", id: "registration_form_url", required: true,
                        aria_describedby: "registration-form-url-help",
                        value: config.registration_form_url.unwrap_or_default(),
                        oninput: move |event| draft.write().registration_form_url = Some(event.value()),
                    }
                    div { id: "registration-form-url-help", class: "form-text",
                        "Use the full docs.google.com link containing ARCTOS_CODE. Arctos replaces this placeholder for each player."
                    }
                }
                div { class: "mb-3",
                    label { class: "form-label", r#for: "registration_form_sheet_url", "Response spreadsheet link" }
                    input {
                        class: "form-control", r#type: "url", id: "registration_form_sheet_url", required: true,
                        aria_describedby: "registration-form-sheet-help",
                        value: config.registration_form_sheet_url.unwrap_or_default(),
                        oninput: move |event| draft.write().registration_form_sheet_url = Some(event.value()),
                    }
                    div { id: "registration-form-sheet-help", class: "form-text",
                        "Open the response tab in Google Sheets and copy its link. This link is visible only to organizers."
                        if let Some(email) = config.registration_form_service_account_email {
                            p { class: "mb-0 mt-1",
                                "Share the spreadsheet with "
                                strong { style: "overflow-wrap: anywhere;", "{email}" }
                                " as a Viewer. Keep general access Restricted."
                            }
                        }
                    }
                }
                div { class: "mb-3",
                    label { class: "form-label", r#for: "registration_form_code_column", "Registration code column heading" }
                    input {
                        class: "form-control", r#type: "text", id: "registration_form_code_column", required: true,
                        aria_describedby: "registration-form-column-help",
                        value: config.registration_form_code_column.unwrap_or_default(),
                        oninput: move |event| draft.write().registration_form_code_column = Some(event.value()),
                    }
                    div { id: "registration-form-column-help", class: "form-text",
                        "Copy the first-row heading for the registration code question from the response tab."
                    }
                }
                p { class: "form-text mb-0", "Saving checks spreadsheet access and the code column. After saving, use a player account to submit its personal form and complete registration to test the setup." }
            }
        }
    }
}
