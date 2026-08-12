//! Team/referee token input with autocomplete and inline chips (Gmail-style).
//! Supports: team pseudonym/id, MatchName::winner, MatchName::loser, tag::TagName.

use crate::api;
use crate::types::*;
use dioxus::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast as _;

#[derive(Clone, Debug, PartialEq)]
pub enum TokenKind {
    Team,
    Tag,
    Winner,
    Loser,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Token {
    pub kind: TokenKind,
    pub display: String,
    pub value: String,
}

fn parse_value_into_tokens(
    value: &str,
    multiple: bool,
    team_options: &[TeamOption],
    _tags: &[TagSetupData],
    _matches: &[MatchSetupData],
) -> Vec<Token> {
    let segments: Vec<&str> = if multiple {
        value.split(',').map(str::trim).filter(|s| !s.is_empty()).collect()
    } else if value.trim().is_empty() {
        return vec![];
    } else {
        vec![value.trim()]
    };
    let mut tokens = Vec::with_capacity(segments.len());
    for seg in segments {
        if seg.ends_with("::winner") {
            let name = seg.strip_suffix("::winner").unwrap_or(seg).trim();
            tokens.push(Token {
                kind: TokenKind::Winner,
                display: name.to_string(),
                value: seg.to_string(),
            });
        } else if seg.ends_with("::loser") {
            let name = seg.strip_suffix("::loser").unwrap_or(seg).trim();
            tokens.push(Token {
                kind: TokenKind::Loser,
                display: name.to_string(),
                value: seg.to_string(),
            });
        } else if seg.to_lowercase().starts_with("tag::") {
            let name = seg.get(5..).unwrap_or("").trim();
            tokens.push(Token {
                kind: TokenKind::Tag,
                display: name.to_string(),
                value: seg.to_string(),
            });
        } else {
            let team_id = team_options
                .iter()
                .find(|t| t.id == seg || t.pseudonym.as_deref() == Some(seg))
                .map(|t| t.id.clone())
                .unwrap_or_else(|| seg.to_string());
            let display = team_options
                .iter()
                .find(|t| t.id == team_id)
                .and_then(|t| t.pseudonym.clone())
                .map(|p| format!("{p} ({})", team_id))
                .unwrap_or_else(|| team_id.clone());
            tokens.push(Token {
                kind: TokenKind::Team,
                display,
                value: team_id,
            });
        }
    }
    tokens
}

fn chip_class_suffix(t: &Token) -> &'static str {
    match &t.kind {
        TokenKind::Team => "team",
        TokenKind::Tag => "tag",
        TokenKind::Winner => "winner",
        TokenKind::Loser => "loser",
    }
}

fn chip_display_and_suffix(t: &Token) -> (String, &'static str) {
    (t.display.clone(), chip_class_suffix(t))
}

/// Resolved team info for tag/winner/loser tokens when they resolve to a known team.
#[derive(Clone, Debug)]
struct ResolvedTeam {
    pub profile_photo: Option<String>,
    pub display: String,
}

fn resolve_token_to_team(
    token: &Token,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> Option<ResolvedTeam> {
    match &token.kind {
        TokenKind::Tag => {
            let name = token.value.strip_prefix("tag::")?.trim();
            let tag = tags.iter().find(|t| t.name.eq_ignore_ascii_case(name))?;
            let team_id = tag.team.as_ref()?;
            let team = team_options.iter().find(|t| t.id == *team_id)?;
            let display = team
                .pseudonym
                .clone()
                .map(|p| format!("{p} ({})", team.id))
                .unwrap_or_else(|| team.id.clone());
            Some(ResolvedTeam {
                profile_photo: team.profile_photo.clone(),
                display,
            })
        }
        TokenKind::Winner => {
            let name = token.value.strip_suffix("::winner")?.trim();
            let m = matches.iter().find(|m| m.name.eq_ignore_ascii_case(name))?;
            if !m.status.eq_ignore_ascii_case("COMPLETED") {
                return None;
            }
            let team_id = m.match_winner.as_ref().and_then(|side| {
                if side.eq_ignore_ascii_case("TEAM1") {
                    m.team1.clone()
                } else if side.eq_ignore_ascii_case("TEAM2") {
                    m.team2.clone()
                } else {
                    None
                }
            })?;
            let team = team_options.iter().find(|t| t.id == *team_id)?;
            let display = team
                .pseudonym
                .clone()
                .map(|p| format!("{p} ({})", team.id))
                .unwrap_or_else(|| team.id.clone());
            Some(ResolvedTeam {
                profile_photo: team.profile_photo.clone(),
                display,
            })
        }
        TokenKind::Loser => {
            let name = token.value.strip_suffix("::loser")?.trim();
            let m = matches.iter().find(|m| m.name.eq_ignore_ascii_case(name))?;
            if !m.status.eq_ignore_ascii_case("COMPLETED") {
                return None;
            }
            let loser_id = m.match_winner.as_ref().and_then(|side| {
                if side.eq_ignore_ascii_case("TEAM1") {
                    m.team2.clone()
                } else if side.eq_ignore_ascii_case("TEAM2") {
                    m.team1.clone()
                } else {
                    None
                }
            })?;
            let team = team_options.iter().find(|t| t.id == loser_id)?;
            let display = team
                .pseudonym
                .clone()
                .map(|p| format!("{p} ({})", team.id))
                .unwrap_or_else(|| team.id.clone());
            Some(ResolvedTeam {
                profile_photo: team.profile_photo.clone(),
                display,
            })
        }
        TokenKind::Team => None,
    }
}

/// Resolves a value string (which may contain team ids, tag::Name, or Match::winner/loser) into
/// a string of team IDs only (comma-separated when multiple is true). Returns None if any token
/// cannot be resolved to a team ID.
pub fn resolve_value_to_team_ids(
    value: &str,
    multiple: bool,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> Option<String> {
    let tokens = parse_value_into_tokens(value, multiple, team_options, tags, matches);
    let mut ids = Vec::with_capacity(tokens.len());
    for t in &tokens {
        let id = match &t.kind {
            TokenKind::Team => t.value.clone(),
            TokenKind::Tag => {
                let name = t.value.strip_prefix("tag::")?.trim();
                let tag = tags.iter().find(|x| x.name.eq_ignore_ascii_case(name))?;
                tag.team.clone()?
            }
            TokenKind::Winner => {
                let name = t.value.strip_suffix("::winner")?.trim();
                let m = matches.iter().find(|m| m.name.eq_ignore_ascii_case(name))?;
                if !m.status.eq_ignore_ascii_case("COMPLETED") {
                    return None;
                }
                m.match_winner.as_ref().and_then(|side| {
                    if side.eq_ignore_ascii_case("TEAM1") {
                        m.team1.clone()
                    } else if side.eq_ignore_ascii_case("TEAM2") {
                        m.team2.clone()
                    } else {
                        None
                    }
                })?
            }
            TokenKind::Loser => {
                let name = t.value.strip_suffix("::loser")?.trim();
                let m = matches.iter().find(|m| m.name.eq_ignore_ascii_case(name))?;
                if !m.status.eq_ignore_ascii_case("COMPLETED") {
                    return None;
                }
                m.match_winner.as_ref().and_then(|side| {
                    if side.eq_ignore_ascii_case("TEAM1") {
                        m.team2.clone()
                    } else if side.eq_ignore_ascii_case("TEAM2") {
                        m.team1.clone()
                    } else {
                        None
                    }
                })?
            }
        };
        ids.push(id);
    }
    if multiple {
        Some(ids.join(", "))
    } else {
        ids.into_iter().next()
    }
}

/// Returns true if every token in `value` is a known team, tag (with team set), or completed match reference.
/// Tags without a team and winner/loser refs for non-COMPLETED matches are considered unknown.
pub fn all_tokens_known(
    value: &str,
    multiple: bool,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> bool {
    let tokens = parse_value_into_tokens(value, multiple, team_options, tags, matches);
    for t in &tokens {
        let known = match &t.kind {
            TokenKind::Team => team_options.iter().any(|o| o.id == t.value),
            TokenKind::Tag => tags.iter().any(|tag| {
                format!("tag::{}", tag.name).eq_ignore_ascii_case(&t.value) && tag.team.is_some()
            }),
            TokenKind::Winner | TokenKind::Loser => matches.iter().any(|m| {
                m.status.eq_ignore_ascii_case("COMPLETED")
                    && (t.value.eq_ignore_ascii_case(&format!("{}::winner", m.name))
                        || t.value.eq_ignore_ascii_case(&format!("{}::loser", m.name)))
            }),
        };
        if !known {
            #[cfg(target_arch = "wasm32")]
            web_sys::console::log_1(
                &format!("[all_tokens_known] unknown token kind={:?} value={:?}", t.kind, t.value).into(),
            );
            return false;
        }
    }
    true
}

fn opt_parts(o: &AutocompleteOption) -> (TokenKind, String, String) {
    (o.kind.clone(), o.display.clone(), o.value.clone())
}

fn tokens_to_value(tokens: &[Token], multiple: bool) -> String {
    if multiple {
        tokens.iter().map(|t| t.value.as_str()).collect::<Vec<_>>().join(", ")
    } else {
        tokens.first().map(|t| t.value.clone()).unwrap_or_default()
    }
}

/// Merge pending text into the field value and return normalized token string (if any change).
fn commit_pending_text(
    pending: &str,
    current_value: &str,
    multiple: bool,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> Option<String> {
    let pending = pending.trim();
    if pending.is_empty() {
        return None;
    }
    let combined = if multiple {
        let base = current_value.trim();
        if base.is_empty() {
            pending.to_string()
        } else {
            format!("{},{}", base, pending)
        }
    } else {
        pending.to_string()
    };
    let tokens = parse_value_into_tokens(&combined, multiple, team_options, tags, matches);
    let new_val = tokens_to_value(&tokens, multiple);
    if new_val == current_value.trim() {
        None
    } else {
        Some(new_val)
    }
}

#[derive(Clone, Debug)]
pub struct AutocompleteOption {
    pub kind: TokenKind,
    pub display: String,
    pub value: String,
    /// When present, show " → avatar Team" in the dropdown (for tags/winner/loser).
    pub resolved: Option<ResolvedTeam>,
}

fn collect_autocomplete(
    query: &str,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> Vec<AutocompleteOption> {
    let q = query.trim().to_lowercase();
    let mut out = Vec::new();
    if q.is_empty() {
        for t in team_options.iter().take(15) {
            let display = t
                .pseudonym
                .clone()
                .map(|p| format!("{p} ({})", t.id))
                .unwrap_or_else(|| t.id.clone());
            out.push(AutocompleteOption {
                kind: TokenKind::Team,
                display,
                value: t.id.clone(),
                resolved: None,
            });
        }
        for tag in tags.iter().take(5) {
            let value = format!("tag::{}", tag.name);
            let token = Token {
                kind: TokenKind::Tag,
                display: tag.name.clone(),
                value: value.clone(),
            };
            let resolved = resolve_token_to_team(&token, team_options, tags, matches);
            out.push(AutocompleteOption {
                kind: TokenKind::Tag,
                display: tag.name.clone(),
                value,
                resolved,
            });
        }
        for m in matches.iter().take(10) {
            let winner_value = format!("{}::winner", m.name);
            let winner_token = Token {
                kind: TokenKind::Winner,
                display: format!("{} winner", m.name),
                value: winner_value.clone(),
            };
            let loser_value = format!("{}::loser", m.name);
            let loser_token = Token {
                kind: TokenKind::Loser,
                display: format!("{} loser", m.name),
                value: loser_value.clone(),
            };
            out.push(AutocompleteOption {
                kind: TokenKind::Winner,
                display: format!("{} winner", m.name),
                value: winner_value,
                resolved: resolve_token_to_team(&winner_token, team_options, tags, matches),
            });
            out.push(AutocompleteOption {
                kind: TokenKind::Loser,
                display: format!("{} loser", m.name),
                value: loser_value,
                resolved: resolve_token_to_team(&loser_token, team_options, tags, matches),
            });
        }
        return out;
    }
    for t in team_options.iter() {
        let id_lower = t.id.to_lowercase();
        let pseudo_lower = t.pseudonym.as_deref().unwrap_or("").to_lowercase();
        if id_lower.contains(&q) || pseudo_lower.contains(&q) {
            let display = t
                .pseudonym
                .clone()
                .map(|p| format!("{p} ({})", t.id))
                .unwrap_or_else(|| t.id.clone());
            out.push(AutocompleteOption {
                kind: TokenKind::Team,
                display,
                value: t.id.clone(),
                resolved: None,
            });
        }
    }
    for tag in tags.iter() {
        if tag.name.to_lowercase().contains(&q) {
            let value = format!("tag::{}", tag.name);
            let token = Token {
                kind: TokenKind::Tag,
                display: tag.name.clone(),
                value: value.clone(),
            };
            let resolved = resolve_token_to_team(&token, team_options, tags, matches);
            out.push(AutocompleteOption {
                kind: TokenKind::Tag,
                display: tag.name.clone(),
                value,
                resolved,
            });
        }
    }
    for m in matches.iter() {
        if format!("{} winner", m.name.to_lowercase()).contains(&q) {
            let winner_value = format!("{}::winner", m.name);
            let winner_token = Token {
                kind: TokenKind::Winner,
                display: format!("{} winner", m.name),
                value: winner_value.clone(),
            };
            out.push(AutocompleteOption {
                kind: TokenKind::Winner,
                display: format!("{} winner", m.name),
                value: winner_value,
                resolved: resolve_token_to_team(&winner_token, team_options, tags, matches),
            });
        }
        if format!("{} loser", m.name.to_lowercase()).contains(&q) {
            let loser_value = format!("{}::loser", m.name);
            let loser_token = Token {
                kind: TokenKind::Loser,
                display: format!("{} loser", m.name),
                value: loser_value.clone(),
            };
            out.push(AutocompleteOption {
                kind: TokenKind::Loser,
                display: format!("{} loser", m.name),
                value: loser_value,
                resolved: resolve_token_to_team(&loser_token, team_options, tags, matches),
            });
        }
    }
    out
}

#[component]
pub fn TeamTokenInput(
    team_options: Vec<TeamOption>,
    tags: Vec<TagSetupData>,
    matches: Vec<MatchSetupData>,
    value: String,
    on_change: EventHandler<String>,
    multiple: bool,
    placeholder: String,
) -> Element {
    let mut input_text = use_signal(|| String::new());
    let mut show_autocomplete = use_signal(|| false);
    let mut ac_pos = use_signal(|| 0usize);
    let mut focused_chip = use_signal(|| None::<usize>);
    
    // Generate a unique ID for this component instance (generated once)
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let container_id = use_memo(move || {
        let id = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        format!("team-token-container-{}", id)
    });

    let value_rc = Rc::new(value.clone());
    let team_options_rc = Rc::new(team_options);
    let tags_rc = Rc::new(tags);
    let matches_rc = Rc::new(matches);
    let base_url = api::base_url();

    let tokens = parse_value_into_tokens(
        value_rc.as_ref(),
        multiple,
        team_options_rc.as_ref(),
        tags_rc.as_ref(),
        matches_rc.as_ref(),
    );
    let options = collect_autocomplete(
        input_text().as_str(),
        team_options_rc.as_ref(),
        tags_rc.as_ref(),
        matches_rc.as_ref(),
    );
    let options_len = options.len();
    let current_ac_pos = ac_pos();
    let tokens_empty = tokens.is_empty();
    let tokens_len = tokens.len();

    let on_change_remove = on_change.clone();

    let value_rc_add = value_rc.clone();
    let value_rc2 = value_rc.clone();
    let team_options_rc2 = team_options_rc.clone();
    let team_options_rc3 = team_options_rc.clone();
    let tags_rc2 = tags_rc.clone();
    let matches_rc2 = matches_rc.clone();
    let tags_rc_add = tags_rc.clone();
    let matches_rc_add = matches_rc.clone();
    let add_token_rc: Rc<RefCell<Box<dyn FnMut(Token)>>> = Rc::new(RefCell::new(Box::new(move |t: Token| {
        let mut new_tokens = parse_value_into_tokens(
            value_rc_add.as_ref(),
            multiple,
            team_options_rc3.as_ref(),
            tags_rc_add.as_ref(),
            matches_rc_add.as_ref(),
        );
        if multiple {
            new_tokens.push(t);
        } else {
            new_tokens = vec![t];
        }
        on_change.call(tokens_to_value(&new_tokens, multiple));
        input_text.set(String::new());
        show_autocomplete.set(false);
        ac_pos.set(0);
    })));

    let remove_token_rc: Rc<RefCell<Box<dyn FnMut(usize)>>> = Rc::new(RefCell::new(Box::new(move |idx: usize| {
        let mut new_tokens = parse_value_into_tokens(
            value_rc2.as_ref(),
            multiple,
            team_options_rc2.as_ref(),
            tags_rc2.as_ref(),
            matches_rc2.as_ref(),
        );
        if idx < new_tokens.len() {
            new_tokens.remove(idx);
            on_change_remove.call(tokens_to_value(&new_tokens, multiple));
        }
        focused_chip.set(None);
    })));

    let mut input_text_blur = input_text.clone();
    let value_for_commit = value_rc.clone();
    let on_change_blur = on_change.clone();
    let toc_blur = team_options_rc.clone();
    let tgc_blur = tags_rc.clone();
    let matc_blur = matches_rc.clone();
    let mult_blur = multiple;
    let commit_blur_rc: Rc<RefCell<Box<dyn FnMut()>>> = Rc::new(RefCell::new(Box::new(move || {
        let pending = input_text_blur();
        if pending.trim().is_empty() {
            input_text_blur.set(String::new());
            return;
        }
        if let Some(new_val) = commit_pending_text(
            pending.as_str(),
            value_for_commit.as_ref(),
            mult_blur,
            toc_blur.as_ref(),
            tgc_blur.as_ref(),
            matc_blur.as_ref(),
        ) {
            on_change_blur.call(new_val);
        }
        input_text_blur.set(String::new());
    })));

    rsx! {
        div {
            id: "{container_id()}",
            class: "team-token-input form-control",
            role: "combobox",
            "aria-expanded": "{show_autocomplete()}",
            tabindex: 0,
            onfocus: move |_| {
                show_autocomplete.set(true);
                // Focus the inner input field when the outer div receives focus
                let container_id_local = container_id();
                #[cfg(target_arch = "wasm32")]
                {
                    spawn(async move {
                        gloo_timers::future::TimeoutFuture::new(0).await;
                        if let Some(window) = web_sys::window() {
                            if let Some(doc) = window.document() {
                                if let Ok(Some(container)) = doc.query_selector(&format!("#{}", container_id_local)) {
                                    if let Ok(Some(input_el)) = container.query_selector(".team-token-input-field") {
                                        if let Ok(input) = input_el.dyn_into::<web_sys::HtmlInputElement>() {
                                            let _ = input.focus();
                                        }
                                    }
                                }
                            }
                        }
                    });
                }
            },
            onkeydown: move |ev: Event<KeyboardData>| {
                let key = ev.key().to_string();
                if show_autocomplete() && options_len > 0 {
                    if key == "ArrowDown" {
                        ev.prevent_default();
                        ac_pos.set((ac_pos() + 1) % options_len);
                        return;
                    }
                    if key == "ArrowUp" {
                        ev.prevent_default();
                        ac_pos.set(ac_pos().saturating_sub(1).max(0));
                        return;
                    }
                    if key == "Enter" {
                        ev.prevent_default();
                        if let Some(opt) = options.get(ac_pos()) {
                            add_token_rc.borrow_mut()(Token {
                                kind: opt.kind.clone(),
                                display: opt.display.clone(),
                                value: opt.value.clone(),
                            });
                        }
                        return;
                    }
                    if key == "Escape" {
                        ev.prevent_default();
                        show_autocomplete.set(false);
                        return;
                    }
                }
                if key == "Backspace" && input_text().is_empty() && !tokens_empty {
                    ev.prevent_default();
                    if let Some(focus) = focused_chip() {
                        remove_token_rc.borrow_mut()(focus);
                    } else {
                        remove_token_rc.borrow_mut()(tokens_len - 1);
                    }
                    return;
                }
                if key == "ArrowLeft" && input_text().is_empty() && !tokens_empty {
                    ev.prevent_default();
                    let next = focused_chip()
                        .map(|i| i.saturating_sub(1))
                        .or(Some(tokens_len - 1));
                    if next == Some(0) {
                        focused_chip.set(Some(0));
                    } else if next.unwrap_or(0) > 0 {
                        focused_chip.set(next);
                    }
                    return;
                }
                if key == "ArrowRight" {
                    ev.prevent_default();
                    if let Some(focus) = focused_chip() {
                        if focus + 1 >= tokens_len {
                            focused_chip.set(None);
                        } else {
                            focused_chip.set(Some(focus + 1));
                        }
                    }
                    return;
                }
            },
            onblur: move |_| {
                #[cfg(target_arch = "wasm32")]
                {
                    let mut show_ac = show_autocomplete.clone();
                    spawn(async move {
                        gloo_timers::future::TimeoutFuture::new(150).await;
                        show_ac.set(false);
                    });
                }
                #[cfg(not(target_arch = "wasm32"))]
                show_autocomplete.set(false);
            },

            div { class: "team-token-input-inner",
                for (idx, entry) in tokens.iter().enumerate() {
                    {
                        let remove_rc = remove_token_rc.clone();
                        let remove_rc2 = remove_token_rc.clone();
                        let pair = chip_display_and_suffix(entry);
                        let chip_label = pair.0;
                        let chip_suffix = pair.1;
                        let is_focused = focused_chip() == Some(idx);
                        let chip_class = format!(
                            "team-token-chip {} team-token-chip-{}",
                            if is_focused { "team-token-chip-focused " } else { "" },
                            chip_suffix
                        );
                        let team_photo = team_options_rc
                            .iter()
                            .find(|t| t.id == entry.value)
                            .and_then(|t| t.profile_photo.clone());
                        let team_content = if let Some(photo) = &team_photo {
                            rsx! {
                                img {
                                    src: "{base_url}/static/{photo}",
                                    alt: "{chip_label}",
                                    class: "team-token-avatar rounded-circle",
                                    style: "width: 1.5em; height: 1.5em; object-fit: cover;"
                                }
                                span { class: "team-token-label", "{chip_label}" }
                            }
                        } else {
                            rsx! {
                                span { class: "team-token-avatar", "{chip_label.chars().next().unwrap_or('?')}" }
                                span { class: "team-token-label", "{chip_label}" }
                            }
                        };
                        let tag_content = rsx! {
                            img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "Tag" }
                            span { class: "team-token-label", "{chip_label}" }
                            if let Some(resolved) = resolve_token_to_team(entry, team_options_rc.as_ref(), tags_rc.as_ref(), matches_rc.as_ref()) {
                                span { class: "team-token-resolved text-muted ms-1",
                                    " → "
                                    if let Some(photo) = &resolved.profile_photo {
                                        img {
                                            src: "{base_url}/static/{photo}",
                                            alt: "",
                                            class: "team-token-avatar small rounded-circle ms-1",
                                            style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                        }
                                    } else {
                                        span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{resolved.display.chars().next().unwrap_or('?')}" }
                                    }
                                    span { "{resolved.display}" }
                                }
                            }
                        };
                        let winner_content = rsx! {
                            img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" }
                            span { class: "team-token-label", "{chip_label} winner" }
                            if let Some(resolved) = resolve_token_to_team(entry, team_options_rc.as_ref(), tags_rc.as_ref(), matches_rc.as_ref()) {
                                span { class: "team-token-resolved text-muted ms-1",
                                    " → "
                                    if let Some(photo) = &resolved.profile_photo {
                                        img {
                                            src: "{base_url}/static/{photo}",
                                            alt: "",
                                            class: "team-token-avatar small rounded-circle ms-1",
                                            style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                        }
                                    } else {
                                        span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{resolved.display.chars().next().unwrap_or('?')}" }
                                    }
                                    span { "{resolved.display}" }
                                }
                            }
                        };
                        let loser_content = rsx! {
                            img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" }
                            span { class: "team-token-label", "{chip_label} loser" }
                            if let Some(resolved) = resolve_token_to_team(entry, team_options_rc.as_ref(), tags_rc.as_ref(), matches_rc.as_ref()) {
                                span { class: "team-token-resolved text-muted ms-1",
                                    " → "
                                    if let Some(photo) = &resolved.profile_photo {
                                        img {
                                            src: "{base_url}/static/{photo}",
                                            alt: "",
                                            class: "team-token-avatar small rounded-circle ms-1",
                                            style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                        }
                                    } else {
                                        span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{resolved.display.chars().next().unwrap_or('?')}" }
                                    }
                                    span { "{resolved.display}" }
                                }
                            }
                        };
                        let chip_inner = if chip_suffix == "team" { team_content } else if chip_suffix == "tag" { tag_content } else if chip_suffix == "winner" { winner_content } else { loser_content };
                        rsx! {
                    div {
                        class: "{chip_class}",
                        key: "{idx}-{chip_label}",
                        tabindex: 0,
                        onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); focused_chip.set(Some(idx)); },
                        onblur: move |_| {
                            #[cfg(target_arch = "wasm32")]
                            {
                                let mut show_ac = show_autocomplete.clone();
                                spawn(async move {
                                    gloo_timers::future::TimeoutFuture::new(150).await;
                                    show_ac.set(false);
                                });
                            }
                            #[cfg(not(target_arch = "wasm32"))]
                            show_autocomplete.set(false);
                        },
                        onkeydown: move |ev: Event<KeyboardData>| {
                            if ev.key().to_string() == "Backspace" {
                                ev.prevent_default();
                                remove_rc.borrow_mut()(idx);
                            }
                        },
                        {chip_inner}
                        button {
                            class: "team-token-remove",
                            "type": "button",
                            "aria-label": "Remove",
                            onclick: move |ev: Event<MouseData>| { ev.stop_propagation(); remove_rc2.borrow_mut()(idx); },
                            "×"
                        }
                    }
                        }
                    }
                }
                input {
                    class: "team-token-input-field",
                    "type": "text",
                    placeholder: if tokens_empty { placeholder.clone() } else { "".to_string() },
                    value: "{input_text}",
                    oninput: move |e| {
                        input_text.set(e.value());
                        show_autocomplete.set(true);
                        ac_pos.set(0);
                        focused_chip.set(None);
                    },
                    onfocus: move |_| {
                        focused_chip.set(None);
                        show_autocomplete.set(true);
                    },
                    onblur: move |_| {
                        commit_blur_rc.borrow_mut()();
                        #[cfg(target_arch = "wasm32")]
                        {
                            let mut show_ac = show_autocomplete.clone();
                            spawn(async move {
                                gloo_timers::future::TimeoutFuture::new(150).await;
                                show_ac.set(false);
                            });
                        }
                        #[cfg(not(target_arch = "wasm32"))]
                        show_autocomplete.set(false);
                    },
                }
            }

            if show_autocomplete() && !options.is_empty() {
                ul { class: "team-token-autocomplete dropdown-menu show",
                    for (idx, opt) in options.iter().enumerate() {
                        {
                            let add_rc = add_token_rc.clone();
                            let parts = opt_parts(opt);
                            let opt_kind = parts.0;
                            let opt_display = parts.1;
                            let opt_value = parts.2;
                            let is_cur = current_ac_pos == idx;
                            let opt_suffix = match &opt_kind {
                                TokenKind::Team => "team",
                                TokenKind::Tag => "tag",
                                TokenKind::Winner => "winner",
                                TokenKind::Loser => "loser",
                            };
                            let item_class = format!("dropdown-item {}", if is_cur { "active" } else { "" });
                            let opt_team_photo = team_options_rc
                                .iter()
                                .find(|t| t.id == opt_value)
                                .and_then(|t| t.profile_photo.clone());
                            let opt_team = if let Some(photo) = &opt_team_photo {
                                rsx! {
                                    img {
                                        src: "{base_url}/static/{photo}",
                                        alt: "{opt_display}",
                                        class: "team-token-avatar small me-1 rounded-circle",
                                        style: "width: 1.5em; height: 1.5em; object-fit: cover;"
                                    }
                                    span { "{opt_display}" }
                                }
                            } else {
                                rsx! {
                                    span { class: "team-token-avatar small me-1", "{opt_display.chars().next().unwrap_or('?')}" }
                                    span { "{opt_display}" }
                                }
                            };
                            let opt_resolved = opt.resolved.clone();
                            let opt_tag = rsx! {
                                img { class: "icon-primary-svg me-1", src: "{base_url}/static/tag.svg", alt: "Tag" }
                                span { "{opt_display}" }
                                if let Some(ref resolved) = opt_resolved {
                                    span { class: "text-muted ms-1",
                                        " → "
                                        if let Some(photo) = &resolved.profile_photo {
                                            img {
                                                src: "{base_url}/static/{photo}",
                                                alt: "",
                                                class: "team-token-avatar small rounded-circle ms-1",
                                                style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                            }
                                        } else {
                                            span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{resolved.display.chars().next().unwrap_or('?')}" }
                                        }
                                        span { "{resolved.display}" }
                                    }
                                }
                            };
                            let opt_winner = rsx! {
                                img { class: "icon-primary-svg me-1", src: "{base_url}/static/reference.svg", alt: "Reference" }
                                span { "{opt_display}" }
                                if let Some(ref resolved) = opt_resolved {
                                    span { class: "text-muted ms-1",
                                        " → "
                                        if let Some(photo) = &resolved.profile_photo {
                                            img {
                                                src: "{base_url}/static/{photo}",
                                                alt: "",
                                                class: "team-token-avatar small rounded-circle ms-1",
                                                style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                            }
                                        } else {
                                            span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{resolved.display.chars().next().unwrap_or('?')}" }
                                        }
                                        span { "{resolved.display}" }
                                    }
                                }
                            };
                            let opt_loser = rsx! {
                                img { class: "icon-primary-svg me-1", src: "{base_url}/static/reference.svg", alt: "Reference" }
                                span { "{opt_display}" }
                                if let Some(ref resolved) = opt_resolved {
                                    span { class: "text-muted ms-1",
                                        " → "
                                        if let Some(photo) = &resolved.profile_photo {
                                            img {
                                                src: "{base_url}/static/{photo}",
                                                alt: "",
                                                class: "team-token-avatar small rounded-circle ms-1",
                                                style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                            }
                                        } else {
                                            span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{resolved.display.chars().next().unwrap_or('?')}" }
                                        }
                                        span { "{resolved.display}" }
                                    }
                                }
                            };
                            let opt_inner = if opt_suffix == "team" { opt_team } else if opt_suffix == "tag" { opt_tag } else if opt_suffix == "winner" { opt_winner } else { opt_loser };
                            rsx! {
                                li {
                                    key: "{idx}-{opt_value}",
                                    class: "{item_class}",
                                    role: "option",
                                    "aria-selected": "{is_cur}",
                                    onmousedown: move |ev: Event<MouseData>| {
                                        ev.prevent_default();
                                        ev.stop_propagation();
                                        add_rc.borrow_mut()(Token { kind: opt_kind.clone(), display: opt_display.clone(), value: opt_value.clone() });
                                    },
                                    onmouseenter: move |_| { ac_pos.set(idx); },
                                    {opt_inner}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/// Form field that wraps TeamTokenInput with label and optional help text.
/// Use for team 1, team 2, referees, or any single/multi token selection with a consistent look.
#[component]
pub fn TeamSelectionField(
    label: String,
    team_options: Vec<TeamOption>,
    tags: Vec<TagSetupData>,
    matches: Vec<MatchSetupData>,
    value: String,
    on_change: EventHandler<String>,
    multiple: bool,
    placeholder: String,
    #[props(optional)] help_text: Option<String>,
    /// Default "mb-3". Use "mb-2" for tighter spacing (e.g. ref slots).
    #[props(optional)] wrapper_class: Option<String>,
    /// Default "form-label". Use "form-label small" for smaller labels.
    #[props(optional)] label_class: Option<String>,
) -> Element {
    let wrapper = wrapper_class.as_deref().unwrap_or("mb-3");
    let label_cls = label_class.as_deref().unwrap_or("form-label");

    rsx! {
        div { class: "{wrapper}",
            label { class: "{label_cls}", "{label}" }
            TeamTokenInput {
                team_options,
                tags,
                matches,
                value,
                on_change,
                multiple,
                placeholder,
            }
            if let Some(help) = &help_text {
                div { class: "form-text", "{help}" }
            }
        }
    }
}
