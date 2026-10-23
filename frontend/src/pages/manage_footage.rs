use crate::api;
use crate::types::FootageCameraRow;
use crate::Route;
use dioxus::prelude::*;

/// Prefer a full YouTube watch URL; tolerate legacy bare video IDs.
fn youtube_href(link: &str) -> String {
    let trimmed = link.trim();
    if trimmed.starts_with("https://") || trimmed.starts_with("http://") {
        trimmed.to_string()
    } else {
        format!("https://www.youtube.com/watch?v={trimmed}")
    }
}

#[component]
pub fn ManageFootage(url: String) -> Element {
    let mut query = use_signal(|| String::new());
    let refresh = use_signal(|| 0u32);
    let delete_error = use_signal(|| None::<String>);
    let mut deleting = use_signal(|| None::<String>);

    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        let _ = refresh();
        async move {
            let tournament = api::tournament_detail(&u)
                .await
                .map_err(|e| e.to_string())?;
            let cams = api::list_event_footage(&u)
                .await
                .map_err(|e| e.to_string())?;
            Ok::<(String, Vec<FootageCameraRow>), String>((
                tournament.tournament.name,
                cams.cameras,
            ))
        }
    });
    let val = data.value();

    rsx! {
        if let Some(Ok((tournament_name, cameras))) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h2 { "Match footage" }
                    p { class: "text-muted", "Review recordings for {tournament_name}. Open footage to check it before removing an entry." }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::TournamentHome { url: url.clone() }, "{tournament_name}" } }
                            li { class: "breadcrumb-item active", "Manage Footage" }
                        }
                    }
                }
            }

            div { class: "row mb-3",
                div { class: "col-md-8",
                    input {
                        class: "form-control",
                        r#type: "text",
                        placeholder: "Search match, field, camera, status, user, timestamp",
                        aria_label: "Search match footage",
                        value: "{query}",
                        oninput: move |e| query.set(e.value()),
                    }
                }
            }

            if let Some(err) = delete_error() {
                div { class: "alert alert-danger", role: "alert", "{err}" }
            }

            div { class: "row",
                div { class: "col-12",
                    div { class: "card data-section",
                        div { class: "card-body",
                            div { class: "table-responsive", role: "region", "aria-label": "Recorded match footage", tabindex: "0",
                                table { class: "table table-striped align-middle footage-table",
                                    thead {
                                        tr {
                                            th { class: "table-nowrap", "Match" }
                                            th { class: "table-nowrap", "Field" }
                                            th { class: "table-nowrap", "Camera" }
                                            th { class: "table-nowrap", "Status" }
                                            th { class: "table-nowrap", "User" }
                                            th { class: "table-nowrap", "World Start Timestamp" }
                                            th { class: "table-nowrap", "Link" }
                                            th { class: "table-nowrap", "Actions" }
                                        }
                                    }
                                    tbody {
                                        for cam in cameras.iter().filter(|cam| {
                                            let q = query().to_lowercase();
                                            if q.is_empty() {
                                                return true;
                                            }
                                            let hay = format!(
                                                "{} {} {} {} {} {}",
                                                cam.match_name,
                                                cam.field_name,
                                                cam.camera_name,
                                                cam.status,
                                                cam.user.clone().unwrap_or_default(),
                                                cam.world_start_timestamp.clone().unwrap_or_default(),
                                            )
                                            .to_lowercase();
                                            hay.contains(&q)
                                        }) {
                                            tr { key: "{cam.uuid}",
                                                td { class: "table-copy", "{cam.match_name}" }
                                                td { class: "table-copy", "{cam.field_name}" }
                                                td { class: "table-copy", "{cam.camera_name}" }
                                                td { class: "table-nowrap", "{cam.status}" }
                                                td { class: "table-copy", "{cam.user.clone().unwrap_or_else(|| \"-\".to_string())}" }
                                                td { class: "table-nowrap", "{cam.world_start_timestamp.clone().unwrap_or_else(|| \"-\".to_string())}" }
                                                td { class: "table-nowrap",
                                                    if let Some(link) = &cam.link {
                                                        a {
                                                            href: "{youtube_href(link)}",
                                                            target: "_blank",
                                                            rel: "noopener noreferrer",
                                                            "Open"
                                                        }
                                                    } else {
                                                        span { class: "text-muted", "-" }
                                                    }
                                                }
                                                td { class: "table-nowrap",
                                                    button {
                                                        class: "btn btn-sm btn-outline-danger",
                                                        disabled: deleting().is_some(),
                                                        onclick: {
                                                            let u = url.clone();
                                                            let match_uuid = cam.match_uuid.clone();
                                                            let uuid = cam.uuid.clone();
                                                            let description = format!("Delete footage from camera ‘{}’ for match ‘{}’? This cannot be undone.", cam.camera_name, cam.match_name);
                                                            let mut delete_error_sig = delete_error;
                                                            let mut refresh_sig = refresh;
                                                            move |_| {
                                                                if deleting().is_some() { return; }
                                                                #[cfg(target_arch = "wasm32")]
                                                                if !web_sys::window().and_then(|window| window.confirm_with_message(&description).ok()).unwrap_or(false) {
                                                                    return;
                                                                }
                                                                delete_error_sig.set(None);
                                                                deleting.set(Some(uuid.clone()));
                                                                let u = u.clone();
                                                                let match_uuid = match_uuid.clone();
                                                                let uuid = uuid.clone();
                                                                spawn(async move {
                                                                    match api::delete_match_footage(&u, &match_uuid, &uuid).await {
                                                                        Ok(()) => refresh_sig.set(refresh_sig() + 1),
                                                                        Err(e) => delete_error_sig.set(Some(e)),
                                                                    }
                                                                    deleting.set(None);
                                                                });
                                                            }
                                                        },
                                                        if deleting().as_deref() == Some(cam.uuid.as_str()) { "Deleting…" } else { "Delete" }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { class: "text-muted", "Loading..." }
        }
    }
}
