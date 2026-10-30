//! Fixed-time activities share an editor across schedules and side competitions.

use super::{Modal, ModalKind};
use crate::{
    api,
    types::{SaveScheduleItem, ScheduleItemData},
    Route,
};
use dioxus::prelude::*;

use crate::time_format::{
    datetime_local_input as schedule_item_local_input, datetime_local_to_utc as local_input_utc,
};

fn activity_date_time_label(iso: &str) -> String {
    chrono::NaiveDateTime::parse_from_str(&schedule_item_local_input(iso), "%Y-%m-%dT%H:%M")
        .map(|dt| dt.format("%a, %b %-d · %H:%M").to_string())
        .unwrap_or_else(|_| iso.to_string())
}

pub fn activity_time_label(iso: &str) -> String {
    chrono::NaiveDateTime::parse_from_str(&schedule_item_local_input(iso), "%Y-%m-%dT%H:%M")
        .map(|dt| dt.format("%H:%M").to_string())
        .unwrap_or_else(|_| iso.to_string())
}

pub fn activity_time_range(start: &str, end: &str) -> String {
    let start_local = schedule_item_local_input(start);
    let end_local = schedule_item_local_input(end);
    let same_day = start_local.get(..10) == end_local.get(..10);
    let end_label = if same_day {
        activity_time_label(end)
    } else {
        activity_date_time_label(end)
    };
    format!("{} – {}", activity_date_time_label(start), end_label)
}

fn input_utc_preserving_offset(value: &str, original: Option<&str>) -> Option<String> {
    if let Some(original) = original {
        if schedule_item_local_input(original) == value {
            return Some(original.to_string());
        }
    }
    local_input_utc(value)
}

fn end_after_start_change(
    old_start: &str,
    old_end: &str,
    new_start: &str,
    original: Option<&(String, String)>,
) -> Option<String> {
    let old_start = chrono::DateTime::parse_from_rfc3339(&input_utc_preserving_offset(
        old_start,
        original.map(|times| times.0.as_str()),
    )?)
    .ok()?;
    let old_end = chrono::DateTime::parse_from_rfc3339(&input_utc_preserving_offset(
        old_end,
        original.map(|times| times.1.as_str()),
    )?)
    .ok()?;
    let new_start = chrono::DateTime::parse_from_rfc3339(&input_utc_preserving_offset(
        new_start,
        original.map(|times| times.0.as_str()),
    )?)
    .ok()?;
    let duration = old_end - old_start;
    if duration <= chrono::Duration::zero() {
        return None;
    }
    Some(schedule_item_local_input(
        &new_start.checked_add_signed(duration)?.to_rfc3339(),
    ))
}

fn timezone_label() -> String {
    #[cfg(target_arch = "wasm32")]
    {
        let formatter =
            js_sys::Intl::DateTimeFormat::new(&js_sys::Array::new(), &js_sys::Object::new());
        if let Ok(value) = js_sys::Reflect::get(
            &formatter.resolved_options(),
            &wasm_bindgen::JsValue::from_str("timeZone"),
        ) {
            if let Some(zone) = value.as_string() {
                return zone.replace('_', " ");
            }
        }
    }
    "UTC".into()
}

#[component]
pub fn ScheduleItemDetails(
    tournament_url: String,
    item_id: i32,
    on_close: EventHandler<()>,
) -> Element {
    let url = tournament_url.clone();
    let mut detail = use_resource(move || {
        let url = url.clone();
        async move { api::schedule_item(&url, item_id).await }
    });
    rsx! {
        style { {include_str!("schedule_item.css")} }
        Modal {
            id: "schedule-activity-details",
            labelled_by: "schedule-activity-title",
            on_close,
            div { class: "modal-header",
                h2 {
                    id: "schedule-activity-title",
                    tabindex: "-1",
                    class: "modal-title h5",
                    if let Some(Ok(item)) = detail.read().as_ref() {
                        "{item.title}"
                    } else {
                        "Schedule details"
                    }
                }
                button {
                    r#type: "button",
                    class: "btn-close",
                    aria_label: "Close details",
                    onclick: move |_| on_close.call(()),
                }
            }
            div { class: "modal-body",
                match detail.read().as_ref() {
                    Some(Ok(item)) => rsx! {
                        p { class: "small text-muted mb-2",
                            if item.sidecomp_id.is_some() {
                                "Side competition"
                            } else {
                                "Activity"
                            }
                        }
                        p { class: "mb-2",
                            i { class: "far fa-clock me-2", aria_hidden: true }
                            "{activity_time_range(&item.start_time, &item.end_time)}"
                        }
                        p { class: "small text-muted", "{timezone_label()}" }
                        if let Some(field) = &item.field {
                            p { class: "mb-2",
                                i { class: "fas fa-map-marker-alt me-2", aria_hidden: true }
                                "{field}"
                            }
                        }
                        if let Some(location) = &item.location {
                            p { class: "mb-3", "{location}" }
                        }
                        if let Some(description) = &item.description {
                            p { class: "schedule-activity-description", "{description}" }
                        }
                        if let Some(comp_id) = item.sidecomp_id {
                            Link {
                                to: Route::SideCompDetail {
                                    url: tournament_url.clone(),
                                    comp_id,
                                },
                                class: "btn btn-primary",
                                "View competition"
                            }
                        }
                    },
                    Some(Err(error)) => rsx! {
                        p { role: "alert", "{error}" }
                        button { class: "btn btn-outline-secondary", onclick: move |_| detail.restart(), "Try again" }
                    },
                    None => rsx! {
                        p { role: "status", "Loading details…" }
                    },
                }
            }
        }
    }
}

#[component]
pub fn ScheduleItemDialog(
    tournament_url: String,
    #[props(default)] existing: Option<ScheduleItemData>,
    #[props(default)] initial_sidecomp: Option<i32>,
    #[props(default)] initial_date: Option<String>,
    #[props(default)] initial_start_local: Option<String>,
    #[props(default)] initial_end_local: Option<String>,
    #[props(default)] initial_field: Option<String>,
    #[props(default)] initial_sidecomp_mode: bool,
    #[props(default)] return_focus_id: Option<String>,
    on_close: EventHandler<()>,
    on_saved: EventHandler<()>,
) -> Element {
    let url = tournament_url.clone();
    let mut setup = use_resource(move || {
        let url = url.clone();
        async move { api::schedule_setup(&url).await }
    });
    let url = tournament_url.clone();
    let mut competitions = use_resource(move || {
        let url = url.clone();
        async move { api::sidecomps_list(&url).await }
    });
    let mut mode = use_signal(|| {
        if existing
            .as_ref()
            .and_then(|i| i.sidecomp_id)
            .or(initial_sidecomp)
            .is_some()
            || (existing.is_none() && initial_sidecomp_mode)
        {
            "existing".to_string()
        } else {
            "activity".to_string()
        }
    });
    let mut competition_id = use_signal(|| {
        existing
            .as_ref()
            .and_then(|i| i.sidecomp_id)
            .or(initial_sidecomp)
            .map(|id| id.to_string())
            .unwrap_or_default()
    });
    let mut title = use_signal(|| {
        existing
            .as_ref()
            .filter(|i| i.sidecomp_id.is_none())
            .map(|i| i.title.clone())
            .unwrap_or_default()
    });
    let mut description = use_signal(|| {
        existing
            .as_ref()
            .filter(|i| i.sidecomp_id.is_none())
            .and_then(|i| i.description.clone())
            .unwrap_or_default()
    });
    let mut kind = use_signal(|| "DUELING".to_string());
    let mut start = use_signal(|| {
        existing
            .as_ref()
            .map(|i| schedule_item_local_input(&i.start_time))
            .or_else(|| initial_start_local.clone())
            .unwrap_or_default()
    });
    let mut end = use_signal(|| {
        existing
            .as_ref()
            .map(|i| schedule_item_local_input(&i.end_time))
            .or(initial_end_local)
            .or_else(|| {
                let start = chrono::NaiveDateTime::parse_from_str(
                    initial_start_local.as_deref()?,
                    "%Y-%m-%dT%H:%M",
                )
                .ok()?;
                Some(
                    start
                        .checked_add_signed(chrono::Duration::minutes(30))?
                        .format("%Y-%m-%dT%H:%M")
                        .to_string(),
                )
            })
            .unwrap_or_default()
    });
    let mut field = use_signal(|| {
        existing
            .as_ref()
            .map(|i| i.field.clone().unwrap_or_default())
            .or(initial_field)
            .unwrap_or_default()
    });
    let mut location = use_signal(|| {
        existing
            .as_ref()
            .and_then(|i| i.location.clone())
            .unwrap_or_default()
    });
    let mut busy = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut confirm_remove = use_signal(|| false);
    let selected_details = use_resource(move || async move {
        if let Ok(id) = competition_id().parse::<i32>() {
            api::sidecomp_detail(id).await.ok()
        } else {
            None
        }
    });
    use_effect(move || {
        if start.peek().is_empty() {
            if let Some(Ok(data)) = setup.read().as_ref() {
                let day = initial_date
                    .clone()
                    .filter(|v| !v.is_empty())
                    .unwrap_or_else(|| data.tournament.start_date.chars().take(10).collect());
                start.set(format!("{day}T09:00"));
                end.set(format!("{day}T09:30"));
            }
        }
    });
    let original_times = existing
        .as_ref()
        .map(|item| (item.start_time.clone(), item.end_time.clone()));
    let editing_id = existing.as_ref().map(|i| i.id);
    let original_for_shift = original_times.clone();
    let request_id = use_signal(|| uuid::Uuid::new_v4().to_string());
    let url_for_save = tournament_url.clone();
    let url_for_remove = tournament_url.clone();
    let loaded = matches!(setup.read().as_ref(), Some(Ok(data)) if data.is_to);
    rsx! {
        style { {include_str!("schedule_item.css")} }
        Modal {
            id: "schedule-activity-editor",
            labelled_by: "schedule-activity-editor-title",
            return_focus_id,
            dismissible: !busy(),
            dismiss_on_backdrop: false,
            on_close,
            div { class: "modal-header",
                h2 {
                    id: "schedule-activity-editor-title",
                    class: "modal-title h5",
                    tabindex: "-1",
                    if editing_id.is_some() {
                        "Edit scheduled activity"
                    } else {
                        "Add to schedule"
                    }
                }
                button {
                    r#type: "button",
                    class: "btn-close",
                    aria_label: "Close editor",
                    disabled: busy(),
                    onclick: move |_| on_close.call(()),
                }
            }
            form {
                class: "schedule-activity-form",
                onsubmit: move |event| {
                    event.prevent_default();
                    if busy() || !loaded {
                        return;
                    }
                    let Some(start_time) = input_utc_preserving_offset(&start(), original_times.as_ref().map(|times| times.0.as_str())) else {
                        error.set(Some("Choose a valid start date and time.".into()));
                        return;
                    };
                    let Some(end_time) = input_utc_preserving_offset(&end(), original_times.as_ref().map(|times| times.1.as_str())) else {
                        error.set(Some("Choose a valid end date and time.".into()));
                        return;
                    };
                    if chrono::DateTime::parse_from_rfc3339(&end_time).ok() <= chrono::DateTime::parse_from_rfc3339(&start_time).ok() {
                        error.set(Some("End time must be after start time.".into()));
                        return;
                    }
                    let sidecomp_id = if mode() == "existing" {
                        match competition_id().parse::<i32>() {
                            Ok(id) => Some(id),
                            Err(_) => {
                                error.set(Some("Choose a side competition.".into()));
                                return;
                            }
                        }
                    } else {
                        None
                    };
                    let request = SaveScheduleItem {
                        request_id: request_id(),
                        sidecomp_id,
                        title: (mode() == "activity").then(|| title()),
                        description: (mode() == "activity").then(|| description()),
                        start_time,
                        end_time,
                        field: (!field().is_empty()).then(|| field()),
                        location: (!location().trim().is_empty()).then(|| location()),
                    };
                    let url = url_for_save.clone();
                    let new_comp = mode() == "new";
                    busy.set(true);
                    error.set(None);
                    spawn(async move {
                        let result = if new_comp {
                            api::create_scheduled_sidecomp(
                                    &url,
                                    &title(),
                                    &kind(),
                                    &description(),
                                    &request,
                                )
                                .await
                                .map(|_| ())
                        } else {
                            api::save_schedule_item(&url, editing_id, &request).await.map(|_| ())
                        };
                        busy.set(false);
                        match result {
                            Ok(()) => on_saved.call(()),
                            Err(message) => error.set(Some(message)),
                        }
                    });
                },
                div { class: "modal-body",
                    match setup.read().as_ref() {
                        Some(Err(message)) => rsx! {
                            p { role: "alert", "{message}" }
                            button {
                                r#type: "button",
                                class: "btn btn-outline-secondary",
                                onclick: move |_| setup.restart(),
                                "Try again"
                            }
                        },
                        None => rsx! {
                            p { role: "status", "Loading schedule…" }
                        },
                        Some(Ok(data)) if !data.is_to => rsx! {
                            p { role: "alert", "Only event organizers can edit the schedule." }
                        },
                        Some(Ok(data)) => rsx! {
                            if editing_id.is_none() && initial_sidecomp.is_none() {
                                div { class: "mb-3",
                                    label { class: "form-label", r#for: "schedule-activity-kind", "What are you adding?" }
                                    select {
                                        id: "schedule-activity-kind",
                                        class: "form-select",
                                        value: "{mode}",
                                        disabled: busy(),
                                        onchange: move |e| mode.set(e.value()),
                                        option { value: "activity", selected: mode() == "activity", "Activity or announcement" }
                                        option { value: "existing", selected: mode() == "existing", "Existing side competition" }
                                        option { value: "new", selected: mode() == "new", "New side competition" }
                                    }
                                }
                            }
                            if mode() == "existing" {
                                div { class: "mb-3",
                                    label { class: "form-label", r#for: "schedule-activity-comp", "Side competition" }
                                    select {
                                        id: "schedule-activity-comp",
                                        class: "form-select",
                                        value: "{competition_id}",
                                        required: true,
                                        disabled: busy() || editing_id.is_some() || initial_sidecomp.is_some(),
                                        onchange: move |e| competition_id.set(e.value()),
                                        option { value: "", selected: competition_id().is_empty(), "Choose a competition" }
                                        if let Some(Ok(items)) = competitions.read().as_ref() {
                                            for item in items {
                                                option { value: "{item.id}", selected: competition_id() == item.id.to_string(), "{item.name}" }
                                            }
                                        }
                                    }
                                    if let Some(Err(message)) = competitions.read().as_ref() {
                                        p { role: "alert", "{message}" }
                                        button {
                                            r#type: "button",
                                            class: "btn btn-link",
                                            onclick: move |_| competitions.restart(),
                                            "Try again"
                                        }
                                    }
                                    if let Some(Some(comp)) = selected_details.read().as_ref() {
                                        if let Some(description) = &comp.description {
                                            p { class: "schedule-activity-description small text-muted mt-2",
                                                "{description}"
                                            }
                                        }
                                    }
                                }
                            } else {
                                div { class: "mb-3",
                                    label { class: "form-label", r#for: "schedule-activity-name", "Name" }
                                    input {
                                        id: "schedule-activity-name",
                                        class: "form-control",
                                        value: "{title}",
                                        required: true,
                                        maxlength: if mode() == "new" { "100" } else { "200" },
                                        disabled: busy(),
                                        oninput: move |e| title.set(e.value()),
                                        placeholder: "e.g. Opening announcements",
                                    }
                                }
                                if mode() == "new" {
                                    div { class: "mb-3",
                                        label { class: "form-label", r#for: "schedule-competition-type", "Competition type" }
                                        select {
                                            id: "schedule-competition-type",
                                            class: "form-select",
                                            value: "{kind}",
                                            disabled: busy(),
                                            onchange: move |e| kind.set(e.value()),
                                            option { value: "DUELING", selected: kind() == "DUELING", "Dueling" }
                                            option { value: "CHAIN_BREAKING", selected: kind() == "CHAIN_BREAKING", "Chain / Breaking" }
                                            option { value: "OTHER", selected: kind() == "OTHER", "Other" }
                                        }
                                    }
                                }
                                div { class: "mb-3",
                                    label { class: "form-label", r#for: "schedule-activity-description", "Description" }
                                    textarea {
                                        id: "schedule-activity-description",
                                        class: "form-control",
                                        rows: "3",
                                        maxlength: "20000",
                                        value: "{description}",
                                        disabled: busy(),
                                        oninput: move |e| description.set(e.value()),
                                    }
                                }
                            }
                            div { class: "schedule-activity-times",
                                div {
                                    label { class: "form-label", r#for: "schedule-activity-start", "Starts" }
                                    input {
                                        id: "schedule-activity-start",
                                        r#type: "datetime-local",
                                        class: "form-control",
                                        value: "{start}",
                                        required: true,
                                        disabled: busy(),
                                        oninput: move |e| {
                                            let next = e.value();
                                            if let Some(next_end) = end_after_start_change(&start(), &end(), &next, original_for_shift.as_ref()) {
                                                end.set(next_end);
                                            }
                                            start.set(next);
                                        },
                                    }
                                }
                                div {
                                    label { class: "form-label", r#for: "schedule-activity-end", "Ends" }
                                    input {
                                        id: "schedule-activity-end",
                                        r#type: "datetime-local",
                                        class: "form-control",
                                        value: "{end}",
                                        required: true,
                                        disabled: busy(),
                                        oninput: move |e| end.set(e.value()),
                                    }
                                }
                            }
                            p { class: "form-text mb-3", "{timezone_label()}. This time stays fixed if matches run late." }
                            div { class: "mb-3",
                                label { class: "form-label", r#for: "schedule-activity-field", "Where it happens (optional)" }
                                select {
                                    id: "schedule-activity-field",
                                    class: "form-select",
                                    value: "{field}",
                                    disabled: busy(),
                                    onchange: move |e| field.set(e.value()),
                                    option { value: "", selected: field().is_empty(), "No field" }
                                    for option in &data.fields {
                                        option { value: "{option.name}", selected: field() == option.name, "{option.name}" }
                                    }
                                }
                                p { class: "form-text", "Shown on every field. Choosing a field here does not block matches." }
                            }
                            div {
                                label { class: "form-label", r#for: "schedule-activity-location", "Location" }
                                input {
                                    id: "schedule-activity-location",
                                    class: "form-control",
                                    value: "{location}",
                                    maxlength: "200",
                                    disabled: busy(),
                                    placeholder: "Optional, e.g. registration desk",
                                    oninput: move |e| location.set(e.value()),
                                }
                            }
                        },
                    }
                    if let Some(message) = error() {
                        div {
                            class: "alert alert-danger mt-3 mb-0",
                            role: "alert",
                            "{message}"
                        }
                    }
                }
                div { class: "modal-footer schedule-activity-footer",
                    if editing_id.is_some() {
                        button {
                            r#type: "button",
                            class: "btn btn-link text-danger me-auto",
                            disabled: busy(),
                            onclick: move |_| confirm_remove.set(true),
                            "Remove"
                        }
                    }
                    button {
                        r#type: "button",
                        class: "btn btn-outline-secondary",
                        disabled: busy(),
                        onclick: move |_| on_close.call(()),
                        "Cancel"
                    }
                    button {
                        r#type: "submit",
                        class: "btn btn-primary",
                        disabled: busy() || !loaded,
                        if busy() {
                            "Saving…"
                        } else if editing_id.is_some() {
                            "Save changes"
                        } else {
                            "Add to schedule"
                        }
                    }
                }
            }
        }
        if confirm_remove() {
            Modal {
                id: "schedule-remove-confirm",
                labelled_by: "schedule-remove-title",
                kind: ModalKind::AlertDialog,
                dismissible: !busy(),
                on_close: move |_| confirm_remove.set(false),
                div { class: "modal-header",
                    h2 {
                        id: "schedule-remove-title",
                        tabindex: "-1",
                        class: "modal-title h5",
                        "Remove from schedule?"
                    }
                }
                div { class: "modal-body",
                    p { class: "mb-0",
                        if mode() == "existing" {
                            "The side competition and its results will remain available."
                        } else {
                            "This activity will be removed from the schedule."
                        }
                    }
                }
                div { class: "modal-footer",
                    button {
                        r#type: "button",
                        class: "btn btn-outline-secondary",
                        disabled: busy(),
                        onclick: move |_| confirm_remove.set(false),
                        "Keep activity"
                    }
                    button {
                        r#type: "button",
                        class: "btn btn-danger",
                        disabled: busy(),
                        onclick: move |_| {
                            let Some(id) = editing_id else {
                                return;
                            };
                            if busy() {
                                return;
                            }
                            let url = url_for_remove.clone();
                            busy.set(true);
                            spawn(async move {
                                let result = api::delete_schedule_item(&url, id).await;
                                busy.set(false);
                                confirm_remove.set(false);
                                match result {
                                    Ok(_) => on_saved.call(()),
                                    Err(message) => error.set(Some(message)),
                                }
                            });
                        },
                        if busy() {
                            "Removing…"
                        } else {
                            "Remove from schedule"
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn moving_start_preserves_duration_across_dates() {
        assert_eq!(
            end_after_start_change(
                "2026-10-26T09:00",
                "2026-10-26T10:30",
                "2026-10-27T23:00",
                None
            ),
            Some("2026-10-28T00:30".into())
        );
    }

    #[test]
    fn incomplete_or_invalid_times_do_not_overwrite_end() {
        assert_eq!(
            end_after_start_change(
                "2026-10-26T09:00",
                "2026-10-26T08:00",
                "2026-10-27T12:00",
                None
            ),
            None
        );
        assert_eq!(
            end_after_start_change("2026-10-26T09:00", "2026-10-26T10:00", "", None),
            None
        );
        assert!(local_input_utc("2026-02-30T09:00").is_none());
    }

    #[test]
    fn overnight_range_keeps_both_dates() {
        assert_eq!(activity_time_label("2026-10-26T14:00:00Z"), "14:00");
        assert_eq!(
            activity_time_range("2026-10-26T23:00:00Z", "2026-10-27T01:00:00Z"),
            "Mon, Oct 26 · 23:00 – Tue, Oct 27 · 01:00"
        );
        assert_eq!(
            activity_time_range("2026-10-26T09:00:00Z", "2026-10-26T10:00:00Z"),
            "Mon, Oct 26 · 09:00 – 10:00"
        );
    }
}
