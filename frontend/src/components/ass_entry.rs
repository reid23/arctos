//! ASS (Arctos Schedule Script) expression editor.
//!
//! Single-input component for entering skip conditions:
//! - Auto-closes `(`, `[`, `{`.
//! - Pops up a function dropdown when the cursor is inside `( ... )`.
//! - Pops up a team/tag/match-ref dropdown inside `[ ... ]`.
//! - Pops up a match dropdown inside `{ ... }`.
//! - Renders parsed `[...]` and `{...}` literals as chips in a live preview.
//! - Calls `validate_dsl` on blur and shows error/simplified output.

use crate::api;
use crate::types::*;
use dioxus::html::ModifiersInteraction;
use dioxus::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsCast as _;

/// (name, signature, short description)
const DSL_FUNCTIONS: &[(&str, &str, &str)] = &[
    (
        "wins",
        "(wins TEAM) / (wins TEAM MATCHLIST) -> INT",
        "Wins for a team (event-wide or over MATCHLIST)",
    ),
    (
        "losses",
        "(losses TEAM) / (losses TEAM MATCHLIST) -> INT",
        "Losses for a team (event-wide or over MATCHLIST)",
    ),
    ("winner", "(winner MATCH) -> TEAM", "Winner of a match"),
    ("loser", "(loser MATCH) -> TEAM", "Loser of a match"),
    (
        "points-won",
        "(points-won TEAM) / (points-won TEAM MATCH|MATCHLIST) -> INT",
        "Points won (event, one match, or sum over MATCHLIST)",
    ),
    (
        "points-lost",
        "(points-lost TEAM) / (points-lost TEAM MATCH|MATCHLIST) -> INT",
        "Points lost (event, one match, or sum over MATCHLIST)",
    ),
    ("won?", "(won? TEAM MATCH) -> BOOL", "True if TEAM won MATCH"),
    (
        "is-skipped",
        "(is-skipped MATCH) -> BOOL",
        "True if match was skipped",
    ),
    ("if", "(if COND IF_TRUE IF_FALSE)", "Conditional"),
    ("let", "(let ((name expr) ...) BODY)", "Sequential local bindings"),
    ("cond", "(cond (PRED EXPR) ...)", "Multi-branch conditional; else nil"),
    ("and", "(and *BOOL) -> BOOL", "Logical and (variadic)"),
    ("or", "(or *BOOL) -> BOOL", "Logical or (variadic)"),
    ("not", "(not BOOL) -> BOOL", "Logical not"),
    ("==", "(== ANY ANY) -> BOOL", "Equality"),
    (">", "(> INT INT) -> BOOL", "Greater than"),
    ("<", "(< INT INT) -> BOOL", "Less than"),
    (">=", "(>= INT INT) -> BOOL", "Greater or equal"),
    ("<=", "(<= INT INT) -> BOOL", "Less or equal"),
    ("+", "(+ INT INT) -> INT", "Addition"),
    ("-", "(- INT INT) -> INT", "Subtraction"),
    ("*", "(* INT INT) -> INT", "Multiplication"),
    ("/", "(/ INT INT) -> INT", "Integer division"),
    ("quote", "(quote EXPR) / 'EXPR", "Literal expression, unevaluated"),
    ("list", "(list *ARGS) -> LIST", "Build a list from arguments"),
    ("cons", "(cons X LIST) -> LIST", "Prepend X onto LIST"),
    ("append", "(append LIST LIST) -> LIST", "Concatenate two lists"),
    ("car", "(car LIST)", "First element"),
    ("cdr", "(cdr LIST)", "All but the first element"),
    ("get", "(get INDEX LIST)", "Element at INDEX, or NIL"),
    ("len", "(len LIST) -> INT", "Length of a list"),
    ("empty?", "(empty? LIST) -> BOOL", "True if list is empty"),
    ("member?", "(member? X LIST) -> BOOL", "True if X is in LIST"),
    (
        "or-default",
        "(or-default VAL DEFAULT)",
        "VAL if not NIL else DEFAULT",
    ),
    (
        "map",
        "(map LIST FUNC) -> LIST",
        "Apply FUNC to each element",
    ),
    (
        "map-indexed",
        "(map-indexed LIST FUNC) -> LIST",
        "Apply FUNC(i, x) to each element with index",
    ),
    (
        "filter",
        "(filter LIST PREDFN) -> LIST",
        "Keep elements where PREDFN is true",
    ),
    (
        "reduce",
        "(reduce LIST FUNC) / (reduce LIST INIT FUNC)",
        "Fold list with FUNC; optional INIT",
    ),
    (
        "sort-by",
        "(sort-by LIST *KEYFNS) -> LIST",
        "Sort descending by key function(s), stable",
    ),
    ("range", "(range N) -> LIST", "List 0 .. N-1"),
    ("max", "(max LIST)", "Maximum of a list"),
    ("min", "(min LIST)", "Minimum of a list"),
    (
        "max-by",
        "(max-by LIST FUNC)",
        "Element with max FUNC value",
    ),
    (
        "min-by",
        "(min-by LIST FUNC)",
        "Element with min FUNC value",
    ),
    ("lambda", "(lambda (args) body)", "Define a function"),
];

/// Format a "type mismatch" message for the validity row.
fn type_mismatch_message(expected: &[String], got: &[String]) -> String {
    let exp_label = expected.join(" or ");
    if got.is_empty() || got.iter().any(|t| t == "UNKNOWN") {
        format!("Expected {exp_label}, but the type couldn't be determined.")
    } else {
        let got_label = got.join(" | ");
        format!("Expected {exp_label}, got {got_label}.")
    }
}

/// Returns Ok(()) when `got` is a non-empty subset of `expected` (with no UNKNOWN);
/// otherwise Err with a human-readable explanation. `expected` empty means no constraint.
fn check_expected_type(expected: Option<&[String]>, got: &[String]) -> Result<(), String> {
    let Some(exp) = expected else {
        return Ok(());
    };
    if exp.is_empty() {
        return Ok(());
    }
    if got.is_empty() || got.iter().any(|t| t == "UNKNOWN") {
        return Err(type_mismatch_message(exp, got));
    }
    if got.iter().all(|t| exp.iter().any(|e| e == t)) {
        Ok(())
    } else {
        Err(type_mismatch_message(exp, got))
    }
}

/// Apply a successful validate-dsl response to the ass-entry signals: surface error / simplified
/// chips, run the expected-type check, and report the final verdict via `on_validity_change`.
fn apply_validation_response(
    res: ValidateDslResponse,
    validated_for: String,
    expected: Option<&[String]>,
    mut error_msg: Signal<Option<String>>,
    mut simplified_msg: Signal<Option<(String, String)>>,
    on_validity_change: Option<EventHandler<Option<Result<(), String>>>>,
) {
    if !res.valid {
        let err = res
            .error
            .unwrap_or_else(|| "invalid expression".to_string());
        error_msg.set(Some(err.clone()));
        simplified_msg.set(None);
        if let Some(h) = on_validity_change {
            h.call(Some(Err(err)));
        }
        return;
    }
    if let Some(simp) = res.simplified.clone() {
        simplified_msg.set(Some((validated_for, simp)));
    }
    match check_expected_type(expected, &res.result_type) {
        Ok(()) => {
            error_msg.set(None);
            if let Some(h) = on_validity_change {
                h.call(Some(Ok(())));
            }
        }
        Err(msg) => {
            error_msg.set(Some(msg.clone()));
            if let Some(h) = on_validity_change {
                h.call(Some(Err(msg)));
            }
        }
    }
}

/// Set a textarea's height to fit its content: clear `height`, then read `scrollHeight`
/// and write it back. Caps at a max so a runaway expression doesn't take over the form.
#[cfg(target_arch = "wasm32")]
fn autosize_textarea(el: &web_sys::HtmlTextAreaElement) {
    let style = el.style();
    let _ = style.set_property("height", "auto");
    let h = el.scroll_height();
    let max = 400;
    let h = h.max(0).min(max);
    let _ = style.set_property("height", &format!("{h}px"));
    let _ = style.set_property(
        "overflow-y",
        if el.scroll_height() > max {
            "auto"
        } else {
            "hidden"
        },
    );
}

/// Find matching close bracket from open_pos (byte index of open char). Returns byte index of close char.
fn find_matching_close(
    s: &str,
    open_byte_pos: usize,
    open_c: char,
    close_c: char,
) -> Option<usize> {
    let after_open = open_byte_pos + open_c.len_utf8();
    let rest = s.get(after_open..)?;
    let mut depth = 1u32;
    for (i, c) in rest.char_indices() {
        if c == open_c {
            depth += 1;
        } else if c == close_c {
            depth -= 1;
            if depth == 0 {
                return Some(after_open + i);
            }
        }
    }
    None
}

/// Find matching open bracket for a close at `close_byte_pos`. Returns byte index of open char.
fn find_matching_open(
    s: &str,
    close_byte_pos: usize,
    open_c: char,
    close_c: char,
) -> Option<usize> {
    let before = s.get(..close_byte_pos)?;
    let mut depth = 1u32;
    for (i, c) in before.char_indices().rev() {
        if c == close_c {
            depth += 1;
        } else if c == open_c {
            depth = depth.saturating_sub(1);
            if depth == 0 {
                return Some(i);
            }
        }
    }
    None
}

/// Highlight state for the bracket under (or immediately left of) the cursor.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BracketPairHighlight {
    /// Byte indices of the open and close bracket characters (open ≤ close).
    Matched { open: usize, close: usize },
    /// Byte index of a bracket with no matching partner.
    Unmatched { pos: usize },
}

/// If the cursor is on a bracket (char at caret, else char just before caret), return
/// the pair highlight for that bracket. Matches VS Code-style adjacency.
pub fn bracket_highlight_at_cursor(s: &str, cursor_char: usize) -> Option<BracketPairHighlight> {
    let chars: Vec<(usize, char)> = s.char_indices().collect();
    let is_bracket = |c: char| matching_close(c).is_some() || matching_open(c).is_some();

    let at = chars.get(cursor_char).copied().filter(|(_, c)| is_bracket(*c));
    let before = cursor_char
        .checked_sub(1)
        .and_then(|i| chars.get(i).copied())
        .filter(|(_, c)| is_bracket(*c));

    // Prefer the bracket the caret is sitting on (to the right); fall back to the one just left.
    let (pos, c) = at.or(before)?;

    if let Some(close_c) = matching_close(c) {
        match find_matching_close(s, pos, c, close_c) {
            Some(close) => Some(BracketPairHighlight::Matched { open: pos, close }),
            None => Some(BracketPairHighlight::Unmatched { pos }),
        }
    } else if let Some(open_c) = matching_open(c) {
        match find_matching_open(s, pos, open_c, c) {
            Some(open) => Some(BracketPairHighlight::Matched { open, close: pos }),
            None => Some(BracketPairHighlight::Unmatched { pos }),
        }
    } else {
        None
    }
}

/// Split `s` into (text, optional CSS class) segments for the bracket-highlight backdrop.
/// Non-highlighted text uses class `None` (rendered transparent so only backgrounds show).
fn bracket_highlight_segments(
    s: &str,
    hl: Option<BracketPairHighlight>,
) -> Vec<(String, Option<&'static str>)> {
    let mut marks: Vec<(usize, &'static str)> = Vec::new();
    match hl {
        Some(BracketPairHighlight::Matched { open, close }) => {
            marks.push((open, "ass-entry-bracket-match"));
            marks.push((close, "ass-entry-bracket-match"));
        }
        Some(BracketPairHighlight::Unmatched { pos }) => {
            marks.push((pos, "ass-entry-bracket-unmatched"));
        }
        None => {}
    }
    marks.sort_by_key(|(p, _)| *p);

    let mut out: Vec<(String, Option<&'static str>)> = Vec::new();
    let mut cursor = 0usize;
    for (pos, cls) in marks {
        if pos < cursor || pos >= s.len() {
            continue;
        }
        if pos > cursor {
            out.push((s[cursor..pos].to_string(), None));
        }
        // Single char at pos (brackets are always ASCII in ASS).
        let end = pos + s[pos..].chars().next().map(|c| c.len_utf8()).unwrap_or(1);
        out.push((s[pos..end].to_string(), Some(cls)));
        cursor = end;
    }
    if cursor < s.len() {
        out.push((s[cursor..].to_string(), None));
    } else if s.is_empty() {
        // Keep an empty segment so the backdrop still participates in layout.
        out.push((String::new(), None));
    }
    out
}

/// Convert a cursor position in characters to byte offset.
fn cursor_byte(s: &str, cursor_char: usize) -> usize {
    s.char_indices()
        .nth(cursor_char)
        .map(|(i, _)| i)
        .unwrap_or(s.len())
}

/// Diff `old` and `new` as one contiguous edit: `(byte_pos, replaced_old, inserted_new)`.
///
/// Computes the longest common prefix and suffix (snapped to char boundaries) and
/// reports the differing middle. Handles plain insertions, deletions, and selection
/// replacements uniformly — the cases that broke the old `new_char_index` length check.
fn detect_change(old: &str, new: &str) -> Option<(usize, String, String)> {
    let old_bytes = old.as_bytes();
    let new_bytes = new.as_bytes();

    let max_prefix = old_bytes.len().min(new_bytes.len());
    let mut prefix = 0;
    while prefix < max_prefix && old_bytes[prefix] == new_bytes[prefix] {
        prefix += 1;
    }
    while prefix > 0 && (!old.is_char_boundary(prefix) || !new.is_char_boundary(prefix)) {
        prefix -= 1;
    }

    let max_suffix = (old_bytes.len() - prefix).min(new_bytes.len() - prefix);
    let mut suffix = 0;
    while suffix < max_suffix
        && old_bytes[old_bytes.len() - 1 - suffix] == new_bytes[new_bytes.len() - 1 - suffix]
    {
        suffix += 1;
    }
    while suffix > 0
        && (!old.is_char_boundary(old_bytes.len() - suffix)
            || !new.is_char_boundary(new_bytes.len() - suffix))
    {
        suffix -= 1;
    }

    let replaced = old[prefix..old_bytes.len() - suffix].to_string();
    let inserted = new[prefix..new_bytes.len() - suffix].to_string();
    if replaced.is_empty() && inserted.is_empty() {
        return None;
    }
    Some((prefix, replaced, inserted))
}

/// Read the textarea's current value and selection synchronously from the DOM.
/// Returns (value, selection_start, selection_end) in UTF-16 code units (which equal
/// chars and bytes for the ASCII text we expect in this DSL).
#[cfg(target_arch = "wasm32")]
fn read_textarea_state(id: &str) -> Option<(String, usize, usize)> {
    let window = web_sys::window()?;
    let doc = window.document()?;
    let el = doc.query_selector(&format!("#{}", id)).ok()??;
    let ta: web_sys::HtmlTextAreaElement = el.dyn_into().ok()?;
    let value = ta.value();
    let start = ta.selection_start().ok().flatten()? as usize;
    let end = ta.selection_end().ok().flatten()? as usize;
    Some((value, start, end))
}

/// Byte offset of the Nth char in `s`, clamped to `s.len()`.
fn nth_char_byte(s: &str, n: usize) -> usize {
    s.char_indices().nth(n).map(|(i, _)| i).unwrap_or(s.len())
}

/// Closing bracket paired with `c`, if any.
fn matching_close(c: char) -> Option<char> {
    match c {
        '(' => Some(')'),
        '[' => Some(']'),
        '{' => Some('}'),
        _ => None,
    }
}

/// Opening bracket paired with `c`, if any.
fn matching_open(c: char) -> Option<char> {
    match c {
        ')' => Some('('),
        ']' => Some('['),
        '}' => Some('{'),
        _ => None,
    }
}

/// Innermost bracket whose content contains the cursor, recorded as (content_start_byte, content_end_byte).
/// content_end_byte is the byte position of the closing char, or s.len() if unclosed.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum InnermostBracket {
    Paren(usize, usize),
    Square(usize, usize),
    Curly(usize, usize),
}

/// Pick the bracket whose open is closest to the cursor (largest content_start ≤ cursor) among
/// brackets that either contain the cursor (close ≥ cursor) or are unclosed.
pub fn innermost_around_cursor(s: &str, cursor_char: usize) -> Option<InnermostBracket> {
    let cb = cursor_byte(s, cursor_char);
    let mut best: Option<(usize, InnermostBracket)> = None;

    let consider = |open_c: char,
                    close_c: char,
                    best: &mut Option<(usize, InnermostBracket)>,
                    s: &str,
                    cb: usize| {
        let Some(open_pos) = s[..cb].rfind(open_c) else {
            return;
        };
        let close = find_matching_close(s, open_pos, open_c, close_c);
        let end = close.unwrap_or(s.len());
        if close.is_some() && end < cb {
            return;
        }
        let content_start = open_pos + open_c.len_utf8();
        if content_start > cb {
            return;
        }
        let bracket = match open_c {
            '(' => InnermostBracket::Paren(content_start, end),
            '[' => InnermostBracket::Square(content_start, end),
            '{' => InnermostBracket::Curly(content_start, end),
            _ => return,
        };
        if best.map_or(true, |(cs, _)| content_start > cs) {
            *best = Some((content_start, bracket));
        }
    };
    consider('(', ')', &mut best, s, cb);
    consider('[', ']', &mut best, s, cb);
    consider('{', '}', &mut best, s, cb);
    best.map(|(_, b)| b)
}

/// Tokenize the expression for the preview row. Each token is a chunk of text the user wrote,
/// classified as a literal kind so we can render chips for [..] and {..}.
///
/// As a special case, the patterns `(winner {NAME})` and `(loser {NAME})` collapse into a
/// single `WinnerCall`/`LoserCall` token — they're conceptually a single team-reference,
/// just spelled differently from `[NAME::winner]` / `[NAME::loser]`.
#[derive(Clone, Debug)]
enum PreviewToken {
    Text(String),
    Team(String),
    Match(String),
    WinnerCall(String),
    LoserCall(String),
    OpenBracket(char),
    CloseBracket(char),
    /// A literal newline in the input — rendered as a flex-row break so subsequent
    /// chips wrap to a new line, mirroring the textarea layout.
    Newline,
}

/// If `s[i..]` starts with `( <ws>* (winner|loser) <ws>+ {NAME} <ws>* )`, return
/// `(winner_or_loser, name, end_byte)` so we can collapse it into a single chip.
fn match_winner_loser_call(s: &str, i: usize) -> Option<(bool, String, usize)> {
    let bytes = s.as_bytes();
    if bytes.get(i).copied() != Some(b'(') {
        return None;
    }
    let mut j = i + 1;
    while j < bytes.len() && bytes[j].is_ascii_whitespace() {
        j += 1;
    }
    let is_winner = if s[j..].starts_with("winner") {
        j += "winner".len();
        true
    } else if s[j..].starts_with("loser") {
        j += "loser".len();
        false
    } else {
        return None;
    };
    if !bytes.get(j).map_or(false, |c| c.is_ascii_whitespace()) {
        return None;
    }
    while j < bytes.len() && bytes[j].is_ascii_whitespace() {
        j += 1;
    }
    if bytes.get(j).copied() != Some(b'{') {
        return None;
    }
    let after_open = j + 1;
    let close_rel = s[after_open..].find('}')?;
    let name = s[after_open..after_open + close_rel].trim().to_string();
    let mut k = after_open + close_rel + 1;
    while k < bytes.len() && bytes[k].is_ascii_whitespace() {
        k += 1;
    }
    if bytes.get(k).copied() != Some(b')') {
        return None;
    }
    Some((is_winner, name, k + 1))
}

fn tokenize_preview(s: &str) -> Vec<PreviewToken> {
    let mut out: Vec<PreviewToken> = Vec::new();
    let bytes = s.as_bytes();
    let mut i = 0;
    let mut text_buf = String::new();
    // Split accumulated text at `\n` so each newline becomes its own token. Empty
    // segments between consecutive newlines are still pushed as empty Text so the
    // resulting layout has the right number of row breaks.
    let flush_text = |buf: &mut String, out: &mut Vec<PreviewToken>| {
        if buf.is_empty() {
            return;
        }
        let taken = std::mem::take(buf);
        let mut first = true;
        for line in taken.split('\n') {
            if !first {
                out.push(PreviewToken::Newline);
            }
            first = false;
            if !line.is_empty() {
                out.push(PreviewToken::Text(line.to_string()));
            }
        }
    };
    while i < bytes.len() {
        let c = bytes[i] as char;
        if c == '(' {
            if let Some((is_winner, name, end)) = match_winner_loser_call(s, i) {
                flush_text(&mut text_buf, &mut out);
                if is_winner {
                    out.push(PreviewToken::WinnerCall(name));
                } else {
                    out.push(PreviewToken::LoserCall(name));
                }
                i = end;
                continue;
            }
        }
        if c == '[' || c == '{' {
            let close_c = if c == '[' { ']' } else { '}' };
            // Find first close in same kind without trying to support nesting (literals don't nest).
            let after = i + 1;
            if let Some(rel) = s[after..].find(close_c) {
                let inner = &s[after..after + rel];
                flush_text(&mut text_buf, &mut out);
                if c == '[' {
                    out.push(PreviewToken::Team(inner.to_string()));
                } else {
                    out.push(PreviewToken::Match(inner.to_string()));
                }
                i = after + rel + 1;
                continue;
            } else {
                // Unclosed: render as open bracket then continue rendering remaining text
                flush_text(&mut text_buf, &mut out);
                out.push(PreviewToken::OpenBracket(c));
                i += 1;
                continue;
            }
        }
        if c == ']' || c == '}' {
            flush_text(&mut text_buf, &mut out);
            out.push(PreviewToken::CloseBracket(c));
            i += 1;
            continue;
        }
        text_buf.push(c);
        i += 1;
    }
    flush_text(&mut text_buf, &mut out);
    out
}

#[derive(Clone, Debug)]
struct TeamRefResolved {
    profile_photo: Option<String>,
    display: String,
}

/// Resolve a `[...]` literal to display info: pseudonym, tag→team, MatchName::winner/loser.
/// Returns the kind label and resolved team if available.
fn resolve_team_literal(
    inner: &str,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> (TeamRefKind, Option<TeamRefResolved>) {
    let trimmed = inner.trim();
    if let Some(rest) = trimmed.strip_suffix("::winner") {
        let name = rest.trim();
        let resolved = matches
            .iter()
            .find(|m| {
                m.name.eq_ignore_ascii_case(name) && m.status.eq_ignore_ascii_case("COMPLETED")
            })
            .and_then(|m| match m.match_winner.as_deref() {
                Some(s) if s.eq_ignore_ascii_case("TEAM1") => m.team1.clone(),
                Some(s) if s.eq_ignore_ascii_case("TEAM2") => m.team2.clone(),
                _ => None,
            })
            .and_then(|tid| team_options.iter().find(|t| t.id == tid))
            .map(|t| TeamRefResolved {
                profile_photo: t.profile_photo.clone(),
                display: t
                    .pseudonym
                    .clone()
                    .map(|p| format!("{p} ({})", t.id))
                    .unwrap_or_else(|| t.id.clone()),
            });
        return (TeamRefKind::Winner(name.to_string()), resolved);
    }
    if let Some(rest) = trimmed.strip_suffix("::loser") {
        let name = rest.trim();
        let resolved = matches
            .iter()
            .find(|m| {
                m.name.eq_ignore_ascii_case(name) && m.status.eq_ignore_ascii_case("COMPLETED")
            })
            .and_then(|m| match m.match_winner.as_deref() {
                Some(s) if s.eq_ignore_ascii_case("TEAM1") => m.team2.clone(),
                Some(s) if s.eq_ignore_ascii_case("TEAM2") => m.team1.clone(),
                _ => None,
            })
            .and_then(|tid| team_options.iter().find(|t| t.id == tid))
            .map(|t| TeamRefResolved {
                profile_photo: t.profile_photo.clone(),
                display: t
                    .pseudonym
                    .clone()
                    .map(|p| format!("{p} ({})", t.id))
                    .unwrap_or_else(|| t.id.clone()),
            });
        return (TeamRefKind::Loser(name.to_string()), resolved);
    }
    if trimmed.len() >= 5 && trimmed[..5].eq_ignore_ascii_case("tag::") {
        let name = trimmed[5..].trim();
        let resolved = tags
            .iter()
            .find(|t| t.name.eq_ignore_ascii_case(name))
            .and_then(|t| t.team.clone())
            .and_then(|tid| team_options.iter().find(|t| t.id == tid).cloned())
            .map(|t| TeamRefResolved {
                profile_photo: t.profile_photo.clone(),
                display: t
                    .pseudonym
                    .clone()
                    .map(|p| format!("{p} ({})", t.id))
                    .unwrap_or_else(|| t.id.clone()),
            });
        return (TeamRefKind::Tag(name.to_string()), resolved);
    }
    let team = team_options
        .iter()
        .find(|t| t.id.eq_ignore_ascii_case(trimmed));
    let resolved = team.map(|t| TeamRefResolved {
        profile_photo: t.profile_photo.clone(),
        display: t
            .pseudonym
            .clone()
            .map(|p| format!("{p} ({})", t.id))
            .unwrap_or_else(|| t.id.clone()),
    });
    (TeamRefKind::Team(trimmed.to_string()), resolved)
}

#[derive(Clone, Debug)]
enum TeamRefKind {
    Team(String),
    Tag(String),
    Winner(String),
    Loser(String),
}

/// Compact display name for a team — prefer `shortname` when present, then `pseudonym`, then id.
fn team_short_label(t: &TeamOption) -> String {
    t.shortname
        .clone()
        .or_else(|| t.pseudonym.clone())
        .unwrap_or_else(|| t.id.clone())
}

/// One ASS atom (the kind of thing that can stand in for a team in a match): a team id,
/// `tag::TagName`, or `MatchName::winner` / `MatchName::loser`. Used to render compact
/// inline references in the match autocomplete.
#[derive(Clone, Debug)]
enum AssAtom {
    Team(String),   // team id
    Tag(String),    // tag name (without the "tag::" prefix)
    Winner(String), // match name
    Loser(String),  // match name
}

fn parse_ass_atom(raw: &str) -> AssAtom {
    let s = raw.trim();
    if let Some(rest) = s.strip_suffix("::winner") {
        return AssAtom::Winner(rest.trim().to_string());
    }
    if let Some(rest) = s.strip_suffix("::loser") {
        return AssAtom::Loser(rest.trim().to_string());
    }
    if s.len() >= 5 && s[..5].eq_ignore_ascii_case("tag::") {
        return AssAtom::Tag(s[5..].trim().to_string());
    }
    AssAtom::Team(s.to_string())
}

/// Render an ASS atom as a compact inline pill: avatar + shortname for teams, an
/// icon + label for tags / winner / loser. Used in the match autocomplete to show
/// who's playing and reffing without taking much space.
fn render_atom_compact(
    raw: &str,
    base_url: &str,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
) -> Element {
    let atom = parse_ass_atom(raw);
    match atom {
        AssAtom::Team(id) => {
            if let Some(t) = team_options.iter().find(|t| t.id.eq_ignore_ascii_case(&id)) {
                let label = team_short_label(t);
                if let Some(p) = t.profile_photo.clone() {
                    rsx! {
                        span { class: "ass-atom",
                            img {
                                src: "{base_url}/static/{p}",
                                alt: "",
                                class: "ass-atom-avatar rounded-circle",
                            }
                            span { class: "ass-atom-label", "{label}" }
                        }
                    }
                } else {
                    let initial = label.chars().next().unwrap_or('?').to_string();
                    rsx! {
                        span { class: "ass-atom",
                            span { class: "ass-atom-avatar ass-atom-avatar-text", "{initial}" }
                            span { class: "ass-atom-label", "{label}" }
                        }
                    }
                }
            } else {
                rsx! {
                    span { class: "ass-atom ass-atom-unknown",
                        span { class: "ass-atom-avatar ass-atom-avatar-text", "?" }
                        span { class: "ass-atom-label", "{id}" }
                    }
                }
            }
        }
        AssAtom::Tag(name) => {
            let known = tags.iter().any(|t| t.name.eq_ignore_ascii_case(&name));
            let cls = if known {
                "ass-atom"
            } else {
                "ass-atom ass-atom-unknown"
            };
            rsx! {
                span { class: "{cls}",
                    img { class: "ass-atom-icon icon-primary-svg", src: "{base_url}/static/tag.svg", alt: "" }
                    span { class: "ass-atom-label", "{name}" }
                }
            }
        }
        AssAtom::Winner(name) => rsx! {
            span { class: "ass-atom",
                img { class: "ass-atom-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "" }
                span { class: "ass-atom-label", "{name}" }
                span { class: "ass-atom-badge winner-badge", "W" }
            }
        },
        AssAtom::Loser(name) => rsx! {
            span { class: "ass-atom",
                img { class: "ass-atom-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "" }
                span { class: "ass-atom-label", "{name}" }
                span { class: "ass-atom-badge loser-badge", "L" }
            }
        },
    }
}

/// Split a comma-separated list of ASS atoms ("team1, tag::Foo, Match1::winner") into trimmed pieces.
fn split_atoms(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// Render an ASS expression string as a row of chips: literals (`[..]`/`{..}`) and the
/// `(winner ...)` / `(loser ...)` patterns become labeled chips with resolution arrows;
/// everything else passes through as plain text.
fn render_expression_chips(
    s: &str,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
    base_url: &str,
    key_prefix: &str,
) -> Vec<Element> {
    tokenize_preview(s)
        .into_iter()
        .enumerate()
        .map(|(i, tok)| match tok {
            PreviewToken::Text(s) => {
                let key = format!("{key_prefix}-{i}");
                rsx! { span { key: "{key}", class: "ass-entry-preview-text", "{s}" } }
            }
            PreviewToken::Newline => {
                let key = format!("{key_prefix}-{i}");
                // Empty flex item that takes the full row, forcing subsequent siblings to wrap.
                rsx! { span { key: "{key}", class: "ass-entry-preview-break" } }
            }
            PreviewToken::OpenBracket(c) | PreviewToken::CloseBracket(c) => {
                let key = format!("{key_prefix}-{i}");
                let txt = c.to_string();
                rsx! { span { key: "{key}", class: "ass-entry-preview-bracket text-warning", "{txt}" } }
            }
            PreviewToken::Team(inner) => {
                let key = format!("{key_prefix}-{i}");
                let (kind, resolved) = resolve_team_literal(&inner, team_options, tags, matches);
                let (chip_class, label, icon) = match &kind {
                    TeamRefKind::Team(name) => ("team-token-chip team-token-chip-team", name.clone(), None),
                    TeamRefKind::Tag(name) => (
                        "team-token-chip team-token-chip-tag",
                        name.clone(),
                        Some(("tag.svg", "Tag")),
                    ),
                    TeamRefKind::Winner(name) => (
                        "team-token-chip team-token-chip-winner",
                        format!("{} winner", name),
                        Some(("reference.svg", "Reference")),
                    ),
                    TeamRefKind::Loser(name) => (
                        "team-token-chip team-token-chip-loser",
                        format!("{} loser", name),
                        Some(("reference.svg", "Reference")),
                    ),
                };
                let avatar = match (&kind, &resolved) {
                    (TeamRefKind::Team(_), Some(r)) => {
                        if let Some(p) = r.profile_photo.clone() {
                            rsx! { img {
                                src: "{base_url}/static/{p}",
                                alt: "",
                                class: "team-token-avatar rounded-circle",
                                style: "width: 1.4em; height: 1.4em; object-fit: cover;"
                            } }
                        } else {
                            rsx! { span { class: "team-token-avatar", "{r.display.chars().next().unwrap_or('?')}" } }
                        }
                    }
                    (TeamRefKind::Team(name), None) => {
                        rsx! { span { class: "team-token-avatar", "{name.chars().next().unwrap_or('?')}" } }
                    }
                    _ => {
                        if let Some((icon_name, alt)) = icon {
                            rsx! { img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/{icon_name}", alt: "{alt}" } }
                        } else {
                            rsx! {}
                        }
                    }
                };
                let resolved_arrow = match (&kind, resolved.clone()) {
                    (TeamRefKind::Team(_), _) => rsx! {},
                    (_, Some(r)) => {
                        let disp = r.display.clone();
                        let photo = r.profile_photo.clone();
                        rsx! {
                            span { class: "team-token-resolved text-muted ms-1",
                                " → "
                                if let Some(p) = photo {
                                    img {
                                        src: "{base_url}/static/{p}",
                                        alt: "",
                                        class: "team-token-avatar small rounded-circle ms-1",
                                        style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                    }
                                } else {
                                    span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{disp.chars().next().unwrap_or('?')}" }
                                }
                                span { "{disp}" }
                            }
                        }
                    }
                    _ => rsx! {},
                };
                rsx! {
                    span { key: "{key}", class: "{chip_class}",
                        {avatar}
                        span { class: "team-token-label", "{label}" }
                        {resolved_arrow}
                    }
                }
            }
            PreviewToken::Match(inner) => {
                let key = format!("{key_prefix}-{i}");
                let name = inner.trim().to_string();
                let known = matches.iter().any(|m| m.name.eq_ignore_ascii_case(&name));
                let extra_class = if known { "" } else { " ass-entry-preview-unknown" };
                rsx! {
                    span { key: "{key}", class: "team-token-chip team-token-chip-match{extra_class}",
                        img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Match" }
                        span { class: "team-token-label", "{name}" }
                    }
                }
            }
            ref tok @ (PreviewToken::WinnerCall(_) | PreviewToken::LoserCall(_)) => {
                let key = format!("{key_prefix}-{i}");
                let (name, is_winner) = match tok {
                    PreviewToken::WinnerCall(n) => (n.clone(), true),
                    PreviewToken::LoserCall(n) => (n.clone(), false),
                    _ => unreachable!(),
                };
                let chip_class = if is_winner {
                    "team-token-chip team-token-chip-winner"
                } else {
                    "team-token-chip team-token-chip-loser"
                };
                let label = if is_winner {
                    format!("{} winner", name)
                } else {
                    format!("{} loser", name)
                };
                let resolved = matches
                    .iter()
                    .find(|m| m.name.eq_ignore_ascii_case(&name) && m.status.eq_ignore_ascii_case("COMPLETED"))
                    .and_then(|m| {
                        let side = m.match_winner.as_deref()?;
                        let id = if is_winner {
                            if side.eq_ignore_ascii_case("TEAM1") { m.team1.clone() } else if side.eq_ignore_ascii_case("TEAM2") { m.team2.clone() } else { None }
                        } else if side.eq_ignore_ascii_case("TEAM1") { m.team2.clone() } else if side.eq_ignore_ascii_case("TEAM2") { m.team1.clone() } else { None }?;
                        Some(id)
                    })
                    .and_then(|tid| team_options.iter().find(|t| t.id == tid).cloned());
                let resolved_arrow = if let Some(t) = resolved {
                    let team_label = team_short_label(&t);
                    let photo = t.profile_photo.clone();
                    rsx! {
                        span { class: "team-token-resolved text-muted ms-1",
                            " → "
                            if let Some(p) = photo {
                                img {
                                    src: "{base_url}/static/{p}",
                                    alt: "",
                                    class: "team-token-avatar small rounded-circle ms-1",
                                    style: "width: 1em; height: 1em; object-fit: cover; vertical-align: middle;"
                                }
                            } else {
                                span { class: "team-token-avatar small ms-1", style: "display: inline-flex; width: 1em; height: 1em; align-items: center; justify-content: center; font-size: 0.85em;", "{team_label.chars().next().unwrap_or('?')}" }
                            }
                            span { "{team_label}" }
                        }
                    }
                } else {
                    rsx! {}
                };
                rsx! {
                    span { key: "{key}", class: "{chip_class}",
                        img { class: "team-token-icon icon-primary-svg", src: "{base_url}/static/reference.svg", alt: "Reference" }
                        span { class: "team-token-label", "{label}" }
                        {resolved_arrow}
                    }
                }
            }
        })
        .collect()
}

#[derive(Clone, Debug)]
enum AcOption {
    Function {
        name: String,
        signature: String,
        description: String,
    },
    Team {
        insert: String,
        display: String,
        photo: Option<String>,
    },
    Tag {
        insert: String,
        display: String,
        resolved_team: Option<String>,
    },
    MatchRef {
        insert: String,
        display: String,
        is_winner: bool,
    },
    Match {
        insert: String,
        display: String,
        field: Option<String>,
        team1: Option<String>,
        team2: Option<String>,
        refs: Vec<String>,
    },
}

fn collect_function_options(prefix: &str) -> Vec<AcOption> {
    let q = prefix.to_lowercase();
    DSL_FUNCTIONS
        .iter()
        .filter(|(n, _, _)| q.is_empty() || n.to_lowercase().starts_with(&q))
        .take(20)
        .map(|(n, s, d)| AcOption::Function {
            name: (*n).to_string(),
            signature: (*s).to_string(),
            description: (*d).to_string(),
        })
        .collect()
}

fn collect_team_options(
    query: &str,
    team_options: &[TeamOption],
    tags: &[TagSetupData],
    matches: &[MatchSetupData],
) -> Vec<AcOption> {
    let q = query.to_lowercase();
    // Build each kind separately, then interleave with per-kind caps so tags/matches always
    // get a fair slice when there's no query (otherwise a long team list can starve them).
    let team_opts: Vec<AcOption> = team_options
        .iter()
        .filter(|t| {
            q.is_empty()
                || t.id.to_lowercase().contains(&q)
                || t.pseudonym
                    .as_deref()
                    .unwrap_or("")
                    .to_lowercase()
                    .contains(&q)
        })
        .map(|t| AcOption::Team {
            insert: t.id.clone(),
            display: t
                .pseudonym
                .clone()
                .map(|p| format!("{p} ({})", t.id))
                .unwrap_or_else(|| t.id.clone()),
            photo: t.profile_photo.clone(),
        })
        .collect();
    let tag_opts: Vec<AcOption> = tags
        .iter()
        .filter(|tag| q.is_empty() || tag.name.to_lowercase().contains(&q))
        .map(|tag| AcOption::Tag {
            insert: format!("tag::{}", tag.name),
            display: tag.name.clone(),
            resolved_team: tag.team.clone(),
        })
        .collect();
    let mut match_ref_opts: Vec<AcOption> = Vec::new();
    for m in matches.iter() {
        if q.is_empty() || m.name.to_lowercase().contains(&q) {
            match_ref_opts.push(AcOption::MatchRef {
                insert: format!("{}::winner", m.name),
                display: format!("{} winner", m.name),
                is_winner: true,
            });
            match_ref_opts.push(AcOption::MatchRef {
                insert: format!("{}::loser", m.name),
                display: format!("{} loser", m.name),
                is_winner: false,
            });
        }
    }
    // Caps: when the query is empty, give each kind a fair slice. With a query, prefer the
    // best matches but keep tag/match-ref space available.
    let (team_cap, tag_cap, match_cap) = if q.is_empty() {
        (12, 8, 10)
    } else {
        (15, 8, 10)
    };
    let mut out: Vec<AcOption> = Vec::new();
    out.extend(team_opts.into_iter().take(team_cap));
    out.extend(tag_opts.into_iter().take(tag_cap));
    out.extend(match_ref_opts.into_iter().take(match_cap));
    out.into_iter().take(30).collect()
}

fn collect_match_options(query: &str, matches: &[MatchSetupData]) -> Vec<AcOption> {
    let q = query.to_lowercase();
    matches
        .iter()
        .filter(|m| q.is_empty() || m.name.to_lowercase().contains(&q))
        .take(25)
        .map(|m| AcOption::Match {
            insert: m.name.clone(),
            display: m.name.clone(),
            field: m.field.clone(),
            team1: m.team1_initial.clone().or_else(|| m.team1.clone()),
            team2: m.team2_initial.clone().or_else(|| m.team2.clone()),
            refs: m
                .refs_initial
                .as_deref()
                .or(m.refs.as_deref())
                .map(split_atoms)
                .unwrap_or_default(),
        })
        .collect()
}

#[component]
pub fn AssEntry(
    /// Unique suffix for input ID (e.g. "create", "edit", "modal"). Multiple instances need distinct IDs.
    id_suffix: String,
    value: String,
    on_change: EventHandler<String>,
    team_options: Vec<TeamOption>,
    tags: Vec<TagSetupData>,
    matches: Vec<MatchSetupData>,
    /// For server-side validate-dsl on blur. Pass empty to skip server validation.
    tournament_url: String,
    #[props(default = String::from("e.g. (== 0 (losses [Team]))"))] placeholder: String,
    /// If set, the expression is valid only when its result type is a subset of this list.
    /// Type names match the backend: "INT" | "BOOL" | "NIL" | "TEAM" | "MATCH" | "LIST" | "FUNC".
    /// "UNKNOWN" is always treated as a mismatch (we can't prove it's the right type).
    /// Leave None to skip type checking.
    #[props(optional)]
    expected_type: Option<Vec<String>>,
    /// Reports the latest validation status to the parent. `Some(Ok(()))` means the
    /// expression validated and matched `expected_type`; `Some(Err(msg))` means it
    /// failed (parse error, server error, or type mismatch); `None` means no result
    /// yet (empty input or in-flight). Useful for blocking form submission.
    #[props(optional)]
    on_validity_change: Option<EventHandler<Option<Result<(), String>>>>,
) -> Element {
    let input_id = format!("ass-entry-{}", id_suffix);

    let value_rc = Rc::new(value.clone());
    let team_options_rc = Rc::new(team_options);
    let tags_rc = Rc::new(tags);
    let matches_rc = Rc::new(matches);

    let mut cursor_pos = use_signal(|| None::<usize>);
    let mut pending_cursor = use_signal(|| None::<usize>);
    let mut ac_index = use_signal(|| 0usize);
    let mut ac_open = use_signal(|| false);
    let mut error_msg = use_signal(|| None::<String>);
    // Tagged with the input string the simplification was computed for, so we can hide it
    // when the user has typed something that no longer matches.
    let mut simplified_msg = use_signal(|| None::<(String, String)>);
    // Bumped on every keystroke so debounced async tasks can detect they're stale.
    let mut validate_gen = use_signal(|| 0u64);

    // Initial autosize so a pre-filled value (edit modal) shows at the right height.
    #[cfg(target_arch = "wasm32")]
    {
        let id_init = input_id.clone();
        use_hook(move || {
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id_init)) {
                            if let Ok(ta) = el.dyn_into::<web_sys::HtmlTextAreaElement>() {
                                autosize_textarea(&ta);
                            }
                        }
                    }
                }
            });
        });
    }

    // After auto-close insertions, we need to reposition the cursor on the next tick.
    #[cfg(target_arch = "wasm32")]
    {
        let id_eff = input_id.clone();
        use_effect(move || {
            if let Some(p) = pending_cursor() {
                pending_cursor.set(None);
                // Keep autocomplete + bracket-highlight in sync with the forced caret.
                cursor_pos.set(Some(p));
                let id = id_eff.clone();
                spawn(async move {
                    gloo_timers::future::TimeoutFuture::new(0).await;
                    if let Some(window) = web_sys::window() {
                        if let Some(doc) = window.document() {
                            if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id)) {
                                if let Ok(input) = el.dyn_into::<web_sys::HtmlTextAreaElement>() {
                                    let _ = input.set_selection_range(p as u32, p as u32);
                                    let _ = input.focus();
                                    autosize_textarea(&input);
                                    // Mirror scroll after resize (highlight backdrop is absolute-filled).
                                    if let Ok(Some(hl_el)) =
                                        doc.query_selector(&format!("#{}-bracket-hl", id))
                                    {
                                        hl_el.set_scroll_top(input.scroll_top());
                                        hl_el.set_scroll_left(input.scroll_left());
                                    }
                                }
                            }
                        }
                    }
                });
            }
        });
    }

    // Compute autocomplete state from the current value and cursor.
    let v = value_rc.as_ref().clone();
    let cur = cursor_pos();
    let inn = cur.and_then(|c| innermost_around_cursor(&v, c));
    let cursor_b = cur.map(|c| cursor_byte(&v, c)).unwrap_or(0);

    // Text the user is actively filtering on (empty ⇒ browsing / not filtering).
    // Used both to build the option list and to decide whether ↑/↓ navigate the
    // menu or move the caret (empty filter ⇒ never steal arrows).
    let ac_filter: String = match &inn {
        Some(InnermostBracket::Paren(cs, ce)) => {
            let end = (*ce).min(cursor_b).max(*cs);
            let content = &v[*cs..end];
            // Function-name slot only: once there's whitespace, the cursor is in
            // the args and function AC must not stay open (it was stealing arrows
            // while editing arguments because prefix stayed as the first word).
            if content.chars().any(|c| c.is_whitespace()) {
                String::new()
            } else {
                content.to_string()
            }
        }
        Some(InnermostBracket::Square(cs, ce)) | Some(InnermostBracket::Curly(cs, ce)) => {
            let end = (*ce).min(cursor_b).max(*cs);
            v[*cs..end].trim().to_string()
        }
        None => String::new(),
    };
    // True when the user has typed something to filter — safe to bind ↑/↓ to the menu.
    let ac_arrows_capture = !ac_filter.is_empty();

    let ac_options: Vec<AcOption> = if ac_open() {
        match &inn {
            Some(InnermostBracket::Paren(cs, ce)) => {
                let end = (*ce).min(cursor_b).max(*cs);
                let content = &v[*cs..end];
                if content.chars().any(|c| c.is_whitespace()) {
                    // In argument position — no function completions.
                    vec![]
                } else {
                    // Empty prefix still lists functions (discoverability); arrows
                    // won't capture until the user types a letter (see keydown).
                    collect_function_options(content)
                }
            }
            Some(InnermostBracket::Square(cs, ce)) => {
                let end = (*ce).min(cursor_b).max(*cs);
                let q = v[*cs..end].trim();
                collect_team_options(
                    q,
                    team_options_rc.as_ref(),
                    tags_rc.as_ref(),
                    matches_rc.as_ref(),
                )
            }
            Some(InnermostBracket::Curly(cs, ce)) => {
                let end = (*ce).min(cursor_b).max(*cs);
                let q = v[*cs..end].trim();
                collect_match_options(q, matches_rc.as_ref())
            }
            None => vec![],
        }
    } else {
        vec![]
    };

    let ac_idx = ac_index().min(ac_options.len().saturating_sub(1));

    let preview_tokens = tokenize_preview(&v);
    let base_url = api::base_url();

    let v_for_oninput = v.clone();
    let value_rc_input = value_rc.clone();
    let on_change_input = on_change.clone();
    let url_for_input = tournament_url.clone();
    let id_for_oninput = input_id.clone();
    let expected_type_input = expected_type.clone();
    let oninput_handler = move |e: Event<FormData>| {
        let new_val = e.value();
        let old = value_rc_input.as_ref().clone();
        let _ = v_for_oninput;
        // The longest-common-prefix diff is structurally ambiguous when the inserted
        // character matches the char already at that position (e.g. typing `(` right
        // before another `(`). Read the live cursor to anchor the actual insertion site.
        #[cfg(target_arch = "wasm32")]
        let cursor_after_input: Option<usize> =
            read_textarea_state(&id_for_oninput).map(|(_, s, _)| s);
        #[cfg(not(target_arch = "wasm32"))]
        let cursor_after_input: Option<usize> = None;

        let (out, after_open) = match detect_change(&old, &new_val) {
            Some((diff_byte_i, replaced, inserted))
                if inserted.chars().count() == 1
                    && matching_close(inserted.chars().next().unwrap()).is_some() =>
            {
                let open_c = inserted.chars().next().unwrap();
                let close_c = matching_close(open_c).unwrap();
                // Real insertion site = cursor after typing − inserted length. Falls
                // back to the diff position if we can't read the cursor.
                let actual_open_byte = match cursor_after_input {
                    Some(cur) => {
                        let insert_char = cur.saturating_sub(inserted.chars().count());
                        nth_char_byte(&new_val, insert_char)
                    }
                    None => diff_byte_i,
                };
                let after_open_byte = actual_open_byte + open_c.len_utf8();
                // Standard editor heuristic: don't auto-close before a word char or
                // another opening bracket — those are cases where the user is
                // grouping/prefixing existing content rather than starting a new pair.
                let next_char = new_val[after_open_byte..].chars().next();
                let blocks_close = matches!(
                    next_char,
                    Some(c) if c.is_alphanumeric() || c == '_' || matches!(c, '(' | '[' | '{')
                );

                if replaced.is_empty() && blocks_close {
                    (new_val, None)
                } else if replaced.is_empty() {
                    // Plain auto-close: insert close right after the open. Cursor goes between them.
                    let out_str = format!(
                        "{}{}{}",
                        &new_val[..after_open_byte],
                        close_c,
                        &new_val[after_open_byte..]
                    );
                    (out_str, Some(after_open_byte))
                } else {
                    // Wrap the just-replaced selection: re-insert it between the brackets.
                    // Cursor lands right before the close, at the end of the wrapped content.
                    // Suppression doesn't apply — the user explicitly selected text to wrap.
                    let out_str = format!(
                        "{}{}{}{}",
                        &new_val[..after_open_byte],
                        replaced,
                        close_c,
                        &new_val[after_open_byte..]
                    );
                    (out_str, Some(after_open_byte + replaced.len()))
                }
            }
            _ => (new_val, None),
        };
        on_change_input.call(out.clone());
        ac_open.set(true);
        ac_index.set(0);
        error_msg.set(None);

        // Resize the textarea to fit its content on the next tick (after the DOM applies the new value).
        #[cfg(target_arch = "wasm32")]
        {
            let id_resize = id_for_oninput.clone();
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                if let Some(window) = web_sys::window() {
                    if let Some(doc) = window.document() {
                        if let Ok(Some(el)) = doc.query_selector(&format!("#{}", id_resize)) {
                            if let Ok(ta) = el.dyn_into::<web_sys::HtmlTextAreaElement>() {
                                autosize_textarea(&ta);
                            }
                        }
                    }
                }
            });
        }
        // Don't wipe simplified_msg here. It's tagged with the input it was computed for, so
        // the rsx guard hides it automatically once the input no longer matches; that's
        // gentler than the row blinking out on every keystroke.
        if let Some(byte_after_open) = after_open {
            // ASCII brackets only — byte position equals char position.
            pending_cursor.set(Some(byte_after_open));
        }

        // Debounced re-validation: bump the generation counter, wait 500ms, only validate
        // if no further keystroke happened in that window. Also requires the value to
        // still match what we captured — defends against late-arriving prior responses.
        let gen = validate_gen() + 1;
        validate_gen.set(gen);
        let url = url_for_input.clone();
        let expected = expected_type_input.clone();
        let validated_for = out;
        if !validated_for.trim().is_empty() && !url.is_empty() {
            #[cfg(target_arch = "wasm32")]
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(500).await;
                if validate_gen() != gen {
                    return;
                }
                if let Ok(res) = api::validate_dsl(&url, &validated_for).await {
                    if validate_gen() != gen {
                        return;
                    }
                    apply_validation_response(
                        res,
                        validated_for,
                        expected.as_deref(),
                        error_msg,
                        simplified_msg,
                        on_validity_change,
                    );
                }
            });
            #[cfg(not(target_arch = "wasm32"))]
            let _ = (url, validated_for, gen, expected);
        }
    };

    let id_for_keydown = input_id.clone();
    let value_rc_kd = value_rc.clone();
    let on_change_kd = on_change.clone();
    let ac_options_kd = ac_options.clone();
    let ac_arrows_capture_kd = ac_arrows_capture;
    let onkeydown_handler = move |ev: Event<KeyboardData>| {
        let key = ev.key().to_string();
        let n = ac_options_kd.len();
        if ac_open() && n > 0 {
            // ↑/↓ navigate the menu only while the user is actively filtering
            // (non-empty prefix/query). Otherwise they move the caret — previously
            // any open menu with options (e.g. full function list after `(`) ate
            // arrows and made multi-line / wrapped editing impossible.
            if key == "ArrowDown" || key == "ArrowUp" {
                if ac_arrows_capture_kd {
                    ev.prevent_default();
                    if key == "ArrowDown" {
                        ac_index.set((ac_idx + 1) % n);
                    } else {
                        ac_index.set((ac_idx + n - 1) % n);
                    }
                    return;
                }
                // Not filtering — let the caret move and dismiss so the menu
                // doesn't stick around while navigating a multi-line expression.
                ac_open.set(false);
                // fall through (no preventDefault)
            }
            // Left/right: dismiss the menu so subsequent keys behave normally.
            // (Don't preventDefault — the caret should still move.)
            if key == "ArrowLeft" || key == "ArrowRight" {
                ac_open.set(false);
                // fall through
            }
            if key == "Tab" || (key == "Enter" && !ev.modifiers().contains(Modifiers::SHIFT)) {
                if let Some(opt) = ac_options_kd.get(ac_idx) {
                    ev.prevent_default();
                    let v_now = value_rc_kd.as_ref().clone();
                    let cur_char = cursor_pos().unwrap_or(0);
                    let cur_b = cursor_byte(&v_now, cur_char);
                    let inn_now = innermost_around_cursor(&v_now, cur_char);
                    match (opt, inn_now) {
                        (
                            AcOption::Function { name, .. },
                            Some(InnermostBracket::Paren(cs, ce)),
                        ) => {
                            // Replace the prefix word inside the parens with the function name.
                            let end = ce.min(cur_b).max(cs);
                            let prefix_end = v_now[cs..end]
                                .find(|c: char| c.is_whitespace())
                                .map(|i| cs + i)
                                .unwrap_or(end);
                            let new_v = format!("{}{}{}", &v_now[..cs], name, &v_now[prefix_end..]);
                            let cs_chars = v_now[..cs].chars().count();
                            let new_cursor = cs_chars + name.chars().count();
                            on_change_kd.call(new_v);
                            pending_cursor.set(Some(new_cursor));
                            ac_open.set(false);
                            return;
                        }
                        (
                            AcOption::Team { insert, .. }
                            | AcOption::Tag { insert, .. }
                            | AcOption::MatchRef { insert, .. },
                            Some(InnermostBracket::Square(cs, ce)),
                        ) => {
                            let end = ce.min(cur_b).max(cs);
                            // Replace from cs to end with insert
                            let new_v = format!("{}{}{}", &v_now[..cs], insert, &v_now[end..]);
                            let cs_chars = v_now[..cs].chars().count();
                            let new_cursor = cs_chars + insert.chars().count();
                            on_change_kd.call(new_v);
                            pending_cursor.set(Some(new_cursor));
                            ac_open.set(false);
                            return;
                        }
                        (AcOption::Match { insert, .. }, Some(InnermostBracket::Curly(cs, ce))) => {
                            let end = ce.min(cur_b).max(cs);
                            let new_v = format!("{}{}{}", &v_now[..cs], insert, &v_now[end..]);
                            let cs_chars = v_now[..cs].chars().count();
                            let new_cursor = cs_chars + insert.chars().count();
                            on_change_kd.call(new_v);
                            pending_cursor.set(Some(new_cursor));
                            ac_open.set(false);
                            return;
                        }
                        _ => {}
                    }
                }
            }
            if key == "Escape" {
                ev.prevent_default();
                ac_open.set(false);
                return;
            }
        }

        // Standard editor auto-bracket behaviors driven off the live textarea state.
        // Reading from the DOM here (rather than the prop / cursor signal) is the only
        // way to get the cursor position synchronously before the keypress is applied.
        #[cfg(target_arch = "wasm32")]
        {
            // Skip-over: typing a closing bracket when it's already the next character
            // advances the cursor instead of inserting a duplicate.
            if let Some(close_c) = key.chars().next().filter(|c| matching_open(*c).is_some()) {
                if key.chars().count() == 1 {
                    if let Some((value, sel_start, sel_end)) = read_textarea_state(&id_for_keydown) {
                        if sel_start == sel_end {
                            let byte_pos = nth_char_byte(&value, sel_start);
                            if value[byte_pos..].chars().next() == Some(close_c) {
                                ev.prevent_default();
                                pending_cursor.set(Some(sel_start + 1));
                                return;
                            }
                        }
                    }
                }
            }
            // Pair-delete: backspace between an empty auto-inserted pair removes both.
            if key == "Backspace" && !ev.modifiers().contains(Modifiers::SHIFT) {
                if let Some((value, sel_start, sel_end)) = read_textarea_state(&id_for_keydown) {
                    if sel_start == sel_end && sel_start > 0 {
                        let cur_byte = nth_char_byte(&value, sel_start);
                        let prev_byte = nth_char_byte(&value, sel_start - 1);
                        let prev_char = value[prev_byte..cur_byte].chars().next();
                        let next_char = value[cur_byte..].chars().next();
                        let pair = match (prev_char, next_char) {
                            (Some(p), Some(n)) => matching_close(p) == Some(n),
                            _ => false,
                        };
                        if pair {
                            ev.prevent_default();
                            let next_len = next_char.map(|c| c.len_utf8()).unwrap_or(0);
                            let new_v = format!(
                                "{}{}",
                                &value[..prev_byte],
                                &value[cur_byte + next_len..]
                            );
                            on_change_kd.call(new_v);
                            pending_cursor.set(Some(sel_start - 1));
                            return;
                        }
                    }
                }
            }
        }

        // Plain Enter inserts a newline. Stop propagation so the parent form's
        // Enter-handler (which prevents default to block submission) doesn't squash it.
        // Shift+Enter still bubbles up so the modal's Save shortcut keeps working.
        if key == "Enter" && !ev.modifiers().contains(Modifiers::SHIFT) {
            ev.stop_propagation();
        }
        let _ = id_for_keydown.clone();
    };

    /// Read selection from the textarea after the browser applies the key/click, then
    /// update `cursor_pos` (drives autocomplete + opposing-bracket highlight).
    let schedule_cursor_sync = {
        let id_base = input_id.clone();
        move || {
            let id = id_base.clone();
            spawn(async move {
                #[cfg(target_arch = "wasm32")]
                {
                    gloo_timers::future::TimeoutFuture::new(0).await;
                    if let Some((_, start, _)) = read_textarea_state(&id) {
                        cursor_pos.set(Some(start));
                    }
                }
                #[cfg(not(target_arch = "wasm32"))]
                let _ = id;
            });
        }
    };
    // Rc so keyup/click/select/focus handlers can all share one scheduler.
    let schedule_cursor_sync = Rc::new(RefCell::new(schedule_cursor_sync));

    let schedule_keyup = schedule_cursor_sync.clone();
    let onkeyup_handler = move |_| {
        (schedule_keyup.borrow_mut())();
    };

    // Click / selection changes without a keyup path (e.g. mouse).
    let schedule_select = schedule_cursor_sync.clone();
    let onselect_handler = move |_| {
        (schedule_select.borrow_mut())();
    };
    let schedule_click = schedule_cursor_sync.clone();
    let onclick_handler = move |_| {
        (schedule_click.borrow_mut())();
    };

    // Keep the bracket-highlight backdrop scrolled in lockstep with the textarea.
    let id_for_scroll = input_id.clone();
    let onscroll_handler = move |_| {
        #[cfg(target_arch = "wasm32")]
        {
            if let Some(window) = web_sys::window() {
                if let Some(doc) = window.document() {
                    if let Ok(Some(ta_el)) = doc.query_selector(&format!("#{}", id_for_scroll)) {
                        if let Ok(ta) = ta_el.dyn_into::<web_sys::HtmlTextAreaElement>() {
                            if let Ok(Some(hl_el)) =
                                doc.query_selector(&format!("#{}-bracket-hl", id_for_scroll))
                            {
                                hl_el.set_scroll_top(ta.scroll_top());
                                hl_el.set_scroll_left(ta.scroll_left());
                            }
                        }
                    }
                }
            }
        }
        #[cfg(not(target_arch = "wasm32"))]
        let _ = &id_for_scroll;
    };

    let schedule_focus = schedule_cursor_sync.clone();
    let onfocus_handler = move |_| {
        ac_open.set(true);
        (schedule_focus.borrow_mut())();
    };

    let url_for_blur = tournament_url.clone();
    let value_rc_blur = value_rc.clone();
    let expected_type_blur = expected_type.clone();
    let onblur_handler = move |_| {
        ac_open.set(false);
        let expr = value_rc_blur.as_ref().clone();
        let url = url_for_blur.clone();
        let expected = expected_type_blur.clone();
        if expr.trim().is_empty() {
            error_msg.set(None);
            simplified_msg.set(None);
            if let Some(h) = on_validity_change {
                h.call(None);
            }
            return;
        }
        if url.is_empty() {
            return;
        }
        spawn(async move {
            let validated_for = expr.clone();
            match api::validate_dsl(&url, &expr).await {
                Ok(res) => {
                    apply_validation_response(
                        res,
                        validated_for,
                        expected.as_deref(),
                        error_msg,
                        simplified_msg,
                        on_validity_change,
                    );
                }
                Err(e) => {
                    error_msg.set(Some(e.clone()));
                    simplified_msg.set(None);
                    if let Some(h) = on_validity_change {
                        h.call(Some(Err(e)));
                    }
                }
            }
        });
    };

    // Build dropdown items.
    let team_options_for_render = team_options_rc.clone();
    let tags_for_render = tags_rc.clone();
    let matches_for_render = matches_rc.clone();
    let value_rc_click = value_rc.clone();
    let on_change_click = on_change.clone();
    let click_rc: Rc<RefCell<Box<dyn FnMut(usize)>>> = {
        let opts = ac_options.clone();
        Rc::new(RefCell::new(Box::new(move |idx: usize| {
            let Some(opt) = opts.get(idx).cloned() else {
                return;
            };
            let v_now = value_rc_click.as_ref().clone();
            let cur_char = cursor_pos().unwrap_or(v_now.chars().count());
            let cur_b = cursor_byte(&v_now, cur_char);
            let inn_now = innermost_around_cursor(&v_now, cur_char);
            match (opt, inn_now) {
                (AcOption::Function { name, .. }, Some(InnermostBracket::Paren(cs, ce))) => {
                    let end = ce.min(cur_b).max(cs);
                    let prefix_end = v_now[cs..end]
                        .find(|c: char| c.is_whitespace())
                        .map(|i| cs + i)
                        .unwrap_or(end);
                    let new_v = format!("{}{}{}", &v_now[..cs], name, &v_now[prefix_end..]);
                    let cs_chars = v_now[..cs].chars().count();
                    let new_cursor = cs_chars + name.chars().count();
                    on_change_click.call(new_v);
                    pending_cursor.set(Some(new_cursor));
                    ac_open.set(false);
                }
                (
                    AcOption::Team { insert, .. }
                    | AcOption::Tag { insert, .. }
                    | AcOption::MatchRef { insert, .. },
                    Some(InnermostBracket::Square(cs, ce)),
                ) => {
                    let end = ce.min(cur_b).max(cs);
                    let new_v = format!("{}{}{}", &v_now[..cs], insert, &v_now[end..]);
                    let cs_chars = v_now[..cs].chars().count();
                    let new_cursor = cs_chars + insert.chars().count();
                    on_change_click.call(new_v);
                    pending_cursor.set(Some(new_cursor));
                    ac_open.set(false);
                }
                (AcOption::Match { insert, .. }, Some(InnermostBracket::Curly(cs, ce))) => {
                    let end = ce.min(cur_b).max(cs);
                    let new_v = format!("{}{}{}", &v_now[..cs], insert, &v_now[end..]);
                    let cs_chars = v_now[..cs].chars().count();
                    let new_cursor = cs_chars + insert.chars().count();
                    on_change_click.call(new_v);
                    pending_cursor.set(Some(new_cursor));
                    ac_open.set(false);
                }
                _ => {}
            }
        })))
    };

    let dropdown_items: Vec<_> = ac_options
        .iter()
        .enumerate()
        .map(|(idx, opt)| {
            let click = click_rc.clone();
            let is_active = idx == ac_idx;
            let li_class = if is_active {
                "ass-entry-ac-item ass-entry-ac-item-active"
            } else {
                "ass-entry-ac-item"
            };
            let inner = match opt {
                AcOption::Function { name, signature, description } => {
                    let n = name.clone();
                    let s = signature.clone();
                    let d = description.clone();
                    rsx! {
                        span { class: "ass-entry-ac-fn-name", "{n}" }
                        span { class: "ass-entry-ac-fn-sig text-muted", " {s}" }
                        div { class: "ass-entry-ac-fn-desc text-muted small", "{d}" }
                    }
                }
                AcOption::Team { display, photo, .. } => {
                    let d = display.clone();
                    if let Some(p) = photo.clone() {
                        rsx! {
                            img {
                                class: "team-token-avatar small me-1 rounded-circle",
                                style: "width: 1.4em; height: 1.4em; object-fit: cover;",
                                src: "{base_url}/static/{p}",
                                alt: "{d}",
                            }
                            span { "{d}" }
                        }
                    } else {
                        rsx! {
                            span { class: "team-token-avatar small me-1", "{d.chars().next().unwrap_or('?')}" }
                            span { "{d}" }
                        }
                    }
                }
                AcOption::Tag { display, resolved_team, .. } => {
                    let d = display.clone();
                    let resolved_node = resolved_team
                        .as_ref()
                        .and_then(|tid| team_options_for_render.iter().find(|t| &t.id == tid))
                        .map(|t| {
                            let label = team_short_label(t);
                            let photo = t.profile_photo.clone();
                            rsx! {
                                span { class: "ass-entry-ac-resolved text-muted ms-2",
                                    " → "
                                    if let Some(p) = photo {
                                        img {
                                            src: "{base_url}/static/{p}",
                                            alt: "",
                                            class: "ass-atom-avatar rounded-circle",
                                        }
                                    } else {
                                        span { class: "ass-atom-avatar ass-atom-avatar-text", "{label.chars().next().unwrap_or('?')}" }
                                    }
                                    span { "{label}" }
                                }
                            }
                        });
                    rsx! {
                        span { class: "ass-entry-ac-row",
                            img { class: "icon-primary-svg me-1", src: "{base_url}/static/tag.svg", alt: "Tag", style: "width: 1.25em; height: 1.25em;" }
                            span { "{d}" }
                            if let Some(r) = resolved_node { {r} }
                        }
                    }
                }
                AcOption::MatchRef { display, is_winner, .. } => {
                    let d = display.clone();
                    let badge = if *is_winner { "winner" } else { "loser" };
                    rsx! {
                        img { class: "icon-primary-svg me-1", src: "{base_url}/static/reference.svg", alt: "Reference", style: "width: 1.25em; height: 1.25em;" }
                        span { "{d}" }
                        span { class: "team-token-badge ms-1 {badge}-badge small", "{badge}" }
                    }
                }
                AcOption::Match { display, field, team1, team2, refs, .. } => {
                    let d = display.clone();
                    let field_str = field.clone().unwrap_or_default();
                    let teams_for_atom = team_options_for_render.clone();
                    let tags_for_atom = tags_for_render.clone();
                    let team1_node = team1.as_deref().map(|raw| render_atom_compact(raw, &base_url, teams_for_atom.as_ref(), tags_for_atom.as_ref()));
                    let teams_for_atom2 = team_options_for_render.clone();
                    let tags_for_atom2 = tags_for_render.clone();
                    let team2_node = team2.as_deref().map(|raw| render_atom_compact(raw, &base_url, teams_for_atom2.as_ref(), tags_for_atom2.as_ref()));
                    let teams_for_refs = team_options_for_render.clone();
                    let tags_for_refs = tags_for_render.clone();
                    let refs_clone = refs.clone();
                    let ref_nodes: Vec<_> = refs_clone
                        .iter()
                        .map(|raw| render_atom_compact(raw, &base_url, teams_for_refs.as_ref(), tags_for_refs.as_ref()))
                        .collect();
                    rsx! {
                        div { class: "ass-entry-ac-match-head",
                            img { class: "icon-primary-svg me-1", src: "{base_url}/static/reference.svg", alt: "Match", style: "width: 1.25em; height: 1.25em;" }
                            span { class: "ass-entry-ac-match-name", "{d}" }
                            if !field_str.is_empty() {
                                span { class: "ass-entry-ac-match-field text-muted ms-2", "on {field_str}" }
                            }
                        }
                        div { class: "ass-entry-ac-match-meta small text-muted",
                            if let Some(t) = team1_node { {t} }
                            span { class: "ass-entry-ac-vs mx-1", "vs" }
                            if let Some(t) = team2_node { {t} }
                            if !ref_nodes.is_empty() {
                                span { class: "ass-entry-ac-refs ms-2",
                                    span { class: "me-1", "refs:" }
                                    for ref_n in ref_nodes.iter() {
                                        {ref_n.clone()}
                                    }
                                }
                            }
                        }
                    }
                }
            };
            rsx! {
                li {
                    key: "{idx}",
                    class: "{li_class}",
                    onmousedown: move |ev: Event<MouseData>| { ev.prevent_default(); },
                    onclick: move |_| { click.borrow_mut()(idx); },
                    onmouseenter: move |_| { ac_index.set(idx); },
                    {inner}
                }
            }
        })
        .collect();

    let preview_chips = render_expression_chips(
        &v,
        team_options_for_render.as_ref(),
        tags_for_render.as_ref(),
        matches_for_render.as_ref(),
        &base_url,
        "input",
    );
    // Show the simplified row only when the cached simplification was computed for the
    // exact value currently in the input — keeps stale results from confusing the user.
    let simplified_value: Option<String> = simplified_msg().and_then(|(input_at, simp)| {
        if input_at.trim() == v.trim() {
            Some(simp)
        } else {
            None
        }
    });
    let simplified_chips = simplified_value.as_deref().map(|simp| {
        render_expression_chips(
            simp,
            team_options_for_render.as_ref(),
            tags_for_render.as_ref(),
            matches_for_render.as_ref(),
            &base_url,
            "simp",
        )
    });
    let _ = preview_tokens;

    // Opposing-bracket highlight: backdrop layer paints matched/unmatched marks
    // under a transparent-background textarea (text still comes from the textarea).
    let bracket_hl = cur.and_then(|c| bracket_highlight_at_cursor(&v, c));
    let hl_segments = bracket_highlight_segments(&v, bracket_hl);
    let hl_id = format!("{input_id}-bracket-hl");

    rsx! {
        div { class: "ass-entry position-relative",
            div { class: "ass-entry-editor",
                // Backdrop: same glyphs as the textarea; only highlighted brackets are visible
                // (via background). Must stay pixel-aligned (font/padding/border/line-height).
                pre {
                    id: "{hl_id}",
                    class: "ass-entry-bracket-hl",
                    aria_hidden: "true",
                    for (i, (seg, cls)) in hl_segments.iter().enumerate() {
                        if let Some(c) = cls {
                            span { key: "{i}", class: "{c}", "{seg}" }
                        } else {
                            span { key: "{i}", "{seg}" }
                        }
                    }
                }
                textarea {
                    id: "{input_id}",
                    class: "form-control font-monospace ass-entry-input",
                    rows: "1",
                    placeholder: "{placeholder}",
                    value: "{value}",
                    oninput: oninput_handler,
                    onkeydown: onkeydown_handler,
                    onkeyup: onkeyup_handler,
                    onselect: onselect_handler,
                    onclick: onclick_handler,
                    onscroll: onscroll_handler,
                    onfocus: onfocus_handler,
                    onblur: onblur_handler,
                }
            }
            if ac_open() && !ac_options.is_empty() {
                ul { class: "ass-entry-ac dropdown-menu show",
                    for item in dropdown_items.iter() {
                        {item.clone()}
                    }
                }
            }
            if !value.trim().is_empty() {
                div { class: "ass-entry-preview small",
                    for chip in preview_chips.iter() {
                        {chip.clone()}
                    }
                }
            }
            if let Some(chips) = simplified_chips.as_ref() {
                div { class: "ass-entry-simplified small",
                    span { class: "ass-entry-simplified-label text-muted me-1", "simplified:" }
                    for chip in chips.iter() {
                        {chip.clone()}
                    }
                }
            }
            if let Some(err) = error_msg() {
                div { class: "form-text text-danger ass-entry-error", "✗ {err}" }
            } else if simplified_value.is_some() {
                div { class: "form-text text-success", "✓ Valid" }
            } else if !value.trim().is_empty() {
                div { class: "form-text text-success", "✓" }
            }
        }
    }
}

#[cfg(test)]
mod bracket_highlight_tests {
    use super::*;

    #[test]
    fn matched_open_paren() {
        let s = "(+ 1 2)";
        // cursor on '('
        assert_eq!(
            bracket_highlight_at_cursor(s, 0),
            Some(BracketPairHighlight::Matched { open: 0, close: 6 })
        );
        // cursor just after '('
        assert_eq!(
            bracket_highlight_at_cursor(s, 1),
            Some(BracketPairHighlight::Matched { open: 0, close: 6 })
        );
        // cursor on ')'
        assert_eq!(
            bracket_highlight_at_cursor(s, 6),
            Some(BracketPairHighlight::Matched { open: 0, close: 6 })
        );
    }

    #[test]
    fn nested_parens_pick_adjacent_pair() {
        let s = "(outer (inner))";
        // cursor on inner open — second '('
        let inner_open = s.find("(inner)").unwrap();
        let inner_close = inner_open + "(inner".len(); // position of ')' after "inner"
        assert_eq!(&s[inner_close..inner_close + 1], ")");
        let cur = s[..inner_open].chars().count();
        assert_eq!(
            bracket_highlight_at_cursor(s, cur),
            Some(BracketPairHighlight::Matched {
                open: inner_open,
                close: inner_close
            })
        );
        // cursor on outer close — last ')'
        let outer_close = s.len() - 1;
        let cur_outer = s.chars().count() - 1;
        assert_eq!(
            bracket_highlight_at_cursor(s, cur_outer),
            Some(BracketPairHighlight::Matched {
                open: 0,
                close: outer_close
            })
        );
    }

    #[test]
    fn unmatched_open_is_red() {
        let s = "(+ 1 2";
        assert_eq!(
            bracket_highlight_at_cursor(s, 0),
            Some(BracketPairHighlight::Unmatched { pos: 0 })
        );
    }

    #[test]
    fn unmatched_close_is_red() {
        let s = "+ 1 2)";
        let close_pos = s.find(')').unwrap();
        let cur = s.chars().count() - 1;
        assert_eq!(
            bracket_highlight_at_cursor(s, cur),
            Some(BracketPairHighlight::Unmatched { pos: close_pos })
        );
    }

    #[test]
    fn square_and_curly() {
        let s = "[team]{m}";
        assert_eq!(
            bracket_highlight_at_cursor(s, 0),
            Some(BracketPairHighlight::Matched { open: 0, close: 5 })
        );
        let curly_open = s.find('{').unwrap();
        let curly_cur = s[..curly_open].chars().count();
        assert_eq!(
            bracket_highlight_at_cursor(s, curly_cur),
            Some(BracketPairHighlight::Matched {
                open: curly_open,
                close: s.len() - 1
            })
        );
    }

    #[test]
    fn no_highlight_away_from_brackets() {
        let s = "(+ 1 2)";
        // cursor on '1'
        let cur = s.find('1').unwrap(); // byte == char for ascii
        assert_eq!(bracket_highlight_at_cursor(s, cur), None);
    }

    #[test]
    fn segments_mark_both_ends() {
        let s = "(+ 1)";
        let hl = bracket_highlight_at_cursor(s, 0);
        let segs = bracket_highlight_segments(s, hl);
        let joined: String = segs.iter().map(|(t, _)| t.as_str()).collect();
        assert_eq!(joined, s);
        let marked: Vec<_> = segs
            .iter()
            .filter_map(|(t, c)| c.map(|cls| (t.as_str(), cls)))
            .collect();
        assert_eq!(
            marked,
            vec![
                ("(", "ass-entry-bracket-match"),
                (")", "ass-entry-bracket-match")
            ]
        );
    }
}
