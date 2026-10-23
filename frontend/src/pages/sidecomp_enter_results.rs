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
    is_win: bool,
    count: usize,
    flagged: bool,
    result_uuids: Vec<String>,
}

fn collapse_history(events: &[HistoryEvent]) -> Vec<HistoryGroup> {
    // Build from oldest to newest so consecutive same-player, same-outcome
    // events merge, and the resulting list is chronological (newest last).
    let chronological: Vec<&HistoryEvent> = events.iter().rev().collect();
    let mut groups: Vec<HistoryGroup> = Vec::new();
    for ev in chronological {
        let is_win = ev.result.points > 0;
        if let Some(last) = groups.last_mut() {
            if last.registration_id == ev.result.player && last.is_win == is_win {
                last.count += 1;
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
            is_win,
            count: 1,
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
    let mut busy_player = use_signal(|| None::<String>);
    let mut undo_busy = use_signal(|| false);

    // Keep the newest history entry visible on the right; older entries overflow left.
    use_effect(move || {
        let _len = history().len();
        #[cfg(target_arch = "wasm32")]
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
                background: var(--ui-bg);
                color: var(--ui-text);
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
                color: var(--ui-text);
            }}
            .sc-search-display.is-placeholder {{
                color: var(--ui-muted);
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
                background: var(--ui-surface);
                border-radius: 0.75rem;
                padding: 0.55rem 0.45rem;
                margin-bottom: 0.55rem;
                box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            }}
            .sc-unregistered {{
                display: flex;
                flex-direction: column;
                align-items: center;
                color: var(--bs-warning-text-emphasis);
                font-size: 1.3rem;
                line-height: 1;
            }}
            .sc-unregistered-label {{
                font-size: 0.5rem;
                margin-top: 0.15rem;
            }}
            .sc-player-row.wins-only {{
                grid-template-columns: 2.4rem minmax(0, 1fr) 2.4rem 3.2rem;
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
                font-size: 1.3rem;
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
                color: var(--ui-muted);
                min-width: 0;
            }}
            .sc-meta-text {{
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-history {{
                flex-shrink: 0;
                border-top: 1px solid var(--ui-border);
                border-bottom: 1px solid var(--ui-border);
                background: var(--ui-surface);
                padding: 0.3rem 0.35rem;
                overflow-x: auto;
                display: flex;
                flex-direction: row;
                gap: 0.3rem;
                min-height: 3.4rem;
                -webkit-overflow-scrolling: touch;
            }}
            .sc-history > :first-child {{
                margin-left: auto;
            }}
            .sc-hist-card {{
                position: relative;
                flex: 0 0 auto;
                width: 4.9rem;
                background: var(--ui-surface-muted);
                border-radius: 0.45rem;
                padding: 0.22rem 0.25rem 0.2rem;
                font-size: 0.66rem;
                line-height: 1.1;
                cursor: pointer;
                border: 1px solid transparent;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                text-align: center;
                gap: 0.05rem;
            }}
            .sc-hist-card.flagged {{
                border-color: var(--bs-danger-border-subtle);
                background: var(--bs-danger-bg-subtle);
            }}
            .sc-hist-flag {{
                position: absolute;
                top: 0.1rem;
                right: 0.15rem;
                color: var(--bs-danger-text-emphasis);
                font-size: 0.6rem;
            }}
            .sc-hist-line {{
                width: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.2rem;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-hist-points {{
                text-align: center;
                font-size: 0.85rem;
                font-weight: 800;
            }}
            .sc-hist-points.win {{ color: var(--bs-success-text-emphasis); }}
            .sc-hist-points.loss {{ color: var(--bs-danger-text-emphasis); }}
            .sc-numpad {{
                flex-shrink: 0;
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 0.35rem;
                padding: 0.55rem;
                background: var(--ui-surface-muted);
            }}
            .sc-num-btn {{
                border: none;
                border-radius: 0.55rem;
                background: var(--ui-surface);
                color: var(--ui-text);
                font-size: 1.45rem;
                font-weight: 600;
                padding: 0.7rem 0;
                box-shadow: 0 1px 1px rgba(0,0,0,0.05);
            }}
            .sc-num-btn:active {{ background: var(--ui-surface-hover); }}
        "# }

        div { class: "sc-enter",
            div { class: "sc-enter-top d-flex align-items-center justify-content-between gap-2",
                Link {
                    to: Route::SideCompDetail { url: url_back, comp_id },
                    class: "btn btn-sm btn-outline-secondary",
                    "Back"
                }
                div {
                    class: if query().is_empty() { "sc-search-display is-placeholder flex-grow-1 text-center" } else { "sc-search-display flex-grow-1 text-center" },
                    if query().is_empty() { "type to search" } else { "{query}" }
                }
                button {
                    class: "btn btn-sm btn-outline-secondary",
                    disabled: history().is_empty() || undo_busy(),
                    onclick: move |_| {
                        let Some(last) = history().first().cloned() else {
                            return;
                        };
                        undo_busy.set(true);
                        error.set(None);
                        spawn(async move {
                            match api::sidecomp_delete_result(&last.result.uuid).await {
                                Ok(_) => {
                                    history.write().retain(|ev| ev.result.uuid != last.result.uuid);
                                }
                                Err(e) => error.set(Some(e)),
                            }
                            undo_busy.set(false);
                        });
                    },
                    "Undo"
                }
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
                                    let player_key = p.player_id.clone();
                                    let p_plus = p.clone();
                                    let p_minus = p.clone();
                                    let pseudo = p.jersey_name.clone().unwrap_or_else(|| "—".to_string());
                                    let jersey_num = p.jersey_number.clone().unwrap_or_default();
                                    let shortname = p.team_shortname.clone().unwrap_or_else(|| "merc".to_string());
                                    let weapon = p.weapon.clone().unwrap_or_default();
                                    let busy = busy_player().as_deref() == Some(player_key.as_str());
                                    let hist_short = shortname.clone();
                                    let hist_short_plus = shortname.clone();
                                    rsx! {
                                        div {
                                            class: if p.track_losses { "sc-player-row" } else { "sc-player-row wins-only" },
                                            key: "{player_key}",
                                            div { class: "sc-entry-num", "#{p.entry_number}" }
                                            if p.track_losses {
                                                button {
                                                    class: "sc-pm-btn minus",
                                                    disabled: busy,
                                                    onclick: move |_| {
                                                        let player = p_minus.clone();
                                                        let team_short = hist_short.clone();
                                                        busy_player.set(Some(player.player_id.clone()));
                                                        error.set(None);
                                                        spawn(async move {
                                                            match api::sidecomp_log_result(comp_id, player.registration_id, &player.player_id, -1).await {
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
                                                            busy_player.set(None);
                                                        });
                                                    },
                                                    "+L"
                                                }
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
                                                if !p.registered {
                                                    div { class: "sc-unregistered", title: "unregistered",
                                                        i { class: "fas fa-exclamation-triangle" }
                                                        div { class: "sc-unregistered-label", "unregistered" }
                                                    }
                                                } else if !weapon.is_empty() {
                                                    PompfenIcon { weapon: weapon, size: "1.7em".to_string() }
                                                }
                                            }
                                            button {
                                                class: "sc-pm-btn plus",
                                                disabled: busy,
                                                onclick: move |_| {
                                                    let player = p_plus.clone();
                                                    let team_short = hist_short_plus.clone();
                                                    busy_player.set(Some(player.player_id.clone()));
                                                    error.set(None);
                                                    spawn(async move {
                                                        match api::sidecomp_log_result(comp_id, player.registration_id, &player.player_id, 1).await {
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
                                                        busy_player.set(None);
                                                    });
                                                },
                                                "+W"
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
                                let points_label = if g.is_win {
                                    format!("+{} W", g.count)
                                } else {
                                    format!("+{} L", g.count)
                                };
                                let points_class = if g.is_win { "sc-hist-points win" } else { "sc-hist-points loss" };
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
