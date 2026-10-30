//! Interactive open-canvas bracket builder / viewer.
//!
//! TOs enter edit mode to place matches, text, labeled teams, and images;
//! wire winner/loser outputs; multi-select; resize; zoom/pan. Viewers see
//! the same canvas without editing chrome (auto-fit).

use std::collections::{HashMap, HashSet};

use dioxus::html::input_data::MouseButton;
use dioxus::prelude::*;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::{JsCast, JsValue};

use super::legacy_bracket::LegacyBracketDiagrams;
use super::tournament_home::TournamentHomeContent;
use crate::components::Modal;
use crate::display::short_or_truncate;
use crate::pages::TeamSelectionField;
use crate::types::{
    BracketImageData, BracketItem, BracketLabeledTeamData, BracketLayoutResponse, BracketMatchData,
    BracketMatchInfo, BracketMatchesResponse, BracketPlacementData, BracketPlacementRow,
    BracketTextData, MatchSetupData, TagSetupData, TeamOption,
};
use crate::{api, Route};

const DEFAULT_WIDTH: f64 = 280.0;
const DEFAULT_HEIGHT: f64 = 100.0;
const CANVAS_MIN_W: f64 = 1200.0;
const CANVAS_MIN_H: f64 = 800.0;
const PORT_INSET_Y_FRAC_TOP: f64 = 0.30;
const PORT_INSET_Y_FRAC_BOT: f64 = 0.70;
#[allow(dead_code)]
const LABELED_TEAM_W: f64 = 200.0;
const LABELED_TEAM_H: f64 = 36.0;
/// Effectively unbounded zoom-out (still clamped away from 0 for maths).
const MIN_ZOOM: f64 = 0.02;
const MAX_ZOOM: f64 = 3.0;
/// Minimum on-screen spacing between grid dots before we thin the lattice.
const MIN_GRID_SCREEN_PX: f64 = 10.0;
const REF_AIRWIRE_COLOR: &str = "var(--bs-warning-text-emphasis)";
const REF_AIRWIRE_RTL_COLOR: &str = "var(--bs-danger-text-emphasis)";
/// Categorical field colors resolve per appearance mode; warning/danger colors are reserved for refs.
const FIELD_AIRWIRE_COLORS: &[&str] = &[
    "var(--bracket-field-0)",
    "var(--bracket-field-1)",
    "var(--bracket-field-2)",
    "var(--bracket-field-3)",
    "var(--bracket-field-4)",
    "var(--bracket-field-5)",
    "var(--bracket-field-6)",
    "var(--bracket-field-7)",
    "var(--bracket-field-8)",
    "var(--bracket-field-9)",
    "var(--bracket-field-10)",
    "var(--bracket-field-11)",
    "var(--bracket-field-12)",
    "var(--bracket-field-13)",
    "var(--bracket-field-14)",
    "var(--bracket-field-15)",
    "var(--bracket-field-16)",
    "var(--bracket-field-17)",
    "var(--bracket-field-18)",
    "var(--bracket-field-19)",
];
/// Available snap-grid densities (world px). 0 = off.
#[allow(dead_code)]
const GRID_SIZE_OPTIONS: [f64; 6] = [0.0, 5.0, 10.0, 20.0, 40.0, 80.0];
const DEFAULT_GRID_SIZE: f64 = 20.0;

const PAGE_CSS: &str = include_str!("bracket_canvas.css");
const SCHEDULE_TOKEN_CSS: &str = include_str!("schedule_timeline.css");

// ---------------------------------------------------------------------------
// Core helpers
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Side {
    Team1,
    Team2,
}
impl Side {
    fn as_str(self) -> &'static str {
        match self {
            Side::Team1 => "team1",
            Side::Team2 => "team2",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Qual {
    Winner,
    Loser,
}

fn parse_match_ref(initial: &str) -> Option<(String, Qual)> {
    let s = initial.trim();
    let lower = s.to_ascii_lowercase();
    if let Some(idx) = lower.rfind("::winner") {
        let name = s[..idx].trim();
        if !name.is_empty() {
            return Some((name.to_string(), Qual::Winner));
        }
    }
    if let Some(idx) = lower.rfind("::loser") {
        let name = s[..idx].trim();
        if !name.is_empty() {
            return Some((name.to_string(), Qual::Loser));
        }
    }
    None
}

fn is_net(mode: &str) -> bool {
    mode.eq_ignore_ascii_case("NET")
}

fn placement_or_default(m: &BracketMatchData) -> BracketPlacementData {
    m.placement.clone().unwrap_or(BracketPlacementData {
        x_pos: None,
        y_pos: None,
        width: DEFAULT_WIDTH,
        height: DEFAULT_HEIGHT,
        team1: "LABEL".into(),
        team2: "LABEL".into(),
        inputs_flipped: false,
        placed: false,
    })
}

fn is_placed(m: &BracketMatchData) -> bool {
    m.placement.as_ref().map(|p| p.is_placed()).unwrap_or(false)
}

fn port_y(y: f64, h: f64, side: Side, flipped: bool) -> f64 {
    let top = match side {
        Side::Team1 => !flipped,
        Side::Team2 => flipped,
    };
    y + h * if top {
        PORT_INSET_Y_FRAC_TOP
    } else {
        PORT_INSET_Y_FRAC_BOT
    }
}

fn out_port_y(y: f64, h: f64, qual: Qual) -> f64 {
    y + h * match qual {
        Qual::Winner => PORT_INSET_Y_FRAC_TOP,
        Qual::Loser => PORT_INSET_Y_FRAC_BOT,
    }
}

fn wire_path(x1: f64, y1: f64, x2: f64, y2: f64) -> String {
    let dx = ((x2 - x1).abs() * 0.5).max(40.0);
    format!(
        "M {x1:.1} {y1:.1} C {c1x:.1} {y1:.1}, {c2x:.1} {y2:.1}, {x2:.1} {y2:.1}",
        c1x = x1 + dx,
        c2x = x2 - dx,
    )
}

/// Straight airwire (no curves) used for ref/field overlays.
fn airwire_path(x1: f64, y1: f64, x2: f64, y2: f64) -> String {
    format!("M {x1:.1} {y1:.1} L {x2:.1} {y2:.1}")
}

fn snap_coord(v: f64, grid: f64) -> f64 {
    if grid <= 0.0 {
        v
    } else {
        (v / grid).round() * grid
    }
}

/// Delta from labeled-team *snap origin* → stored top-left (`x_pos`, `y_pos`).
///
/// Chosen so that when the snap origin sits on the default 20px lattice, the
/// input port (left edge, vertical center) lands halfway between grid squares
/// (10, 30, 50, …).
fn labeled_origin_offset() -> (f64, f64) {
    let half = DEFAULT_GRID_SIZE * 0.5;
    (half, half - LABELED_TEAM_H * 0.5)
}

fn labeled_to_snap_origin(x: f64, y: f64) -> (f64, f64) {
    let (dx, dy) = labeled_origin_offset();
    (x - dx, y - dy)
}

fn labeled_from_snap_origin(lx: f64, ly: f64) -> (f64, f64) {
    let (dx, dy) = labeled_origin_offset();
    (lx + dx, ly + dy)
}

/// Snap a multi-select move so the group's shared origin (min x/y) lands on
/// the grid while preserving internal spacing. Alt/Meta fine-adjust skips snap.
fn snap_move_delta(
    origins: &HashMap<String, (f64, f64)>,
    dx: f64,
    dy: f64,
    grid: f64,
    fine_adjust: bool,
) -> (f64, f64) {
    if fine_adjust || grid <= 0.0 || origins.is_empty() {
        return (dx, dy);
    }
    let (ox, oy) = origins
        .values()
        .fold((f64::INFINITY, f64::INFINITY), |(mx, my), &(x, y)| {
            (mx.min(x), my.min(y))
        });
    if !ox.is_finite() || !oy.is_finite() {
        return (dx, dy);
    }
    let nx = snap_coord(ox + dx, grid);
    let ny = snap_coord(oy + dy, grid);
    (nx - ox, ny - oy)
}

fn match_field_key(m: &BracketMatchData) -> String {
    m.field
        .as_ref()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "(unassigned)".into())
}

/// Remembered field hover (not a Dioxus signal — avoids full-tree re-renders).
std::thread_local! {
    static FIELD_HOVER: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
}

fn clear_field_hover() {
    set_field_hover(None, None);
}

/// Instant field-ratsnest highlight + floating HTML tooltip (no Dioxus re-render).
/// `cursor` is client coords for the tooltip; pass None to hide it.
fn set_field_hover(field: Option<&str>, cursor: Option<(f64, f64)>) {
    let next = field.filter(|s| !s.is_empty()).map(|s| s.to_string());
    let changed = FIELD_HOVER.with(|slot| {
        let prev = slot.borrow().clone();
        let ch = prev != next;
        if ch {
            *slot.borrow_mut() = next.clone();
        }
        ch
    });
    // Only re-stamp classes when the hovered field changes — re-stamping every
    // mousemove caused visible blink (remove-all then re-add). VDOM wipes are
    // repaired by baking FIELD_HOVER into rsx classes + post-render rAF.
    if changed {
        apply_field_hover_dom();
    }
    update_field_tooltip(next.as_deref(), cursor);
}

fn current_field_hover() -> Option<String> {
    FIELD_HOVER.with(|slot| slot.borrow().clone())
}

fn apply_field_hover_dom() {
    #[cfg(target_arch = "wasm32")]
    {
        let field = FIELD_HOVER.with(|slot| slot.borrow().clone());
        let Some(window) = web_sys::window() else {
            return;
        };
        let Some(doc) = window.document() else {
            return;
        };
        let Some(wrap) = doc.get_element_by_id("bracket-canvas-wrap") else {
            return;
        };
        // Clear previous hot marks.
        if let Ok(nodes) = doc.query_selector_all(".field-hot") {
            for i in 0..nodes.length() {
                if let Some(node) = nodes.item(i) {
                    if let Ok(el) = node.dyn_into::<web_sys::Element>() {
                        let _ = el.class_list().remove_1("field-hot");
                    }
                }
            }
        }
        let _ = wrap.class_list().remove_1("field-hovering");

        let Some(field) = field else {
            return;
        };
        let _ = wrap.class_list().add_1("field-hovering");
        // Match by data-field attribute (exact string compare — safe for any name).
        if let Ok(nodes) = doc.query_selector_all("[data-field]") {
            for i in 0..nodes.length() {
                if let Some(node) = nodes.item(i) {
                    if let Ok(el) = node.dyn_into::<web_sys::Element>() {
                        if el.get_attribute("data-field").as_deref() == Some(field.as_str()) {
                            let _ = el.class_list().add_1("field-hot");
                        }
                    }
                }
            }
        }
    }
}

fn ensure_field_tooltip() -> Option<web_sys::HtmlElement> {
    #[cfg(target_arch = "wasm32")]
    {
        let window = web_sys::window()?;
        let doc = window.document()?;
        if let Some(el) = doc.get_element_by_id("bracket-field-tooltip") {
            return el.dyn_into::<web_sys::HtmlElement>().ok();
        }
        let el = doc.create_element("div").ok()?;
        el.set_id("bracket-field-tooltip");
        el.set_class_name("bracket-field-tooltip");
        // Hidden until first hover.
        if let Ok(html) = el.clone().dyn_into::<web_sys::HtmlElement>() {
            let _ = html.style().set_property("display", "none");
        }
        doc.body()?.append_child(&el).ok()?;
        el.dyn_into::<web_sys::HtmlElement>().ok()
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        None
    }
}

fn update_field_tooltip(field: Option<&str>, cursor: Option<(f64, f64)>) {
    update_hover_tooltip(field.map(|n| format!("Field: {n}")).as_deref(), cursor);
}

fn update_hover_tooltip(text: Option<&str>, cursor: Option<(f64, f64)>) {
    #[cfg(target_arch = "wasm32")]
    {
        let Some(tip) = ensure_field_tooltip() else {
            return;
        };
        match (text, cursor) {
            (Some(msg), Some((x, y))) => {
                tip.set_text_content(Some(msg));
                let style = tip.style();
                let _ = style.set_property("display", "block");
                // Offset so the cursor isn't on top of the tip (which would steal hits).
                let _ = style.set_property("left", &format!("{:.0}px", x + 14.0));
                let _ = style.set_property("top", &format!("{:.0}px", y + 16.0));
            }
            _ => {
                let _ = tip.style().set_property("display", "none");
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (text, cursor);
    }
}

/// Track field/ref airwire hover via elementFromPoint (SVG enter/leave is flaky).
fn track_field_hover_from_pointer(ev: &Event<PointerData>) {
    #[cfg(target_arch = "wasm32")]
    {
        let cx = ev.client_coordinates().x;
        let cy = ev.client_coordinates().y;
        let hit = (|| {
            let window = web_sys::window()?;
            let doc = window.document()?;
            if let Some(tip) = doc.get_element_by_id("bracket-field-tooltip") {
                if let Ok(html) = tip.dyn_into::<web_sys::HtmlElement>() {
                    let _ = html.style().set_property("pointer-events", "none");
                }
            }
            let el = doc.element_from_point(cx as f32, cy as f32)?;
            if let Some(group) = el.closest(".bracket-airwire-group.field").ok().flatten() {
                let field = group.get_attribute("data-field")?;
                return Some(("field", field));
            }
            if let Some(group) = el.closest(".bracket-airwire-group.ref").ok().flatten() {
                let rtl = group.get_attribute("data-rtl").as_deref() == Some("1");
                let msg = if rtl {
                    "warning: lhs match depends on rhs match for refs".to_string()
                } else {
                    return Some(("ref", String::new()));
                };
                return Some(("ref-rtl", msg));
            }
            None
        })();
        match hit {
            Some(("field", field)) => {
                set_field_hover(Some(&field), Some((cx, cy)));
            }
            Some(("ref-rtl", msg)) => {
                // Clear field highlight; show RTL warning tip.
                set_field_hover(None, None);
                update_hover_tooltip(Some(&msg), Some((cx, cy)));
            }
            _ => {
                set_field_hover(None, None);
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = ev;
    }
}

/// Stable color from the fixed distinguishable palette (excludes ref yellow/red).
fn field_airwire_color(field: &str) -> String {
    let mut h: u32 = 2166136261;
    for b in field.bytes() {
        h ^= b as u32;
        h = h.wrapping_mul(16777619);
    }
    let idx = (h as usize) % FIELD_AIRWIRE_COLORS.len();
    FIELD_AIRWIRE_COLORS[idx].to_string()
}

/// On-screen grid pitch: start from world `base`, thin by ×5 until ≥ MIN_GRID_SCREEN_PX.
fn grid_screen_pitch(base_world: f64, zoom: f64) -> f64 {
    let z = zoom.max(0.001);
    let mut pitch = base_world.max(1.0) * z;
    // Every fifth, then every 25th, etc.
    while pitch + f64::EPSILON < MIN_GRID_SCREEN_PX {
        pitch *= 5.0;
    }
    pitch
}

/// Play-order key for field airwire chains (earlier first).
fn match_play_order_key(m: &BracketMatchData) -> (i32, String, String) {
    // Prefer confirmed, then nominal, then scheduled; missing sorts last.
    let t = m
        .confirmed_start_time
        .as_ref()
        .or(m.nominal_start_time.as_ref())
        .or(m.scheduled_start_time.as_ref())
        .cloned()
        .unwrap_or_else(|| "\u{ffff}".into());
    let has = if t == "\u{ffff}" { 1 } else { 0 };
    (has, t, m.name.clone())
}

fn sel_match(uuid: &str) -> String {
    format!("m:{uuid}")
}
fn sel_text(id: &str) -> String {
    format!("t:{id}")
}
fn sel_labeled(id: &str) -> String {
    format!("lt:{id}")
}
fn sel_image(id: &str) -> String {
    format!("i:{id}")
}

fn matches_to_setup(matches: &[BracketMatchData]) -> Vec<MatchSetupData> {
    matches
        .iter()
        .map(|m| MatchSetupData {
            uuid: m.uuid.clone(),
            name: m.name.clone(),
            field: None,
            team1: m.team1.clone(),
            team2: m.team2.clone(),
            team1_initial: m.team1_initial.clone(),
            team2_initial: m.team2_initial.clone(),
            status: m.status.clone(),
            scheduled_start_time: None,
            nominal_start_time: None,
            confirmed_start_time: None,
            completed_time: None,
            schedule_type: m.schedule_type.clone(),
            set_type: None,
            nominal_length: None,
            previous_match: None,
            next_match: None,
            refs: None,
            refs_initial: None,
            ribbon: false,
            skip_condition: None,
            nsets: None,
            stones_per_set: None,
            stones_remaining: None,
            match_winner: m.match_winner.clone(),
        })
        .collect()
}

fn all_placements_json(matches: &[BracketMatchData]) -> Vec<serde_json::Value> {
    matches
        .iter()
        .filter_map(|m| {
            let p = m.placement.as_ref()?;
            Some(serde_json::json!({
                "match": m.uuid,
                "x_pos": p.x_pos,
                "y_pos": p.y_pos,
                "width": p.width,
                "height": p.height,
                "team1": p.team1,
                "team2": p.team2,
                "inputs_flipped": p.inputs_flipped,
            }))
        })
        .collect()
}

fn texts_json(texts: &[BracketTextData]) -> Vec<serde_json::Value> {
    texts
        .iter()
        .map(|t| {
            serde_json::json!({
                "id": t.id, "text": t.text, "x_pos": t.x_pos, "y_pos": t.y_pos, "size": t.size,
            })
        })
        .collect()
}

fn labeled_json(items: &[BracketLabeledTeamData]) -> Vec<serde_json::Value> {
    items
        .iter()
        .map(|t| {
            serde_json::json!({
                "id": t.id,
                "label": t.label,
                "team": t.team,
                "kind": t.kind,
                "x_pos": t.x_pos,
                "y_pos": t.y_pos,
            })
        })
        .collect()
}

fn images_json(items: &[BracketImageData]) -> Vec<serde_json::Value> {
    items
        .iter()
        .map(|i| {
            serde_json::json!({
                "id": i.id, "image": i.image, "x_pos": i.x_pos, "y_pos": i.y_pos,
                "width": i.width, "height": i.height,
            })
        })
        .collect()
}

/// How far net-labels stick out left of a match (CSS max-width + margin +
/// port).
const NET_LABEL_LEFT_EXTENT: f64 = 120.0;
/// How far output-side net labels stick out right of a match.
const NET_LABEL_RIGHT_EXTENT: f64 = 190.0;
/// Horizontal gap between an output port and an auto-created team label.
const OUTPUT_LABEL_GAP: f64 = 60.0;
/// Small overhang for port stubs / borders outside the match box.
const PORT_STUB_EXTENT: f64 = 8.0;

fn content_bounds(
    matches: &[BracketMatchData],
    texts: &[BracketTextData],
    labeled: &[BracketLabeledTeamData],
    images: &[BracketImageData],
) -> (f64, f64, f64, f64) {
    let mut min_x = f64::INFINITY;
    let mut min_y = f64::INFINITY;
    let mut max_x = f64::NEG_INFINITY;
    let mut max_y = f64::NEG_INFINITY;
    let mut any = false;
    let mut bump = |x: f64, y: f64, w: f64, h: f64| {
        any = true;
        min_x = min_x.min(x);
        min_y = min_y.min(y);
        max_x = max_x.max(x + w);
        max_y = max_y.max(y + h);
    };
    for m in matches {
        if let Some(p) = &m.placement {
            if let (Some(x), Some(y)) = (p.x_pos, p.y_pos) {
                // Match body + port stubs on left/right edges.
                bump(
                    x - PORT_STUB_EXTENT,
                    y,
                    p.width + PORT_STUB_EXTENT * 2.0,
                    p.height,
                );
                // LABEL-mode inputs render net-label chips to the left of the match.
                if !is_net(&p.team1) || !is_net(&p.team2) {
                    bump(
                        x - NET_LABEL_LEFT_EXTENT,
                        y,
                        NET_LABEL_LEFT_EXTENT,
                        p.height,
                    );
                }
                // Output-side net labels sit on the W/L ports, right of the match.
                if [Qual::Winner, Qual::Loser]
                    .into_iter()
                    .any(|q| !output_consumers(m, q, matches, labeled).unwired.is_empty())
                {
                    bump(x + p.width, y, NET_LABEL_RIGHT_EXTENT, p.height);
                }
            }
        }
    }
    for t in texts {
        let w = (t.text.chars().count() as f64) * t.size * 0.55 + 12.0;
        bump(t.x_pos, t.y_pos, w.max(40.0), t.size * 1.5);
    }
    for t in labeled {
        // Fit-content chips; estimate width from caption length.
        let caption_len = if t.resolved {
            t.label.chars().count() + t.display_text.chars().count() + 4
        } else {
            t.label.chars().count().max(1)
        };
        let w = (caption_len as f64) * 8.5 + 36.0;
        bump(t.x_pos, t.y_pos, w.max(48.0), LABELED_TEAM_H);
    }
    for i in images {
        bump(i.x_pos, i.y_pos, i.width, i.height);
    }
    if !any {
        return (0.0, 0.0, CANVAS_MIN_W, CANVAS_MIN_H);
    }
    (min_x, min_y, max_x, max_y)
}

fn fit_canvas_size(
    matches: &[BracketMatchData],
    texts: &[BracketTextData],
    labeled: &[BracketLabeledTeamData],
    images: &[BracketImageData],
    mut canvas_size: Signal<(f64, f64)>,
) {
    let (_x0, _y0, x1, y1) = content_bounds(matches, texts, labeled, images);
    canvas_size.set((x1.max(CANVAS_MIN_W) + 240.0, y1.max(CANVAS_MIN_H) + 240.0));
}

fn join_matches_with_placements(
    infos: Vec<BracketMatchInfo>,
    placements: &[BracketPlacementRow],
) -> Vec<BracketMatchData> {
    let place_map: HashMap<String, BracketPlacementData> = placements
        .iter()
        .map(|p| (p.match_id.clone(), p.to_placement_data()))
        .collect();
    infos
        .into_iter()
        .map(|info| {
            let p = place_map.get(&info.uuid).cloned();
            BracketMatchData::from_info(info, p)
        })
        .collect()
}

/// Apply layout-only mutation/GET payload: merge placements onto existing matches.
fn apply_layout(
    resp: BracketLayoutResponse,
    mut local_matches: Signal<Vec<BracketMatchData>>,
    mut local_texts: Signal<Vec<BracketTextData>>,
    mut local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    mut local_images: Signal<Vec<BracketImageData>>,
    mut dirty: Signal<bool>,
    canvas_size: Signal<(f64, f64)>,
) {
    let place_map: HashMap<String, BracketPlacementData> = resp
        .placements
        .iter()
        .map(|p| (p.match_id.clone(), p.to_placement_data()))
        .collect();
    let mut ms = local_matches();
    for m in ms.iter_mut() {
        m.placement = place_map.get(&m.uuid).cloned();
    }
    fit_canvas_size(
        &ms,
        &resp.texts,
        &resp.labeled_teams,
        &resp.images,
        canvas_size,
    );
    local_matches.set(ms);
    local_texts.set(resp.texts);
    local_labeled.set(resp.labeled_teams);
    local_images.set(resp.images);
    dirty.set(false);
}

/// Full initial hydrate from layout + matches endpoints.
fn apply_bootstrap(
    layout: BracketLayoutResponse,
    matches_resp: BracketMatchesResponse,
    mut local_matches: Signal<Vec<BracketMatchData>>,
    mut local_texts: Signal<Vec<BracketTextData>>,
    mut local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    mut local_images: Signal<Vec<BracketImageData>>,
    mut dirty: Signal<bool>,
    canvas_size: Signal<(f64, f64)>,
) {
    let joined = join_matches_with_placements(matches_resp.matches, &layout.placements);
    fit_canvas_size(
        &joined,
        &layout.texts,
        &layout.labeled_teams,
        &layout.images,
        canvas_size,
    );
    local_matches.set(joined);
    local_texts.set(layout.texts);
    local_labeled.set(layout.labeled_teams);
    local_images.set(layout.images);
    dirty.set(false);
}

/// Complete idempotent layout write. Only committed gestures enter the queue.
#[derive(Clone, PartialEq)]
struct LayoutSnapshot {
    placements: Vec<serde_json::Value>,
    texts: Vec<serde_json::Value>,
    labeled: Vec<serde_json::Value>,
    images: Vec<serde_json::Value>,
    clear_missing: bool,
}

impl LayoutSnapshot {
    fn capture(
        matches: &[BracketMatchData],
        texts: &[BracketTextData],
        labeled: &[BracketLabeledTeamData],
        images: &[BracketImageData],
        clear_missing: bool,
    ) -> Self {
        Self {
            placements: all_placements_json(matches),
            texts: texts_json(texts),
            labeled: labeled_json(labeled),
            images: images_json(images),
            clear_missing,
        }
    }
}

#[derive(Clone, Default)]
struct LayoutSaveState {
    pending: Option<LayoutSnapshot>,
    revision: u64,
    running: bool,
    failed: bool,
    saved: bool,
}

impl LayoutSaveState {
    fn can_acknowledge(
        &self,
        revision: u64,
        sent: &LayoutSnapshot,
        current: &LayoutSnapshot,
    ) -> bool {
        self.revision == revision && sent == current
    }
    fn enqueue(&mut self, snapshot: LayoutSnapshot) -> bool {
        self.revision += 1;
        self.pending = Some(snapshot);
        self.saved = false;
        if self.running || self.failed {
            return false;
        }
        self.running = true;
        true
    }
}

#[derive(Clone, Copy)]
struct BracketSave {
    busy: Signal<bool>,
    layout: Signal<LayoutSaveState>,
    error: Signal<Option<String>>,
    gesture: Signal<bool>,
}

impl std::ops::Deref for BracketSave {
    type Target = Signal<bool>;
    fn deref(&self) -> &Self::Target {
        &self.busy
    }
}

impl std::fmt::Display for BracketSave {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", (self.busy)())
    }
}

impl BracketSave {
    fn error(self) -> Option<String> {
        (self.error)()
    }
    fn layout(self) -> LayoutSaveState {
        (self.layout)()
    }
    fn set(&mut self, value: bool) {
        self.busy.set(value);
    }
    fn structural_busy(self) -> bool {
        ((self.busy)() && !self.layout.peek().running)
            || (self.error.peek().is_some() && !self.layout.peek().failed)
    }
    fn begin(&mut self, dirty: bool) -> bool {
        if (self.busy)() || dirty || self.error.peek().is_some() {
            return false;
        }
        self.busy.set(true);
        true
    }
    fn fail(&mut self, message: impl Into<String>) {
        self.error.set(Some(message.into()));
    }
}

fn persist_all(
    url: String,
    matches: Vec<BracketMatchData>,
    texts: Vec<BracketTextData>,
    labeled: Vec<BracketLabeledTeamData>,
    images: Vec<BracketImageData>,
    clear_missing: bool,
    local_matches: Signal<Vec<BracketMatchData>>,
    local_texts: Signal<Vec<BracketTextData>>,
    local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    local_images: Signal<Vec<BracketImageData>>,
    mut dirty: Signal<bool>,
    mut saving: BracketSave,
    canvas_size: Signal<(f64, f64)>,
) {
    let snapshot = LayoutSnapshot::capture(&matches, &texts, &labeled, &images, clear_missing);
    if !saving.layout.write().enqueue(snapshot) {
        return;
    }
    saving.set(true);
    spawn(async move {
        loop {
            let (snapshot, revision) = {
                let mut state = saving.layout.write();
                let Some(snapshot) = state.pending.take() else {
                    break;
                };
                (snapshot, state.revision)
            };
            match api::save_bracket_placements(
                &url,
                &snapshot.placements,
                &snapshot.texts,
                &snapshot.labeled,
                &snapshot.images,
                snapshot.clear_missing,
            )
            .await
            {
                Ok(resp) => {
                    // A response must not replace a newer committed edit or a drag still underway.
                    let current = LayoutSnapshot::capture(
                        &local_matches(),
                        &local_texts(),
                        &local_labeled(),
                        &local_images(),
                        snapshot.clear_missing,
                    );
                    if saving
                        .layout
                        .peek()
                        .can_acknowledge(revision, &snapshot, &current)
                    {
                        if !(saving.gesture)() {
                            apply_layout(
                                resp,
                                local_matches,
                                local_texts,
                                local_labeled,
                                local_images,
                                dirty,
                                canvas_size,
                            );
                        } else {
                            // Pan/marquee do not change the snapshot. Acknowledge the save
                            // without replacing coordinates under an active pointer.
                            dirty.set(false);
                        }
                        saving.layout.write().saved = true;
                        saving.error.set(None);
                    }
                }
                Err(message) => {
                    let mut state = saving.layout.write();
                    if state.pending.is_none() {
                        state.pending = Some(snapshot);
                    }
                    state.failed = true;
                    drop(state);
                    saving.fail(format!("Changes have not been confirmed saved. {message}"));
                    break;
                }
            }
        }
        saving.layout.write().running = false;
        saving.set(false);
    });
}

fn convert_match_port(
    url: String,
    match_uuid: String,
    side: &str,
    mode: &str,
    local_matches: Signal<Vec<BracketMatchData>>,
    local_texts: Signal<Vec<BracketTextData>>,
    local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    local_images: Signal<Vec<BracketImageData>>,
    dirty: Signal<bool>,
    mut saving: BracketSave,
    canvas_size: Signal<(f64, f64)>,
) {
    let side = side.to_string();
    let mode = mode.to_string();
    if !saving.begin(dirty()) {
        return;
    }
    spawn(async move {
        match api::convert_bracket_port(&url, &match_uuid, &side, &mode).await {
            Ok(resp) => apply_layout(
                resp,
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                canvas_size,
            ),
            Err(e) => {
                saving.fail(e);
            }
        }
        saving.set(false);
    });
}

/// Consumers of one output of a match: whether any exist, and display names of
/// those not drawn as a wire (LABEL mode or unplaced), deduplicated in order.
struct OutputConsumers {
    any: bool,
    unwired: Vec<String>,
}

fn output_consumers(
    source: &BracketMatchData,
    qual: Qual,
    matches: &[BracketMatchData],
    labeled: &[BracketLabeledTeamData],
) -> OutputConsumers {
    let refs_source = |token: &str| {
        parse_match_ref(token)
            .map(|(name, q)| q == qual && name.eq_ignore_ascii_case(&source.name))
            .unwrap_or(false)
    };
    let mut any = false;
    let mut unwired: Vec<String> = Vec::new();
    let mut push = |name: String| {
        if !unwired.contains(&name) {
            unwired.push(name);
        }
    };
    for m in matches {
        if m.uuid == source.uuid {
            continue;
        }
        for (initial, mode) in [
            (
                m.team1_initial.as_deref(),
                m.placement.as_ref().map(|p| p.team1.as_str()),
            ),
            (
                m.team2_initial.as_deref(),
                m.placement.as_ref().map(|p| p.team2.as_str()),
            ),
        ] {
            if !initial.map(&refs_source).unwrap_or(false) {
                continue;
            }
            any = true;
            if !(is_placed(m) && mode.map(is_net).unwrap_or(false)) {
                push(m.name.clone());
            }
        }
    }
    for lt in labeled {
        if !refs_source(&lt.team) {
            continue;
        }
        any = true;
        if !is_net(&lt.kind) {
            push(if lt.label.is_empty() {
                "Label".into()
            } else {
                lt.label.clone()
            });
        }
    }
    OutputConsumers { any, unwired }
}

/// Toggle LABEL↔NET on every consumer of `source`'s winner or loser output.
/// Same semantics as clicking a net-label / wire on each consumer input:
/// NET → LABEL, LABEL → NET (auto-placing unplaced consumers first).
/// With `wire_only`, consumers already drawn as wires are left alone.
/// When the output has no consumers at all, a wired team label is created
/// right next to the output port instead.
fn toggle_output_consumers(
    url: String,
    source: &BracketMatchData,
    qual: Qual,
    wire_only: bool,
    local_matches: Signal<Vec<BracketMatchData>>,
    local_texts: Signal<Vec<BracketTextData>>,
    local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    local_images: Signal<Vec<BracketImageData>>,
    dirty: Signal<bool>,
    mut saving: BracketSave,
    canvas_size: Signal<(f64, f64)>,
) {
    let src_name = source.name.clone();
    let sp = placement_or_default(source);
    let (sx, sy) = (sp.x_pos.unwrap_or(40.0), sp.y_pos.unwrap_or(40.0));

    if !output_consumers(source, qual, &local_matches(), &local_labeled()).any {
        let token = match qual {
            Qual::Winner => format!("{src_name}::winner"),
            Qual::Loser => format!("{src_name}::loser"),
        };
        let lx = sx + sp.width + OUTPUT_LABEL_GAP;
        let ly = out_port_y(sy, sp.height, qual) - LABELED_TEAM_H * 0.5;
        if !saving.begin(dirty()) {
            return;
        }
        spawn(async move {
            match api::add_bracket_labeled_team_with(&url, lx, ly, &token, "NET").await {
                Ok(resp) => apply_layout(
                    resp,
                    local_matches,
                    local_texts,
                    local_labeled,
                    local_images,
                    dirty,
                    canvas_size,
                ),
                Err(e) => {
                    saving.fail(e);
                }
            }
            saving.set(false);
        });
        return;
    }

    #[derive(Clone)]
    enum Action {
        /// Place match then convert side to mode.
        Match {
            uuid: String,
            side: String,
            mode: String,
            place_at: Option<(f64, f64)>,
        },
        Labeled {
            id: String,
            mode: String,
        },
    }

    let mut actions: Vec<Action> = Vec::new();
    let mut place_i = 0usize;

    for m in local_matches().iter() {
        if m.uuid == source.uuid {
            continue;
        }
        for (side, initial, port_is_net) in [
            (
                "team1",
                m.team1_initial.as_deref(),
                m.placement
                    .as_ref()
                    .map(|p| is_net(&p.team1))
                    .unwrap_or(false),
            ),
            (
                "team2",
                m.team2_initial.as_deref(),
                m.placement
                    .as_ref()
                    .map(|p| is_net(&p.team2))
                    .unwrap_or(false),
            ),
        ] {
            let Some(init) = initial else { continue };
            let Some((ref_name, q)) = parse_match_ref(init) else {
                continue;
            };
            if !ref_name.eq_ignore_ascii_case(&src_name) || q != qual {
                continue;
            }
            if wire_only && port_is_net && is_placed(m) {
                continue;
            }
            let mode = if port_is_net && !wire_only {
                "LABEL"
            } else {
                "NET"
            };
            let place_at = if !is_placed(m) && mode == "NET" {
                let nx = sx + sp.width + 80.0;
                let ny = sy + (place_i as f64) * (DEFAULT_HEIGHT + 24.0);
                place_i += 1;
                Some((nx, ny))
            } else if !is_placed(m) {
                // Unplaced + already would be LABEL — nothing to do.
                continue;
            } else {
                None
            };
            actions.push(Action::Match {
                uuid: m.uuid.clone(),
                side: side.into(),
                mode: mode.into(),
                place_at,
            });
        }
    }

    for lt in local_labeled().iter() {
        let Some((ref_name, q)) = parse_match_ref(&lt.team) else {
            continue;
        };
        if !ref_name.eq_ignore_ascii_case(&src_name) || q != qual {
            continue;
        }
        if wire_only && is_net(&lt.kind) {
            continue;
        }
        let mode = if is_net(&lt.kind) { "LABEL" } else { "NET" };
        actions.push(Action::Labeled {
            id: lt.id.clone(),
            mode: mode.into(),
        });
    }

    if actions.is_empty() {
        return;
    }

    if !saving.begin(dirty()) {
        return;
    }
    spawn(async move {
        let mut last_ok = None;
        for act in actions {
            match act {
                Action::Match {
                    uuid,
                    side,
                    mode,
                    place_at,
                } => {
                    if let Some((nx, ny)) = place_at {
                        match api::add_bracket_placement(&url, &uuid, nx, ny).await {
                            Ok(resp) => last_ok = Some(resp),
                            Err(e) => {
                                saving.fail(e);
                                continue;
                            }
                        }
                    }
                    match api::convert_bracket_port(&url, &uuid, &side, &mode).await {
                        Ok(resp) => last_ok = Some(resp),
                        Err(e) => {
                            saving.fail(e);
                        }
                    }
                }
                Action::Labeled { id, mode } => {
                    match api::convert_labeled_team_port(&url, &id, &mode).await {
                        Ok(resp) => last_ok = Some(resp),
                        Err(e) => {
                            saving.fail(e);
                        }
                    }
                }
            }
        }
        if let Some(resp) = last_ok {
            apply_layout(
                resp,
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                canvas_size,
            );
        }
        saving.set(false);
    });
}

fn delete_selected(
    mut local_matches: Signal<Vec<BracketMatchData>>,
    mut local_texts: Signal<Vec<BracketTextData>>,
    mut local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    mut local_images: Signal<Vec<BracketImageData>>,
    interaction: Signal<Interaction>,
    mut dirty: Signal<bool>,
) -> bool {
    let sel = interaction().selected;
    if sel.is_empty() {
        return false;
    }
    let mut ms = local_matches();
    let victim_names: HashSet<String> = ms
        .iter()
        .filter(|m| sel.contains(&sel_match(&m.uuid)))
        .map(|m| m.name.to_ascii_lowercase())
        .collect();
    let mut any = false;
    for m in ms.iter_mut() {
        if sel.contains(&sel_match(&m.uuid)) {
            if let Some(p) = m.placement.as_mut() {
                p.x_pos = None;
                p.y_pos = None;
                p.placed = false;
                any = true;
            }
        } else if let Some(p) = m.placement.as_mut() {
            for (mode, init) in [
                (&mut p.team1, m.team1_initial.as_deref()),
                (&mut p.team2, m.team2_initial.as_deref()),
            ] {
                if is_net(mode) {
                    if let Some(init) = init {
                        if let Some((n, _)) = parse_match_ref(init) {
                            if victim_names.contains(&n.to_ascii_lowercase()) {
                                *mode = "LABEL".into();
                                any = true;
                            }
                        }
                    }
                }
            }
        }
    }
    let mut lts = local_labeled();
    for t in lts.iter_mut() {
        if is_net(&t.kind) {
            if let Some((n, _)) = parse_match_ref(&t.team) {
                if victim_names.contains(&n.to_ascii_lowercase()) {
                    t.kind = "LABEL".into();
                    any = true;
                }
            }
        }
    }
    let before_t = local_texts().len();
    let texts: Vec<_> = local_texts()
        .into_iter()
        .filter(|t| !sel.contains(&sel_text(&t.id)))
        .collect();
    if texts.len() != before_t {
        any = true;
    }
    let before_l = lts.len();
    lts.retain(|t| !sel.contains(&sel_labeled(&t.id)));
    if lts.len() != before_l {
        any = true;
    }
    let before_i = local_images().len();
    let images: Vec<_> = local_images()
        .into_iter()
        .filter(|i| !sel.contains(&sel_image(&i.id)))
        .collect();
    if images.len() != before_i {
        any = true;
    }
    if any {
        local_matches.set(ms);
        local_texts.set(texts);
        local_labeled.set(lts);
        local_images.set(images);
        dirty.set(true);
    }
    any
}

fn swap_selected_inputs(
    mut local_matches: Signal<Vec<BracketMatchData>>,
    interaction: Signal<Interaction>,
    mut dirty: Signal<bool>,
) -> bool {
    let sel = interaction().selected;
    let mut ms = local_matches();
    let mut any = false;
    for m in ms.iter_mut() {
        if sel.contains(&sel_match(&m.uuid)) {
            if let Some(p) = m.placement.as_mut() {
                p.inputs_flipped = !p.inputs_flipped;
                any = true;
            }
        }
    }
    if any {
        local_matches.set(ms);
        dirty.set(true);
    }
    any
}

fn focus_bracket_root() {
    #[cfg(target_arch = "wasm32")]
    {
        if let Some(window) = web_sys::window() {
            if let Some(doc) = window.document() {
                if let Some(el) = doc.get_element_by_id("bracket-keyboard-focus") {
                    if let Some(html_el) = el.dyn_ref::<web_sys::HtmlElement>() {
                        // Keep the event banner and current viewport in place when restoring hotkeys.
                        let options = web_sys::FocusOptions::new();
                        options.set_prevent_scroll(true);
                        let _ = html_el.focus_with_options(&options);
                    }
                }
            }
        }
    }
}

/// World-space origin for newly added elements, inside the current viewport.
///
/// ``stagger`` offsets successive adds so they don't stack exactly on top of
/// each other. Coordinates are clamped to stay non-negative.
/// Place a new labeled team so its snap origin lands on `grid` (port → half-cell).
fn labeled_add_position(zoom: f64, pan: (f64, f64), stagger: usize, grid: f64) -> (f64, f64) {
    let (ax, ay) = view_add_position(zoom, pan, stagger);
    let g = if grid > 0.0 { grid } else { DEFAULT_GRID_SIZE };
    let (lx, ly) = labeled_to_snap_origin(ax, ay);
    let (lx, ly) = (snap_coord(lx, g), snap_coord(ly, g));
    labeled_from_snap_origin(lx, ly)
}

fn view_add_position(zoom: f64, pan: (f64, f64), stagger: usize) -> (f64, f64) {
    let (vw, vh) = {
        #[cfg(target_arch = "wasm32")]
        {
            if let Some(window) = web_sys::window() {
                if let Some(doc) = window.document() {
                    if let Some(el) = doc.get_element_by_id("bracket-canvas-wrap") {
                        let r = el.get_bounding_client_rect();
                        (r.width().max(200.0), r.height().max(200.0))
                    } else {
                        (1000.0, 600.0)
                    }
                } else {
                    (1000.0, 600.0)
                }
            } else {
                (1000.0, 600.0)
            }
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            (1000.0, 600.0)
        }
    };
    let z = zoom.max(0.001);
    // Place near the upper-left of the visible area with a small cascade.
    let step = 28.0;
    let col = (stagger % 4) as f64;
    let row = ((stagger / 4) % 6) as f64;
    let sx = 48.0 + col * step;
    let sy = 48.0 + row * step + col * 6.0;
    // Keep inside the viewport if the wrap is tiny.
    let sx = sx.min((vw * 0.5_f64).max(24.0));
    let sy = sy.min((vh * 0.5_f64).max(24.0));
    let wx = (sx - pan.0) / z;
    let wy = (sy - pan.1) / z;
    (wx, wy)
}

fn next_add_stagger(
    matches: &[BracketMatchData],
    texts: &[BracketTextData],
    labeled: &[BracketLabeledTeamData],
    images: &[BracketImageData],
) -> usize {
    let placed = matches.iter().filter(|m| is_placed(m)).count();
    placed + texts.len() + labeled.len() + images.len()
}

fn canvas_pointer(ev: &Event<PointerData>, zoom: f64, pan: (f64, f64)) -> (f64, f64) {
    #[cfg(target_arch = "wasm32")]
    {
        if let Some(window) = web_sys::window() {
            if let Some(doc) = window.document() {
                if let Some(el) = doc.get_element_by_id("bracket-canvas-wrap") {
                    let rect = el.get_bounding_client_rect();
                    let sx = ev.client_coordinates().x - rect.left();
                    let sy = ev.client_coordinates().y - rect.top();
                    // Convert screen coords inside wrap to world coords.
                    let z = zoom.max(0.001);
                    return ((sx - pan.0) / z, (sy - pan.1) / z);
                }
            }
        }
    }
    let c = ev.element_coordinates();
    let z = zoom.max(0.001);
    ((c.x - pan.0) / z, (c.y - pan.1) / z)
}

fn screen_pointer(ev: &Event<PointerData>) -> (f64, f64) {
    (ev.client_coordinates().x, ev.client_coordinates().y)
}

/// Capture the original hit target so pointer-up/cancel still bubbles through the
/// canvas outside its bounds, without retargeting ordinary clicks to the wrapper.
fn capture_canvas_pointer(ev: &Event<PointerData>) {
    #[cfg(target_arch = "wasm32")]
    {
        use dioxus_web::WebEventExt;
        if let Some(target) = ev
            .data()
            .try_as_web_event()
            .and_then(|event| event.target())
            .and_then(|target| target.dyn_into::<web_sys::Element>().ok())
        {
            let _ = target.set_pointer_capture(ev.pointer_id());
        }
    }
}

fn release_canvas_pointer(ev: &Event<PointerData>) {
    #[cfg(target_arch = "wasm32")]
    {
        use dioxus_web::WebEventExt;
        if let Some(target) = ev
            .data()
            .try_as_web_event()
            .and_then(|event| event.target())
            .and_then(|target| target.dyn_into::<web_sys::Element>().ok())
        {
            let _ = target.release_pointer_capture(ev.pointer_id());
        }
    }
}

fn underline_label(label: &str, key: char) -> Element {
    // Render label with the shortcut letter underlined (first match,
    // case-insensitive).
    let lower_key = key.to_ascii_lowercase();
    let mut chars: Vec<char> = label.chars().collect();
    let mut idx = None;
    for (i, ch) in chars.iter().enumerate() {
        if ch.to_ascii_lowercase() == lower_key {
            idx = Some(i);
            break;
        }
    }
    if let Some(i) = idx {
        let before: String = chars[..i].iter().collect();
        let under = chars[i];
        let after: String = chars[i + 1..].iter().collect();
        rsx! {
            span {
                "{before}"
                span { style: "text-decoration: underline;", "{under}" }
                "{after}"
            }
        }
    } else {
        rsx! { span { "{label}" } }
    }
}

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
enum DragKind {
    Move {
        origins: HashMap<String, (f64, f64)>,
        pointer_start: (f64, f64),
    },
    Resize {
        id: String,
        mode: String, // corner | e | s
        start_w: f64,
        start_h: f64,
        aspect: f64,
        pointer_start: (f64, f64),
    },
    Marquee {
        start: (f64, f64),
        current: (f64, f64),
    },
    Pan {
        pointer_start: (f64, f64),
        pan_start: (f64, f64),
    },
}

#[derive(Clone, Debug, Default)]
struct Interaction {
    drag: Option<DragKind>,
    selected: HashSet<String>,
}

/// Unsaved pointer gestures can be cancelled by the browser (for example when
/// a system gesture takes over). Keep their starting state so cancellation never
/// leaves a half-dragged node to be saved by a later action.
#[derive(Clone)]
struct PointerGesture {
    pointer_id: i32,
    interaction: Interaction,
    matches: Vec<BracketMatchData>,
    texts: Vec<BracketTextData>,
    labeled: Vec<BracketLabeledTeamData>,
    images: Vec<BracketImageData>,
    pan: (f64, f64),
    dirty: bool,
}

#[derive(Clone, Debug, PartialEq)]
enum ActiveModal {
    None,
    AddMenu,
    RatsnestMenu,
    AddMatch,
    EditText { id: String },
    EditLabeledTeam { id: String },
    AddImage,
    LegacyManage,
}

/// Only the first editor for a newly created node may roll it back on dismissal.
fn dismisses_provisional_node(modal: &ActiveModal, pending: Option<&(String, String)>) -> bool {
    let Some((kind, pending_id)) = pending else { return false; };
    match modal {
        ActiveModal::EditText { id } => kind == "text" && pending_id == id,
        ActiveModal::EditLabeledTeam { id } => kind == "labeled" && pending_id == id,
        _ => false,
    }
}

impl Default for ActiveModal {
    fn default() -> Self {
        Self::None
    }
}

#[derive(Clone, Debug, PartialEq)]
enum LabelKind {
    Team {
        display: String,
    },
    Tag {
        name: String,
    },
    Winner {
        match_name: String,
    },
    Loser {
        match_name: String,
    },
    /// Output side: comma-separated consumers not drawn as wires.
    Destinations {
        names: String,
    },
    Raw(String),
}

fn resolve_label(
    initial: &str,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    _matches: &[BracketMatchData],
) -> LabelKind {
    let s = initial.trim();
    if s.is_empty() {
        return LabelKind::Raw("—".into());
    }
    if let Some((name, qual)) = parse_match_ref(s) {
        return match qual {
            Qual::Winner => LabelKind::Winner { match_name: name },
            Qual::Loser => LabelKind::Loser { match_name: name },
        };
    }
    if let Some(rest) = s.strip_prefix("tag::").or_else(|| {
        if s.to_ascii_lowercase().starts_with("tag::") {
            Some(&s[5..])
        } else {
            None
        }
    }) {
        return LabelKind::Tag {
            name: rest.trim().to_string(),
        };
    }
    if let Some(opt) = team_options
        .iter()
        .find(|t| t.id == s || t.pseudonym.as_deref() == Some(s))
    {
        return LabelKind::Team {
            display: opt.pseudonym.clone().unwrap_or_else(|| opt.id.clone()),
        };
    }
    let _ = tags;
    LabelKind::Raw(s.to_string())
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

#[component]
pub fn Bracket(url: String) -> Element {
    rsx! {
        TournamentHomeContent {
            key: "{url}",
            url: url.clone(),
            initial_tab: Some("bracket".to_string()),
            content: Some(rsx! { BracketContent { url } }),
        }
    }
}

#[component]
fn BracketContent(url: String) -> Element {
    let url_for_data = url.clone();
    let mut data = use_resource(move || {
        let u = url_for_data.clone();
        async move {
            let layout = api::tournament_bracket(&u)
                .await
                .map_err(|e| e.to_string())?;
            let matches = api::tournament_bracket_matches(&u)
                .await
                .map_err(|e| e.to_string())?;
            Ok::<(BracketLayoutResponse, BracketMatchesResponse), String>((layout, matches))
        }
    });

    let mut edit_mode = use_signal(|| false);
    let mut local_matches = use_signal(|| Vec::<BracketMatchData>::new());
    let mut local_texts = use_signal(|| Vec::<BracketTextData>::new());
    let mut local_labeled = use_signal(|| Vec::<BracketLabeledTeamData>::new());
    let mut local_images = use_signal(|| Vec::<BracketImageData>::new());
    let mut legacy_brackets = use_signal(|| Vec::<BracketItem>::new());
    let mut bracket_published = use_signal(|| false);
    let mut team_options_sig = use_signal(|| Vec::<TeamOption>::new());
    let mut tags_sig = use_signal(|| Vec::<TagSetupData>::new());
    let mut is_to_sig = use_signal(|| false);
    let mut interaction = use_signal(Interaction::default);
    // Hold-H hand tool: pointer motion pans without a button (trackpad-friendly).
    let mut hand_pan = use_signal(|| false);
    let mut hand_last = use_signal(|| None::<(f64, f64)>);
    let mut active_modal = use_signal(ActiveModal::default);
    let mut add_match_query = use_signal(String::new);
    let mut saving = BracketSave {
        busy: use_signal(|| false),
        layout: use_signal(LayoutSaveState::default),
        error: use_signal(|| None),
        gesture: use_signal(|| false),
    };
    let mut dirty = use_signal(|| false);
    let mut canvas_size = use_signal(|| (CANVAS_MIN_W, CANVAS_MIN_H));
    let mut initialized = use_signal(|| false);
    let mut zoom = use_signal(|| 1.0_f64);
    let mut pan = use_signal(|| (0.0_f64, 0.0_f64));
    let mut text_draft = use_signal(String::new);
    let mut text_size_draft = use_signal(|| 18.0_f64);
    let mut team_draft = use_signal(String::new);
    let mut label_draft = use_signal(|| "Label".to_string());
    let mut fit_tick = use_signal(|| 0u32);
    /// When not editing, wrap height is derived from width-fit scale (px).
    let mut view_wrap_height = use_signal(|| None::<f64>);
    /// Snap pitch in world px; 0 = free move. Visual grid uses the same pitch.
    let mut grid_size = use_signal(|| DEFAULT_GRID_SIZE);
    let mut show_refs = use_signal(|| false);
    let mut show_field = use_signal(|| false);
    /// When set, Cancel on the edit dialog deletes this just-created element.
    /// Cleared after the first Save (subsequent edits keep the element on cancel).
    let mut pending_create = use_signal(|| None::<(String, String)>);
    // Native Escape, X and Cancel share exactly the same provisional rollback.
    let modal_cancel_url = url.clone();
    let close_editor_modal = EventHandler::<()>::new(move |_| {
        if saving() { return; }
        let pending = pending_create();
        if dismisses_provisional_node(&active_modal(), pending.as_ref()) {
            pending_create.set(None);
            cancel_pending_create(
                pending, local_texts, local_labeled, local_images, dirty,
                modal_cancel_url.clone(), local_matches, saving, canvas_size,
            );
        }
        active_modal.set(ActiveModal::None);
    });
    let navigator = use_navigator();
    let mut pointer_gesture = use_signal(|| None::<PointerGesture>);
    let mut begin_pointer_gesture = move |ev: &Event<PointerData>| {
        // The first finger owns the drag; extra touches must not replace it.
        if saving.structural_busy()
            || saving.error.peek().is_some() && !saving.layout.peek().failed
            || !ev.is_primary()
            || pointer_gesture.peek().is_some()
        {
            return false;
        }
        saving.gesture.set(true);
        pointer_gesture.set(Some(PointerGesture {
            pointer_id: ev.pointer_id(),
            interaction: interaction(),
            matches: local_matches(),
            texts: local_texts(),
            labeled: local_labeled(),
            images: local_images(),
            pan: pan(),
            dirty: dirty(),
        }));
        capture_canvas_pointer(ev);
        true
    };
    let cancel_url = url.clone();
    let cancel_pointer_gesture = EventHandler::<()>::new(move |_| {
        let origin = pointer_gesture.write().take();
        let Some(origin) = origin else {
            return;
        };
        saving.gesture.set(false);
        interaction.set(origin.interaction);
        local_matches.set(origin.matches);
        local_texts.set(origin.texts);
        local_labeled.set(origin.labeled);
        local_images.set(origin.images);
        pan.set(origin.pan);
        dirty.set(origin.dirty);
        if origin.dirty {
            persist_all(
                cancel_url.clone(),
                local_matches(),
                local_texts(),
                local_labeled(),
                local_images(),
                true,
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                saving,
                canvas_size,
            );
        }
        hand_last.set(None);
        clear_field_hover();
    });

    // Hoist resource read handle so effects/rsx share one stable Readable
    // (calling data.value() inside use_effect can ValueDroppedError).
    let val = data.value();

    // After any canvas re-render, re-stamp field-hover classes (VDOM resets `class`
    // on airwire groups). rAF so we run after the browser applies the patch.
    use_effect(move || {
        let _ = local_matches();
        let _ = local_texts();
        let _ = local_labeled();
        let _ = local_images();
        let _ = zoom();
        let _ = pan();
        let _ = show_field();
        let _ = edit_mode();
        apply_field_hover_dom();
        #[cfg(target_arch = "wasm32")]
        {
            if let Some(window) = web_sys::window() {
                let cb = wasm_bindgen::closure::Closure::once_into_js(move |_: JsValue| {
                    apply_field_hover_dom();
                });
                let _ = window.request_animation_frame(cb.as_ref().unchecked_ref());
            }
        }
    });

    use_effect(move || {
        if initialized() {
            return;
        }
        let borrow = val.read();
        if let Some(Ok((layout, matches_resp))) = borrow.as_ref() {
            let layout = layout.clone();
            let matches_resp = matches_resp.clone();
            drop(borrow);
            team_options_sig.set(matches_resp.team_options.clone());
            tags_sig.set(matches_resp.tags.clone());
            is_to_sig.set(layout.is_to);
            legacy_brackets.set(layout.legacy_brackets.clone());
            bracket_published.set(layout.bracket_published || layout.tournament.bracket_published);
            apply_bootstrap(
                layout,
                matches_resp,
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                canvas_size,
            );
            initialized.set(true);
            fit_tick.set(fit_tick() + 1);
        }
    });

    // Restore shortcuts only inside this bracket, without taking focus away
    // from controls. Remove native listeners when the bracket is unmounted.
    #[cfg(target_arch = "wasm32")]
    {
        use std::cell::RefCell;
        use std::rc::Rc;
        use wasm_bindgen::closure::Closure;

        const INTERACTIVE: &str = "summary, a[href], button, input, textarea, select, label, \
            [contenteditable]:not([contenteditable='false']), dialog, [role='dialog'], \
            [role='button'], [role='link'], [role='menuitem'], [tabindex]:not([tabindex='-1'])";

        fn is_interactive(element: &web_sys::Element) -> bool {
            // The shortcut root itself is focusable, but is not a control.
            element
                .closest(INTERACTIVE)
                .ok()
                .flatten()
                .is_some_and(|ancestor| ancestor.id() != "bracket-keyboard-focus")
        }

        fn can_restore_focus(document: &web_sys::Document) -> bool {
            if document.hidden() {
                return false;
            }
            let Some(root) = document.get_element_by_id("bracket-keyboard-focus") else {
                return false;
            };
            let Some(active) = document.active_element() else {
                return true;
            };
            if is_interactive(&active) {
                return false;
            }
            active.tag_name() == "BODY" || root.contains(Some(&active))
        }

        struct FocusListeners {
            window: web_sys::Window,
            document: web_sys::Document,
            focus: Closure<dyn FnMut(web_sys::Event)>,
            click: Closure<dyn FnMut(web_sys::Event)>,
        }
        impl Drop for FocusListeners {
            fn drop(&mut self) {
                let _ = self.window.remove_event_listener_with_callback(
                    "focus",
                    self.focus.as_ref().unchecked_ref(),
                );
                let _ = self.document.remove_event_listener_with_callback(
                    "visibilitychange",
                    self.focus.as_ref().unchecked_ref(),
                );
                let _ = self.document.remove_event_listener_with_callback(
                    "click",
                    self.click.as_ref().unchecked_ref(),
                );
            }
        }

        let listeners = use_hook(|| Rc::new(RefCell::new(None::<FocusListeners>)));
        let mounted = listeners.clone();
        use_effect(move || {
            if mounted.borrow().is_some() {
                return;
            }
            let Some(window) = web_sys::window() else {
                return;
            };
            let Some(document) = window.document() else {
                return;
            };
            let focus_document = document.clone();
            let focus = Closure::wrap(Box::new(move |_: web_sys::Event| {
                if can_restore_focus(&focus_document) {
                    focus_bracket_root();
                }
            }) as Box<dyn FnMut(_)>);
            let click_document = document.clone();
            let click = Closure::wrap(Box::new(move |event: web_sys::Event| {
                let Some(target) = event
                    .target()
                    .and_then(|target| target.dyn_into::<web_sys::Element>().ok())
                else {
                    return;
                };
                let Some(root) = click_document.get_element_by_id("bracket-keyboard-focus") else {
                    return;
                };
                if root.contains(Some(&target))
                    && !is_interactive(&target)
                    && can_restore_focus(&click_document)
                {
                    focus_bracket_root();
                }
            }) as Box<dyn FnMut(_)>);
            let _ =
                window.add_event_listener_with_callback("focus", focus.as_ref().unchecked_ref());
            let _ = document.add_event_listener_with_callback(
                "visibilitychange",
                focus.as_ref().unchecked_ref(),
            );
            let _ =
                document.add_event_listener_with_callback("click", click.as_ref().unchecked_ref());
            *mounted.borrow_mut() = Some(FocusListeners {
                window,
                document,
                focus,
                click,
            });
        });
        use_drop(move || {
            listeners.borrow_mut().take();
        });
    }

    // Auto-fit when not editing: scale to fill available width, then set
    // wrap height from that scale so the full bracket is visible (no vertical
    // crop).
    use_effect(move || {
        let _ = fit_tick();
        if edit_mode() {
            view_wrap_height.set(None);
            return;
        }
        let ms = local_matches();
        let ts = local_texts();
        let ls = local_labeled();
        let im = local_images();
        let (x0, y0, x1, y1) = content_bounds(&ms, &ts, &ls, &im);
        // Generous padding so left-side net labels / outlines aren't clipped.
        let pad = 56.0;
        let bw = (x1 - x0).max(80.0) + pad * 2.0;
        let bh = (y1 - y0).max(80.0) + pad * 2.0;
        let vw = {
            #[cfg(target_arch = "wasm32")]
            {
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let Some(el) = doc.get_element_by_id("bracket-canvas-wrap") {
                            el.get_bounding_client_rect().width().max(200.0)
                        } else if let Some(el) = doc
                            .query_selector("main, .container, .container-fluid")
                            .ok()
                            .flatten()
                        {
                            el.get_bounding_client_rect().width().max(200.0)
                        } else {
                            1000.0
                        }
                    } else {
                        1000.0
                    }
                } else {
                    1000.0
                }
            }
            #[cfg(not(target_arch = "wasm32"))]
            {
                1000.0
            }
        };
        // Fill width; allow modest upscale but don't blow past MAX_ZOOM.
        let z = (vw / bw).clamp(MIN_ZOOM, MAX_ZOOM);
        zoom.set(z);
        // Top-left align content inside the padded view box.
        pan.set(((pad - x0) * z, (pad - y0) * z));
        let h = (bh * z).max(120.0);
        view_wrap_height.set(Some(h));
    });

    // Re-fit on window resize while in view mode.
    #[cfg(target_arch = "wasm32")]
    {
        use_effect(move || {
            let window = match web_sys::window() {
                Some(w) => w,
                None => return,
            };
            let mut fit_tick = fit_tick;
            let cb = wasm_bindgen::closure::Closure::wrap(Box::new(move |_ev: web_sys::Event| {
                fit_tick.set(fit_tick() + 1);
            }) as Box<dyn FnMut(_)>);
            let _ = window.add_event_listener_with_callback("resize", cb.as_ref().unchecked_ref());
            cb.forget();
        });
    }

    let backend = api::base_url();

    rsx! {
        style { {PAGE_CSS} }
        style { {SCHEDULE_TOKEN_CSS} }

        if let Some(Ok((_layout, _matches_boot))) = val.read().as_ref() {
            {
                let is_to = is_to_sig();
                let team_options = team_options_sig();
                let tags = tags_sig();
                let tournament_url = url.clone();

                let matches_snap = local_matches();
                let texts_snap = local_texts();
                let labeled_snap = local_labeled();
                let images_snap = local_images();
                let legacy_snap = legacy_brackets();
                let setup_matches = matches_to_setup(&matches_snap);
                let canvas_empty = matches_snap.iter().filter(|m| is_placed(m)).count() == 0
                    && texts_snap.is_empty()
                    && labeled_snap.is_empty()
                    && images_snap.is_empty();
                let has_legacy = !legacy_snap.is_empty();
                let show_legacy_fallback = canvas_empty && has_legacy && !edit_mode();

                let by_name: HashMap<String, BracketMatchData> = matches_snap
                    .iter()
                    .cloned()
                    .map(|m| (m.name.to_ascii_lowercase(), m))
                    .collect();
                let placed: Vec<BracketMatchData> = matches_snap
                    .iter()
                    .filter(|m| is_placed(m))
                    .cloned()
                    .collect();
                // Used outputs + wires (matches + labeled teams)
                let mut used_outputs: HashSet<(String, Qual)> = HashSet::new();
                #[derive(Clone)]
                struct WireDesc {
                    key: String,
                    path: String,
                    // "match:{uuid}:{side}" or "lt:{id}"
                    target_key: String,
                    is_labeled: bool,
                }
                let mut wires: Vec<WireDesc> = Vec::new();

                for m in &placed {
                    let p = placement_or_default(m);
                    let (tx, ty) = match (p.x_pos, p.y_pos) {
                        (Some(x), Some(y)) => (x, y),
                        _ => continue,
                    };
                    for (side, mode, initial) in [
                        (Side::Team1, p.team1.as_str(), m.team1_initial.as_deref()),
                        (Side::Team2, p.team2.as_str(), m.team2_initial.as_deref()),
                    ] {
                        if !is_net(mode) { continue; }
                        let Some(init) = initial else { continue };
                        let Some((src_name, qual)) = parse_match_ref(init) else { continue };
                        let Some(src) = by_name.get(&src_name.to_ascii_lowercase()) else { continue };
                        if !is_placed(src) { continue; }
                        let sp = placement_or_default(src);
                        let (sx, sy) = match (sp.x_pos, sp.y_pos) {
                            (Some(x), Some(y)) => (x, y),
                            _ => continue,
                        };
                        used_outputs.insert((src.uuid.clone(), qual));
                        wires.push(WireDesc {
                            key: format!("mw-{}-{}-{}", src.uuid, m.uuid, side.as_str()),
                            path: wire_path(sx + sp.width, out_port_y(sy, sp.height, qual), tx, port_y(ty, p.height, side, p.inputs_flipped)),
                            target_key: format!("{}:{}", m.uuid, side.as_str()),
                            is_labeled: false,
                        });
                    }
                }
                for lt in &labeled_snap {
                    if !is_net(&lt.kind) { continue; }
                    let Some((src_name, qual)) = parse_match_ref(&lt.team) else { continue };
                    let Some(src) = by_name.get(&src_name.to_ascii_lowercase()) else { continue };
                    if !is_placed(src) { continue; }
                    let sp = placement_or_default(src);
                    let (sx, sy) = match (sp.x_pos, sp.y_pos) {
                        (Some(x), Some(y)) => (x, y),
                        _ => continue,
                    };
                    used_outputs.insert((src.uuid.clone(), qual));
                    let y_in = lt.y_pos + LABELED_TEAM_H * 0.5;
                    wires.push(WireDesc {
                        key: format!("lw-{}-{}", src.uuid, lt.id),
                        path: wire_path(sx + sp.width, out_port_y(sy, sp.height, qual), lt.x_pos, y_in),
                        target_key: lt.id.clone(),
                        is_labeled: true,
                    });
                }

                // Ref airwires: every match-ref input (LABEL or NET) → straight yellow line
                // from source W/L port to center of the target match's left edge.
                #[derive(Clone)]
                struct AirwireDesc {
                    key: String,
                    path: String,
                    color: String,
                    /// Set for field ratsnest wires (hover highlight key / tooltip).
                    field: Option<String>,
                    /// Ref wire points right-to-left (source is to the right of sink).
                    rtl: bool,
                }
                let mut ref_airwires: Vec<AirwireDesc> = Vec::new();
                if edit_mode() && is_to && show_refs() {
                    for m in &placed {
                        let p = placement_or_default(m);
                        let (tx, ty) = match (p.x_pos, p.y_pos) {
                            (Some(x), Some(y)) => (x, y),
                            _ => continue,
                        };
                        let in_y = ty + p.height * 0.5;
                        for (side_tag, initial) in [
                            ("t1", m.team1_initial.as_deref()),
                            ("t2", m.team2_initial.as_deref()),
                        ] {
                            let Some(init) = initial else { continue };
                            let Some((src_name, qual)) = parse_match_ref(init) else { continue };
                            let Some(src) = by_name.get(&src_name.to_ascii_lowercase()) else { continue };
                            if !is_placed(src) { continue; }
                            let sp = placement_or_default(src);
                            let (sx, sy) = match (sp.x_pos, sp.y_pos) {
                                (Some(x), Some(y)) => (x, y),
                                _ => continue,
                            };
                            let x_out = sx + sp.width;
                            // Wire runs right→left when the source sits to the right of the sink.
                            let rtl = x_out > tx;
                            ref_airwires.push(AirwireDesc {
                                key: format!("ref-{}-{}-{}", src.uuid, m.uuid, side_tag),
                                path: airwire_path(
                                    x_out,
                                    out_port_y(sy, sp.height, qual),
                                    tx,
                                    in_y,
                                ),
                                color: if rtl {
                                    REF_AIRWIRE_RTL_COLOR.into()
                                } else {
                                    REF_AIRWIRE_COLOR.into()
                                },
                                field: None,
                                rtl,
                            });
                        }
                    }
                }

                // Field airwires: per-field chain of placed matches in play order.
                let mut field_airwires: Vec<AirwireDesc> = Vec::new();
                if edit_mode() && is_to && show_field() {
                    let mut by_field: HashMap<String, Vec<&BracketMatchData>> = HashMap::new();
                    for m in &placed {
                        by_field.entry(match_field_key(m)).or_default().push(m);
                    }
                    for (field_name, mut group) in by_field {
                        group.sort_by_key(|m| match_play_order_key(m));
                        let color = field_airwire_color(&field_name);
                        for pair in group.windows(2) {
                            let a = pair[0];
                            let b = pair[1];
                            let pa = placement_or_default(a);
                            let pb = placement_or_default(b);
                            let (Some(ax), Some(ay)) = (pa.x_pos, pa.y_pos) else { continue };
                            let (Some(bx), Some(by)) = (pb.x_pos, pb.y_pos) else { continue };
                            // Same geometry family as refs: right-side output → left-center input.
                            // Field chain has no W/L, so use vertical center of the right edge.
                            let out_y = ay + pa.height * 0.5;
                            let in_y = by + pb.height * 0.5;
                            field_airwires.push(AirwireDesc {
                                key: format!("field-{}-{}-{}", field_name, a.uuid, b.uuid),
                                path: airwire_path(ax + pa.width, out_y, bx, in_y),
                                color: color.clone(),
                                field: Some(field_name.clone()),
                                rtl: false,
                            });
                        }
                    }
                }

                let (cw, ch) = canvas_size();
                let ix = interaction();
                let z = zoom();
                let (px, py) = pan();
                // View mode still uses pan/zoom for auto-fit (not interactive).
                let transform = format!("translate({px}px, {py}px) scale({z})");
                let active_field_hover = current_field_hover();

                let add_q = add_match_query();
                let add_q_lower = add_q.trim().to_ascii_lowercase();
                let filtered_add_matches: Vec<BracketMatchData> = {
                    let src = &matches_snap;
                    if add_q_lower.is_empty() {
                        src.clone()
                    } else {
                        src.iter()
                            .filter(|m| {
                                m.name.to_ascii_lowercase().contains(&add_q_lower)
                                    || m.team1_name.to_ascii_lowercase().contains(&add_q_lower)
                                    || m.team2_name.to_ascii_lowercase().contains(&add_q_lower)
                            })
                            .cloned()
                            .collect()
                    }
                };

                let handle_keydown = {
                    let u_key = tournament_url.clone();
                    move |ev: Event<KeyboardData>| {
                        if !edit_mode() || !is_to || saving.structural_busy() { return; }
                        let key_str = ev.key().to_string();
                        let modal = active_modal();

                        // Add-menu shortcuts
                        if modal == ActiveModal::AddMenu {
                            match key_str.as_str() {
                                "Escape" => {
                                    ev.prevent_default();
                                    active_modal.set(ActiveModal::None);
                                    focus_bracket_root();
                                }
                                "c" | "C" => {
                                    ev.prevent_default();
                                    add_match_query.set(String::new());
                                    active_modal.set(ActiveModal::AddMatch);
                                }
                                "x" | "X" => {
                                    ev.prevent_default();
                                    let u = u_key.clone();
                                    let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                    let (ax, ay) = view_add_position(zoom(), pan(), stagger);
                                    active_modal.set(ActiveModal::None);
                                    if !saving.begin(dirty()) { return; }
                                    spawn(async move {
                                        match api::add_bracket_text(&u, ax, ay).await {
                                            Ok(resp) => {
                                                let before: HashSet<String> =
                                                    local_texts().iter().map(|t| t.id.clone()).collect();
                                                apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                if let Some(t) = local_texts().iter().find(|t| !before.contains(&t.id)) {
                                                    text_draft.set(t.text.clone());
                                                    text_size_draft.set(t.size);
                                                    pending_create.set(Some(("text".into(), t.id.clone())));
                                                    active_modal.set(ActiveModal::EditText { id: t.id.clone() });
                                                }
                                            }
                                            Err(e) => {
                                                saving.fail(e);
                                            }
                                        }
                                        saving.set(false);
                                    });
                                }
                                "t" | "T" => {
                                    ev.prevent_default();
                                    let u = u_key.clone();
                                    let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                    let (ax, ay) = labeled_add_position(zoom(), pan(), stagger, grid_size());
                                    active_modal.set(ActiveModal::None);
                                    if !saving.begin(dirty()) { return; }
                                    let before_ids: HashSet<String> =
                                        local_labeled().iter().map(|t| t.id.clone()).collect();
                                    spawn(async move {
                                        match api::add_bracket_labeled_team(&u, ax, ay).await {
                                            Ok(resp) => {
                                                apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                if let Some(t) = local_labeled().iter().find(|t| !before_ids.contains(&t.id)) {
                                                    label_draft.set(if t.label.is_empty() { "Label".into() } else { t.label.clone() });
                                                    team_draft.set(t.team.clone());
                                                    pending_create.set(Some(("labeled".into(), t.id.clone())));
                                                    active_modal.set(ActiveModal::EditLabeledTeam { id: t.id.clone() });
                                                }
                                            }
                                            Err(e) => {
                                                saving.fail(e);
                                            }
                                        }
                                        saving.set(false);
                                    });
                                }
                                "e" | "E" => {
                                    ev.prevent_default();
                                    active_modal.set(ActiveModal::AddImage);
                                }
                                _ => {}
                            }
                            return;
                        }

                        // Other modals: Escape closes (except typing fields handle themselves)
                        if modal != ActiveModal::None {
                            if key_str == "Escape" {
                                ev.prevent_default();
                                // Esc on a just-created element cancels creation (deletes it).
                                let pend = pending_create();
                                let should_cancel = dismisses_provisional_node(&modal, pend.as_ref());
                                if should_cancel {
                                    pending_create.set(None);
                                    cancel_pending_create(
                                        pend, local_texts, local_labeled, local_images, dirty,
                                        u_key.clone(), local_matches, saving, canvas_size,
                                    );
                                }
                                active_modal.set(ActiveModal::None);
                                focus_bracket_root();
                            }
                            return;
                        }

                        match key_str.as_str() {
                            "Escape" => {
                                ev.prevent_default();
                                interaction.write().selected.clear();
                            }
                            "d" | "D" => {
                                if interaction().selected.is_empty() { return; }
                                ev.prevent_default();
                                if delete_selected(local_matches, local_texts, local_labeled, local_images, interaction, dirty) {
                                    interaction.write().selected.clear();
                                    persist_all(
                                                                    u_key.clone(),
                                                                    local_matches(),
                                                                    local_texts(),
                                                                    local_labeled(),
                                                                    local_images(),
                                                                    true,
                                                                    local_matches,
                                                                    local_texts,
                                                                    local_labeled,
                                                                    local_images,
                                                                    dirty,
                                                                    saving,
                                                                    canvas_size,
                                                                );
                                }
                            }
                            "a" | "A" => {
                                ev.prevent_default();
                                active_modal.set(ActiveModal::AddMenu);
                            }
                            "h" | "H" => {
                                let mods = ev.modifiers();
                                if mods.ctrl() || mods.meta() || mods.alt() { return; }
                                ev.prevent_default();
                                if !hand_pan() {
                                    hand_last.set(None);
                                    hand_pan.set(true);
                                    clear_field_hover();
                                    let mut ix = interaction.write();
                                    if !matches!(ix.drag, Some(DragKind::Move { .. } | DragKind::Resize { .. })) {
                                        ix.drag = None;
                                    }
                                }
                            }
                            "s" | "S" => {
                                if interaction().selected.is_empty() { return; }
                                ev.prevent_default();
                                if swap_selected_inputs(local_matches, interaction, dirty) {
                                    persist_all(
                                                                    u_key.clone(),
                                                                    local_matches(),
                                                                    local_texts(),
                                                                    local_labeled(),
                                                                    local_images(),
                                                                    true,
                                                                    local_matches,
                                                                    local_texts,
                                                                    local_labeled,
                                                                    local_images,
                                                                    dirty,
                                                                    saving,
                                                                    canvas_size,
                                                                );
                                }
                            }
                            _ => {}
                        }
                    }
                };

                rsx! {
                    div {
                        id: "bracket-keyboard-focus",
                        class: "bracket-keyboard-focus",
                        tabindex: 0,
                        role: if edit_mode() { "application" } else { "region" },
                        aria_label: if edit_mode() { "Bracket editor" } else { "Tournament bracket" },
                        onkeydown: handle_keydown,
                        onkeyup: move |ev: Event<KeyboardData>| {
                            let k = ev.key().to_string();
                            if k == "h" || k == "H" {
                                hand_pan.set(false);
                                hand_last.set(None);
                            }
                        },
                        // Losing focus means the H keyup may never arrive.
                        onfocusout: move |_| {
                            if hand_pan() {
                                hand_pan.set(false);
                                hand_last.set(None);
                            }
                        },
                        // Clicking gutters/sides of the page restores hotkey focus.
                        onclick: move |_| {
                            focus_bracket_root();
                        },
                        onmounted: move |_| focus_bracket_root(),

                        if is_to {
                            if let Some(message) = saving.error() {
                                div { class: "alert alert-danger d-flex flex-wrap align-items-center gap-2", role: "alert", id: "bracket-save-error",
                                    span { "{message}" }
                                    if saving.layout().failed {
                                        button { r#type: "button", class: "btn btn-outline-danger", disabled: saving(),
                                            onclick: { let u = tournament_url.clone(); move |_| {
                                                saving.layout.write().failed = false;
                                                persist_all(u.clone(), local_matches(), local_texts(), local_labeled(), local_images(), true, local_matches, local_texts, local_labeled, local_images, dirty, saving, canvas_size);
                                            } }, "Retry save"
                                        }
                                        span { class: "small", "Keep this page open until your changes are saved." }
                                    } else {
                                        button { r#type: "button", class: "btn btn-outline-danger", disabled: saving(),
                                            onclick: { let u = tournament_url.clone(); move |_| {
                                                if saving() || dirty() { return; }
                                                saving.set(true);
                                                let u = u.clone();
                                                spawn(async move {
                                                    match api::tournament_bracket(&u).await {
                                                        Ok(resp) => {
                                                            bracket_published.set(resp.bracket_published);
                                                            legacy_brackets.set(resp.legacy_brackets.clone());
                                                            apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                            saving.error.set(None);
                                                        }
                                                        Err(message) => saving.fail(message),
                                                    }
                                                    saving.set(false);
                                                });
                                            } }, "Check saved bracket"
                                        }
                                        span { class: "small", "Check the saved diagram before trying the action again." }
                                    }
                                }
                            }
                            p { class: "small text-muted mb-2", role: "status", aria_live: "polite", id: "bracket-save-status",
                                if saving() { "Saving…" } else if dirty() { "Unsaved changes" } else if saving.layout().saved { "All changes saved" }
                            }
                        }
                        div { class: "row",
                            div { class: "col-12",
                                div { class: "bracket-page-toolbar",
                                    if is_to {
                                        div { class: "form-check form-switch mb-0 ms-2",
                                            input {
                                                class: "form-check-input",
                                                r#type: "checkbox",
                                                role: "switch",
                                                id: "bracketEditMode",
                                                checked: "{edit_mode}",
                                                onchange: {
                                                    let u = tournament_url.clone();
                                                    move |e| {
                                                        let on = e.value() == "true";
                                                        edit_mode.set(on);
                                                        if !on {
                                                            interaction.set(Interaction::default());
                                                            active_modal.set(ActiveModal::None);
                                                            clear_field_hover();
                                                            show_refs.set(false);
                                                            show_field.set(false);
                                                            if dirty() {
                                                                persist_all(
                                                                    u.clone(), local_matches(), local_texts(),
                                                                    local_labeled(), local_images(), true,
                                                                    local_matches, local_texts, local_labeled,
                                                                    local_images, dirty, saving, canvas_size,
                                                                );
                                                            }
                                                            fit_tick.set(fit_tick() + 1);
                                                        } else {
                                                            zoom.set(1.0);
                                                            pan.set((0.0, 0.0));
                                                            focus_bracket_root();
                                                        }
                                                    }
                                                }
                                            }
                                            label { class: "form-check-label small", r#for: "bracketEditMode", "Edit" }
                                        }
                                        div { class: "form-check form-switch mb-0 ms-2",
                                            input {
                                                class: "form-check-input",
                                                r#type: "checkbox",
                                                role: "switch",
                                                id: "bracketPublished",
                                                checked: "{bracket_published}",
                                                disabled: saving() || dirty() || saving.error().is_some(),
                                                onchange: {
                                                    let u = tournament_url.clone();
                                                    move |e| {
                                                        let on = e.value() == "true";
                                                        // Optimistic update; revert on failure.
                                                        let prev = bracket_published();
                                                        bracket_published.set(on);
                                                        if !saving.begin(dirty()) { return; }
                                                        let u = u.clone();
                                                        spawn(async move {
                                                            match api::set_bracket_published(&u, on).await {
                                                                Ok(resp) => {
                                                                    bracket_published.set(resp.bracket_published);
                                                                }
                                                                Err(err) => {
                                                                    bracket_published.set(prev);
                                                                    saving.fail(err);
                                                                }
                                                            }
                                                            saving.set(false);
                                                            focus_bracket_root();
                                                        });
                                                    }
                                                }
                                            }
                                            label {
                                                class: "form-check-label small",
                                                r#for: "bracketPublished",
                                                title: "When on, non-organizers can view this bracket",
                                                "Published"
                                            }
                                        }
                                    }
                                    if edit_mode() && is_to {
                                        div { class: "dropdown",
                                            button {
                                                class: "btn btn-sm btn-primary dropdown-toggle",
                                                disabled: saving() || dirty() || saving.error().is_some(),
                                                r#type: "button",
                                                onclick: move |ev: Event<MouseData>| {
                                                    ev.stop_propagation();
                                                    if active_modal() == ActiveModal::AddMenu {
                                                        active_modal.set(ActiveModal::None);
                                                    } else {
                                                        active_modal.set(ActiveModal::AddMenu);
                                                    }
                                                    focus_bracket_root();
                                                },
                                                {underline_label("Add", 'a')}
                                            }
                                            if active_modal() == ActiveModal::AddMenu {
                                                ul { class: "dropdown-menu show", style: "display:block; position:absolute;",
                                                    li {
                                                        button { class: "dropdown-item",
                                                            onclick: move |_| {
                                                                add_match_query.set(String::new());
                                                                active_modal.set(ActiveModal::AddMatch);
                                                            },
                                                            {underline_label("Match", 'c')}
                                                        }
                                                    }
                                                    li {
                                                        button { class: "dropdown-item",
                                                            onclick: {
                                                                let u = tournament_url.clone();
                                                                move |_| {
                                                                    let u = u.clone();
                                                                    let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                                                    let (ax, ay) = view_add_position(zoom(), pan(), stagger);
                                                                    active_modal.set(ActiveModal::None);
                                                                    if !saving.begin(dirty()) { return; }
                                                                    spawn(async move {
                                                                        match api::add_bracket_text(&u, ax, ay).await { Ok(resp) => {
                                                                            let before: HashSet<String> =
                                                                                local_texts().iter().map(|t| t.id.clone()).collect();
                                                                            apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                            if let Some(t) = local_texts().iter().find(|t| !before.contains(&t.id)) {
                                                                                text_draft.set(t.text.clone());
                                                                                text_size_draft.set(t.size);
                                                                                pending_create.set(Some(("text".into(), t.id.clone())));
                                                                                active_modal.set(ActiveModal::EditText { id: t.id.clone() });
                                                                            }
                                                                        }, Err(message) => saving.fail(message), }
                                                                        saving.set(false);
                                                                    });
                                                                }
                                                            },
                                                            {underline_label("Text", 'x')}
                                                        }
                                                    }
                                                    li {
                                                        button { class: "dropdown-item",
                                                            onclick: {
                                                                let u = tournament_url.clone();
                                                                move |_| {
                                                                    let u = u.clone();
                                                                    let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                                                    let (ax, ay) = labeled_add_position(zoom(), pan(), stagger, grid_size());
                                                                    active_modal.set(ActiveModal::None);
                                                                    if !saving.begin(dirty()) { return; }
                                                                    spawn(async move {
                                                                        let before_ids: HashSet<String> =
                                                                            local_labeled().iter().map(|t| t.id.clone()).collect();
                                                                        match api::add_bracket_labeled_team(&u, ax, ay).await { Ok(resp) => {
                                                                            apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                            if let Some(t) = local_labeled().iter().find(|t| !before_ids.contains(&t.id)) {
                                                                                label_draft.set(if t.label.is_empty() { "Label".into() } else { t.label.clone() });
                                                                                team_draft.set(t.team.clone());
                                                                                pending_create.set(Some(("labeled".into(), t.id.clone())));
                                                                                active_modal.set(ActiveModal::EditLabeledTeam { id: t.id.clone() });
                                                                            }
                                                                        }, Err(message) => saving.fail(message), }
                                                                        saving.set(false);
                                                                    });
                                                                }
                                                            },
                                                            {underline_label("Team", 't')}
                                                        }
                                                    }
                                                    li {
                                                        button { class: "dropdown-item",
                                                            onclick: move |_| active_modal.set(ActiveModal::AddImage),
                                                            {underline_label("Image", 'e')}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        button {
                                            class: "btn btn-sm btn-outline-secondary",
                                            disabled: saving.structural_busy() || interaction().selected.is_empty(),
                                            title: "Swap team1/team2 vertical order (S)",
                                            onclick: {
                                                let u = tournament_url.clone();
                                                move |_| {
                                                if swap_selected_inputs(local_matches, interaction, dirty) {
                                                    persist_all(
                                                                    u.clone(),
                                                                    local_matches(),
                                                                    local_texts(),
                                                                    local_labeled(),
                                                                    local_images(),
                                                                    true,
                                                                    local_matches,
                                                                    local_texts,
                                                                    local_labeled,
                                                                    local_images,
                                                                    dirty,
                                                                    saving,
                                                                    canvas_size,
                                                                );
                                                }
                                                focus_bracket_root();
                                            }},
                                            {underline_label("Swap Inputs", 's')}
                                        }
                                        button {
                                            class: "btn btn-sm btn-outline-danger",
                                            disabled: saving.structural_busy() || interaction().selected.is_empty(),
                                            title: "Remove selected (D)",
                                            onclick: {
                                                let u = tournament_url.clone();
                                                move |_| {
                                                if delete_selected(local_matches, local_texts, local_labeled, local_images, interaction, dirty) {
                                                    interaction.write().selected.clear();
                                                    persist_all(
                                                                    u.clone(),
                                                                    local_matches(),
                                                                    local_texts(),
                                                                    local_labeled(),
                                                                    local_images(),
                                                                    true,
                                                                    local_matches,
                                                                    local_texts,
                                                                    local_labeled,
                                                                    local_images,
                                                                    dirty,
                                                                    saving,
                                                                    canvas_size,
                                                                );
                                                }
                                                focus_bracket_root();
                                            }},
                                            {underline_label("Delete", 'd')}
                                        }
                                        label {
                                            class: "small text-muted mb-0 ms-1",
                                            r#for: "bracketSnapSize",
                                            "Grid:"
                                        }
                                        select {
                                            id: "bracketSnapSize",
                                            class: "form-select form-select-sm bracket-grid-select",
                                            title: "Round moved items to this pitch. Hold Alt/Meta to suppress snapping.",
                                            value: "{grid_size()}",
                                            onchange: move |e| {
                                                if let Ok(v) = e.value().parse::<f64>() {
                                                    grid_size.set(v);
                                                }
                                                focus_bracket_root();
                                            },
                                            option { value: "0", selected: grid_size() == 0.0, "Off" }
                                            option { value: "5", selected: grid_size() == 5.0, "5px" }
                                            option { value: "10", selected: grid_size() == 10.0, "10px" }
                                            option { value: "20", selected: grid_size() == 20.0, "20px" }
                                            option { value: "40", selected: grid_size() == 40.0, "40px" }
                                            option { value: "80", selected: grid_size() == 80.0, "80px" }
                                        }
                                        div { class: "dropdown",
                                            button {
                                                class: "btn btn-sm btn-outline-secondary dropdown-toggle",
                                                r#type: "button",
                                                title: "Debug airwire overlays",
                                                onclick: move |ev: Event<MouseData>| {
                                                    ev.stop_propagation();
                                                    if active_modal() == ActiveModal::RatsnestMenu {
                                                        active_modal.set(ActiveModal::None);
                                                    } else {
                                                        active_modal.set(ActiveModal::RatsnestMenu);
                                                    }
                                                    focus_bracket_root();
                                                },
                                                "Ratsnest"
                                            }
                                            if active_modal() == ActiveModal::RatsnestMenu {
                                                ul {
                                                    class: "dropdown-menu show bracket-ratsnest-menu",
                                                    style: "display:block; position:absolute;",
                                                    onclick: move |ev: Event<MouseData>| ev.stop_propagation(),
                                                    li { class: "px-3 py-1",
                                                        div { class: "form-check form-switch mb-0",
                                                            input {
                                                                class: "form-check-input",
                                                                r#type: "checkbox",
                                                                role: "switch",
                                                                id: "bracketShowRefs",
                                                                checked: "{show_refs}",
                                                                onchange: move |e| {
                                                                    show_refs.set(e.value() == "true");
                                                                    focus_bracket_root();
                                                                },
                                                            }
                                                            label {
                                                                class: "form-check-label small",
                                                                r#for: "bracketShowRefs",
                                                                title: "Yellow airwires from winner/loser ports to match inputs",
                                                                "Show refs"
                                                            }
                                                        }
                                                    }
                                                    li { class: "px-3 py-1",
                                                        div { class: "form-check form-switch mb-0",
                                                            input {
                                                                class: "form-check-input",
                                                                r#type: "checkbox",
                                                                role: "switch",
                                                                id: "bracketShowField",
                                                                checked: "{show_field}",
                                                                onchange: move |e| {
                                                                    let on = e.value() == "true";
                                                                    show_field.set(on);
                                                                    if !on {
                                                                        clear_field_hover();
                                                                    }
                                                                    focus_bracket_root();
                                                                },
                                                            }
                                                            label {
                                                                class: "form-check-label small",
                                                                r#for: "bracketShowField",
                                                                title: "Color-coded airwires chaining matches per field in play order",
                                                                "Show field"
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        details { class: "bracket-view-menu",
                                            onclick: move |event: Event<MouseData>| event.stop_propagation(),
                                            onkeydown: move |event: Event<KeyboardData>| event.stop_propagation(),
                                            summary { class: "btn btn-outline-secondary btn-sm", "View controls" }
                                            div { class: "bracket-view-controls", role: "group", aria_label: "Bracket canvas view",
                                                // Keep controls keyboard-focusable; the canvas root otherwise reclaims focus on clicks.
                                                onclick: move |event: Event<MouseData>| event.stop_propagation(),
                                                onkeydown: move |event: Event<KeyboardData>| event.stop_propagation(),
                                                div { class: "bracket-zoom-controls", role: "group", aria_label: "Zoom controls",
                                                    button {
                                                        r#type: "button", class: "btn btn-outline-secondary btn-sm",
                                                        aria_label: "Zoom out", title: "Zoom out",
                                                        disabled: zoom() <= MIN_ZOOM,
                                                        onclick: move |_| { zoom.set((zoom() / 1.2).clamp(MIN_ZOOM, MAX_ZOOM)); },
                                                        "−"
                                                    }
                                                    output { class: "bracket-zoom-value", aria_label: "Zoom level", "{(zoom() * 100.0).round()}%" }
                                                    button {
                                                        r#type: "button", class: "btn btn-outline-secondary btn-sm",
                                                        aria_label: "Zoom in", title: "Zoom in",
                                                        disabled: zoom() >= MAX_ZOOM,
                                                        onclick: move |_| { zoom.set((zoom() * 1.2).clamp(MIN_ZOOM, MAX_ZOOM)); },
                                                        "+"
                                                    }
                                                }
                                                div { class: "bracket-pan-controls", role: "group", aria_label: "Move diagram",
                                                    button { r#type: "button", class: "btn btn-outline-secondary btn-sm", aria_label: "Move diagram left", title: "Move diagram left", onclick: move |_| { let (x, y) = pan(); pan.set((x - 120.0, y)); }, "←" }
                                                    button { r#type: "button", class: "btn btn-outline-secondary btn-sm", aria_label: "Move diagram up", title: "Move diagram up", onclick: move |_| { let (x, y) = pan(); pan.set((x, y - 120.0)); }, "↑" }
                                                    button { r#type: "button", class: "btn btn-outline-secondary btn-sm", aria_label: "Move diagram down", title: "Move diagram down", onclick: move |_| { let (x, y) = pan(); pan.set((x, y + 120.0)); }, "↓" }
                                                    button { r#type: "button", class: "btn btn-outline-secondary btn-sm", aria_label: "Move diagram right", title: "Move diagram right", onclick: move |_| { let (x, y) = pan(); pan.set((x + 120.0, y)); }, "→" }
                                                }
                                                button { r#type: "button", class: "btn btn-outline-secondary btn-sm", onclick: move |_| { zoom.set(1.0); pan.set((0.0, 0.0)); }, "Reset view" }
                                            }
                                        }
                                        details { class: "bracket-shortcut-help text-muted small",
                                            summary { "Keyboard and mouse shortcuts" }
                                            p { class: "mb-0 mt-2", "Drag an item to move it · Drag empty space to pan on touchscreens · Scroll to zoom · Right-drag to pan · Shift+click for multiple selection · Alt/Meta drag for fine adjustment" }
                                        }
                                    }
                                }
                            }
                        }


                        if has_legacy && !canvas_empty {
                            div { class: "row",
                                div { class: "col-12",
                                    div { class: "alert alert-warning py-2 px-3 mb-3", role: "alert",
                                        "This tournament has a legacy-style bracket diagram still configured. "
                                        Link {
                                            to: Route::LegacyBracket { url: tournament_url.clone() },
                                            class: "alert-link",
                                            "Click here to see it."
                                        }
                                        if is_to {
                                            " To delete legacy brackets and get rid of this message, "
                                            a {
                                                href: "#",
                                                class: "alert-link",
                                                onclick: move |ev: Event<MouseData>| {
                                                    ev.prevent_default();
                                                    ev.stop_propagation();
                                                    active_modal.set(ActiveModal::LegacyManage);
                                                },
                                                "click here."
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if show_legacy_fallback {
                            div { class: "row",
                                div { class: "col-12",
                                    if is_to {
                                        div { class: "alert alert-info py-2 px-3 mb-3", role: "alert",
                                            "Showing the legacy image bracket because the new canvas is empty. Turn on Edit to build a canvas bracket. "
                                            "To delete legacy brackets, "
                                            a {
                                                href: "#",
                                                class: "alert-link",
                                                onclick: move |ev: Event<MouseData>| {
                                                    ev.prevent_default();
                                                    ev.stop_propagation();
                                                    active_modal.set(ActiveModal::LegacyManage);
                                                },
                                                "click here."
                                            }
                                        }
                                    }
                                    LegacyBracketDiagrams {
                                        url: tournament_url.clone(),
                                        brackets: legacy_snap.clone(),
                                    }
                                }
                            }
                        }

                        if !show_legacy_fallback && !edit_mode() {
                            p { class: "bracket-mobile-hint text-muted small", "Pinch to zoom in. Tap a match name for its details." }
                        }

                        if !show_legacy_fallback {
                            div {
                            id: "bracket-canvas-wrap",
                            class: {
                                let mut c = if edit_mode() {
                                    String::from("bracket-canvas-wrap edit-mode")
                                } else {
                                    String::from("bracket-canvas-wrap view-mode")
                                };
                                // Infinite screen-space grid (not on the transformed canvas).
                                // Always drawn in edit mode; snap pitch is independent (0 = free).
                                if edit_mode() {
                                    c.push_str(" show-grid");
                                }
                                if active_field_hover.is_some() {
                                    c.push_str(" field-hovering");
                                }
                                if edit_mode() && hand_pan() {
                                    c.push_str(" hand-pan");
                                }
                                c
                            },
                            style: {
                                let mut s = if edit_mode() {
                                    // Large fraction of the viewport so a short view-mode
                                    // diagram doesn't leave a tiny edit workspace. Inline
                                    // so it always overrides the previous view-mode height.
                                    "height: min(85vh, calc(100vh - 160px)); min-height: 70vh; max-height: none;".to_string()
                                } else if let Some(h) = view_wrap_height() {
                                    format!("height: {h}px; max-height: none;")
                                } else {
                                    "height: auto; max-height: none;".to_string()
                                };
                                // Grid lines live on the wrap in *screen* space so they fill
                                // the viewport forever. Size/offset track pan+zoom so lines
                                // stay locked to world coordinates (snap pitch when on,
                                // else a reference DEFAULT_GRID_SIZE).
                                if edit_mode() {
                                    let g = if grid_size() > 0.0 {
                                        grid_size()
                                    } else {
                                        DEFAULT_GRID_SIZE
                                    };
                                    let z = zoom().max(0.001);
                                    let screen_g = grid_screen_pitch(g, z);
                                    let (px, py) = pan();
                                    // background-position shifts with pan; modulo keeps values small.
                                    let ox = px.rem_euclid(screen_g);
                                    let oy = py.rem_euclid(screen_g);
                                    let faint = if grid_size() > 0.0 { "1" } else { "0.55" };
                                    s.push_str(&format!(
                                        " --grid-size: {screen_g}px; --grid-ox: {ox}px; --grid-oy: {oy}px; --grid-alpha: {faint};"
                                    ));
                                }
                                s
                            },
                            onwheel: move |ev: Event<WheelData>| {
                                if !edit_mode() {
                                    // View mode: normal page scroll, no canvas zoom.
                                    return;
                                }
                                // Zoom with the scroll wheel (no modifier required).
                                ev.prevent_default();
                                clear_field_hover();
                                let dy = ev.delta().strip_units().y;
                                let factor = if dy > 0.0 { 0.9 } else { 1.1 };
                                let old_z = zoom();
                                let new_z = (old_z * factor).clamp(MIN_ZOOM, MAX_ZOOM);
                                // Zoom toward cursor
                                let (sx, sy) = {
                                    #[cfg(target_arch = "wasm32")]
                                    {
                                        // approximate via element
                                        let c = ev.element_coordinates();
                                        (c.x, c.y)
                                    }
                                    #[cfg(not(target_arch = "wasm32"))]
                                    { (0.0, 0.0) }
                                };
                                let (px0, py0) = pan();
                                let wx = (sx - px0) / old_z.max(0.001);
                                let wy = (sy - py0) / old_z.max(0.001);
                                pan.set((sx - wx * new_z, sy - wy * new_z));
                                zoom.set(new_z);
                            },
                            onpointermove: move |ev: Event<PointerData>| {
                                let active_pointer = pointer_gesture.read().as_ref().map(|gesture| gesture.pointer_id);
                                if active_pointer.is_some_and(|id| id != ev.pointer_id()) { return; }
                                if active_pointer.is_none() && ev.pointer_type() != "mouse" { return; }
                                if edit_mode() && hand_pan() && active_pointer.is_none() {
                                    let (sx, sy) = screen_pointer(&ev);
                                    if let Some((lx, ly)) = hand_last() {
                                        let (px, py) = pan();
                                        pan.set((px + (sx - lx), py + (sy - ly)));
                                    }
                                    hand_last.set(Some((sx, sy)));
                                    return;
                                }
                                let mut ix = interaction.write();
                                let Some(drag) = ix.drag.clone() else {
                                    // No drag: continuously resolve field-airwire hover from
                                    // the element under the cursor (fixes sticky SVG leave).
                                    drop(ix);
                                    track_field_hover_from_pointer(&ev);
                                    return;
                                };
                                // Dragging: force-clear so highlight never sticks mid-gesture.
                                clear_field_hover();
                                match drag {
                                    DragKind::Pan { pointer_start, pan_start } => {
                                        let (sx, sy) = screen_pointer(&ev);
                                        pan.set((
                                            pan_start.0 + (sx - pointer_start.0),
                                            pan_start.1 + (sy - pointer_start.1),
                                        ));
                                        ix.drag = Some(DragKind::Pan { pointer_start, pan_start });
                                    }
                                    DragKind::Move { origins, pointer_start } => {
                                        let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                        let raw_dx = cx - pointer_start.0;
                                        let raw_dy = cy - pointer_start.1;
                                        let mods = ev.modifiers();
                                        let fine = mods.alt() || mods.meta();
                                        let (dx, dy) = snap_move_delta(
                                            &origins,
                                            raw_dx,
                                            raw_dy,
                                            grid_size(),
                                            fine,
                                        );
                                        let mut ms = local_matches();
                                        let mut ts = local_texts();
                                        let mut ls = local_labeled();
                                        let mut im = local_images();
                                        for m in ms.iter_mut() {
                                            let key = sel_match(&m.uuid);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                if let Some(p) = m.placement.as_mut() {
                                                    p.x_pos = Some(*ox + dx);
                                                    p.y_pos = Some(*oy + dy);
                                                }
                                            }
                                        }
                                        for t in ts.iter_mut() {
                                            let key = sel_text(&t.id);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                t.x_pos = *ox + dx;
                                                t.y_pos = *oy + dy;
                                            }
                                        }
                                        for t in ls.iter_mut() {
                                            let key = sel_labeled(&t.id);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                let (x, y) = labeled_from_snap_origin(*ox + dx, *oy + dy);
                                                t.x_pos = x;
                                                t.y_pos = y;
                                            }
                                        }
                                        for i in im.iter_mut() {
                                            let key = sel_image(&i.id);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                i.x_pos = *ox + dx;
                                                i.y_pos = *oy + dy;
                                            }
                                        }
                                        local_matches.set(ms);
                                        local_texts.set(ts);
                                        local_labeled.set(ls);
                                        local_images.set(im);
                                        dirty.set(true);
                                        ix.drag = Some(DragKind::Move { origins, pointer_start });
                                    }
                                    DragKind::Resize { id, mode, start_w, start_h, aspect, pointer_start } => {
                                        let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                        let dw = cx - pointer_start.0;
                                        let dh = cy - pointer_start.1;
                                        if id.starts_with("m:") {
                                            let uuid = id[2..].to_string();
                                            let mut ms = local_matches();
                                            if let Some(m) = ms.iter_mut().find(|m| m.uuid == uuid) {
                                                if let Some(p) = m.placement.as_mut() {
                                                    p.width = (start_w + dw).clamp(160.0, 800.0);
                                                    p.height = (start_h + dh).clamp(70.0, 400.0);
                                                }
                                            }
                                            local_matches.set(ms);
                                        } else if id.starts_with("i:") {
                                            let iid = id[2..].to_string();
                                            let mut im = local_images();
                                            if let Some(img) = im.iter_mut().find(|i| i.id == iid) {
                                                match mode.as_str() {
                                                    "e" => {
                                                        img.width = (start_w + dw).clamp(20.0, 4000.0);
                                                    }
                                                    "s" => {
                                                        img.height = (start_h + dh).clamp(20.0, 4000.0);
                                                    }
                                                    _ => {
                                                        // corner — lock aspect
                                                        let nw = (start_w + dw).max(20.0);
                                                        let nh = if aspect > 0.0 { nw / aspect } else { start_h + dh };
                                                        img.width = nw.clamp(20.0, 4000.0);
                                                        img.height = nh.clamp(20.0, 4000.0);
                                                    }
                                                }
                                            }
                                            local_images.set(im);
                                        }
                                        dirty.set(true);
                                        ix.drag = Some(DragKind::Resize { id, mode, start_w, start_h, aspect, pointer_start });
                                    }
                                    DragKind::Marquee { start, .. } => {
                                        let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                        ix.drag = Some(DragKind::Marquee { start, current: (cx, cy) });
                                    }
                                }
                            },
                            onpointerup: {
                                let u_up = tournament_url.clone();
                                move |ev: Event<PointerData>| {
                                if pointer_gesture.read().as_ref().map(|gesture| gesture.pointer_id) != Some(ev.pointer_id()) { return; }
                                pointer_gesture.set(None);
                                saving.gesture.set(false);
                                release_canvas_pointer(&ev);
                                let mut ix = interaction.write();
                                let Some(drag) = ix.drag.take() else { return };
                                match drag {
                                    DragKind::Move { .. } | DragKind::Resize { .. } => {
                                        if dirty() {
                                            persist_all(
                                                                    u_up.clone(),
                                                                    local_matches(),
                                                                    local_texts(),
                                                                    local_labeled(),
                                                                    local_images(),
                                                                    true,
                                                                    local_matches,
                                                                    local_texts,
                                                                    local_labeled,
                                                                    local_images,
                                                                    dirty,
                                                                    saving,
                                                                    canvas_size,
                                                                );
                                        }
                                    }
                                    DragKind::Marquee { start, current } => {
                                        let x0 = start.0.min(current.0);
                                        let y0 = start.1.min(current.1);
                                        let x1 = start.0.max(current.0);
                                        let y1 = start.1.max(current.1);
                                        let mut sel = HashSet::new();
                                        for m in local_matches().iter() {
                                            if let Some(p) = &m.placement {
                                                if let (Some(mx), Some(my)) = (p.x_pos, p.y_pos) {
                                                    if mx < x1 && mx + p.width > x0 && my < y1 && my + p.height > y0 {
                                                        sel.insert(sel_match(&m.uuid));
                                                    }
                                                }
                                            }
                                        }
                                        for t in local_texts().iter() {
                                            let w = (t.text.chars().count() as f64) * t.size * 0.55 + 12.0;
                                            let h = t.size * 1.5;
                                            if t.x_pos < x1 && t.x_pos + w > x0 && t.y_pos < y1 && t.y_pos + h > y0 {
                                                sel.insert(sel_text(&t.id));
                                            }
                                        }
                                        for t in local_labeled().iter() {
                                            let tw = {
                                                let caption_len = if t.resolved {
                                                    t.label.chars().count() + t.display_text.chars().count() + 4
                                                } else {
                                                    t.label.chars().count().max(1)
                                                };
                                                ((caption_len as f64) * 8.5 + 36.0).max(48.0)
                                            };
                                            if t.x_pos < x1 && t.x_pos + tw > x0 && t.y_pos < y1 && t.y_pos + LABELED_TEAM_H > y0 {
                                                sel.insert(sel_labeled(&t.id));
                                            }
                                        }
                                        for i in local_images().iter() {
                                            if i.x_pos < x1 && i.x_pos + i.width > x0 && i.y_pos < y1 && i.y_pos + i.height > y0 {
                                                sel.insert(sel_image(&i.id));
                                            }
                                        }
                                        ix.selected = sel;
                                    }
                                    DragKind::Pan { .. } => {}
                                }
                            }},
                            onpointercancel: move |ev: Event<PointerData>| {
                                if pointer_gesture.read().as_ref().map(|gesture| gesture.pointer_id) == Some(ev.pointer_id()) {
                                    cancel_pointer_gesture.call(());
                                    release_canvas_pointer(&ev);
                                }
                            },
                            onlostpointercapture: move |ev: Event<PointerData>| {
                                if pointer_gesture.read().as_ref().map(|gesture| gesture.pointer_id) == Some(ev.pointer_id()) {
                                    cancel_pointer_gesture.call(());
                                }
                            },
                            onpointerdown: move |ev: Event<PointerData>| {
                                if !edit_mode() || !ev.is_primary() { return; }
                                let btn = ev.trigger_button();
                                // Touch the background to pan; mouse users retain right/middle
                                // drag and the H hand tool. Touching an item still moves it.
                                if matches!(btn, Some(MouseButton::Secondary) | Some(MouseButton::Auxiliary))
                                    || ev.pointer_type() == "touch" || hand_pan()
                                {
                                    if !begin_pointer_gesture(&ev) { return; }
                                    ev.prevent_default();
                                    clear_field_hover();
                                    let (sx, sy) = screen_pointer(&ev);
                                    interaction.write().drag = Some(DragKind::Pan {
                                        pointer_start: (sx, sy), pan_start: pan(),
                                    });
                                    return;
                                }
                                if btn != Some(MouseButton::Primary) || !begin_pointer_gesture(&ev) { return; }
                                let mods = ev.modifiers();
                                let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                let mut ix = interaction.write();
                                if !mods.shift() { ix.selected.clear(); }
                                ix.drag = Some(DragKind::Marquee { start: (cx, cy), current: (cx, cy) });
                            },
                            // Suppress the browser context menu so right-drag can pan.
                            oncontextmenu: move |ev: Event<MouseData>| {
                                if edit_mode() { ev.prevent_default(); }
                            },
                            // Safety net: leaving the viewport always drops field highlight
                            // (SVG stroke mouseleave is unreliable at edges / while panning).
                            onpointerleave: move |_| {
                                clear_field_hover();
                                hand_last.set(None);
                            },

                            div {
                                class: "bracket-canvas",
                                id: "bracket-canvas",
                                style: "width: {cw}px; height: {ch}px; transform: {transform}; transform-origin: 0 0;",

                                if placed.is_empty() && texts_snap.is_empty() && labeled_snap.is_empty() && images_snap.is_empty() {
                                    div { class: "bracket-empty-hint",
                                        if edit_mode() {
                                            "Empty bracket. Use Add to place matches, text, teams, or images."
                                        } else {
                                            "Bracket has not been configured yet."
                                        }
                                    }
                                }

                                // Wires
                                svg {
                                    class: "bracket-wires-layer",
                                    width: "{cw}",
                                    height: "{ch}",
                                    style: "pointer-events: none;",
                                    for w in wires.iter() {
                                        {
                                            let w = w.clone();
                                            let is_lt = w.is_labeled;
                                            let tkey = w.target_key.clone();
                                            rsx! {
                                                g {
                                                    key: "{w.key}",
                                                    class: "bracket-wire-group",
                                                    style: "pointer-events: stroke;",
                                                    onclick: {
                                                        let u = tournament_url.clone();
                                                        let tkey = tkey.clone();
                                                        move |ev: Event<MouseData>| {
                                                            if !edit_mode() || saving.structural_busy() { return; }
                                                            ev.stop_propagation();
                                                            if is_lt {
                                                                let u = u.clone();
                                                                let id = tkey.clone();
                                                                if !saving.begin(dirty()) { return; }
                                                                spawn(async move {
                                                                    match api::convert_labeled_team_port(&u, &id, "LABEL").await { Ok(resp) => {
                                                                        apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                    }, Err(message) => saving.fail(message), }
                                                                    saving.set(false);
                                                                });
                                                            } else {
                                                                // match:uuid:side
                                                                let parts: Vec<_> = tkey.splitn(2, ':').collect();
                                                                if parts.len() == 2 {
                                                                    convert_match_port(
                                                                        u.clone(), parts[0].to_string(), parts[1], "LABEL",
                                                                        local_matches, local_texts, local_labeled, local_images,
                                                                        dirty, saving, canvas_size,
                                                                    );
                                                                }
                                                            }
                                                        }
                                                    },
                                                    path { class: "bracket-wire-hit", d: "{w.path}" }
                                                    path { class: "bracket-wire", d: "{w.path}" }
                                                }
                                            }
                                        }
                                    }
                                }

                                // Images (behind matches)
                                for img in images_snap.iter() {
                                    {
                                        let img = img.clone();
                                        let key = sel_image(&img.id);
                                        let selected = ix.selected.contains(&key);
                                        let id_for_down = key.clone();
                                        let id_r1 = key.clone();
                                        let id_r2 = key.clone();
                                        let id_r3 = key.clone();
                                        let iw = img.width;
                                        let ih = img.height;
                                        rsx! {
                                            div {
                                                key: "img-{img.id}",
                                                class: if selected { "bracket-image selected" } else { "bracket-image" },
                                                style: "left: {img.x_pos}px; top: {img.y_pos}px; width: {img.width}px; height: {img.height}px;",
                                                onpointerdown: move |ev: Event<PointerData>| {
                                                    if !edit_mode() || ev.trigger_button() != Some(MouseButton::Primary) || hand_pan() { return; }
                                                    if !begin_pointer_gesture(&ev) { return; }
                                                    ev.stop_propagation();
                                                    let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                    let mut ix = interaction.write();
                                                    let id = id_for_down.clone();
                                                    if ev.modifiers().shift() {
                                                        if ix.selected.contains(&id) { ix.selected.remove(&id); } else { ix.selected.insert(id.clone()); }
                                                    } else if !ix.selected.contains(&id) {
                                                        ix.selected.clear();
                                                        ix.selected.insert(id.clone());
                                                    }
                                                    let mut origins = HashMap::new();
                                                    collect_origins(&ix.selected, &local_matches(), &local_texts(), &local_labeled(), &local_images(), &mut origins);
                                                    ix.drag = Some(DragKind::Move { origins, pointer_start: (cx, cy) });
                                                },
                                                img {
                                                    src: "{backend}/static/{img.image}",
                                                    alt: "",
                                                    style: "width:100%; height:100%; object-fit: fill; pointer-events: none; user-select: none;",
                                                    draggable: "false",
                                                }
                                                if edit_mode() {
                                                    div {
                                                        class: "bracket-resize-handle corner",
                                                        onpointerdown: move |ev: Event<PointerData>| {
                                                            if ev.trigger_button() != Some(MouseButton::Primary) || !begin_pointer_gesture(&ev) { return; }
                                                            ev.stop_propagation();
                                                            let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                            let aspect = if ih > 0.0 { iw / ih } else { 1.0 };
                                                            interaction.write().drag = Some(DragKind::Resize {
                                                                id: id_r1.clone(),
                                                                mode: "corner".into(),
                                                                start_w: iw,
                                                                start_h: ih,
                                                                aspect,
                                                                pointer_start: (cx, cy),
                                                            });
                                                        },
                                                    }
                                                    div {
                                                        class: "bracket-resize-handle edge-e",
                                                        onpointerdown: move |ev: Event<PointerData>| {
                                                            if ev.trigger_button() != Some(MouseButton::Primary) || !begin_pointer_gesture(&ev) { return; }
                                                            ev.stop_propagation();
                                                            let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                            interaction.write().drag = Some(DragKind::Resize {
                                                                id: id_r2.clone(),
                                                                mode: "e".into(),
                                                                start_w: iw,
                                                                start_h: ih,
                                                                aspect: 1.0,
                                                                pointer_start: (cx, cy),
                                                            });
                                                        },
                                                    }
                                                    div {
                                                        class: "bracket-resize-handle edge-s",
                                                        onpointerdown: move |ev: Event<PointerData>| {
                                                            if ev.trigger_button() != Some(MouseButton::Primary) || !begin_pointer_gesture(&ev) { return; }
                                                            ev.stop_propagation();
                                                            let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                            interaction.write().drag = Some(DragKind::Resize {
                                                                id: id_r3.clone(),
                                                                mode: "s".into(),
                                                                start_w: iw,
                                                                start_h: ih,
                                                                aspect: 1.0,
                                                                pointer_start: (cx, cy),
                                                            });
                                                        },
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }

                                // Texts
                                for t in texts_snap.iter() {
                                    {
                                        let t = t.clone();
                                        let key = sel_text(&t.id);
                                        let selected = ix.selected.contains(&key);
                                        let id_for_down = key.clone();
                                        let tid = t.id.clone();
                                        rsx! {
                                            div {
                                                key: "txt-{t.id}",
                                                class: if selected { "bracket-text selected" } else { "bracket-text" },
                                                style: "left: {t.x_pos}px; top: {t.y_pos}px; font-size: {t.size}px;",
                                                onpointerdown: move |ev: Event<PointerData>| {
                                                    if !edit_mode() || ev.trigger_button() != Some(MouseButton::Primary) || hand_pan() { return; }
                                                    if !begin_pointer_gesture(&ev) { return; }
                                                    ev.stop_propagation();
                                                    let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                    let mut ix = interaction.write();
                                                    let id = id_for_down.clone();
                                                    if ev.modifiers().shift() {
                                                        if ix.selected.contains(&id) { ix.selected.remove(&id); } else { ix.selected.insert(id.clone()); }
                                                    } else if !ix.selected.contains(&id) {
                                                        ix.selected.clear();
                                                        ix.selected.insert(id.clone());
                                                    }
                                                    let mut origins = HashMap::new();
                                                    collect_origins(&ix.selected, &local_matches(), &local_texts(), &local_labeled(), &local_images(), &mut origins);
                                                    ix.drag = Some(DragKind::Move { origins, pointer_start: (cx, cy) });
                                                },
                                                ondblclick: move |ev: Event<MouseData>| {
                                                    if !edit_mode() || saving.structural_busy() { return; }
                                                    ev.stop_propagation();
                                                    text_draft.set(t.text.clone());
                                                    text_size_draft.set(t.size);
                                                    active_modal.set(ActiveModal::EditText { id: tid.clone() });
                                                },
                                                "{t.text}"
                                            }
                                        }
                                    }
                                }

                                // Labeled teams
                                for lt in labeled_snap.iter() {
                                    {
                                        let lt = lt.clone();
                                        let key = sel_labeled(&lt.id);
                                        let selected = ix.selected.contains(&key);
                                        let id_for_down = key.clone();
                                        let lid = lt.id.clone();
                                        let show_net_label = !is_net(&lt.kind) && !lt.team.is_empty();
                                        let label_kind = resolve_label(&lt.team, &team_options, &tags, &matches_snap);
                                        let caption = if lt.label.is_empty() {
                                            "Label".to_string()
                                        } else {
                                            lt.label.clone()
                                        };
                                        let team_name = short_or_truncate(
                                            if lt.display_text.is_empty() {
                                                lt.team.as_str()
                                            } else {
                                                lt.display_text.as_str()
                                            },
                                            lt.shortname.as_deref(),
                                        );
                                        let resolved = lt.resolved || lt.team_id.is_some();
                                        rsx! {
                                            if show_net_label {
                                                {
                                                    let y_port = lt.y_pos + LABELED_TEAM_H * 0.5;
                                                    let can_wire = parse_match_ref(&lt.team).is_some();
                                                    rsx! {
                                                        NetLabelView {
                                                            key: "ltlbl-{lt.id}",
                                                            x: lt.x_pos,
                                                            y: y_port,
                                                            kind: label_kind,
                                                            backend: backend.clone(),
                                                            editable: edit_mode() && can_wire,
                                                            on_click: {
                                                                let u = tournament_url.clone();
                                                                let id = lid.clone();
                                                                move |_| {
                                                                    if !edit_mode() || !can_wire { return; }
                                                                    let u = u.clone();
                                                                    let id = id.clone();
                                                                    if !saving.begin(dirty()) { return; }
                                                                    spawn(async move {
                                                                        match api::convert_labeled_team_port(&u, &id, "NET").await { Ok(resp) => {
                                                                            apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                        }, Err(message) => saving.fail(message), }
                                                                        saving.set(false);
                                                                    });
                                                                }
                                                            },
                                                        }
                                                    }
                                                }
                                            }
                                            div {
                                                class: "bracket-port-stub",
                                                style: "left: {lt.x_pos}px; top: {lt.y_pos + LABELED_TEAM_H * 0.5}px;",
                                            }
                                            div {
                                                key: "lt-{lt.id}",
                                                class: if selected { "bracket-labeled-team selected" } else { "bracket-labeled-team" },
                                                style: "left: {lt.x_pos}px; top: {lt.y_pos}px; height: {LABELED_TEAM_H}px;",
                                                onpointerdown: move |ev: Event<PointerData>| {
                                                    if !edit_mode() || ev.trigger_button() != Some(MouseButton::Primary) || hand_pan() { return; }
                                                    if !begin_pointer_gesture(&ev) { return; }
                                                    ev.stop_propagation();
                                                    let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                    let mut ix = interaction.write();
                                                    let id = id_for_down.clone();
                                                    if ev.modifiers().shift() {
                                                        if ix.selected.contains(&id) { ix.selected.remove(&id); } else { ix.selected.insert(id.clone()); }
                                                    } else if !ix.selected.contains(&id) {
                                                        ix.selected.clear();
                                                        ix.selected.insert(id.clone());
                                                    }
                                                    let mut origins = HashMap::new();
                                                    collect_origins(&ix.selected, &local_matches(), &local_texts(), &local_labeled(), &local_images(), &mut origins);
                                                    ix.drag = Some(DragKind::Move { origins, pointer_start: (cx, cy) });
                                                },
                                                ondblclick: move |ev: Event<MouseData>| {
                                                    if !edit_mode() || saving.structural_busy() { return; }
                                                    ev.stop_propagation();
                                                    label_draft.set(if lt.label.is_empty() { "Label".into() } else { lt.label.clone() });
                                                    team_draft.set(lt.team.clone());
                                                    active_modal.set(ActiveModal::EditLabeledTeam { id: lid.clone() });
                                                },
                                                if resolved {
                                                    span { class: "caption",
                                                        span { class: "caption-prefix", "{caption}: " }
                                                        if let Some(photo) = &lt.profile_photo {
                                                            img { class: "avatar", src: "{backend}/static/{photo}", alt: "" }
                                                        }
                                                        span { class: "label", "{team_name}" }
                                                    }
                                                } else {
                                                    span { class: "label", "{caption}" }
                                                }
                                            }
                                        }
                                    }
                                }

                                // Match blocks
                                for m in placed.iter() {
                                    {
                                        let m = m.clone();
                                        let p = placement_or_default(&m);
                                        let x = p.x_pos.unwrap_or(0.0);
                                        let y = p.y_pos.unwrap_or(0.0);
                                        let w = p.width;
                                        let h = p.height;
                                        let key = sel_match(&m.uuid);
                                        let selected = ix.selected.contains(&key);
                                        let flipped = p.inputs_flipped;
                                        let t1_label = short_or_truncate(&m.team1_name, m.team1_shortname.as_deref());
                                        let t2_label = short_or_truncate(&m.team2_name, m.team2_shortname.as_deref());
                                        let w_used = used_outputs.contains(&(m.uuid.clone(), Qual::Winner));
                                        let l_used = used_outputs.contains(&(m.uuid.clone(), Qual::Loser));
                                        let w_dests = output_consumers(&m, Qual::Winner, &matches_snap, &labeled_snap).unwired;
                                        let l_dests = output_consumers(&m, Qual::Loser, &matches_snap, &labeled_snap).unwired;
                                        let w_shown = w_used || !w_dests.is_empty();
                                        let l_shown = l_used || !l_dests.is_empty();
                                        let label1 = if !is_net(&p.team1) { m.team1_initial.clone() } else { None };
                                        let label2 = if !is_net(&p.team2) { m.team2_initial.clone() } else { None };
                                        let uuid_down = m.uuid.clone();
                                        let uuid_resize = m.uuid.clone();
                                        let nav_url = tournament_url.clone();
                                        let nav = navigator;
                                        let p_resize = p.clone();
                                        let y1_port = port_y(y, h, Side::Team1, flipped);
                                        let y2_port = port_y(y, h, Side::Team2, flipped);

                                        // visual top/bottom content depending on flip
                                        let (top_name, top_photo, top_label, bot_name, bot_photo, bot_label) = if flipped {
                                            (m.team2_name.clone(), m.team2_photo.clone(), t2_label.clone(),
                                             m.team1_name.clone(), m.team1_photo.clone(), t1_label.clone())
                                        } else {
                                            (m.team1_name.clone(), m.team1_photo.clone(), t1_label.clone(),
                                             m.team2_name.clone(), m.team2_photo.clone(), t2_label.clone())
                                        };
                                        // Which visual slot is the match winner (if known).
                                        let winner = m.match_winner.as_deref().unwrap_or("");
                                        let top_is_winner = if flipped {
                                            winner.eq_ignore_ascii_case("TEAM2")
                                        } else {
                                            winner.eq_ignore_ascii_case("TEAM1")
                                        };
                                        let bot_is_winner = if flipped {
                                            winner.eq_ignore_ascii_case("TEAM1")
                                        } else {
                                            winner.eq_ignore_ascii_case("TEAM2")
                                        };
                                        let status_class = if !edit_mode() {
                                            match m.status.as_str() {
                                                "IN_PROGRESS" => " status-in-progress",
                                                "COMPLETED" => " status-completed",
                                                _ => "",
                                            }
                                        } else {
                                            ""
                                        };

                                        rsx! {
                                            if let Some(init) = label1.clone() {
                                                {
                                                    let kind = resolve_label(&init, &team_options, &tags, &matches_snap);
                                                    let can_wire = parse_match_ref(&init).is_some();
                                                    let mid = m.uuid.clone();
                                                    rsx! {
                                                        NetLabelView {
                                                            key: "{m.uuid}-l1",
                                                            x: x, y: y1_port, kind: kind, backend: backend.clone(),
                                                            editable: edit_mode() && can_wire,
                                                            on_click: {
                                                                let u = tournament_url.clone();
                                                                let mid = mid.clone();
                                                                move |_| {
                                                                    if !edit_mode() || !can_wire { return; }
                                                                    convert_match_port(
                                                                        u.clone(), mid.clone(), "team1", "NET",
                                                                        local_matches, local_texts, local_labeled, local_images,
                                                                        dirty, saving, canvas_size,
                                                                    );
                                                                }
                                                            },
                                                        }
                                                    }
                                                }
                                            }
                                            if let Some(init) = label2.clone() {
                                                {
                                                    let kind = resolve_label(&init, &team_options, &tags, &matches_snap);
                                                    let can_wire = parse_match_ref(&init).is_some();
                                                    let mid = m.uuid.clone();
                                                    rsx! {
                                                        NetLabelView {
                                                            key: "{m.uuid}-l2",
                                                            x: x, y: y2_port, kind: kind, backend: backend.clone(),
                                                            editable: edit_mode() && can_wire,
                                                            on_click: {
                                                                let u = tournament_url.clone();
                                                                let mid = mid.clone();
                                                                move |_| {
                                                                    if !edit_mode() || !can_wire { return; }
                                                                    convert_match_port(
                                                                        u.clone(), mid.clone(), "team2", "NET",
                                                                        local_matches, local_texts, local_labeled, local_images,
                                                                        dirty, saving, canvas_size,
                                                                    );
                                                                }
                                                            },
                                                        }
                                                    }
                                                }
                                            }

                                            div { class: "bracket-port-stub", style: "left: {x}px; top: {y1_port}px;" }
                                            div { class: "bracket-port-stub", style: "left: {x}px; top: {y2_port}px;" }
                                            if edit_mode() || w_shown {
                                                div { class: "bracket-port-stub output", style: "left: {x + w}px; top: {out_port_y(y, h, Qual::Winner)}px;" }
                                            }
                                            if edit_mode() || l_shown {
                                                div { class: "bracket-port-stub output loser", style: "left: {x + w}px; top: {out_port_y(y, h, Qual::Loser)}px;" }
                                            }
                                            for (qual, dests) in [(Qual::Winner, w_dests.clone()), (Qual::Loser, l_dests.clone())] {
                                                if !dests.is_empty() {
                                                    NetLabelView {
                                                        key: "{m.uuid}-out-{qual:?}",
                                                        x: x + w,
                                                        y: out_port_y(y, h, qual),
                                                        kind: LabelKind::Destinations { names: dests.join(", ") },
                                                        backend: backend.clone(),
                                                        editable: edit_mode(),
                                                        output: true,
                                                        on_click: {
                                                            let u = tournament_url.clone();
                                                            let src = m.clone();
                                                            move |_| {
                                                                if !edit_mode() || saving.structural_busy() { return; }
                                                                toggle_output_consumers(
                                                                    u.clone(),
                                                                    &src,
                                                                    qual,
                                                                    true,
                                                                    local_matches,
                                                                    local_texts,
                                                                    local_labeled,
                                                                    local_images,
                                                                    dirty,
                                                                    saving,
                                                                    canvas_size,
                                                                );
                                                            }
                                                        },
                                                    }
                                                }
                                            }

                                            div {
                                                key: "{m.uuid}",
                                                class: {
                                                    let mut c = String::from("bracket-match");
                                                    if selected { c.push_str(" selected"); }
                                                    if flipped { c.push_str(" inputs-flipped"); }
                                                    c.push_str(status_class);
                                                    let fk = match_field_key(&m);
                                                    if active_field_hover.as_ref() == Some(&fk) {
                                                        c.push_str(" field-hot");
                                                    }
                                                    c
                                                },
                                                // Used by instant DOM field-hover highlight (see set_field_hover).
                                                "data-field": "{match_field_key(&m)}",
                                                style: format!("left: {x}px; top: {y}px; width: {w}px; height: {h}px; cursor: {};", if edit_mode() { "grab" } else { "pointer" }),
                                                onpointerdown: move |ev: Event<PointerData>| {
                                                    if !edit_mode() || !ev.is_primary() || ev.trigger_button() != Some(MouseButton::Primary) || hand_pan() { return; }
                                                    // Ctrl/Cmd+click opens the match page instead of dragging.
                                                    let mods = ev.modifiers();
                                                    if mods.ctrl() || mods.meta() {
                                                        ev.prevent_default();
                                                        ev.stop_propagation();
                                                        nav.push(Route::MatchPageById {
                                                            url: nav_url.clone(),
                                                            match_id: uuid_down.clone(),
                                                        });
                                                        return;
                                                    }
                                                    if !begin_pointer_gesture(&ev) { return; }
                                                    ev.stop_propagation();
                                                    let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                    let mut ix = interaction.write();
                                                    let id = sel_match(&uuid_down);
                                                    if mods.shift() {
                                                        if ix.selected.contains(&id) { ix.selected.remove(&id); } else { ix.selected.insert(id.clone()); }
                                                    } else if !ix.selected.contains(&id) {
                                                        ix.selected.clear();
                                                        ix.selected.insert(id.clone());
                                                    }
                                                    let mut origins = HashMap::new();
                                                    collect_origins(&ix.selected, &local_matches(), &local_texts(), &local_labeled(), &local_images(), &mut origins);
                                                    ix.drag = Some(DragKind::Move { origins, pointer_start: (cx, cy) });
                                                },
                                                div { class: "bracket-match-row slot-top",
                                                    div { class: "bracket-team-slot",
                                                        if let Some(photo) = &top_photo {
                                                            img { class: "avatar", src: "{backend}/static/{photo}", alt: "" }
                                                        }
                                                        span { class: "label", title: "{top_name}", "{top_label}" }
                                                        if top_is_winner {
                                                            span { class: "winner-badge", title: "Match winner", "Winner" }
                                                        }
                                                    }
                                                    if edit_mode() || w_shown {
                                                        span {
                                                            class: if w_shown {
                                                                "bracket-port-badge winner clickable"
                                                            } else {
                                                                "bracket-port-badge winner clickable unused"
                                                            },
                                                            title: if edit_mode() {
                                                                if w_used {
                                                                    "Click to convert winner consumers to labels (unwire)"
                                                                } else if w_dests.is_empty() {
                                                                    "Click to add a team label for this winner"
                                                                } else {
                                                                    "Click to wire/place matches that take this winner"
                                                                }
                                                            } else {
                                                                "Winner output"
                                                            },
                                                            onclick: {
                                                                let u = tournament_url.clone();
                                                                let src = m.clone();
                                                                move |ev: Event<MouseData>| {
                                                                    if !edit_mode() || saving.structural_busy() { return; }
                                                                    ev.prevent_default();
                                                                    ev.stop_propagation();
                                                                    toggle_output_consumers(
                                                                        u.clone(),
                                                                        &src,
                                                                        Qual::Winner,
                                                                        false,
                                                                        local_matches,
                                                                        local_texts,
                                                                        local_labeled,
                                                                        local_images,
                                                                        dirty,
                                                                        saving,
                                                                        canvas_size,
                                                                    );
                                                                }
                                                            },
                                                            onpointerdown: move |ev: Event<PointerData>| {
                                                                // Don't start a drag when clicking W/L.
                                                                ev.stop_propagation();
                                                            },
                                                            "W"
                                                        }
                                                    }
                                                }
                                                div { class: "bracket-match-name", title: if edit_mode() {
                                                        format!("{} (Ctrl+click block to open)", m.name)
                                                    } else {
                                                        m.name.clone()
                                                    },
                                                    if edit_mode() {
                                                        span { class: "text-dark", "{m.name}" }
                                                    } else {
                                                        Link {
                                                            to: Route::MatchPageById { url: tournament_url.clone(), match_id: m.uuid.clone() },
                                                            class: "text-decoration-none text-dark",
                                                            "{m.name}"
                                                        }
                                                    }
                                                }
                                                div { class: "bracket-match-row slot-bot",
                                                    div { class: "bracket-team-slot",
                                                        if let Some(photo) = &bot_photo {
                                                            img { class: "avatar", src: "{backend}/static/{photo}", alt: "" }
                                                        }
                                                        span { class: "label", title: "{bot_name}", "{bot_label}" }
                                                        if bot_is_winner {
                                                            span { class: "winner-badge", title: "Match winner", "Winner" }
                                                        }
                                                    }
                                                    if edit_mode() || l_shown {
                                                        span {
                                                            class: if l_shown {
                                                                "bracket-port-badge loser clickable"
                                                            } else {
                                                                "bracket-port-badge loser clickable unused"
                                                            },
                                                            title: if edit_mode() {
                                                                if l_used {
                                                                    "Click to convert loser consumers to labels (unwire)"
                                                                } else if l_dests.is_empty() {
                                                                    "Click to add a team label for this loser"
                                                                } else {
                                                                    "Click to wire/place matches that take this loser"
                                                                }
                                                            } else {
                                                                "Loser output"
                                                            },
                                                            onclick: {
                                                                let u = tournament_url.clone();
                                                                let src = m.clone();
                                                                move |ev: Event<MouseData>| {
                                                                    if !edit_mode() || saving.structural_busy() { return; }
                                                                    ev.prevent_default();
                                                                    ev.stop_propagation();
                                                                    toggle_output_consumers(
                                                                        u.clone(),
                                                                        &src,
                                                                        Qual::Loser,
                                                                        false,
                                                                        local_matches,
                                                                        local_texts,
                                                                        local_labeled,
                                                                        local_images,
                                                                        dirty,
                                                                        saving,
                                                                        canvas_size,
                                                                    );
                                                                }
                                                            },
                                                            onpointerdown: move |ev: Event<PointerData>| {
                                                                ev.stop_propagation();
                                                            },
                                                            "L"
                                                        }
                                                    }
                                                }
                                                if edit_mode() {
                                                    div {
                                                        class: "bracket-resize-handle corner",
                                                        onpointerdown: move |ev: Event<PointerData>| {
                                                            if ev.trigger_button() != Some(MouseButton::Primary) || !begin_pointer_gesture(&ev) { return; }
                                                            ev.stop_propagation();
                                                            let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                            interaction.write().drag = Some(DragKind::Resize {
                                                                id: sel_match(&uuid_resize),
                                                                mode: "corner".into(),
                                                                start_w: p_resize.width,
                                                                start_h: p_resize.height,
                                                                aspect: 1.0,
                                                                pointer_start: (cx, cy),
                                                            });
                                                        },
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }

                                if let Some(DragKind::Marquee { start, current }) = &ix.drag {
                                    {
                                        let x = start.0.min(current.0);
                                        let y = start.1.min(current.1);
                                        let w = (start.0 - current.0).abs();
                                        let h = (start.1 - current.1).abs();
                                        rsx! {
                                            div { class: "bracket-marquee", style: "left: {x}px; top: {y}px; width: {w}px; height: {h}px;" }
                                        }
                                    }
                                }

                                // Airwires on top of everything (refs + field chains).
                                if !ref_airwires.is_empty() || !field_airwires.is_empty() {
                                    svg {
                                        class: "bracket-airwires-layer",
                                        width: "{cw}",
                                        height: "{ch}",
                                        // Layer itself ignores events except stroke hits on field wires.
                                        style: "pointer-events: none;",
                                        for aw in ref_airwires.iter() {
                                            {
                                                let aw = aw.clone();
                                                let rtl_flag = if aw.rtl { "1" } else { "0" };
                                                let gcls = if aw.rtl {
                                                    "bracket-airwire-group ref rtl"
                                                } else {
                                                    "bracket-airwire-group ref"
                                                };
                                                rsx! {
                                                    g {
                                                        key: "{aw.key}",
                                                        class: "{gcls}",
                                                        "data-rtl": "{rtl_flag}",
                                                        path {
                                                            class: "bracket-airwire-hit ref",
                                                            d: "{aw.path}",
                                                            stroke: "transparent",
                                                        }
                                                        path {
                                                            class: if aw.rtl {
                                                                "bracket-airwire ref rtl"
                                                            } else {
                                                                "bracket-airwire ref"
                                                            },
                                                            d: "{aw.path}",
                                                            stroke: "{aw.color}",
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        for aw in field_airwires.iter() {
                                            {
                                                let aw = aw.clone();
                                                let fname = aw.field.clone().unwrap_or_default();
                                                let is_hot = active_field_hover.as_ref() == Some(&fname);
                                                let gcls = if is_hot {
                                                    "bracket-airwire-group field field-hot"
                                                } else {
                                                    "bracket-airwire-group field"
                                                };
                                                rsx! {
                                                    g {
                                                        key: "{aw.key}",
                                                        class: "{gcls}",
                                                        "data-field": "{fname}",
                                                        path {
                                                            class: "bracket-airwire-hit field",
                                                            d: "{aw.path}",
                                                            stroke: "transparent",
                                                        }
                                                        path {
                                                            class: "bracket-airwire field",
                                                            d: "{aw.path}",
                                                            stroke: "{aw.color}",
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        } // end !show_legacy_fallback

                        // ---- Modals ----

                        if active_modal() == ActiveModal::AddMatch && edit_mode() {
                            Modal {
                                id: "bracket-add-match-dialog",
                                labelled_by: "bracket-add-match-title",
                                return_focus_id: Some("bracket-keyboard-focus".to_string()),
                                initial_focus_id: Some("bracket-add-match-filter".to_string()),
                                dismissible: !saving(),
                                on_close: close_editor_modal,
                                div { class: "modal-header",
                                    h2 { id: "bracket-add-match-title", class: "modal-title h5", tabindex: "-1", "Add Match" }
                                    button { r#type: "button", class: "btn-close", aria_label: "Close dialog", disabled: saving(), onclick: move |_| close_editor_modal.call(()) }
                                }
                                div { class: "modal-body",
                                    div { class: "px-3 pb-2",
                                        input {
                                            id: "bracket-add-match-filter",
                                            class: "form-control form-control-sm",
                                            r#type: "search",
                                            placeholder: "Filter matches…", aria_label: "Filter matches to add",
                                            value: "{add_q}",
                                            oninput: move |e| add_match_query.set(e.value()),
                                        }
                                    }
                                    if filtered_add_matches.is_empty() {
                                        p { class: "text-muted px-3 py-2 mb-0",
                                            if matches_snap.is_empty() { "No playable matches in this tournament." }
                                            else { "No matches match your filter." }
                                        }
                                    } else {
                                        for m in filtered_add_matches.iter() {
                                            {
                                                let mid = m.uuid.clone();
                                                let mname = m.name.clone();
                                                let t1 = m.team1_name.clone();
                                                let t2 = m.team2_name.clone();
                                                let already = is_placed(m);
                                                rsx! {
                                                    button {
                                                        key: "{mid}",
                                                        class: if already {
                                                            "bracket-add-match-item already-placed"
                                                        } else {
                                                            "bracket-add-match-item"
                                                        },
                                                        disabled: already,
                                                        title: if already {
                                                            "Already on the bracket"
                                                        } else {
                                                            "Add to bracket"
                                                        },
                                                        onclick: {
                                                            let u = tournament_url.clone();
                                                            let mid = mid.clone();
                                                            move |_| {
                                                                // Place in the current viewport at click time.
                                                                let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                                                let (nx, ny) = view_add_position(zoom(), pan(), stagger);
                                                                active_modal.set(ActiveModal::None);
                                                                if !saving.begin(dirty()) { return; }
                                                                let u = u.clone();
                                                                let mid = mid.clone();
                                                                spawn(async move {
                                                                    match api::add_bracket_placement(&u, &mid, nx, ny).await { Ok(resp) => {
                                                                        apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                    }, Err(message) => saving.fail(message), }
                                                                    saving.set(false);
                                                                    focus_bracket_root();
                                                                });
                                                            }
                                                        },
                                                        strong { "{mname}" }
                                                        div { class: "meta",
                                                            "{t1} vs {t2}"
                                                            if already {
                                                                span { class: "already-tag", " · on bracket" }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                div { class: "modal-footer",
                                    button {
                                        r#type: "button", class: "btn btn-outline-secondary btn-sm", disabled: saving(),
                                        onclick: move |_| close_editor_modal.call(()),
                                        "Close"
                                    }
                                }
                            }
                        }

                        if let ActiveModal::EditText { id } = active_modal() {
                            {
                                let tid = id.clone();
                                let is_create = pending_create().as_ref().map(|(k, i)| k == "text" && i == &tid).unwrap_or(false);
                                rsx! {
                                    Modal {
                                        id: "bracket-text-dialog",
                                        labelled_by: "bracket-text-title",
                                        return_focus_id: Some("bracket-keyboard-focus".to_string()),
                                        initial_focus_id: Some("bracket-text-content".to_string()),
                                        dismiss_on_backdrop: false,
                                        dismissible: !saving(),
                                        on_close: close_editor_modal,
                                        div { class: "modal-header",
                                            h2 { id: "bracket-text-title", class: "modal-title h5", tabindex: "-1", if is_create { "New Text" } else { "Edit Text" } }
                                            button {
                                                r#type: "button", class: "btn-close", aria_label: "Close dialog", disabled: saving(),
                                                onclick: move |_| close_editor_modal.call(()),
                                            }
                                        }
                                        div { class: "modal-body px-3 py-2",
                                            label { r#for: "bracket-text-content", class: "form-label", "Text" }
                                            textarea { id: "bracket-text-content",
                                                class: "form-control mb-2",
                                                rows: "3",
                                                value: "{text_draft}",
                                                oninput: move |e| text_draft.set(e.value()),
                                            }
                                            label { r#for: "bracket-text-size", class: "form-label", "Font size (px)" }
                                            input { id: "bracket-text-size",
                                                class: "form-control",
                                                r#type: "number",
                                                min: "8",
                                                max: "200",
                                                value: "{text_size_draft()}",
                                                oninput: move |e| {
                                                    if let Ok(v) = e.value().parse::<f64>() {
                                                        text_size_draft.set(v.clamp(8.0, 200.0));
                                                    }
                                                },
                                            }
                                        }
                                        div { class: "modal-footer",
                                            button {
                                                r#type: "button", class: "btn btn-primary btn-sm",
                                                onclick: {
                                                    let tid = tid.clone();
                                                    let u = tournament_url.clone();
                                                    move |_| {
                                                        let mut ts = local_texts();
                                                        if let Some(t) = ts.iter_mut().find(|t| t.id == tid) {
                                                            t.text = text_draft();
                                                            t.size = text_size_draft();
                                                        }
                                                        local_texts.set(ts);
                                                        dirty.set(true);
                                                        // First save commits creation — cancel no longer deletes.
                                                        if pending_create().as_ref().map(|(k, i)| k == "text" && i == &tid).unwrap_or(false) {
                                                            pending_create.set(None);
                                                        }
                                                        active_modal.set(ActiveModal::None);
                                                        persist_all(
                                                                u.clone(),
                                                                local_matches(),
                                                                local_texts(),
                                                                local_labeled(),
                                                                local_images(),
                                                                true,
                                                                local_matches,
                                                                local_texts,
                                                                local_labeled,
                                                                local_images,
                                                                dirty,
                                                                saving,
                                                                canvas_size,
                                                            );
                                                        focus_bracket_root();
                                                    }
                                                },
                                                "Save"
                                            }
                                            button {
                                                r#type: "button", class: "btn btn-outline-secondary btn-sm", disabled: saving(),
                                                onclick: move |_| close_editor_modal.call(()),
                                                "Cancel"
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if let ActiveModal::EditLabeledTeam { id } = active_modal() {
                            {
                                let lid = id.clone();
                                let is_create = pending_create().as_ref().map(|(k, i)| k == "labeled" && i == &lid).unwrap_or(false);
                                rsx! {
                                    Modal {
                                        id: "bracket-team-label-dialog",
                                        labelled_by: "bracket-team-label-title",
                                        return_focus_id: Some("bracket-keyboard-focus".to_string()),
                                        initial_focus_id: Some("bracket-team-label".to_string()),
                                        dismiss_on_backdrop: false,
                                        dismissible: !saving(),
                                        on_close: close_editor_modal,
                                        div { class: "modal-header",
                                            h2 { id: "bracket-team-label-title", class: "modal-title h5", tabindex: "-1", if is_create { "New Team Label" } else { "Edit Team Label" } }
                                            button {
                                                r#type: "button", class: "btn-close", aria_label: "Close dialog", disabled: saving(),
                                                onclick: move |_| close_editor_modal.call(()),
                                            }
                                        }
                                        div { class: "modal-body px-3 py-2",
                                            div { class: "mb-2",
                                                label { r#for: "bracket-team-label", class: "form-label", "Label" }
                                                input { id: "bracket-team-label",
                                                    class: "form-control",
                                                    r#type: "text",
                                                    maxlength: "50",
                                                    value: "{label_draft}",
                                                    placeholder: "e.g. Winner SF1",
                                                    oninput: move |e| {
                                                        let v = e.value();
                                                        let trimmed: String = v.chars().take(50).collect();
                                                        label_draft.set(trimmed);
                                                    },
                                                }
                                                div { class: "form-text", "Shown until the team is known (max 50 chars)." }
                                            }
                                            TeamSelectionField {
                                                label: "Team".to_string(),
                                                team_options: team_options.clone(),
                                                tags: tags.clone(),
                                                matches: setup_matches.clone(),
                                                value: team_draft(),
                                                on_change: move |s| team_draft.set(s),
                                                multiple: false,
                                                placeholder: "Team, Match::winner, tag::Name".to_string(),
                                                help_text: Some("Explicit team, match winner/loser, or tag".to_string()),
                                                wrapper_class: Some("mb-2 bracket-team-token".to_string()),
                                            }
                                        }
                                        div { class: "modal-footer",
                                            button {
                                                r#type: "button", class: "btn btn-primary btn-sm",
                                                onclick: {
                                                    let lid = lid.clone();
                                                    let u = tournament_url.clone();
                                                    move |_| {
                                                        let mut ls = local_labeled();
                                                        if let Some(t) = ls.iter_mut().find(|t| t.id == lid) {
                                                            t.label = label_draft().chars().take(50).collect();
                                                            t.team = team_draft();
                                                            // Reset display optimistically; server refresh fills resolved fields.
                                                            t.display_text = t.team.clone();
                                                            t.resolved = false;
                                                            if is_net(&t.kind) && parse_match_ref(&t.team).is_none() {
                                                                t.kind = "LABEL".into();
                                                            }
                                                        }
                                                        local_labeled.set(ls);
                                                        dirty.set(true);
                                                        if pending_create().as_ref().map(|(k, i)| k == "labeled" && i == &lid).unwrap_or(false) {
                                                            pending_create.set(None);
                                                        }
                                                        active_modal.set(ActiveModal::None);
                                                        persist_all(
                                                                u.clone(),
                                                                local_matches(),
                                                                local_texts(),
                                                                local_labeled(),
                                                                local_images(),
                                                                true,
                                                                local_matches,
                                                                local_texts,
                                                                local_labeled,
                                                                local_images,
                                                                dirty,
                                                                saving,
                                                                canvas_size,
                                                            );
                                                        focus_bracket_root();
                                                    }
                                                },
                                                "Save"
                                            }
                                            button {
                                                r#type: "button", class: "btn btn-outline-secondary btn-sm", disabled: saving(),
                                                onclick: move |_| close_editor_modal.call(()),
                                                "Cancel"
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if active_modal() == ActiveModal::AddImage && edit_mode() {
                            Modal {
                                id: "bracket-add-image-dialog",
                                labelled_by: "bracket-add-image-title",
                                return_focus_id: Some("bracket-keyboard-focus".to_string()),
                                dismissible: !saving(),
                                on_close: close_editor_modal,
                                div { class: "modal-header",
                                    h2 { id: "bracket-add-image-title", class: "modal-title h5", tabindex: "-1", "Add Image" }
                                    button { r#type: "button", class: "btn-close", aria_label: "Close dialog", disabled: saving(), onclick: move |_| close_editor_modal.call(()) }
                                }
                                div { class: "modal-body px-3 py-2",
                                    label { r#for: "bracket-image-file", class: "form-label", "Image file" }
                                    p { id: "bracket-image-help", class: "text-muted small", "Upload an image under 10 MB." }
                                    input { id: "bracket-image-file", aria_describedby: "bracket-image-help",
                                        class: "form-control",
                                        r#type: "file",
                                        accept: "image/*",
                                        onchange: {
                                            let u = tournament_url.clone();
                                            move |evt| {
                                                #[cfg(target_arch = "wasm32")]
                                                {
                                                    let files = evt.files();
                                                    if let Some(file) = files.into_iter().next() {
                                                        let u = u.clone();
                                                        let n = local_images().len() as f64;
                                                        let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                                        let (ax, ay) = view_add_position(zoom(), pan(), stagger);
                                                        active_modal.set(ActiveModal::None);
                                                        if !saving.begin(dirty()) { return; }
                                                        spawn(async move {
                                                            let filename = file.name();
                                                            match file.read_bytes().await {
                                                                Ok(bytes) => {
                                                                    if bytes.len() > 10 * 1024 * 1024 {
                                                                        #[cfg(target_arch = "wasm32")]
                                                                        saving.fail("Image must be under 10 MB");
                                                                        saving.set(false);
                                                                        return;
                                                                    }
                                                                    match api::upload_bracket_image_bytes(&u, n as u32, &filename, bytes).await {
                                                                        Ok(path) => {
                                                                            match api::add_bracket_image_element(
                                                                                &u, &path, ax, ay, 240.0, 160.0,
                                                                            ).await { Ok(resp) => {
                                                                                apply_layout(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                            }, Err(message) => saving.fail(message), }
                                                                        }
                                                                        Err(e) => {
                                                                            saving.fail(e);
                                                                        }
                                                                    }
                                                                }
                                                                Err(message) => saving.fail(message.to_string()),
                                                            }
                                                            saving.set(false);
                                                            focus_bracket_root();
                                                        });
                                                    }
                                                }
                                                #[cfg(not(target_arch = "wasm32"))]
                                                { let _ = (&evt, &u); }
                                            }
                                        },
                                    }
                                }
                                div { class: "modal-footer",
                                    button {
                                        r#type: "button", class: "btn btn-outline-secondary btn-sm", disabled: saving(),
                                        onclick: move |_| close_editor_modal.call(()),
                                        "Close"
                                    }
                                }
                            }
                        }

                        if active_modal() == ActiveModal::LegacyManage && is_to {
                            Modal {
                                id: "bracket-legacy-dialog",
                                labelled_by: "bracket-legacy-title",
                                return_focus_id: Some("bracket-keyboard-focus".to_string()),
                                dismissible: !saving(),
                                on_close: close_editor_modal,
                                div { class: "modal-header",
                                    h2 { id: "bracket-legacy-title", class: "modal-title h5", tabindex: "-1", "Legacy brackets" }
                                    button {
                                        r#type: "button", class: "btn-close", aria_label: "Close dialog", disabled: saving(),
                                        onclick: move |_| close_editor_modal.call(())
                                    }
                                }
                                div { class: "modal-body px-3 py-2",
                                    if let Some(message) = saving.error() {
                                        p { class: "alert alert-danger", role: "alert", "{message}" }
                                        p { class: "small text-muted", "Close this dialog and check the saved bracket before trying again." }
                                    }
                                    if saving() { p { role: "status", "Updating legacy brackets…" } }
                                    p { class: "small text-muted mb-2",
                                        "These image-overlay brackets are from the old setup. Delete them once you no longer need them."
                                    }
                                    if legacy_snap.is_empty() {
                                        p { class: "text-muted mb-0", "No legacy brackets configured." }
                                    } else {
                                        ul { class: "list-group list-group-flush",
                                            for (idx, bracket) in legacy_snap.iter().enumerate() {
                                                {
                                                    let bname = if bracket.name.is_empty() {
                                                        format!("Bracket {}", idx + 1)
                                                    } else {
                                                        bracket.name.clone()
                                                    };
                                                    let bimg = bracket.image.clone();
                                                    let idx_u = idx;
                                                    let u = tournament_url.clone();
                                                    rsx! {
                                                        li {
                                                            class: "list-group-item bracket-legacy-row px-0",
                                                            key: "{idx_u}-{bname}",
                                                            div { class: "bracket-legacy-identity",
                                                                img {
                                                                    src: "{backend}/static/{bimg}",
                                                                    alt: "{bname}",
                                                                    style: "width: 64px; height: 40px; object-fit: cover; border-radius: 4px; background: var(--ui-surface-muted);"
                                                                }
                                                                div { class: "bracket-legacy-copy",
                                                                    strong { "{bname}" }
                                                                    div { class: "small text-muted", "{bimg}" }
                                                                }
                                                            }
                                                            div { class: "bracket-legacy-actions",
                                                                if saving() {
                                                                    span { class: "btn btn-sm btn-outline-primary disabled", aria_disabled: "true", "View" }
                                                                } else {
                                                                    Link {
                                                                        to: Route::LegacyBracket { url: tournament_url.clone() },
                                                                        class: "btn btn-sm btn-outline-primary",
                                                                        "View"
                                                                    }
                                                                }
                                                                button {
                                                                    class: "btn btn-sm btn-outline-danger",
                                                                    disabled: saving() || saving.error().is_some(),
                                                                    onclick: move |_| {
                                                                        let u = u.clone();
                                                                        if !saving.begin(dirty()) { return; }
                                                                        spawn(async move {
                                                                            match api::delete_legacy_bracket(&u, idx_u).await {
                                                                                Ok(resp) => {
                                                                                    legacy_brackets.set(resp.legacy_brackets.clone());
                                                                                    if resp.legacy_brackets.is_empty() {
                                                                                        active_modal.set(ActiveModal::None);
                                                                                    }
                                                                                }
                                                                                Err(e) => {
                                                                                    saving.fail(e);
                                                                                }
                                                                            }
                                                                            saving.set(false);
                                                                        });
                                                                    },
                                                                    "Delete"
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                div { class: "modal-footer",
                                    if !legacy_snap.is_empty() {
                                        button {
                                            class: "btn btn-sm btn-danger me-auto",
                                            disabled: saving() || saving.error().is_some(),
                                            onclick: {
                                                let u = tournament_url.clone();
                                                move |_| {
                                                    let u = u.clone();
                                                    if !saving.begin(dirty()) { return; }
                                                    spawn(async move {
                                                        match api::clear_legacy_brackets(&u).await {
                                                            Ok(resp) => {
                                                                legacy_brackets.set(resp.legacy_brackets.clone());
                                                                active_modal.set(ActiveModal::None);
                                                            }
                                                            Err(e) => {
                                                                saving.fail(e);
                                                            }
                                                        }
                                                        saving.set(false);
                                                    });
                                                }
                                            },
                                            "Delete all"
                                        }
                                    }
                                    button {
                                        r#type: "button", class: "btn btn-outline-secondary btn-sm", disabled: saving(),
                                        onclick: move |_| close_editor_modal.call(()),
                                        "Close"
                                    }
                                }
                            }
                        }

                    }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    p { class: "text-danger", "{e}" }
                }
            }
        } else {
            p { "Loading…" }
        }
    }
}

/// Cancel a just-created element (first edit dialog only).
fn cancel_pending_create(
    pending: Option<(String, String)>,
    mut local_texts: Signal<Vec<BracketTextData>>,
    mut local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    mut local_images: Signal<Vec<BracketImageData>>,
    mut dirty: Signal<bool>,
    url: String,
    local_matches: Signal<Vec<BracketMatchData>>,
    saving: BracketSave,
    canvas_size: Signal<(f64, f64)>,
) {
    let Some((kind, id)) = pending else {
        return;
    };
    match kind.as_str() {
        "text" => {
            let mut ts = local_texts();
            ts.retain(|t| t.id != id);
            local_texts.set(ts);
        }
        "labeled" => {
            let mut ls = local_labeled();
            ls.retain(|t| t.id != id);
            local_labeled.set(ls);
        }
        "image" => {
            let mut im = local_images();
            im.retain(|i| i.id != id);
            local_images.set(im);
        }
        _ => return,
    }
    dirty.set(true);
    persist_all(
        url,
        local_matches(),
        local_texts(),
        local_labeled(),
        local_images(),
        true,
        local_matches,
        local_texts,
        local_labeled,
        local_images,
        dirty,
        saving,
        canvas_size,
    );
}

fn collect_origins(
    selected: &HashSet<String>,
    matches: &[BracketMatchData],
    texts: &[BracketTextData],
    labeled: &[BracketLabeledTeamData],
    images: &[BracketImageData],
    origins: &mut HashMap<String, (f64, f64)>,
) {
    for m in matches {
        let k = sel_match(&m.uuid);
        if selected.contains(&k) {
            if let Some(p) = &m.placement {
                if let (Some(x), Some(y)) = (p.x_pos, p.y_pos) {
                    origins.insert(k, (x, y));
                }
            }
        }
    }
    for t in texts {
        let k = sel_text(&t.id);
        if selected.contains(&k) {
            origins.insert(k, (t.x_pos, t.y_pos));
        }
    }
    for t in labeled {
        let k = sel_labeled(&t.id);
        if selected.contains(&k) {
            // Snap origin is offset so the input port hits half-grid cells.
            origins.insert(k, labeled_to_snap_origin(t.x_pos, t.y_pos));
        }
    }
    for i in images {
        let k = sel_image(&i.id);
        if selected.contains(&k) {
            origins.insert(k, (i.x_pos, i.y_pos));
        }
    }
}

#[component]
fn NetLabelView(
    x: f64,
    y: f64,
    kind: LabelKind,
    backend: String,
    editable: bool,
    /// Sits right of an output port instead of left of an input.
    #[props(default)]
    output: bool,
    on_click: EventHandler<Event<MouseData>>,
) -> Element {
    let mut class = String::from("bracket-net-label");
    if output {
        class.push_str(" output");
    }
    if editable {
        class.push_str(" editable");
    }
    let title = match (&kind, editable) {
        (LabelKind::Destinations { names }, true) => format!("{names} — click to draw as wires"),
        (LabelKind::Destinations { names }, false) => names.clone(),
        (_, true) => "Click to convert to wire".to_string(),
        (_, false) => String::new(),
    };
    rsx! {
        div {
            class: "{class}",
            style: "left: {x}px; top: {y}px;",
            title: "{title}",
            onclick: move |ev: Event<MouseData>| {
                ev.stop_propagation();
                on_click.call(ev);
            },
            onpointerdown: move |ev: Event<PointerData>| {
                if editable {
                    ev.stop_propagation();
                }
            },
            {
                match &kind {
                    LabelKind::Team { display } => rsx! { span { class: "token-text", "{display}" } },
                    LabelKind::Tag { name } => rsx! {
                        img { class: "token-icon", src: "{backend}/static/tag.svg", alt: "tag" }
                        span { class: "token-text", "{name}" }
                    },
                    LabelKind::Winner { match_name } => rsx! {
                        img { class: "token-icon", src: "{backend}/static/reference.svg", alt: "ref" }
                        span { class: "token-text", "{match_name} winner" }
                    },
                    LabelKind::Loser { match_name } => rsx! {
                        img { class: "token-icon", src: "{backend}/static/reference.svg", alt: "ref" }
                        span { class: "token-text", "{match_name} loser" }
                    },
                    LabelKind::Destinations { names } => rsx! {
                        img { class: "token-icon", src: "{backend}/static/reference.svg", alt: "ref" }
                        span { class: "token-text", "{names}" }
                    },
                    LabelKind::Raw(s) => rsx! { span { class: "token-text", "{s}" } },
                }
            }
        }
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::{LayoutSaveState, LayoutSnapshot};
    fn layout(x: i32) -> LayoutSnapshot {
        LayoutSnapshot {
            placements: vec![serde_json::json!({"x_pos": x})],
            texts: vec![],
            labeled: vec![],
            images: vec![],
            clear_missing: true,
        }
    }
    #[test]
    fn saves_coalesce_to_latest_without_parallel_worker() {
        let mut state = LayoutSaveState::default();
        assert!(state.enqueue(layout(1)));
        assert!(!state.enqueue(layout(2)));
        assert!(!state.enqueue(layout(3)));
        assert!(state.pending.as_ref() == Some(&layout(3)));
        assert_eq!(state.revision, 3);
        assert!(state.running);
    }
    #[test]
    fn failed_save_keeps_latest_edit_until_explicit_retry() {
        let mut state = LayoutSaveState {
            failed: true,
            ..Default::default()
        };
        assert!(!state.enqueue(layout(9)));
        assert!(!state.running);
        assert!(state.pending.as_ref() == Some(&layout(9)));
        state.failed = false;
        assert!(state.enqueue(layout(9)));
    }
    #[test]
    fn acknowledgment_depends_on_layout_not_pan_or_marquee() {
        let mut state = LayoutSaveState::default();
        state.enqueue(layout(1));
        assert!(state.can_acknowledge(1, &layout(1), &layout(1)));
        assert!(!state.can_acknowledge(1, &layout(1), &layout(2)));
        state.enqueue(layout(2));
        assert!(!state.can_acknowledge(1, &layout(1), &layout(1)));
    }
}

#[cfg(test)]
mod modal_dismissal_tests {
    use super::{dismisses_provisional_node, ActiveModal};

    #[test]
    fn only_matching_provisional_editor_rolls_back_creation() {
        let text = ActiveModal::EditText { id: "new-text".into() };
        let label = ActiveModal::EditLabeledTeam { id: "new-label".into() };
        let pending_text = ("text".into(), "new-text".into());
        let pending_label = ("labeled".into(), "new-label".into());
        assert!(dismisses_provisional_node(&text, Some(&pending_text)));
        assert!(dismisses_provisional_node(&label, Some(&pending_label)));
        assert!(!dismisses_provisional_node(&text, None));
        assert!(!dismisses_provisional_node(&text, Some(&pending_label)));
        assert!(!dismisses_provisional_node(&ActiveModal::EditText { id: "saved-text".into() }, Some(&pending_text)));
        assert!(!dismisses_provisional_node(&ActiveModal::AddImage, Some(&pending_text)));
    }
}
