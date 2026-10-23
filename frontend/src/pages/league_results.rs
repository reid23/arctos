use super::league_home::LeagueHomeContent;
use crate::api;
use crate::components::{ResultsGamesTable, ResultsRank, ResultsSortHeader, calculate_ranks, rows_for_team};
use crate::Route;
use dioxus::prelude::*;
use std::cmp::Ordering;
use std::collections::HashMap;

#[component]
pub fn LeagueResults(league_url: String) -> Element {
    rsx! {
        LeagueHomeContent {
            key: "{league_url}",
            league_url: league_url.clone(),
            initial_tab: Some("results".to_string()),
            content: Some(rsx! { LeagueResultsContent { league_url } }),
        }
    }
}

#[component]
fn LeagueResultsContent(league_url: String) -> Element {
    let mut include_ribbon = use_signal(|| false);
    let url_for_data = league_url.clone();
    let data = use_resource(use_reactive(&(url_for_data, include_ribbon), |(lu, inc_sig)| {
        let lu = lu.clone();
        let inc_ribbon = inc_sig();
        async move {
            api::league_results(&lu, inc_ribbon)
                .await
                .map_err(|e| e.to_string())
        }
    }));
    let mut sort_column = use_signal(|| "rank".to_string());
    let mut sort_asc = use_signal(|| true);
    let mut expanded = use_signal(|| None::<String>);
    let mut team_matches_cache =
        use_signal(|| HashMap::<String, Option<Result<crate::types::TeamMatchesResponse, String>>>::new());
    let val = data.value();
    let backend = api::base_url();

    rsx! {
        if let Some(Ok(results_data)) = val.read().as_ref() {
            div { class: "d-flex align-items-center gap-2 mt-2 mb-2",
                label { class: "form-check-label d-flex align-items-center gap-1 user-select-none",
                    input {
                        class: "form-check-input",
                        r#type: "checkbox",
                        id: "results-include-ribbon",
                        checked: include_ribbon(),
                        onchange: move |e| include_ribbon.set(e.value() == "true"),
                    }
                    "Include ribbon games"
                }
            }
            if results_data.teams.is_empty() {
                div { class: "alert alert-info mt-3",
                    h4 { "No results yet" }
                    p { "League results will appear here as matches are completed." }
                }
            } else {
                {
                    let ranks = calculate_ranks(&results_data.teams);
                    let mut teams_sorted = results_data.teams.iter().collect::<Vec<_>>();
                    let col = sort_column().clone();
                    let asc = sort_asc();
                    teams_sorted.sort_by(|a, b| {
                        // Keep unranked teams last in either rank sort direction.
                        if col == "rank" {
                            return match (ranks.get(&a.id), ranks.get(&b.id)) {
                                (Some(a_rank), Some(b_rank)) => {
                                    let order = a_rank.cmp(b_rank);
                                    (if asc { order } else { order.reverse() })
                                        .then_with(|| a.pseudonym.to_lowercase().cmp(&b.pseudonym.to_lowercase()))
                                        .then_with(|| a.id.cmp(&b.id))
                                }
                                (Some(_), None) => Ordering::Less,
                                (None, Some(_)) => Ordering::Greater,
                                (None, None) => a.pseudonym.to_lowercase().cmp(&b.pseudonym.to_lowercase())
                                    .then_with(|| a.id.cmp(&b.id)),
                            };
                        }
                        let ord = match col.as_str() {
                            "matches" => {
                                let a_tot = u64::from(a.matches_won) + u64::from(a.matches_lost);
                                let b_tot = u64::from(b.matches_won) + u64::from(b.matches_lost);
                                let a_ratio = if a_tot > 0 { a.matches_won as f64 / a_tot as f64 } else { 0.0 };
                                let b_ratio = if b_tot > 0 { b.matches_won as f64 / b_tot as f64 } else { 0.0 };
                                a_ratio.partial_cmp(&b_ratio).unwrap_or(Ordering::Equal)
                                    .then_with(|| a_tot.cmp(&b_tot))
                                    .then_with(|| a.matches_won.cmp(&b.matches_won))
                            }
                            "points" => {
                                let a_tot = u64::from(a.points_won) + u64::from(a.points_lost);
                                let b_tot = u64::from(b.points_won) + u64::from(b.points_lost);
                                let a_ratio = if a_tot > 0 { a.points_won as f64 / a_tot as f64 } else { 0.0 };
                                let b_ratio = if b_tot > 0 { b.points_won as f64 / b_tot as f64 } else { 0.0 };
                                a_ratio.partial_cmp(&b_ratio).unwrap_or(Ordering::Equal)
                                    .then_with(|| a_tot.cmp(&b_tot))
                                    .then_with(|| a.points_won.cmp(&b.points_won))
                            }
                            _ => a.pseudonym.to_lowercase().cmp(&b.pseudonym.to_lowercase()),
                        };
                        if asc { ord } else { ord.reverse() }
                    });
                rsx! {
                div { class: "data-section mt-3",
                    div {
                        p { id: "standings-ranking-help", class: "text-muted small mb-2", "Ranked by match win percentage, then point win percentage. Ties share a rank." }
                        div { class: "table-responsive", role: "region", aria_label: "League standings", tabindex: "0",
                            table { class: "table table-striped align-middle mb-0", aria_describedby: "standings-ranking-help",
                                caption { class: "visually-hidden", "League standings" }
                                thead {
                                    tr {
                                        th { scope: "col", style: "width: 1%;", span { class: "visually-hidden", "Match details" } }
                                        ResultsSortHeader {
                                            label: "Rank",
                                            numeric: true,
                                            active: sort_column() == "rank",
                                            ascending: sort_asc(),
                                            onsort: move |_| {
                                                if sort_column() == "rank" {
                                                    sort_asc.set(!sort_asc());
                                                } else {
                                                    sort_column.set("rank".to_string());
                                                    sort_asc.set(true);
                                                }
                                            },
                                        }
                                        ResultsSortHeader {
                                            label: "Team",
                                            active: sort_column() == "team",
                                            ascending: sort_asc(),
                                            onsort: move |_| {
                                                if sort_column() == "team" {
                                                    sort_asc.set(!sort_asc());
                                                } else {
                                                    sort_column.set("team".to_string());
                                                    sort_asc.set(true);
                                                }
                                            },
                                        }
                                        ResultsSortHeader {
                                            label: "Matches (W–L)",
                                            numeric: true,
                                            active: sort_column() == "matches",
                                            ascending: sort_asc(),
                                            onsort: move |_| {
                                                if sort_column() == "matches" {
                                                    sort_asc.set(!sort_asc());
                                                } else {
                                                    sort_column.set("matches".to_string());
                                                    sort_asc.set(false);
                                                }
                                            },
                                        }
                                        ResultsSortHeader {
                                            label: "Points (W–L)",
                                            numeric: true,
                                            active: sort_column() == "points",
                                            ascending: sort_asc(),
                                            onsort: move |_| {
                                                if sort_column() == "points" {
                                                    sort_asc.set(!sort_asc());
                                                } else {
                                                    sort_column.set("points".to_string());
                                                    sort_asc.set(false);
                                                }
                                            },
                                        }
                                    }
                                }
                                tbody {
                                    for row_team in teams_sorted.iter() {
                                        {
                                            let tid = row_team.id.clone();
                                            let expanded_current = expanded();
                                            let is_expanded = expanded_current.as_ref() == Some(&tid);
                                            let cache = team_matches_cache();
                                            let has_cached = cache.contains_key(&tid);
                                            let need_fetch = is_expanded && !has_cached;
                                            if need_fetch {
                                                team_matches_cache.write().insert(tid.clone(), None);
                                                let mut cache_sig = team_matches_cache;
                                                let lu_fetch = league_url.clone();
                                                let tid_fetch = tid.clone();
                                                spawn(async move {
                                                    let res = api::league_results_team_matches(&lu_fetch, &tid_fetch).await;
                                                    cache_sig.write().insert(tid_fetch, Some(res));
                                                });
                                            }
                                            let tid_click = tid.clone();
                                            let tid_btn = tid.clone();
                                            rsx! {
                                        tr {
                                            key: "{tid}",
                                            class: if is_expanded { "table-active" } else { "" },
                                            onclick: move |_| {
                                                if is_expanded {
                                                    expanded.set(None);
                                                } else {
                                                    expanded.set(Some(tid_click.clone()));
                                                }
                                            },
                                            td {
                                                class: "text-center",
                                                onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                                                button {
                                                    r#type: "button",
                                                    class: "btn btn-lg p-0 text-secondary results-expand-toggle",
                                                    "aria-label": if is_expanded { "Collapse" } else { "Expand" },
                                                    onclick: move |ev: Event<MouseData>| {
                                                        ev.stop_propagation();
                                                        if is_expanded {
                                                            expanded.set(None);
                                                        } else {
                                                            expanded.set(Some(tid_btn.clone()));
                                                        }
                                                    },
                                                    if is_expanded { "−" } else { "+" }
                                                }
                                            }
                                            td { class: "table-number", ResultsRank { rank: ranks.get(&tid).copied() } }
                                            td { class: "table-copy table-identity",
                                                Link {
                                                    to: Route::TeamProfilePage { id: row_team.id.clone() },
                                                    class: "text-decoration-none d-inline-flex align-items-center",
                                                    onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                                                    if let Some(ph) = &row_team.profile_photo {
                                                        img {
                                                            src: "{backend}/static/{ph}",
                                                            alt: "",
                                                            class: "rounded-circle me-2",
                                                            style: "width: 32px; height: 32px; object-fit: cover;",
                                                        }
                                                    }
                                                    span { "{row_team.pseudonym}" }
                                                }
                                            }
                                            td { class: "table-number", "{row_team.matches_won}–{row_team.matches_lost}" }
                                            td { class: "table-number", "{row_team.points_won}–{row_team.points_lost}" }
                                        }
                                        if is_expanded {
                                            tr {
                                                key: "{tid}-detail",
                                                td { colspan: 5, class: "p-0 bg-light results-games-cell",
                                                        match team_matches_cache().get(&tid) {
                                                            None | Some(None) => rsx! { p { class: "text-muted mb-0", "Loading…" } },
                                                            Some(Some(Err(e))) => rsx! { p { class: "text-danger mb-0", "{e}" } },
                                                            Some(Some(Ok(ref resp))) => {
                                                            let visible_matches: Vec<_> = resp
                                                                .matches
                                                                .iter()
                                                                .filter(|m| include_ribbon() || !m.ribbon)
                                                                .cloned()
                                                                .collect();
                                                            let game_rows = rows_for_team(
                                                                &visible_matches,
                                                                row_team.profile_photo.clone(),
                                                                &results_data.teams,
                                                                |m| m.event.clone().unwrap_or_default(),
                                                            );
                                                            let max_sets = game_rows.iter().map(|r| r.top_sets.len().max(r.bottom_sets.len())).max().unwrap_or(1);
                                                            rsx! {
                                                                if game_rows.is_empty() {
                                                                    p { class: "text-muted mb-0 p-3", "No matches in this league yet." }
                                                                } else {
                                                                    ResultsGamesTable { rows: game_rows, max_sets }
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
                            }
                        }
                    }
                }
                }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            div { class: "alert alert-danger", "{e}" }
        } else {
            p { class: "text-muted", "Loading…" }
        }
    }
}
