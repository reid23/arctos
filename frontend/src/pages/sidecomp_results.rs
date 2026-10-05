use crate::Route;
use crate::api;
use crate::components::{ALL_POMPFEN, PompfenIcon};
use crate::types::{SideCompStandingRow, SideCompStandingTable};
use dioxus::prelude::*;
use gloo_timers::callback::Interval;

fn photo_src(path: &Option<String>) -> Option<String> {
    path.as_ref()
        .map(|p| format!("{}/static/{}", api::base_url(), p))
}

#[component]
fn StandingAvatar(photo: Option<String>, is_team: bool) -> Element {
    if let Some(src) = photo_src(&photo) {
        rsx! {
            img {
                src: "{src}",
                alt: "",
                class: "rounded-circle me-2",
                style: "width: 28px; height: 28px; object-fit: cover;",
            }
        }
    } else {
        let icon = if is_team {
            "fas fa-users"
        } else {
            "fas fa-user"
        };
        rsx! {
            span {
                class: "d-inline-flex align-items-center justify-content-center bg-secondary rounded-circle text-white me-2",
                style: "width: 28px; height: 28px;",
                i { class: "{icon}", style: "font-size: 0.75rem;" }
            }
        }
    }
}

#[component]
fn StandingsTableView(table: SideCompStandingTable) -> Element {
    let mut weapon_filter = use_signal(|| None::<String>);
    let rows = table.rows.clone();
    let filtered: Vec<SideCompStandingRow> = match weapon_filter() {
        None => rows.clone(),
        Some(w) => rows
            .iter()
            .filter(|r| r.weapon.as_deref() == Some(w.as_str()))
            .cloned()
            .collect(),
    };
    let weapons_in_table: Vec<String> = {
        let mut seen = Vec::new();
        for r in rows.iter() {
            if let Some(w) = r.weapon.as_ref() {
                if !seen.iter().any(|s: &String| s == w) {
                    seen.push(w.clone());
                }
            }
        }
        // Keep stable pompfen order
        seen.sort_by_key(|w| {
            ALL_POMPFEN
                .iter()
                .position(|p| p.id == w)
                .unwrap_or(usize::MAX)
        });
        seen
    };

    rsx! {
        div { class: "mb-4",
            div { class: "d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2",
                h3 { class: "h4 mb-0", "{table.title}" }
                div { class: "d-flex flex-wrap gap-1",
                    button {
                        class: if weapon_filter().is_none() { "btn btn-sm btn-primary" } else { "btn btn-sm btn-outline-secondary" },
                        onclick: move |_| weapon_filter.set(None),
                        "All"
                    }
                    for w in weapons_in_table.iter() {
                        {
                            let wid = w.clone();
                            let wid_btn = w.clone();
                            let active = weapon_filter().as_deref() == Some(wid.as_str());
                            rsx! {
                                button {
                                    class: if active { "btn btn-sm btn-primary d-inline-flex align-items-center gap-1" } else { "btn btn-sm btn-outline-secondary d-inline-flex align-items-center gap-1" },
                                    onclick: move |_| weapon_filter.set(Some(wid_btn.clone())),
                                    PompfenIcon { weapon: wid.clone(), size: "1.1em".to_string() }
                                    "{wid}"
                                }
                            }
                        }
                    }
                }
            }
            if filtered.is_empty() {
                p { class: "text-muted", "No players to show." }
            } else {
                div { class: "table-responsive",
                    table { class: "table table-striped align-middle",
                        thead {
                            tr {
                                th { "Rank" }
                                th { "#" }
                                th { "Player" }
                                th { "Role" }
                                th { "Wins" }
                            }
                        }
                        tbody {
                            for row in filtered.iter() {
                                tr { key: "{row.registration_id}",
                                    td { "{row.rank}" }
                                    td { "{row.entry_number}" }
                                    td {
                                        div { class: "d-flex align-items-center",
                                            StandingAvatar { photo: row.profile_photo.clone(), is_team: false }
                                            div {
                                                div { class: "fw-semibold", "{row.display_name}" }
                                                div { class: "small text-muted d-flex align-items-center",
                                                    StandingAvatar { photo: row.team_profile_photo.clone(), is_team: true }
                                                    span {
                                                        {
                                                            let pseudo = row.jersey_name.clone().unwrap_or_else(|| "—".to_string());
                                                            let num = row.jersey_number.clone().unwrap_or_default();
                                                            let short = row.team_shortname.clone().unwrap_or_else(|| "merc".to_string());
                                                            if num.is_empty() {
                                                                format!("{pseudo} · {short}")
                                                            } else {
                                                                format!("{pseudo} ({num}) · {short}")
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    td {
                                        if let Some(w) = row.weapon.as_ref() {
                                            PompfenIcon { weapon: w.clone(), size: "1.5em".to_string() }
                                        }
                                    }
                                    td { class: "fw-bold", "{row.wins}" }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

#[component]
pub fn SideCompResults(url: String, comp_id: i32) -> Element {
    let mut tick = use_signal(|| 0u32);
    let mut poll_started = use_signal(|| false);
    let standings = use_resource(move || {
        let _ = tick();
        async move { api::sidecomp_standings(comp_id).await }
    });

    use_effect(move || {
        if !poll_started() {
            let mut tick = tick;
            let handle = Interval::new(1000, move || {
                tick.set(tick().wrapping_add(1));
            });
            poll_started.set(true);
            std::mem::forget(handle);
        }
    });

    let url_back = url.clone();

    rsx! {
        div { class: "row",
            div { class: "col-12",
                Link {
                    to: Route::SideCompDetail { url: url_back, comp_id },
                    class: "btn btn-link",
                    "<- Back to side competition"
                }
                match standings.read().as_ref() {
                    Some(Ok(data)) => {
                        if !data.results_page_enabled {
                            rsx! {
                                div { class: "alert alert-secondary", "Results are not being shown for this side competition." }
                            }
                        } else {
                            let tables = data.tables.clone();
                            let name = data.name.clone();
                            let title = if data.finalized {
                                format!("{name} - Results (Final)")
                            } else {
                                format!("{name} - Results (Unofficial)")
                            };
                            rsx! {
                                h1 { "{title}" }
                                for table in tables.into_iter() {
                                    StandingsTableView { key: "{table.id}", table }
                                }
                            }
                        }
                    }
                    Some(Err(e)) => rsx! { div { class: "alert alert-danger", "{e}" } },
                    None => rsx! { div { class: "spinner-border" } },
                }
            }
        }
    }
}
