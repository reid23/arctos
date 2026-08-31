use crate::api;
use crate::types::ScoreboardPointForStones;
use dioxus::prelude::*;
#[cfg(target_arch = "wasm32")]
use gloo_timers::callback::Interval;

/// Compute stones elapsed = number of global 1.5s beat boundaries crossed (in sync with global clock). Server time when end is None.
#[cfg(target_arch = "wasm32")]
fn scoreboard_stones_elapsed(
    start_stamp: Option<&str>,
    end_stamp: Option<&str>,
    time_sync: &crate::time_sync::TimeSync,
) -> Option<u32> {
    const BEAT: f64 = 1.5;
    let start = start_stamp.and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())?;
    let start_secs = start.timestamp_millis() as f64 / 1000.0;
    let end_secs = match end_stamp {
        Some(e) => chrono::DateTime::parse_from_rfc3339(e).ok()?.timestamp_millis() as f64 / 1000.0,
        None => time_sync.server_now_secs(),
    };
    let start_beat = (start_secs / BEAT).floor() as i64;
    let end_beat = (end_secs / BEAT).floor() as i64;
    Some((end_beat - start_beat).max(0) as u32)
}

/// Compute stones remaining from points list (same logic as match page): during point = from ongoing; between = from last completed point.
#[cfg(target_arch = "wasm32")]
fn scoreboard_compute_stones_remaining(
    points: &[ScoreboardPointForStones],
    time_sync: &crate::time_sync::TimeSync,
) -> Option<u32> {
    if points.is_empty() {
        return None;
    }
    let last = points.last()?;
    // Ongoing point: compute from last point's stones_at_start and elapsed to now
    if last.end_stamp.is_none() {
        let stones_at_start = last.stones_at_start?;
        let elapsed = scoreboard_stones_elapsed(last.stamp.as_deref(), None, time_sync)?;
        return Some(stones_at_start.saturating_sub(elapsed));
    }
    // Between points: use last completed point's remaining (stones_at_start - elapsed for that point)
    for pt in points.iter().rev() {
        if let (Some(stones_at_start), Some(start), Some(end)) =
            (pt.stones_at_start, pt.stamp.as_deref(), pt.end_stamp.as_deref())
        {
            if let Some(elapsed) = scoreboard_stones_elapsed(Some(start), Some(end), time_sync) {
                return Some(stones_at_start.saturating_sub(elapsed));
            }
        }
    }
    None
}

fn get_query_param(name: &str) -> Option<String> {
    #[cfg(target_arch = "wasm32")]
    {
        let window = web_sys::window()?;
        let search = window.location().search().ok()?;
        let params = web_sys::UrlSearchParams::new_with_str(&search).ok()?;
        params.get(name)
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = name;
        None
    }
}

#[component]
pub fn Scoreboard(url: String, field: String) -> Element {
    let poll_tick = use_signal(|| 0u32);
    let poll_started = use_signal(|| false);
    #[cfg(target_arch = "wasm32")]
    {
        let mut poll_tick = poll_tick;
        let mut poll_started = poll_started;
        use_effect(move || {
            if !poll_started() {
                let handle = Interval::new(2000, move || {
                    poll_tick.set(poll_tick() + 1);
                });
                poll_started.set(true);
                std::mem::forget(handle);
            }
        });
    }
    let url_for_poll = url.clone();
    let field_for_poll = field.clone();
    let data = use_resource(move || {
        let u = url_for_poll.clone();
        let f = field_for_poll.clone();
        let _tick = poll_tick();
        async move {
            api::scoreboard_state(&u, &f).await.map_err(|e| e.to_string())
        }
    });
    let val = data.value();

    // Shared client-server time sync (converges to the server clock; the probe
    // loop lives in the module). A 100ms tick drives live re-render during a point.
    #[cfg_attr(not(target_arch = "wasm32"), allow(unused_variables))]
    let time_sync = crate::time_sync::use_time_sync();
    #[cfg(target_arch = "wasm32")]
    let stones_update_tick = use_signal(|| 0u32);
    #[cfg(target_arch = "wasm32")]
    let stones_interval_handle = use_signal(|| None as Option<Interval>);
    #[cfg(target_arch = "wasm32")]
    {
        let mut stones_update_tick = stones_update_tick;
        let mut stones_interval_handle = stones_interval_handle;
        use_effect(move || {
            let guard = val.read();
            let s: Option<&crate::types::ScoreboardStateResponse> = guard.as_ref().and_then(|r| r.as_ref().ok());
            let Some(s) = s else { return };
            if !s.has_active_match || s.stones_info.is_none() {
                stones_interval_handle.set(None);
                return;
            }
            if stones_interval_handle.read().is_none() {
                let handle = Interval::new(100, move || {
                    stones_update_tick.set(stones_update_tick() + 1);
                });
                stones_interval_handle.set(Some(handle));
            }
        });
    }
    rsx! {
        style { r#"
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background: transparent; color: #fff; overflow: hidden; }}
        .scoreboard-container {{ background: rgba(0, 0, 0, 0.85); padding: 12px 30px; border-radius: 8px; min-width: 600px; backdrop-filter: blur(10px); }}
        .error-message {{ color: #ff6b6b; padding: 20px; text-align: center; font-size: 18px; }}
        .scoreboard-table {{ width: 100%; border-collapse: collapse; }}
        .team-row {{ height: 60px; }}
        .team-cell {{ padding: 6px 15px; vertical-align: middle; }}
        .team-info {{ display: flex; align-items: center; gap: 12px; }}
        .team-photo {{ width: 50px; height: 50px; border-radius: 50%; object-fit: cover; border: 2px solid rgba(255, 255, 255, 0.3); }}
        .team-name {{ font-size: 24px; font-weight: 600; min-width: 200px; }}
        .score-cell {{ text-align: center; padding: 6px 20px; font-size: 32px; font-weight: bold; border-left: 1px solid rgba(255, 255, 255, 0.2); min-width: 80px; }}
        .score-cell:first-of-type {{ border-left: none; }}
        .stones-info {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255, 255, 255, 0.2); }}
        .stones-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: 18px; }}
        .stones-count {{ font-weight: bold; font-size: 24px; }}
        .progress-bar-container {{ width: 100%; height: 8px; background: rgba(255, 255, 255, 0.2); border-radius: 4px; overflow: hidden; }}
        .progress-bar {{ height: 100%; background: linear-gradient(90deg, #4ecdc4, #44a08d); transition: width 0.3s ease; }}
        .between-matches-label {{ font-size: 24px; color: rgba(255, 255, 255, 0.9); font-weight: 600; margin-right: 15px; }}
        .winner-badge {{ display: inline-block; background: #4ecdc4; color: #000; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 12px; margin-left: 8px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .vs-text {{ margin: 0 15px; font-size: 24px; font-weight: 600; color: rgba(255, 255, 255, 0.7); }}
        "# }

        if let Some(Ok(s)) = val.read().as_ref() {
            if s.has_active_match {
                div { class: "scoreboard-container",
                    table { class: "scoreboard-table",
                        tbody {
                            tr { class: "team-row",
                                td { class: "team-cell",
                                    div { class: "team-info",
                                        if let Some(photo) = &s.team1_photo {
                                            img { src: "{api::base_url()}/static/{photo}", alt: "{s.team1_name.as_deref().unwrap_or(\"\")}", class: "team-photo" }
                                        }
                                        span { class: "team-name", "{s.team1_name.as_deref().unwrap_or(\"-\")}" }
                                    }
                                }
                                if let Some(sets) = &s.sets {
                                    for set_num in sets.iter() {
                                        {
                                            let score = s
                                                .scores_by_set
                                                .as_ref()
                                                .and_then(|m| m.get(&set_num.to_string()))
                                                .and_then(|v| v.get("team1_score"))
                                                .copied()
                                                .unwrap_or(0);
                                            rsx! { td { class: "score-cell", "{score}" } }
                                        }
                                    }
                                }
                            }
                            tr { class: "team-row",
                                td { class: "team-cell",
                                    div { class: "team-info",
                                        if let Some(photo) = &s.team2_photo {
                                            img { src: "{api::base_url()}/static/{photo}", alt: "{s.team2_name.as_deref().unwrap_or(\"\")}", class: "team-photo" }
                                        }
                                        span { class: "team-name", "{s.team2_name.as_deref().unwrap_or(\"-\")}" }
                                    }
                                }
                                if let Some(sets) = &s.sets {
                                    for set_num in sets.iter() {
                                        {
                                            let score = s
                                                .scores_by_set
                                                .as_ref()
                                                .and_then(|m| m.get(&set_num.to_string()))
                                                .and_then(|v| v.get("team2_score"))
                                                .copied()
                                                .unwrap_or(0);
                                            rsx! { td { class: "score-cell", "{score}" } }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if let Some(stones) = &s.stones_info {
                        {
                            #[cfg(target_arch = "wasm32")]
                            let _ = stones_update_tick();
                            // During a point: compute from points (live tick). Between points: use polled stones_remaining (like match page).
                            #[cfg(target_arch = "wasm32")]
                            let remaining = {
                                let during_point = s.points_for_stones.as_ref().and_then(|pts| pts.last()).map_or(false, |last| last.end_stamp.is_none());
                                if during_point {
                                    s.points_for_stones.as_ref().and_then(|pts| scoreboard_compute_stones_remaining(pts, &time_sync)).unwrap_or_else(|| stones.stones_remaining.unwrap_or(0))
                                } else {
                                    stones.stones_remaining.or_else(|| s.points_for_stones.as_ref().and_then(|pts| scoreboard_compute_stones_remaining(pts, &time_sync))).unwrap_or(0)
                                }
                            };
                            #[cfg(not(target_arch = "wasm32"))]
                            let remaining = stones.stones_remaining.unwrap_or(0);
                            let pct = if stones.stones_per_set == 0 {
                                0.0
                            } else {
                                (remaining as f64 / stones.stones_per_set as f64) * 100.0
                            };
                            rsx! {
                                div { class: "stones-info",
                                    div { class: "stones-header",
                                        span { "Stones remaining" }
                                        span { class: "stones-count", "{remaining} / {stones.stones_per_set}" }
                                    }
                                    div { class: "progress-bar-container",
                                        div { class: "progress-bar", style: "width: {pct}%;" }
                                    }
                                }
                            }
                        }
                    }
                }
            } else {
                div { class: "scoreboard-container",
                    table { class: "scoreboard-table",
                        tbody {
                            if let Some(prev) = &s.prev_match {
                                tr { class: "team-row",
                                    td { class: "team-cell",
                                        div { class: "team-info",
                                            span { class: "between-matches-label", "Previous match:" }
                                            if let Some(photo) = &prev.team1_photo {
                                                img { src: "{api::base_url()}/static/{photo}", alt: "{prev.team1_name}", class: "team-photo" }
                                            }
                                            span { class: "team-name",
                                                "{prev.team1_name}"
                                                if prev.winner.as_deref() == Some("TEAM1") {
                                                    span { class: "winner-badge", "Winner" }
                                                }
                                            }
                                            span { class: "vs-text", "vs" }
                                            if let Some(photo) = &prev.team2_photo {
                                                img { src: "{api::base_url()}/static/{photo}", alt: "{prev.team2_name}", class: "team-photo" }
                                            }
                                            span { class: "team-name",
                                                "{prev.team2_name}"
                                                if prev.winner.as_deref() == Some("TEAM2") {
                                                    span { class: "winner-badge", "Winner" }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            if let Some(next) = &s.next_match {
                                tr { class: "team-row",
                                    td { class: "team-cell",
                                        div { class: "team-info",
                                            span { class: "between-matches-label", "Next match:" }
                                            if let Some(photo) = &next.team1_photo {
                                                img { src: "{api::base_url()}/static/{photo}", alt: "{next.team1_name}", class: "team-photo" }
                                            }
                                            span { class: "team-name", "{next.team1_name}" }
                                            span { class: "vs-text", "vs" }
                                            if let Some(photo) = &next.team2_photo {
                                                img { src: "{api::base_url()}/static/{photo}", alt: "{next.team2_name}", class: "team-photo" }
                                            }
                                            span { class: "team-name", "{next.team2_name}" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            div { class: "error-message", "{e}" }
        } else {
            div { class: "error-message", "Loading…" }
        }
    }
}
