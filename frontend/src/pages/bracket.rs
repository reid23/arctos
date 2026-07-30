//! Interactive open-canvas bracket builder / viewer.
//!
//! TOs enter edit mode to place matches, text, labeled teams, and images;
//! wire winner/loser outputs; multi-select; resize; zoom/pan. Viewers see
//! the same canvas without editing chrome (auto-fit).

use crate::api;
use crate::display::short_or_truncate;
use crate::pages::TeamSelectionField;
use super::legacy_bracket::LegacyBracketDiagrams;
use crate::types::{
    BracketImageData, BracketItem, BracketLabeledTeamData, BracketMatchData, BracketPlacementData,
    BracketResponse, BracketTextData, MatchSetupData, TagSetupData, TeamOption,
};
use crate::Route;
use dioxus::prelude::*;
use dioxus::html::input_data::MouseButton;
use std::collections::{HashMap, HashSet};
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast;

const DEFAULT_WIDTH: f64 = 280.0;
const DEFAULT_HEIGHT: f64 = 100.0;
const CANVAS_MIN_W: f64 = 1200.0;
const CANVAS_MIN_H: f64 = 800.0;
const PORT_INSET_Y_FRAC_TOP: f64 = 0.28;
const PORT_INSET_Y_FRAC_BOT: f64 = 0.72;
#[allow(dead_code)]
const LABELED_TEAM_W: f64 = 200.0;
const LABELED_TEAM_H: f64 = 36.0;
const MIN_ZOOM: f64 = 0.15;
const MAX_ZOOM: f64 = 3.0;

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

/// How far net-labels stick out left of a match (CSS max-width + margin + port).
const NET_LABEL_LEFT_EXTENT: f64 = 120.0;
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
                bump(x - PORT_STUB_EXTENT, y, p.width + PORT_STUB_EXTENT * 2.0, p.height);
                // LABEL-mode inputs render net-label chips to the left of the match.
                if !is_net(&p.team1) || !is_net(&p.team2) {
                    bump(x - NET_LABEL_LEFT_EXTENT, y, NET_LABEL_LEFT_EXTENT, p.height);
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

fn apply_response(
    resp: BracketResponse,
    mut local_matches: Signal<Vec<BracketMatchData>>,
    mut local_texts: Signal<Vec<BracketTextData>>,
    mut local_labeled: Signal<Vec<BracketLabeledTeamData>>,
    mut local_images: Signal<Vec<BracketImageData>>,
    mut dirty: Signal<bool>,
    canvas_size: Signal<(f64, f64)>,
) {
    fit_canvas_size(
        &resp.matches,
        &resp.texts,
        &resp.labeled_teams,
        &resp.images,
        canvas_size,
    );
    local_matches.set(resp.matches);
    local_texts.set(resp.texts);
    local_labeled.set(resp.labeled_teams);
    local_images.set(resp.images);
    dirty.set(false);
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
    dirty: Signal<bool>,
    mut saving: Signal<bool>,
    canvas_size: Signal<(f64, f64)>,
) {
    let p = all_placements_json(&matches);
    let t = texts_json(&texts);
    let l = labeled_json(&labeled);
    let i = images_json(&images);
    saving.set(true);
    spawn(async move {
        match api::save_bracket_placements(&url, &p, &t, &l, &i, clear_missing).await {
            Ok(resp) => apply_response(
                resp,
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                canvas_size,
            ),
            Err(e) => {
                #[cfg(target_arch = "wasm32")]
                web_sys::console::error_1(&format!("Bracket save failed: {e}").into());
                let _ = e;
            }
        }
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
    mut saving: Signal<bool>,
    canvas_size: Signal<(f64, f64)>,
) {
    let side = side.to_string();
    let mode = mode.to_string();
    saving.set(true);
    spawn(async move {
        match api::convert_bracket_port(&url, &match_uuid, &side, &mode).await {
            Ok(resp) => apply_response(
                resp,
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                canvas_size,
            ),
            Err(e) => {
                #[cfg(target_arch = "wasm32")]
                web_sys::console::error_1(&format!("Convert failed: {e}").into());
                let _ = e;
            }
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
                        // preventScroll so refocus on window-return doesn't jump the page.
                        let _ = html_el.focus();
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
    let sx = sx.min((vw * 0.5).max(24.0));
    let sy = sy.min((vh * 0.5).max(24.0));
    let wx = (sx - pan.0) / z;
    let wy = (sy - pan.1) / z;
    (wx.max(0.0), wy.max(0.0))
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

fn canvas_pointer(ev: &Event<MouseData>, zoom: f64, pan: (f64, f64)) -> (f64, f64) {
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

fn screen_pointer(ev: &Event<MouseData>) -> (f64, f64) {
    (ev.client_coordinates().x, ev.client_coordinates().y)
}

fn underline_label(label: &str, key: char) -> Element {
    // Render label with the shortcut letter underlined (first match, case-insensitive).
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

#[derive(Clone, Debug, PartialEq)]
enum ActiveModal {
    None,
    AddMenu,
    AddMatch,
    EditText { id: String },
    EditLabeledTeam { id: String },
    AddImage,
    LegacyManage,
}

impl Default for ActiveModal {
    fn default() -> Self {
        Self::None
    }
}

#[derive(Clone, Debug, PartialEq)]
enum LabelKind {
    Team { display: String },
    Tag { name: String },
    Winner { match_name: String },
    Loser { match_name: String },
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
    let url_for_data = url.clone();
    let mut data = use_resource(move || {
        let u = url_for_data.clone();
        async move { api::tournament_bracket(&u).await.map_err(|e| e.to_string()) }
    });

    let mut edit_mode = use_signal(|| false);
    let mut local_matches = use_signal(|| Vec::<BracketMatchData>::new());
    let mut local_texts = use_signal(|| Vec::<BracketTextData>::new());
    let mut local_labeled = use_signal(|| Vec::<BracketLabeledTeamData>::new());
    let mut local_images = use_signal(|| Vec::<BracketImageData>::new());
    let mut legacy_brackets = use_signal(|| Vec::<BracketItem>::new());
    let mut bracket_published = use_signal(|| false);
    let mut interaction = use_signal(Interaction::default);
    let mut active_modal = use_signal(ActiveModal::default);
    let mut add_match_query = use_signal(String::new);
    let mut saving = use_signal(|| false);
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

    use_effect(move || {
        if initialized() {
            return;
        }
        if let Some(Ok(d)) = data.value().read().as_ref() {
            apply_response(
                d.clone(),
                local_matches,
                local_texts,
                local_labeled,
                local_images,
                dirty,
                canvas_size,
            );
            legacy_brackets.set(d.legacy_brackets.clone());
            bracket_published.set(d.bracket_published || d.tournament.bracket_published);
            initialized.set(true);
            fit_tick.set(fit_tick() + 1);
        }
    });

    // Re-focus the hotkey root when the browser tab/window regains focus so
    // shortcuts work without needing to click the center column first.
    #[cfg(target_arch = "wasm32")]
    {
        use_effect(move || {
            let window = match web_sys::window() {
                Some(w) => w,
                None => return,
            };
            let doc = match window.document() {
                Some(d) => d,
                None => return,
            };
            let focus_cb = wasm_bindgen::closure::Closure::wrap(Box::new(move |_ev: web_sys::Event| {
                focus_bracket_root();
            }) as Box<dyn FnMut(_)>);
            let click_cb = wasm_bindgen::closure::Closure::wrap(Box::new(move |ev: web_sys::Event| {
                // Don't steal focus from real form controls.
                if let Some(t) = ev.target() {
                    if let Some(el) = t.dyn_ref::<web_sys::Element>() {
                        let tag = el.tag_name().to_ascii_lowercase();
                        if matches!(tag.as_str(), "input" | "textarea" | "select" | "button" | "a") {
                            return;
                        }
                        // contenteditable
                        if el.get_attribute("contenteditable").as_deref() == Some("true") {
                            return;
                        }
                    }
                }
                focus_bracket_root();
            }) as Box<dyn FnMut(_)>);
            let _ = window.add_event_listener_with_callback("focus", focus_cb.as_ref().unchecked_ref());
            let _ = doc.add_event_listener_with_callback("visibilitychange", focus_cb.as_ref().unchecked_ref());
            let _ = doc.add_event_listener_with_callback("click", click_cb.as_ref().unchecked_ref());
            // Leak listeners for the page lifetime (component is long-lived).
            focus_cb.forget();
            click_cb.forget();
        });
    }

    // Auto-fit when not editing: scale to fill available width, then set
    // wrap height from that scale so the full bracket is visible (no vertical crop).
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
                        } else if let Some(el) = doc.query_selector("main, .container, .container-fluid").ok().flatten() {
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

    let val = data.value();
    let backend = api::base_url();

    rsx! {
        style { {PAGE_CSS} }
        style { {SCHEDULE_TOKEN_CSS} }

        if let Some(Ok(d)) = val.read().as_ref() {
            {
                let is_to = d.is_to;
                let team_options = d.team_options.clone();
                let tags = d.tags.clone();
                let tournament_name = d.tournament.name.clone();
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
                let unplaced: Vec<BracketMatchData> = matches_snap
                    .iter()
                    .filter(|m| !is_placed(m))
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

                let (cw, ch) = canvas_size();
                let ix = interaction();
                let z = zoom();
                let (px, py) = pan();
                let transform = format!("translate({px}px, {py}px) scale({z})");

                let add_q = add_match_query();
                let add_q_lower = add_q.trim().to_ascii_lowercase();
                let filtered_unplaced: Vec<BracketMatchData> = if add_q_lower.is_empty() {
                    unplaced.clone()
                } else {
                    unplaced.iter().filter(|m| {
                        m.name.to_ascii_lowercase().contains(&add_q_lower)
                            || m.team1_name.to_ascii_lowercase().contains(&add_q_lower)
                            || m.team2_name.to_ascii_lowercase().contains(&add_q_lower)
                    }).cloned().collect()
                };

                let handle_keydown = {
                    let u_key = tournament_url.clone();
                    move |ev: Event<KeyboardData>| {
                        if !edit_mode() || !is_to { return; }
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
                                    saving.set(true);
                                    spawn(async move {
                                        match api::add_bracket_text(&u, ax, ay).await {
                                            Ok(resp) => {
                                                let new_id = resp.texts.iter().map(|t| t.id.clone()).max_by_key(|id| id.clone());
                                                apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                if let Some(id) = new_id {
                                                    // pick the one just added (last by comparing before)
                                                    let texts = local_texts();
                                                    if let Some(t) = texts.last() {
                                                        text_draft.set(t.text.clone());
                                                        text_size_draft.set(t.size);
                                                        active_modal.set(ActiveModal::EditText { id: t.id.clone() });
                                                    }
                                                    let _ = id;
                                                }
                                            }
                                            Err(e) => {
                                                #[cfg(target_arch = "wasm32")]
                                                web_sys::console::error_1(&e.into());
                                            }
                                        }
                                        saving.set(false);
                                    });
                                }
                                "t" | "T" => {
                                    ev.prevent_default();
                                    let u = u_key.clone();
                                    let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                    let (ax, ay) = view_add_position(zoom(), pan(), stagger);
                                    active_modal.set(ActiveModal::None);
                                    saving.set(true);
                                    spawn(async move {
                                        match api::add_bracket_labeled_team(&u, ax, ay).await {
                                            Ok(resp) => {
                                                apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                if let Some(t) = local_labeled().last() {
                                                    label_draft.set(if t.label.is_empty() { "Label".into() } else { t.label.clone() });
                                                    team_draft.set(t.team.clone());
                                                    active_modal.set(ActiveModal::EditLabeledTeam { id: t.id.clone() });
                                                }
                                            }
                                            Err(e) => {
                                                #[cfg(target_arch = "wasm32")]
                                                web_sys::console::error_1(&e.into());
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
                        role: "application",
                        aria_label: "Bracket editor",
                        onkeydown: handle_keydown,
                        // Clicking gutters/sides of the page restores hotkey focus.
                        onclick: move |_| {
                            focus_bracket_root();
                        },
                        onmounted: move |ev| {
                            spawn(async move { let _ = ev.data().set_focus(true).await; });
                        },

                        div { class: "row",
                            div { class: "col-12",
                                h1 { "{tournament_name} - Bracket" }
                                div { class: "bracket-page-toolbar",
                                    Link {
                                        to: Route::TournamentHome { url: tournament_url.clone() },
                                        class: "btn btn-outline-secondary btn-sm",
                                        "Back to Tournament"
                                    }
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
                                                disabled: "{saving}",
                                                onchange: {
                                                    let u = tournament_url.clone();
                                                    move |e| {
                                                        let on = e.value() == "true";
                                                        // Optimistic update; revert on failure.
                                                        let prev = bracket_published();
                                                        bracket_published.set(on);
                                                        saving.set(true);
                                                        let u = u.clone();
                                                        spawn(async move {
                                                            match api::set_bracket_published(&u, on).await {
                                                                Ok(resp) => {
                                                                    bracket_published.set(
                                                                        resp.bracket_published
                                                                            || resp.tournament.bracket_published,
                                                                    );
                                                                }
                                                                Err(err) => {
                                                                    bracket_published.set(prev);
                                                                    #[cfg(target_arch = "wasm32")]
                                                                    web_sys::console::error_1(&err.into());
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
                                                                    saving.set(true);
                                                                    spawn(async move {
                                                                        if let Ok(resp) = api::add_bracket_text(&u, ax, ay).await {
                                                                            apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                            if let Some(t) = local_texts().last() {
                                                                                text_draft.set(t.text.clone());
                                                                                text_size_draft.set(t.size);
                                                                                active_modal.set(ActiveModal::EditText { id: t.id.clone() });
                                                                            }
                                                                        }
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
                                                                    let (ax, ay) = view_add_position(zoom(), pan(), stagger);
                                                                    active_modal.set(ActiveModal::None);
                                                                    saving.set(true);
                                                                    spawn(async move {
                                                                        if let Ok(resp) = api::add_bracket_labeled_team(&u, ax, ay).await {
                                                                            apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                            if let Some(t) = local_labeled().last() {
                                                                                label_draft.set(if t.label.is_empty() { "Label".into() } else { t.label.clone() });
                                                                                team_draft.set(t.team.clone());
                                                                                active_modal.set(ActiveModal::EditLabeledTeam { id: t.id.clone() });
                                                                            }
                                                                        }
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
                                            disabled: interaction().selected.is_empty(),
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
                                            disabled: interaction().selected.is_empty(),
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
                                        span { class: "text-muted small ms-1",
                                            "Scroll zoom · Right-drag pan · Shift+click multi-select"
                                        }
                                        if saving() {
                                            span { class: "text-muted small", "Saving…" }
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

                        if !show_legacy_fallback {
                            div {
                            id: "bracket-canvas-wrap",
                            class: if edit_mode() { "bracket-canvas-wrap edit-mode" } else { "bracket-canvas-wrap view-mode" },
                            style: {
                                if edit_mode() {
                                    // Large fraction of the viewport so a short view-mode
                                    // diagram doesn't leave a tiny edit workspace. Inline
                                    // so it always overrides the previous view-mode height.
                                    "height: min(85vh, calc(100vh - 160px)); min-height: 70vh; max-height: none;".to_string()
                                } else if let Some(h) = view_wrap_height() {
                                    format!("height: {h}px; max-height: none;")
                                } else {
                                    "height: auto; max-height: none;".to_string()
                                }
                            },
                            onwheel: move |ev: Event<WheelData>| {
                                // Zoom with the scroll wheel (no modifier required).
                                ev.prevent_default();
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
                            onmousemove: move |ev: Event<MouseData>| {
                                let mut ix = interaction.write();
                                let Some(drag) = ix.drag.clone() else { return };
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
                                        let dx = cx - pointer_start.0;
                                        let dy = cy - pointer_start.1;
                                        let mut ms = local_matches();
                                        let mut ts = local_texts();
                                        let mut ls = local_labeled();
                                        let mut im = local_images();
                                        for m in ms.iter_mut() {
                                            let key = sel_match(&m.uuid);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                if let Some(p) = m.placement.as_mut() {
                                                    p.x_pos = Some((*ox + dx).max(0.0));
                                                    p.y_pos = Some((*oy + dy).max(0.0));
                                                }
                                            }
                                        }
                                        for t in ts.iter_mut() {
                                            let key = sel_text(&t.id);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                t.x_pos = (*ox + dx).max(0.0);
                                                t.y_pos = (*oy + dy).max(0.0);
                                            }
                                        }
                                        for t in ls.iter_mut() {
                                            let key = sel_labeled(&t.id);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                t.x_pos = (*ox + dx).max(0.0);
                                                t.y_pos = (*oy + dy).max(0.0);
                                            }
                                        }
                                        for i in im.iter_mut() {
                                            let key = sel_image(&i.id);
                                            if let Some((ox, oy)) = origins.get(&key) {
                                                i.x_pos = (*ox + dx).max(0.0);
                                                i.y_pos = (*oy + dy).max(0.0);
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
                            onmouseup: {
                                let u_up = tournament_url.clone();
                                move |_ev: Event<MouseData>| {
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
                            onmousedown: move |ev: Event<MouseData>| {
                                // Right-click (or middle-click) drag pans the canvas.
                                let btn = ev.trigger_button();
                                if matches!(btn, Some(MouseButton::Secondary) | Some(MouseButton::Auxiliary)) {
                                    ev.prevent_default();
                                    let (sx, sy) = screen_pointer(&ev);
                                    let mut ix = interaction.write();
                                    ix.drag = Some(DragKind::Pan {
                                        pointer_start: (sx, sy),
                                        pan_start: pan(),
                                    });
                                    return;
                                }
                                if !edit_mode() { return; }
                                let mods = ev.modifiers();
                                let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                let mut ix = interaction.write();
                                if !mods.shift() {
                                    ix.selected.clear();
                                }
                                ix.drag = Some(DragKind::Marquee {
                                    start: (cx, cy),
                                    current: (cx, cy),
                                });
                            },
                            // Suppress the browser context menu so right-drag can pan.
                            oncontextmenu: move |ev: Event<MouseData>| {
                                ev.prevent_default();
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
                                                            if !edit_mode() { return; }
                                                            ev.stop_propagation();
                                                            if is_lt {
                                                                let u = u.clone();
                                                                let id = tkey.clone();
                                                                saving.set(true);
                                                                spawn(async move {
                                                                    if let Ok(resp) = api::convert_labeled_team_port(&u, &id, "LABEL").await {
                                                                        apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                    }
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
                                                onmousedown: move |ev: Event<MouseData>| {
                                                    if !edit_mode() { return; }
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
                                                        onmousedown: move |ev: Event<MouseData>| {
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
                                                        onmousedown: move |ev: Event<MouseData>| {
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
                                                        onmousedown: move |ev: Event<MouseData>| {
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
                                                onmousedown: move |ev: Event<MouseData>| {
                                                    if !edit_mode() { return; }
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
                                                    if !edit_mode() { return; }
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
                                                                    saving.set(true);
                                                                    spawn(async move {
                                                                        if let Ok(resp) = api::convert_labeled_team_port(&u, &id, "NET").await {
                                                                            apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                        }
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
                                                onmousedown: move |ev: Event<MouseData>| {
                                                    if !edit_mode() { return; }
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
                                                    if !edit_mode() { return; }
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
                                        let show_w = used_outputs.contains(&(m.uuid.clone(), Qual::Winner));
                                        let show_l = used_outputs.contains(&(m.uuid.clone(), Qual::Loser));
                                        let label1 = if !is_net(&p.team1) { m.team1_initial.clone() } else { None };
                                        let label2 = if !is_net(&p.team2) { m.team2_initial.clone() } else { None };
                                        let uuid_down = m.uuid.clone();
                                        let uuid_resize = m.uuid.clone();
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
                                            if show_w {
                                                div { class: "bracket-port-stub output", style: "left: {x + w}px; top: {out_port_y(y, h, Qual::Winner)}px;" }
                                            }
                                            if show_l {
                                                div { class: "bracket-port-stub output loser", style: "left: {x + w}px; top: {out_port_y(y, h, Qual::Loser)}px;" }
                                            }

                                            div {
                                                key: "{m.uuid}",
                                                class: {
                                                    let mut c = String::from("bracket-match");
                                                    if selected { c.push_str(" selected"); }
                                                    if flipped { c.push_str(" inputs-flipped"); }
                                                    c.push_str(status_class);
                                                    c
                                                },
                                                style: format!("left: {x}px; top: {y}px; width: {w}px; height: {h}px; cursor: {};", if edit_mode() { "grab" } else { "default" }),
                                                onmousedown: move |ev: Event<MouseData>| {
                                                    if !edit_mode() { return; }
                                                    ev.stop_propagation();
                                                    let (cx, cy) = canvas_pointer(&ev, zoom(), pan());
                                                    let mut ix = interaction.write();
                                                    let id = sel_match(&uuid_down);
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
                                                    if show_w {
                                                        span { class: "bracket-port-badge winner", title: "Winner output", "W" }
                                                    }
                                                }
                                                div { class: "bracket-match-name", title: "{m.name}",
                                                    Link {
                                                        to: Route::MatchPageById { url: tournament_url.clone(), match_id: m.uuid.clone() },
                                                        class: "text-decoration-none text-dark",
                                                        onclick: move |ev: Event<MouseData>| {
                                                            if edit_mode() { ev.prevent_default(); ev.stop_propagation(); }
                                                        },
                                                        "{m.name}"
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
                                                    if show_l {
                                                        span { class: "bracket-port-badge loser", title: "Loser output", "L" }
                                                    }
                                                }
                                                if edit_mode() {
                                                    div {
                                                        class: "bracket-resize-handle corner",
                                                        onmousedown: move |ev: Event<MouseData>| {
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
                            }
                        }

                        } // end !show_legacy_fallback

                        // ---- Modals ----

                        if active_modal() == ActiveModal::AddMatch && edit_mode() {
                            div { class: "bracket-add-modal-backdrop",
                                onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                div { class: "bracket-add-modal",
                                    onclick: move |ev: Event<MouseData>| ev.stop_propagation(),
                                    div { class: "bracket-add-modal-header",
                                        h5 { class: "mb-0", "Add Match" }
                                        button { class: "btn-close", onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); } }
                                    }
                                    div { class: "bracket-add-modal-body",
                                        div { class: "px-3 pb-2",
                                            input {
                                                class: "form-control form-control-sm",
                                                r#type: "search",
                                                placeholder: "Filter matches…",
                                                value: "{add_q}",
                                                oninput: move |e| add_match_query.set(e.value()),
                                                onmounted: move |ev| {
                                                    spawn(async move { let _ = ev.data().set_focus(true).await; });
                                                },
                                            }
                                        }
                                        if filtered_unplaced.is_empty() {
                                            p { class: "text-muted px-3 py-2 mb-0",
                                                if unplaced.is_empty() { "All playable matches are already on the bracket." }
                                                else { "No matches match your filter." }
                                            }
                                        } else {
                                            for m in filtered_unplaced.iter() {
                                                {
                                                    let mid = m.uuid.clone();
                                                    let mname = m.name.clone();
                                                    let t1 = m.team1_name.clone();
                                                    let t2 = m.team2_name.clone();
                                                    rsx! {
                                                        button {
                                                            key: "{mid}",
                                                            class: "bracket-add-match-item",
                                                            onclick: {
                                                                let u = tournament_url.clone();
                                                                let mid = mid.clone();
                                                                move |_| {
                                                                    // Place in the current viewport at click time.
                                                                    let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                                                    let (nx, ny) = view_add_position(zoom(), pan(), stagger);
                                                                    active_modal.set(ActiveModal::None);
                                                                    saving.set(true);
                                                                    let u = u.clone();
                                                                    let mid = mid.clone();
                                                                    spawn(async move {
                                                                        if let Ok(resp) = api::add_bracket_placement(&u, &mid, nx, ny).await {
                                                                            apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                        }
                                                                        saving.set(false);
                                                                        focus_bracket_root();
                                                                    });
                                                                }
                                                            },
                                                            strong { "{mname}" }
                                                            div { class: "meta", "{t1} vs {t2}" }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    div { class: "bracket-add-modal-footer",
                                        button {
                                            class: "btn btn-outline-secondary btn-sm",
                                            onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                            "Close"
                                        }
                                    }
                                }
                            }
                        }

                        if let ActiveModal::EditText { id } = active_modal() {
                            {
                                let tid = id.clone();
                                rsx! {
                                    div { class: "bracket-add-modal-backdrop",
                                        onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                        div { class: "bracket-add-modal",
                                            onclick: move |ev: Event<MouseData>| ev.stop_propagation(),
                                            div { class: "bracket-add-modal-header",
                                                h5 { class: "mb-0", "Edit Text" }
                                                button { class: "btn-close", onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); } }
                                            }
                                            div { class: "bracket-add-modal-body px-3 py-2",
                                                label { class: "form-label", "Text" }
                                                textarea {
                                                    class: "form-control mb-2",
                                                    rows: "3",
                                                    value: "{text_draft}",
                                                    oninput: move |e| text_draft.set(e.value()),
                                                }
                                                label { class: "form-label", "Font size (px)" }
                                                input {
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
                                            div { class: "bracket-add-modal-footer",
                                                button {
                                                    class: "btn btn-primary btn-sm",
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
                                                    class: "btn btn-outline-secondary btn-sm",
                                                    onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                                    "Cancel"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if let ActiveModal::EditLabeledTeam { id } = active_modal() {
                            {
                                let lid = id.clone();
                                rsx! {
                                    div { class: "bracket-add-modal-backdrop",
                                        onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                        div { class: "bracket-add-modal",
                                            onclick: move |ev: Event<MouseData>| ev.stop_propagation(),
                                            div { class: "bracket-add-modal-header",
                                                h5 { class: "mb-0", "Edit Team Label" }
                                                button { class: "btn-close", onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); } }
                                            }
                                            div { class: "bracket-add-modal-body px-3 py-2",
                                                div { class: "mb-2",
                                                    label { class: "form-label", "Label" }
                                                    input {
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
                                                    wrapper_class: Some("mb-2 bracket-setup-team-token".to_string()),
                                                }
                                            }
                                            div { class: "bracket-add-modal-footer",
                                                button {
                                                    class: "btn btn-primary btn-sm",
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
                                                    class: "btn btn-outline-secondary btn-sm",
                                                    onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                                    "Cancel"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if active_modal() == ActiveModal::AddImage && edit_mode() {
                            div { class: "bracket-add-modal-backdrop",
                                onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                div { class: "bracket-add-modal",
                                    onclick: move |ev: Event<MouseData>| ev.stop_propagation(),
                                    div { class: "bracket-add-modal-header",
                                        h5 { class: "mb-0", "Add Image" }
                                        button { class: "btn-close", onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); } }
                                    }
                                    div { class: "bracket-add-modal-body px-3 py-2",
                                        p { class: "text-muted small", "Upload an image under 10 MB." }
                                        input {
                                            class: "form-control",
                                            r#type: "file",
                                            accept: "image/*",
                                            onchange: {
                                                let u = tournament_url.clone();
                                                move |evt| {
                                                    #[cfg(target_arch = "wasm32")]
                                                    {
                                                        use dioxus::html::HasFileData;
                                                        let files = evt.files();
                                                        if let Some(file) = files.into_iter().next() {
                                                            let u = u.clone();
                                                            let n = local_images().len() as f64;
                                                            let stagger = next_add_stagger(&local_matches(), &local_texts(), &local_labeled(), &local_images());
                                                            let (ax, ay) = view_add_position(zoom(), pan(), stagger);
                                                            active_modal.set(ActiveModal::None);
                                                            saving.set(true);
                                                            spawn(async move {
                                                                let filename = file.name();
                                                                match file.read_bytes().await {
                                                                    Ok(bytes) => {
                                                                        if bytes.len() > 10 * 1024 * 1024 {
                                                                            #[cfg(target_arch = "wasm32")]
                                                                            web_sys::console::error_1(&"Image must be under 10 MB".into());
                                                                            saving.set(false);
                                                                            return;
                                                                        }
                                                                        match api::upload_bracket_image_bytes(&u, n as u32, &filename, bytes).await {
                                                                            Ok(path) => {
                                                                                if let Ok(resp) = api::add_bracket_image_element(
                                                                                    &u, &path, ax, ay, 240.0, 160.0,
                                                                                ).await {
                                                                                    apply_response(resp, local_matches, local_texts, local_labeled, local_images, dirty, canvas_size);
                                                                                }
                                                                            }
                                                                            Err(e) => {
                                                                                #[cfg(target_arch = "wasm32")]
                                                                                web_sys::console::error_1(&e.into());
                                                                            }
                                                                        }
                                                                    }
                                                                    Err(_) => {}
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
                                    div { class: "bracket-add-modal-footer",
                                        button {
                                            class: "btn btn-outline-secondary btn-sm",
                                            onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                            "Close"
                                        }
                                    }
                                }
                            }
                        }

                        if active_modal() == ActiveModal::LegacyManage && is_to {
                            div { class: "bracket-add-modal-backdrop",
                                onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                div { class: "bracket-add-modal", style: "max-width: 560px;",
                                    onclick: move |ev: Event<MouseData>| ev.stop_propagation(),
                                    div { class: "bracket-add-modal-header",
                                        h5 { class: "mb-0", "Legacy brackets" }
                                        button {
                                            class: "btn-close",
                                            onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); }
                                        }
                                    }
                                    div { class: "bracket-add-modal-body px-3 py-2",
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
                                                                class: "list-group-item d-flex align-items-center justify-content-between gap-2 px-0",
                                                                key: "{idx_u}-{bname}",
                                                                div { class: "d-flex align-items-center gap-2 min-w-0",
                                                                    img {
                                                                        src: "{backend}/static/{bimg}",
                                                                        alt: "{bname}",
                                                                        style: "width: 64px; height: 40px; object-fit: cover; border-radius: 4px; background: #eee;"
                                                                    }
                                                                    div { class: "text-truncate",
                                                                        strong { "{bname}" }
                                                                        div { class: "small text-muted text-truncate", "{bimg}" }
                                                                    }
                                                                }
                                                                div { class: "d-flex gap-1 flex-shrink-0",
                                                                    Link {
                                                                        to: Route::LegacyBracket { url: tournament_url.clone() },
                                                                        class: "btn btn-sm btn-outline-primary",
                                                                        "View"
                                                                    }
                                                                    button {
                                                                        class: "btn btn-sm btn-outline-danger",
                                                                        disabled: saving(),
                                                                        onclick: move |_| {
                                                                            let u = u.clone();
                                                                            saving.set(true);
                                                                            spawn(async move {
                                                                                match api::delete_legacy_bracket(&u, idx_u).await {
                                                                                    Ok(resp) => {
                                                                                        legacy_brackets.set(resp.legacy_brackets.clone());
                                                                                        if resp.legacy_brackets.is_empty() {
                                                                                            active_modal.set(ActiveModal::None);
                                                                                        }
                                                                                    }
                                                                                    Err(e) => {
                                                                                        #[cfg(target_arch = "wasm32")]
                                                                                        web_sys::console::error_1(&e.into());
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
                                    div { class: "bracket-add-modal-footer",
                                        if !legacy_snap.is_empty() {
                                            button {
                                                class: "btn btn-sm btn-danger me-auto",
                                                disabled: saving(),
                                                onclick: {
                                                    let u = tournament_url.clone();
                                                    move |_| {
                                                        let u = u.clone();
                                                        saving.set(true);
                                                        spawn(async move {
                                                            match api::clear_legacy_brackets(&u).await {
                                                                Ok(resp) => {
                                                                    legacy_brackets.set(resp.legacy_brackets.clone());
                                                                    active_modal.set(ActiveModal::None);
                                                                }
                                                                Err(e) => {
                                                                    #[cfg(target_arch = "wasm32")]
                                                                    web_sys::console::error_1(&e.into());
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
                                            class: "btn btn-outline-secondary btn-sm",
                                            onclick: move |_| { active_modal.set(ActiveModal::None); focus_bracket_root(); },
                                            "Close"
                                        }
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
                    h1 { "Bracket" }
                    Link { to: Route::TournamentHome { url: url.clone() }, class: "btn btn-outline-secondary mb-3", "Back to Tournament" }
                    p { class: "text-danger", "{e}" }
                }
            }
        } else {
            p { "Loading…" }
        }
    }
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
            origins.insert(k, (t.x_pos, t.y_pos));
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
    on_click: EventHandler<Event<MouseData>>,
) -> Element {
    let class = if editable {
        "bracket-net-label editable"
    } else {
        "bracket-net-label"
    };
    rsx! {
        div {
            class: "{class}",
            style: "left: {x}px; top: {y}px;",
            title: if editable { "Click to convert to wire" } else { "" },
            onclick: move |ev: Event<MouseData>| {
                ev.stop_propagation();
                on_click.call(ev);
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
                    LabelKind::Raw(s) => rsx! { span { class: "token-text", "{s}" } },
                }
            }
        }
    }
}
