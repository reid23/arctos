use crate::api;
use crate::components::PompfenIcon;
use crate::types::{SideCompEnterRosterPlayer, SideCompResultEntry};
use crate::Route;
use dioxus::prelude::*;

#[derive(Clone, Debug, PartialEq)]
struct HistoryEvent {
    result: SideCompResultEntry,
    entry_number: i32,
    jersey_name: String,
    team_shortname: String,
    weapon: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
struct HistoryGroup {
    registration_id: i32,
    entry_number: i32,
    jersey_name: String,
    team_shortname: String,
    weapon: Option<String>,
    points: i32,
    flagged: bool,
    result_uuids: Vec<String>,
}

fn collapse_history(events: &[HistoryEvent]) -> Vec<HistoryGroup> {
    // Build from oldest to newest so consecutive same-player events merge,
    // and the resulting list is left→right chronological (newest on the right).
    let chronological: Vec<&HistoryEvent> = events.iter().rev().collect();
    let mut groups: Vec<HistoryGroup> = Vec::new();
    for ev in chronological {
        if let Some(last) = groups.last_mut() {
            if last.registration_id == ev.result.player {
                last.points += ev.result.points;
                last.flagged = last.flagged || ev.result.flagged;
                last.result_uuids.push(ev.result.uuid.clone());
                continue;
            }
        }
        groups.push(HistoryGroup {
            registration_id: ev.result.player,
            entry_number: ev.entry_number,
            jersey_name: ev.jersey_name.clone(),
            team_shortname: ev.team_shortname.clone(),
            weapon: ev.weapon.clone(),
            points: ev.result.points,
            flagged: ev.result.flagged,
            result_uuids: vec![ev.result.uuid.clone()],
        });
    }
    groups
}

fn photo_url(path: &Option<String>) -> Option<String> {
    path.as_ref()
        .map(|p| format!("{}/static/{}", api::base_url(), p))
}

#[component]
fn AvatarCircle(photo: Option<String>, size: String, is_team: bool) -> Element {
    if let Some(src) = photo_url(&photo) {
        rsx! {
            img {
                src: "{src}",
                alt: "",
                class: "rounded-circle",
                style: "width: {size}; height: {size}; object-fit: cover; flex-shrink: 0;",
            }
        }
    } else {
        let icon = if is_team { "fas fa-users" } else { "fas fa-user" };
        rsx! {
            div {
                class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle text-white",
                style: "width: {size}; height: {size}; flex-shrink: 0;",
                i { class: "{icon}" }
            }
        }
    }
}

#[component]
pub fn SideCompEnterResults(url: String, comp_id: i32) -> Element {
    let roster = use_resource(move || async move { api::sidecomp_enter_results_roster(comp_id).await });
    let mut query = use_signal(String::new);
    let mut history = use_signal(Vec::<HistoryEvent>::new);
    let mut error = use_signal(|| None::<String>);
    let mut busy_reg = use_signal(|| None::<i32>);

    // Keep the newest history entry visible on the right; older entries overflow left.
    use_effect(move || {
        let _len = history().len();
        spawn(async move {
            gloo_timers::future::TimeoutFuture::new(0).await;
            if let Some(window) = web_sys::window() {
                if let Some(doc) = window.document() {
                    if let Ok(Some(el)) = doc.query_selector(".sc-history") {
                        el.set_scroll_left(el.scroll_width());
                    }
                }
            }
        });
    });

    let url_back = url.clone();

    rsx! {
        style { r#"
            .sc-enter {{
                display: flex;
                flex-direction: column;
                height: 100dvh;
                max-width: 560px;
                margin: 0 auto;
                background: #f7f7f8;
                color: #111;
                overflow: hidden;
            }}
            .sc-enter-top {{
                padding: 0.75rem 1rem 0.25rem;
                flex-shrink: 0;
            }}
            .sc-search-display {{
                font-size: 1.6rem;
                font-weight: 600;
                letter-spacing: 0.04em;
                min-height: 2.2rem;
                color: #111;
            }}
            .sc-search-display.placeholder {{
                color: #9aa0a6;
                font-weight: 500;
            }}
            .sc-results {{
                flex: 1 1 auto;
                overflow-y: auto;
                padding: 0.5rem 0.75rem;
                min-height: 0;
            }}
            .sc-player-row {{
                display: grid;
                grid-template-columns: 2.4rem 3.2rem minmax(0, 1fr) 2.4rem 3.2rem;
                gap: 0.35rem;
                align-items: center;
                background: #fff;
                border-radius: 0.75rem;
                padding: 0.55rem 0.45rem;
                margin-bottom: 0.55rem;
                box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            }}
            .sc-entry-num {{
                font-weight: 700;
                font-size: 0.95rem;
                text-align: center;
            }}
            .sc-pm-btn {{
                width: 3.2rem;
                height: 3.2rem;
                border: none;
                border-radius: 0.65rem;
                font-size: 1.75rem;
                font-weight: 700;
                line-height: 1;
                color: #fff;
            }}
            .sc-pm-btn.minus {{ background: #c62828; }}
            .sc-pm-btn.plus {{ background: #2e7d32; }}
            .sc-pm-btn:disabled {{ opacity: 0.5; }}
            .sc-center {{
                display: flex;
                align-items: center;
                gap: 0.45rem;
                min-width: 0;
            }}
            .sc-center-text {{
                min-width: 0;
                flex: 1;
            }}
            .sc-display-name {{
                font-weight: 700;
                font-size: 0.98rem;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-meta {{
                display: flex;
                align-items: center;
                gap: 0.35rem;
                font-size: 0.82rem;
                color: #444;
                min-width: 0;
            }}
            .sc-meta-text {{
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-history {{
                flex-shrink: 0;
                border-top: 1px solid #ddd;
                border-bottom: 1px solid #ddd;
                background: #fff;
                padding: 0.45rem 0.5rem;
                overflow-x: auto;
                display: flex;
                flex-direction: row;
                gap: 0.5rem;
                min-height: 4.6rem;
                -webkit-overflow-scrolling: touch;
            }}
            .sc-hist-card {{
                position: relative;
                flex: 0 0 auto;
                width: 7.5rem;
                background: #f3f4f6;
                border-radius: 0.55rem;
                padding: 0.4rem 0.45rem 0.35rem;
                font-size: 0.78rem;
                line-height: 1.15;
                cursor: pointer;
                border: 1px solid transparent;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                text-align: center;
                gap: 0.15rem;
            }}
            .sc-hist-card.flagged {{
                border-color: #c62828;
                background: #fff5f5;
            }}
            .sc-hist-flag {{
                position: absolute;
                top: 0.15rem;
                right: 0.25rem;
                color: #c62828;
                font-size: 0.75rem;
            }}
            .sc-hist-line {{
                width: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.25rem;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-hist-points {{
                text-align: center;
                font-size: 1.15rem;
                font-weight: 800;
                margin-top: 0.1rem;
            }}
            .sc-hist-points.pos {{ color: #2e7d32; }}
            .sc-hist-points.neg {{ color: #c62828; }}
            .sc-numpad {{
                flex-shrink: 0;
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 0.35rem;
                padding: 0.55rem;
                background: #ebecef;
            }}
            .sc-num-btn {{
                border: none;
                border-radius: 0.55rem;
                background: #fff;
                font-size: 1.45rem;
                font-weight: 600;
                padding: 0.7rem 0;
                box-shadow: 0 1px 1px rgba(0,0,0,0.05);
            }}
            .sc-num-btn:active {{ background: #e8e8e8; }}
        "# }

        div { class: "sc-enter",
            div { class: "sc-enter-top d-flex align-items-center justify-content-between gap-2",
                Link {
                    to: Route::SideCompDetail { url: url_back, comp_id },
                    class: "btn btn-sm btn-outline-secondary",
                    "Back"
                }
                div {
                    class: if query().is_empty() { "sc-search-display placeholder flex-grow-1 text-center" } else { "sc-search-display flex-grow-1 text-center" },
                    if query().is_empty() { "type to search" } else { "{query}" }
                }
                span { style: "width: 3.5rem;" }
            }

            if let Some(err) = error() {
                div { class: "alert alert-danger mx-2 py-1 mb-1", "{err}" }
            }

            match roster.read().as_ref() {
                Some(Ok(data)) if !data.active => {
                    rsx! {
                        div { class: "alert alert-warning mx-2 mt-2",
                            "This side competition is not active; results cannot be entered."
                        }
                    }
                }
                _ => rsx! {}
            }

            div { class: "sc-results",
                match roster.read().as_ref() {
                    Some(Ok(data)) if data.active => {
                        let q = query();
                        let matches: Vec<SideCompEnterRosterPlayer> = if q.is_empty() {
                            Vec::new()
                        } else {
                            data.registrants
                                .iter()
                                .filter(|p| p.entry_number.to_string().contains(&q))
                                .cloned()
                                .collect()
                        };
                        rsx! {
                            if !q.is_empty() && matches.is_empty() {
                                p { class: "text-muted text-center mt-3", "No matching players" }
                            }
                            for p in matches.into_iter() {
                                {
                                    let reg_id = p.registration_id;
                                    let p_plus = p.clone();
                                    let p_minus = p.clone();
                                    let pseudo = p.jersey_name.clone().unwrap_or_else(|| "—".to_string());
                                    let jersey_num = p.jersey_number.clone().unwrap_or_default();
                                    let shortname = p.team_shortname.clone().unwrap_or_else(|| "merc".to_string());
                                    let weapon = p.weapon.clone().unwrap_or_default();
                                    let busy = busy_reg() == Some(reg_id);
                                    let hist_short = shortname.clone();
                                    let hist_short_plus = shortname.clone();
                                    rsx! {
                                        div { class: "sc-player-row", key: "{reg_id}",
                                            div { class: "sc-entry-num", "#{p.entry_number}" }
                                            button {
                                                class: "sc-pm-btn minus",
                                                disabled: busy,
                                                onclick: move |_| {
                                                    let player = p_minus.clone();
                                                    let team_short = hist_short.clone();
                                                    busy_reg.set(Some(player.registration_id));
                                                    error.set(None);
                                                    spawn(async move {
                                                        match api::sidecomp_log_result(comp_id, player.registration_id, -1).await {
                                                            Ok(result) => {
                                                                history.write().insert(0, HistoryEvent {
                                                                    result,
                                                                    entry_number: player.entry_number,
                                                                    jersey_name: player.jersey_name.clone().unwrap_or_else(|| "—".to_string()),
                                                                    team_shortname: team_short,
                                                                    weapon: player.weapon.clone(),
                                                                });
                                                            }
                                                            Err(e) => error.set(Some(e)),
                                                        }
                                                        busy_reg.set(None);
                                                    });
                                                },
                                                "−"
                                            }
                                            div { class: "sc-center",
                                                AvatarCircle {
                                                    photo: p.profile_photo.clone(),
                                                    size: "2.6rem".to_string(),
                                                    is_team: false,
                                                }
                                                div { class: "sc-center-text",
                                                    div { class: "sc-display-name", "{p.display_name}" }
                                                    div { class: "sc-meta",
                                                        AvatarCircle {
                                                            photo: p.team_profile_photo.clone(),
                                                            size: "1.25rem".to_string(),
                                                            is_team: true,
                                                        }
                                                        div { class: "sc-meta-text",
                                                            if jersey_num.is_empty() {
                                                                "{pseudo}"
                                                            } else {
                                                                "{pseudo} ({jersey_num})"
                                                            }
                                                            br {}
                                                            "{shortname}"
                                                        }
                                                    }
                                                }
                                            }
                                            div { class: "d-flex justify-content-center",
                                                if !weapon.is_empty() {
                                                    PompfenIcon { weapon: weapon, size: "1.7em".to_string() }
                                                }
                                            }
                                            button {
                                                class: "sc-pm-btn plus",
                                                disabled: busy,
                                                onclick: move |_| {
                                                    let player = p_plus.clone();
                                                    let team_short = hist_short_plus.clone();
                                                    busy_reg.set(Some(player.registration_id));
                                                    error.set(None);
                                                    spawn(async move {
                                                        match api::sidecomp_log_result(comp_id, player.registration_id, 1).await {
                                                            Ok(result) => {
                                                                history.write().insert(0, HistoryEvent {
                                                                    result,
                                                                    entry_number: player.entry_number,
                                                                    jersey_name: player.jersey_name.clone().unwrap_or_else(|| "—".to_string()),
                                                                    team_shortname: team_short,
                                                                    weapon: player.weapon.clone(),
                                                                });
                                                            }
                                                            Err(e) => error.set(Some(e)),
                                                        }
                                                        busy_reg.set(None);
                                                    });
                                                },
                                                "+"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Some(Err(e)) => rsx! { div { class: "alert alert-danger", "{e}" } },
                    None => rsx! { div { class: "text-center mt-4", div { class: "spinner-border" } } },
                    _ => rsx! {},
                }
            }

            {
                let groups = collapse_history(&history());
                rsx! {
                    div { class: "sc-history",
                        if groups.is_empty() {
                            span { class: "text-muted align-self-center px-2", "History" }
                        }
                        for g in groups.into_iter() {
                            {
                                let uuids = g.result_uuids.clone();
                                let currently_flagged = g.flagged;
                                let points_label = if g.points > 0 {
                                    format!("+{}", g.points)
                                } else {
                                    format!("{}", g.points)
                                };
                                let points_class = if g.points >= 0 { "sc-hist-points pos" } else { "sc-hist-points neg" };
                                let card_class = if g.flagged { "sc-hist-card flagged" } else { "sc-hist-card" };
                                let weapon = g.weapon.clone().unwrap_or_default();
                                rsx! {
                                    div {
                                        class: "{card_class}",
                                        key: "{uuids.first().cloned().unwrap_or_default()}",
                                        onclick: move |_| {
                                            let uuids = uuids.clone();
                                            let new_flag = !currently_flagged;
                                            spawn(async move {
                                                let mut ok = true;
                                                for uuid in uuids.iter() {
                                                    if let Err(e) = api::sidecomp_flag_result(uuid, new_flag).await {
                                                        error.set(Some(e));
                                                        ok = false;
                                                        break;
                                                    }
                                                }
                                                if ok {
                                                    let mut hist = history();
                                                    for ev in hist.iter_mut() {
                                                        if uuids.iter().any(|u| u == &ev.result.uuid) {
                                                            ev.result.flagged = new_flag;
                                                        }
                                                    }
                                                    history.set(hist);
                                                }
                                            });
                                        },
                                        if g.flagged {
                                            i { class: "fas fa-flag sc-hist-flag" }
                                        }
                                        div { class: "sc-hist-line", "#{g.entry_number} {g.jersey_name}" }
                                        div { class: "sc-hist-line",
                                            span { "{g.team_shortname}" }
                                            if !weapon.is_empty() {
                                                PompfenIcon { weapon: weapon, size: "1em".to_string() }
                                            }
                                        }
                                        div { class: "{points_class}", "{points_label}" }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            div { class: "sc-numpad",
                for digit in ["1", "2", "3", "4", "5", "6", "7", "8", "9"].iter() {
                    {
                        let d = (*digit).to_string();
                        rsx! {
                            button {
                                class: "sc-num-btn",
                                onclick: move |_| {
                                    let mut q = query();
                                    if q.len() < 6 {
                                        q.push_str(&d);
                                        query.set(q);
                                    }
                                },
                                "{digit}"
                            }
                        }
                    }
                }
                button {
                    class: "sc-num-btn",
                    onclick: move |_| query.set(String::new()),
                    "C"
                }
                button {
                    class: "sc-num-btn",
                    onclick: move |_| {
                        let mut q = query();
                        if q.len() < 6 {
                            q.push('0');
                            query.set(q);
                        }
                    },
                    "0"
                }
                button {
                    class: "sc-num-btn",
                    onclick: move |_| {
                        let mut q = query();
                        q.pop();
                        query.set(q);
                    },
                    "←"
                }
            }
        }
    }
}
