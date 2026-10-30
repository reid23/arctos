//! Expandable per-team match results.
//!
//! The table scrolls inside the dropdown row. When it overflows, team names
//! switch from pseudonyms to shortnames and the result column collapses to
//! the width of W/L.

use crate::Route;
use crate::api;
use crate::display::short_or_truncate;
use crate::types::{TeamMatchDetail, TeamResultRow};
use dioxus::prelude::*;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_SCROLL_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, PartialEq)]
pub struct ResultsGameRow {
    pub id: String,
    pub match_url: String,
    pub match_name: String,
    pub ribbon: bool,
    pub won: bool,
    pub top_name: String,
    pub top_short: String,
    pub top_photo: Option<String>,
    pub bottom_name: String,
    pub bottom_short: String,
    pub bottom_photo: Option<String>,
    pub top_sets: Vec<u32>,
    pub bottom_sets: Vec<u32>,
}

const RESULTS_GAMES_CSS: &str = "\
td.results-games-cell{max-width:0;width:100%;overflow:hidden;padding:0!important}\
.results-games-clip{display:grid;grid-template-columns:minmax(0,1fr);width:100%;min-width:0}\
.results-games-scroll{overflow-x:auto;width:0;min-width:100%;max-width:100%;padding:0.75rem}\
.results-games-table{width:max-content;min-width:100%;table-layout:auto}\
.results-games-table th.match,.results-games-table td.match{width:1%;min-width:4.5rem;white-space:nowrap}\
.results-games-table th.result,.results-games-table td.result{width:1%;white-space:nowrap}\
.results-games-table th.team,.results-games-table td.team{white-space:nowrap}\
.results-games-table th.set,.results-games-table td.set{width:2.25rem;white-space:nowrap}\
.results-games-table--tight th.result,.results-games-table--tight td.result{max-width:1.75rem;overflow:hidden;padding-left:0.35rem;padding-right:0.35rem}\
.results-games-table--tight th.result .result-label{display:inline-block;max-width:1.15em;overflow:hidden;vertical-align:bottom}\
";

fn listed_short<'a>(name: &str, api_short: Option<&'a str>, teams: &'a [TeamResultRow]) -> Option<&'a str> {
    api_short
        .filter(|s| !s.is_empty())
        .or_else(|| {
            teams
                .iter()
                .find(|t| t.pseudonym == name)
                .and_then(|t| t.shortname.as_deref())
                .filter(|s| !s.is_empty())
        })
}

/// One results-table row per match, with our team on top.
pub fn rows_for_team(
    matches: &[TeamMatchDetail],
    our_photo: Option<String>,
    teams: &[TeamResultRow],
    match_url: impl Fn(&TeamMatchDetail) -> String,
) -> Vec<ResultsGameRow> {
    matches
        .iter()
        .map(|game| {
            let won = game.match_winner.as_deref() == game.your_side.as_deref();
            let our_first = game.your_side.as_deref() != Some("TEAM2");
            let (top_name, top_api_short, top_sets, bottom_name, bottom_api_short, bottom_sets) =
                if our_first {
                    (
                        game.team1_name.as_str(),
                        game.team1_shortname.as_deref(),
                        game.sets.iter().map(|s| s.team1_points).collect::<Vec<_>>(),
                        game.team2_name.as_str(),
                        game.team2_shortname.as_deref(),
                        game.sets.iter().map(|s| s.team2_points).collect::<Vec<_>>(),
                    )
                } else {
                    (
                        game.team2_name.as_str(),
                        game.team2_shortname.as_deref(),
                        game.sets.iter().map(|s| s.team2_points).collect::<Vec<_>>(),
                        game.team1_name.as_str(),
                        game.team1_shortname.as_deref(),
                        game.sets.iter().map(|s| s.team1_points).collect::<Vec<_>>(),
                    )
                };
            let bottom_photo = teams
                .iter()
                .find(|t| t.pseudonym == bottom_name)
                .and_then(|t| t.profile_photo.clone());
            ResultsGameRow {
                id: game.uuid.clone(),
                match_url: match_url(game),
                match_name: game.name.clone(),
                ribbon: game.ribbon,
                won,
                top_name: top_name.to_string(),
                top_short: short_or_truncate(top_name, listed_short(top_name, top_api_short, teams)),
                top_photo: our_photo.clone(),
                bottom_name: bottom_name.to_string(),
                bottom_short: short_or_truncate(
                    bottom_name,
                    listed_short(bottom_name, bottom_api_short, teams),
                ),
                bottom_photo,
                top_sets,
                bottom_sets,
            }
        })
        .collect()
}

fn mini_photo(photo: Option<&str>) -> Element {
    let backend = api::base_url();
    if let Some(ph) = photo.filter(|s| !s.is_empty()) {
        let src = format!("{backend}/static/{ph}");
        rsx! {
            img {
                src: "{src}",
                alt: "",
                class: "rounded-circle me-1",
                style: "width:18px;height:18px;object-fit:cover;",
            }
        }
    } else {
        rsx! {
            span {
                class: "d-inline-block rounded-circle me-1 bg-secondary text-white text-center",
                style: "width:18px;height:18px;line-height:18px;font-size:10px;",
                "👥"
            }
        }
    }
}

#[cfg(target_arch = "wasm32")]
fn scroll_overflows(id: &str) -> bool {
    web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id(id))
        .is_some_and(|el| el.scroll_width() > el.client_width() + 1)
}

#[cfg(target_arch = "wasm32")]
struct ResultsResizeListener {
    window: web_sys::Window,
    callback: wasm_bindgen::closure::Closure<dyn FnMut()>,
}

#[cfg(target_arch = "wasm32")]
impl Drop for ResultsResizeListener {
    fn drop(&mut self) {
        use wasm_bindgen::JsCast;
        let _ = self.window.remove_event_listener_with_callback(
            "resize", self.callback.as_ref().unchecked_ref(),
        );
    }
}

#[component]
pub fn ResultsGamesTable(rows: Vec<ResultsGameRow>, max_sets: usize) -> Element {
    let scroll_id = use_hook(|| {
        format!(
            "results-games-{}",
            NEXT_SCROLL_ID.fetch_add(1, Ordering::Relaxed)
        )
    });
    let tight = use_signal(|| false);
    let layout_tick = use_signal(|| 0u32);

    // Re-measure after layout and when the viewport changes. Overflow switches
    // names to shortnames; a wider viewport tries full pseudonyms again.
    // Own the listener for exactly this mounted table. A forgotten callback
    // would keep writing to disposed signals after collapse or ribbon changes.
    #[cfg(target_arch = "wasm32")]
    let _resize_listener = use_hook(move || {
            use wasm_bindgen::JsCast;
            let mut layout_tick = layout_tick;
            let mut tight = tight;
            let closure = wasm_bindgen::closure::Closure::<dyn FnMut()>::new(move || {
                tight.set(false);
                let current = *layout_tick.peek();
                layout_tick.set(current.wrapping_add(1));
            });
            web_sys::window().map(|window| {
                let _ = window.add_event_listener_with_callback(
                    "resize",
                    closure.as_ref().unchecked_ref(),
                );
                std::rc::Rc::new(ResultsResizeListener { window, callback: closure })
            })
    });

    let scroll_id_for_measure = scroll_id.clone();
    use_effect(move || {
        let _tick = layout_tick();
        #[cfg(target_arch = "wasm32")]
        {
            let id = scroll_id_for_measure.clone();
            let mut tight = tight;
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                if scroll_overflows(&id) {
                    tight.set(true);
                }
            });
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            let _ = scroll_id_for_measure.clone();
        }
    });

    let use_short = tight();
    let backend = api::base_url();
    let table_class = if use_short {
        "table table-sm table-bordered mb-0 results-games-table results-games-table--tight"
    } else {
        "table table-sm table-bordered mb-0 results-games-table"
    };
    let max_sets = max_sets.max(1);

    rsx! {
        style { "{RESULTS_GAMES_CSS}" }
        div { class: "results-games-clip",
            div {
                id: "{scroll_id}",
                class: "results-games-scroll",
                table { class: "{table_class}",
                    thead {
                        tr {
                            th { class: "text-start match", "Match" }
                            th { class: "result", title: "Result",
                                span { class: "result-label", "Result" }
                            }
                            th { class: "text-start team", "Team" }
                            for set_idx in 0..max_sets {
                                th { class: "text-start set", "Set {set_idx + 1}" }
                            }
                        }
                    }
                    tbody {
                        for row in rows.iter() {
                            {
                                let result_class = if row.won { "text-success" } else { "text-danger" };
                                let result_text = if row.won { "W" } else { "L" };
                                let top_label = if use_short { row.top_short.as_str() } else { row.top_name.as_str() };
                                let bottom_label = if use_short { row.bottom_short.as_str() } else { row.bottom_name.as_str() };
                                let top_photo = mini_photo(row.top_photo.as_deref());
                                let bottom_photo = mini_photo(row.bottom_photo.as_deref());
                                rsx! {
                                    tr { key: "{row.id}-1",
                                        td { rowspan: 2, class: "text-start align-top match",
                                            span { class: "d-inline-flex align-items-center gap-1",
                                                Link {
                                                    to: Route::MatchPageById {
                                                        url: row.match_url.clone(),
                                                        match_id: row.id.clone(),
                                                    },
                                                    class: "text-decoration-none",
                                                    onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); },
                                                    "{row.match_name}"
                                                }
                                                if row.ribbon {
                                                    span {
                                                        class: "d-inline-flex align-items-center ms-1",
                                                        title: "Ribbon game",
                                                        img {
                                                            src: "{backend}/static/ribbon.svg",
                                                            alt: "Ribbon game",
                                                            style: "width:1em;height:1em;object-fit:contain;vertical-align:middle;filter:invert(27%) sepia(98%) saturate(2476%) hue-rotate(226deg) brightness(99%) contrast(101%);",
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        td { rowspan: 2, class: "align-top result {result_class}", strong { "{result_text}" } }
                                        td { class: "text-muted text-start team",
                                            {top_photo}
                                            span { "{top_label}" }
                                        }
                                        for i in 0..max_sets {
                                            td { class: "text-start set",
                                                "{row.top_sets.get(i).copied().unwrap_or(0)}"
                                            }
                                        }
                                    }
                                    tr {
                                        td { class: "text-muted text-start team",
                                            {bottom_photo}
                                            span { "{bottom_label}" }
                                        }
                                        for i in 0..max_sets {
                                            td { class: "text-start set",
                                                "{row.bottom_sets.get(i).copied().unwrap_or(0)}"
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
