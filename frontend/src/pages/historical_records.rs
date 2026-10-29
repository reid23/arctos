use crate::Route;
use crate::api;
use crate::components::{TeamSelectionField, ToolPageHeader};
use crate::types::{
    HistoricalMatch, HistoricalMatchInput, HistoricalPoint, HistoricalRecordsResponse,
    HistoricalTeam, MatchSetupData, TeamOption,
};
use dioxus::html::ModifiersInteraction;
use dioxus::prelude::*;
use wasm_bindgen::JsCast;

const TEAM_TOKEN_CSS: &str = include_str!("schedule_timeline.css");
const MATCH_NAME_INPUT_ID: &str = "historical-match-name";
const TEAM1: &str = "TEAM1";
const TEAM2: &str = "TEAM2";
const SETS: &str = "SETS";
const STONES: &str = "STONES";

type Records = Signal<Option<HistoricalRecordsResponse>>;

fn confirm(message: &str) -> bool {
    web_sys::window()
        .and_then(|window| window.confirm_with_message(message).ok())
        .unwrap_or(false)
}

fn date_only(iso: &str) -> &str {
    iso.split('T').next().unwrap_or(iso)
}

fn focus_and_select(element_id: &'static str) {
    #[cfg(target_arch = "wasm32")]
    spawn(async move {
        gloo_timers::future::TimeoutFuture::new(0).await;
        let element = web_sys::window()
            .and_then(|window| window.document())
            .and_then(|document| document.get_element_by_id(element_id));
        if let Some(input) =
            element.and_then(|element| element.dyn_into::<web_sys::HtmlInputElement>().ok())
        {
            let _ = input.focus();
            input.select();
        }
    });
    #[cfg(not(target_arch = "wasm32"))]
    let _ = element_id;
}

/// Team inputs only commit typed text on blur, so blur before saving from the keyboard.
fn blur_focused_element() {
    let active = web_sys::window()
        .and_then(|window| window.document())
        .and_then(|document| document.active_element());
    if let Some(element) =
        active.and_then(|element| element.dyn_into::<web_sys::HtmlElement>().ok())
    {
        let _ = element.blur();
    }
}

/// `Match name::winner` or `Match name::loser`, as stored in the `_initial` fields.
fn parse_reference(token: &str) -> Option<(String, &'static str)> {
    let (match_name, qualifier) = token.trim().rsplit_once("::")?;
    let match_name = match_name.trim();
    if match_name.is_empty() {
        return None;
    }
    match qualifier.trim().to_lowercase().as_str() {
        "winner" => Some((match_name.to_string(), "winner")),
        "loser" => Some((match_name.to_string(), "loser")),
        _ => None,
    }
}

fn find_team_id(token: &str, teams: &[HistoricalTeam]) -> Option<String> {
    let token = token.trim();
    let lowered = token.to_lowercase();
    teams
        .iter()
        .find(|team| team.id == token || team.name.to_lowercase() == lowered)
        .map(|team| team.id.clone())
}

fn team_name(team_id: &str, teams: &[HistoricalTeam]) -> String {
    teams
        .iter()
        .find(|team| team.id == team_id)
        .map(|team| team.name.clone())
        .unwrap_or_else(|| team_id.to_string())
}

fn resolve_slot(
    token: &str,
    teams: &[HistoricalTeam],
    matches: &[HistoricalMatch],
) -> Option<String> {
    let Some((match_name, qualifier)) = parse_reference(token) else {
        return find_team_id(token, teams);
    };
    let referenced = matches
        .iter()
        .find(|candidate| candidate.name == match_name)?;
    let winner_is_team1 = referenced.winner.as_deref()? == TEAM1;
    if (qualifier == "winner") == winner_is_team1 {
        referenced.team1.clone()
    } else {
        referenced.team2.clone()
    }
}

fn slot_label(
    token: &str,
    teams: &[HistoricalTeam],
    matches: &[HistoricalMatch],
    fallback: &str,
) -> String {
    if token.trim().is_empty() {
        return fallback.to_string();
    }
    match resolve_slot(token, teams, matches) {
        Some(team_id) => team_name(&team_id, teams),
        None => token.trim().to_string(),
    }
}

/// `(set, team 1 points, team 2 points)` per set, in set order.
fn set_tallies(points: &[HistoricalPoint]) -> Vec<(u32, u32, u32)> {
    let mut tallies: Vec<(u32, u32, u32)> = Vec::new();
    for point in points {
        let index = match tallies.iter().position(|tally| tally.0 == point.set) {
            Some(index) => index,
            None => {
                tallies.push((point.set, 0, 0));
                tallies.len() - 1
            }
        };
        if point.winner == TEAM1 {
            tallies[index].1 += 1;
        } else {
            tallies[index].2 += 1;
        }
    }
    tallies.sort_by_key(|tally| tally.0);
    tallies
}

#[derive(Clone, Debug, PartialEq)]
struct SetScore {
    team1: String,
    team2: String,
}

impl SetScore {
    fn is_filled(&self) -> bool {
        !self.team1.trim().is_empty() || !self.team2.trim().is_empty()
    }

    /// Both sides of a filled row. A blank side is zero. `None` when a side is not a whole number.
    fn points(&self) -> Option<(u32, u32)> {
        if !self.is_filled() {
            return None;
        }
        let parse = |raw: &str| {
            let trimmed = raw.trim();
            if trimmed.is_empty() {
                Some(0)
            } else {
                trimmed.parse::<u32>().ok()
            }
        };
        Some((parse(&self.team1)?, parse(&self.team2)?))
    }
}

fn carried_set_type(matches: &[HistoricalMatch]) -> String {
    matches
        .last()
        .and_then(|existing| existing.set_type.clone())
        .filter(|set_type| set_type == SETS || set_type == STONES)
        .unwrap_or_else(|| SETS.to_string())
}

fn scores_from_points(points: &[HistoricalPoint]) -> Vec<SetScore> {
    set_tallies(points)
        .into_iter()
        .map(|(_, team1_points, team2_points)| SetScore {
            team1: team1_points.to_string(),
            team2: team2_points.to_string(),
        })
        .collect()
}

/// Sets: more sets won, then more points. Stones: more points across every set.
fn inferred_winner(sets: &[SetScore], set_type: &str) -> Option<&'static str> {
    let tallies: Vec<(u32, u32)> = sets.iter().filter_map(SetScore::points).collect();
    if tallies.is_empty() {
        return None;
    }
    let team1_points: u32 = tallies.iter().map(|tally| tally.0).sum();
    let team2_points: u32 = tallies.iter().map(|tally| tally.1).sum();
    if set_type == STONES {
        return match team1_points.cmp(&team2_points) {
            std::cmp::Ordering::Greater => Some(TEAM1),
            std::cmp::Ordering::Less => Some(TEAM2),
            std::cmp::Ordering::Equal => None,
        };
    }
    let team1_sets = tallies.iter().filter(|tally| tally.0 > tally.1).count();
    let team2_sets = tallies.iter().filter(|tally| tally.1 > tally.0).count();
    if team1_sets != team2_sets {
        return Some(if team1_sets > team2_sets {
            TEAM1
        } else {
            TEAM2
        });
    }
    match team1_points.cmp(&team2_points) {
        std::cmp::Ordering::Greater => Some(TEAM1),
        std::cmp::Ordering::Less => Some(TEAM2),
        std::cmp::Ordering::Equal => None,
    }
}

fn points_from_scores(sets: &[SetScore]) -> Result<Vec<HistoricalPoint>, String> {
    let mut points = Vec::new();
    let mut set_number = 1u32;
    for set in sets {
        if !set.is_filled() {
            continue;
        }
        let Some((team1_points, team2_points)) = set.points() else {
            return Err(format!("Set {set_number} scores have to be whole numbers."));
        };
        if team1_points == 0 && team2_points == 0 {
            continue;
        }
        for _ in 0..team1_points {
            points.push(HistoricalPoint {
                set: set_number,
                winner: TEAM1.to_string(),
            });
        }
        for _ in 0..team2_points {
            points.push(HistoricalPoint {
                set: set_number,
                winner: TEAM2.to_string(),
            });
        }
        set_number += 1;
    }
    Ok(points)
}

fn score_summary(points: &[HistoricalPoint]) -> String {
    set_tallies(points)
        .iter()
        .map(|(_, team1_points, team2_points)| format!("{team1_points}-{team2_points}"))
        .collect::<Vec<_>>()
        .join(", ")
}

/// Continues a trailing number in the last entered name ("Pool A 3" becomes "Pool A 4").
fn next_match_name(matches: &[HistoricalMatch]) -> String {
    let taken = |name: &str| matches.iter().any(|existing| existing.name == name);
    if let Some(last) = matches.last() {
        let prefix = last
            .name
            .trim_end_matches(|character: char| character.is_ascii_digit());
        if let Ok(number) = last.name[prefix.len()..].parse::<u64>() {
            let mut candidate = number + 1;
            while taken(&format!("{prefix}{candidate}")) {
                candidate += 1;
            }
            return format!("{prefix}{candidate}");
        }
    }
    let mut candidate = matches.len() + 1;
    while taken(&format!("Match {candidate}")) {
        candidate += 1;
    }
    format!("Match {candidate}")
}

fn team_options(teams: &[HistoricalTeam]) -> Vec<TeamOption> {
    teams
        .iter()
        .map(|team| TeamOption {
            id: team.id.clone(),
            pseudonym: Some(team.name.clone()),
            shortname: team.shortname.clone(),
            profile_photo: team.profile_photo.clone(),
            placeholder: team.placeholder,
        })
        .collect()
}

/// Matches that can be referenced, most recent first.
fn reference_targets(
    matches: &[HistoricalMatch],
    excluded_uuid: Option<&str>,
) -> Vec<MatchSetupData> {
    matches
        .iter()
        .rev()
        .filter(|candidate| Some(candidate.uuid.as_str()) != excluded_uuid)
        .map(|candidate| MatchSetupData {
            uuid: candidate.uuid.clone(),
            name: candidate.name.clone(),
            field: None,
            team1: candidate.team1.clone(),
            team2: candidate.team2.clone(),
            team1_initial: candidate.team1_initial.clone(),
            team2_initial: candidate.team2_initial.clone(),
            status: "COMPLETED".to_string(),
            scheduled_start_time: None,
            nominal_start_time: None,
            confirmed_start_time: None,
            completed_time: None,
            schedule_type: None,
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
            match_winner: candidate.winner.clone(),
        })
        .collect()
}

fn split_tokens(value: &str) -> impl Iterator<Item = &str> {
    value
        .split(',')
        .map(str::trim)
        .filter(|token| !token.is_empty())
}

#[derive(Clone, Debug, PartialEq)]
struct MatchDraft {
    name: String,
    /// Team id, typed team name, or a `Match::winner` / `Match::loser` reference.
    team1: String,
    team2: String,
    /// Comma-separated, same token forms as the team slots.
    referees: String,
    /// Filled set rows only. The entry form always draws one empty row after these.
    sets: Vec<SetScore>,
    /// `SETS` or `STONES`.
    set_type: String,
    /// `None` follows the score.
    chosen_winner: Option<String>,
}

impl MatchDraft {
    fn new(name: String, set_type: String) -> Self {
        Self {
            name,
            team1: String::new(),
            team2: String::new(),
            referees: String::new(),
            sets: Vec::new(),
            set_type,
            chosen_winner: None,
        }
    }

    fn from_match(existing: &HistoricalMatch) -> Self {
        let slot = |initial: &Option<String>, team: &Option<String>| match initial.as_deref() {
            Some(token) if parse_reference(token).is_some() => token.to_string(),
            _ => team.clone().unwrap_or_default(),
        };
        let referees = existing
            .referees
            .iter()
            .map(|referee| slot(&referee.initial, &referee.team_id))
            .filter(|token| !token.is_empty())
            .collect::<Vec<_>>()
            .join(", ");
        let sets = scores_from_points(&existing.points);
        let set_type = existing
            .set_type
            .clone()
            .filter(|set_type| set_type == SETS || set_type == STONES)
            .unwrap_or_else(|| SETS.to_string());
        let chosen_winner = match existing.winner.as_deref() {
            Some(winner) if Some(winner) != inferred_winner(&sets, &set_type) => {
                Some(winner.to_string())
            }
            _ => None,
        };
        Self {
            name: existing.name.clone(),
            team1: slot(&existing.team1_initial, &existing.team1),
            team2: slot(&existing.team2_initial, &existing.team2),
            referees,
            sets,
            set_type,
            chosen_winner,
        }
    }

    fn winner(&self) -> Option<String> {
        self.chosen_winner
            .clone()
            .or_else(|| inferred_winner(&self.sets, &self.set_type).map(String::from))
    }

    fn set_score(&mut self, index: usize, team1: bool, raw: String) {
        let value: String = raw
            .chars()
            .filter(|character| character.is_ascii_digit())
            .collect();
        if index > self.sets.len() {
            return;
        }
        if index == self.sets.len() {
            if value.is_empty() {
                return;
            }
            self.sets.push(SetScore {
                team1: String::new(),
                team2: String::new(),
            });
        }
        if team1 {
            self.sets[index].team1 = value;
        } else {
            self.sets[index].team2 = value;
        }
        if !self.sets[index].is_filled() {
            self.sets.remove(index);
        }
    }

    /// Typed names that are neither references nor teams in the tournament.
    fn unknown_team_names(&self, teams: &[HistoricalTeam]) -> Vec<String> {
        let mut names: Vec<String> = Vec::new();
        let tokens = [self.team1.trim(), self.team2.trim()]
            .into_iter()
            .filter(|token| !token.is_empty())
            .chain(split_tokens(&self.referees));
        for token in tokens {
            if parse_reference(token).is_some() || find_team_id(token, teams).is_some() {
                continue;
            }
            if !names
                .iter()
                .any(|name| name.to_lowercase() == token.to_lowercase())
            {
                names.push(token.to_string());
            }
        }
        names
    }

    fn to_input(
        &self,
        winner: String,
        teams: &[HistoricalTeam],
    ) -> Result<HistoricalMatchInput, String> {
        let slot_value = |token: &str| {
            if parse_reference(token).is_some() {
                token.trim().to_string()
            } else {
                find_team_id(token, teams).unwrap_or_else(|| token.trim().to_string())
            }
        };
        Ok(HistoricalMatchInput {
            name: self.name.trim().to_string(),
            team1: slot_value(&self.team1),
            team2: slot_value(&self.team2),
            referees: split_tokens(&self.referees).map(slot_value).collect(),
            winner,
            set_type: self.set_type.clone(),
            points: points_from_scores(&self.sets)?,
        })
    }
}

fn has_unsaved_changes(
    draft: &MatchDraft,
    editing: Option<&str>,
    matches: &[HistoricalMatch],
) -> bool {
    match editing.and_then(|uuid| matches.iter().find(|existing| existing.uuid == uuid)) {
        Some(existing) => *draft != MatchDraft::from_match(existing),
        None => {
            !draft.team1.is_empty()
                || !draft.team2.is_empty()
                || draft.sets.iter().any(SetScore::is_filled)
        }
    }
}

#[component]
pub fn HistoricalRecords(url: String) -> Element {
    let mut records: Records = use_signal(|| None);
    let mut load_error = use_signal(|| None::<String>);
    let mut draft = use_signal(|| MatchDraft::new(String::new(), SETS.to_string()));
    let editing = use_signal(|| None::<String>);
    let mut publish_error = use_signal(|| None::<String>);

    let url_for_load = url.clone();
    use_future(move || {
        let url = url_for_load.clone();
        async move {
            match api::historical_records(&url).await {
                Ok(response) => {
                    draft.set(MatchDraft::new(
                        next_match_name(&response.matches),
                        carried_set_type(&response.matches),
                    ));
                    records.set(Some(response));
                }
                Err(error) => load_error.set(Some(error)),
            }
        }
    });

    if let Some(error) = load_error() {
        return rsx! {
            div { class: "alert alert-danger", "{error}" }
        };
    }
    let Some(data) = records() else {
        return rsx! {
            p { class: "text-muted", "Loading…" }
        };
    };
    let tournament = data.tournament.clone();
    let url_for_publish = url.clone();

    rsx! {
        style { {TEAM_TOKEN_CSS} }
        ToolPageHeader {
            title: "Records".to_string(),
            description: Some("Enter the matches and scores. Teams and players see invitations, and this event on their profiles, only after you publish.".to_string()),
        }
        div { class: "d-flex flex-wrap align-items-center gap-3 mb-3",
                div { class: "form-check form-switch mb-0",
                    title: "Teams and players see invitations, and this tournament on their profiles, only after you publish.",
                    input {
                        class: "form-check-input",
                        r#type: "checkbox",
                        id: "historical-published",
                        checked: tournament.published,
                        onchange: move |ev| {
                            let published = ev.checked();
                            let url = url_for_publish.clone();
                            let Some(current) = records().map(|response| response.tournament) else {
                                return;
                            };
                            spawn(async move {
                                let result = api::historical_update_details(
                                    &url,
                                    &current.name,
                                    current.location.as_deref().unwrap_or(""),
                                    date_only(&current.start_date),
                                    current.end_date.as_deref().map(date_only).unwrap_or(""),
                                    current.about.as_deref().unwrap_or(""),
                                    published,
                                )
                                .await;
                                match result {
                                    Ok(response) => {
                                        publish_error.set(None);
                                        records.set(Some(response));
                                    }
                                    Err(error) => publish_error.set(Some(error)),
                                }
                            });
                        },
                    }
                    label { class: "form-check-label", r#for: "historical-published",
                        if tournament.published { "Published" } else { "Not published" }
                    }
                }
            }
        if let Some(error) = publish_error() {
            div { class: "alert alert-danger py-2", role: "alert", "{error}" }
        }
        div { class: "row g-4",
            div { class: "col-lg-8",
                MatchEntryCard { url: url.clone(), records, draft, editing }
                MatchListCard { url: url.clone(), records, draft, editing }
            }
            div { class: "col-lg-4",
                TeamsCard { url: url.clone(), records }
            }
        }
    }
}


#[component]
fn MatchEntryCard(
    url: String,
    records: Records,
    draft: Signal<MatchDraft>,
    editing: Signal<Option<String>>,
) -> Element {
    let mut records = records;
    let mut draft = draft;
    let mut editing = editing;
    let mut error = use_signal(|| None::<String>);
    let mut saving = use_signal(|| false);

    let save = use_callback(move |_: ()| {
        if saving() {
            return;
        }
        let Some(snapshot) = records() else {
            return;
        };
        let current = draft();
        if current.name.trim().is_empty() {
            error.set(Some("Give the match a name.".to_string()));
            return;
        }
        if current.team1.trim().is_empty() || current.team2.trim().is_empty() {
            error.set(Some("Choose both teams.".to_string()));
            return;
        }
        let Some(winner) = current.winner() else {
            error.set(Some("Choose who won, or enter the score.".to_string()));
            return;
        };
        if let Err(message) = points_from_scores(&current.sets) {
            error.set(Some(message));
            return;
        }
        let new_team_names = current.unknown_team_names(&snapshot.teams);
        if !new_team_names.is_empty()
            && !confirm(&format!(
                "These teams are not in this tournament yet. Add them?\n\n{}",
                new_team_names.join("\n")
            ))
        {
            return;
        }
        let url = url.clone();
        let match_uuid = editing();
        saving.set(true);
        error.set(None);
        spawn(async move {
            let result: Result<HistoricalRecordsResponse, String> = async {
                let mut teams = snapshot.teams;
                if !new_team_names.is_empty() {
                    let response = api::historical_add_teams(&url, &new_team_names).await?;
                    teams = response.teams.clone();
                    records.set(Some(response));
                }
                let input = current.to_input(winner, &teams)?;
                api::historical_save_match(&url, match_uuid.as_deref(), &input).await
            }
            .await;
            match result {
                Ok(response) => {
                    draft.set(MatchDraft::new(
                        next_match_name(&response.matches),
                        carried_set_type(&response.matches),
                    ));
                    records.set(Some(response));
                    editing.set(None);
                    focus_and_select(MATCH_NAME_INPUT_ID);
                }
                Err(message) => error.set(Some(message)),
            }
            saving.set(false);
        });
    });

    let Some(data) = records() else {
        return rsx! {};
    };
    let current = draft();
    let options = team_options(&data.teams);
    let targets = reference_targets(&data.matches, editing().as_deref());
    let team1_label = slot_label(&current.team1, &data.teams, &data.matches, "Team 1");
    let team2_label = slot_label(&current.team2, &data.teams, &data.matches, "Team 2");
    let mut score_rows = current.sets.clone();
    score_rows.push(SetScore {
        team1: String::new(),
        team2: String::new(),
    });
    let stones = current.set_type == STONES;
    let inferred = inferred_winner(&current.sets, &current.set_type);
    let winner = current.winner();
    let winner_disagrees = matches!((&current.chosen_winner, inferred), (Some(chosen), Some(from_score)) if chosen != from_score);
    let editing_name = editing().and_then(|uuid| {
        data.matches
            .iter()
            .find(|existing| existing.uuid == uuid)
            .map(|existing| existing.name.clone())
    });

    rsx! {
        div {
            class: "edit-section",
            onkeydown: move |ev: Event<KeyboardData>| {
                let modifiers = ev.modifiers();
                let submit_modifier = modifiers.contains(Modifiers::SHIFT)
                    || modifiers.contains(Modifiers::CONTROL)
                    || modifiers.contains(Modifiers::META);
                if ev.key().to_string() == "Enter" && submit_modifier {
                    ev.prevent_default();
                    blur_focused_element();
                    #[cfg(target_arch = "wasm32")]
                    spawn(async move {
                        gloo_timers::future::TimeoutFuture::new(0).await;
                        save.call(());
                    });
                    #[cfg(not(target_arch = "wasm32"))]
                    save.call(());
                }
            },
            div { class: "edit-section-heading d-flex justify-content-between align-items-center",
                h5 { class: "mb-0",
                    if let Some(ref name) = editing_name {
                        i { class: "fas fa-pen me-2 text-primary" }
                        "Editing {name}"
                    } else {
                        "Enter a match"
                    }
                }
                span { class: "small text-muted d-none d-md-inline", "Shift+Enter saves" }
            }
            div { class: "edit-section-body",
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", "{message}" }
                }
                div { class: "row g-2",
                     label { class: "form-label h5", r#for: MATCH_NAME_INPUT_ID, "Name" }
                     input {
                         class: "form-control",
                         id: MATCH_NAME_INPUT_ID,
                         autocomplete: "off",
                         value: "{current.name}",
                         oninput: move |ev| draft.write().name = ev.value(),
                     }
		}
                div { class: "row g-2",
                    h5 { class: "mt-3 mb-0", "Teams" }
                    p { class: "form-text mt-0 mb-0",
                        "Type a team name, or "
                        code { "Semifinal 1 winner" }
                        " / "
                        code { "Semifinal 1 loser" }
                        ", to take that match's winner or loser. The bracket page draws wires from those links. New team names are added when you save."
                   }
                   div { class: "col-md-6",
                        TeamSelectionField {
                            label: "Team 1".to_string(),
                            team_options: options.clone(),
                            tags: vec![],
                            matches: targets.clone(),
                            value: current.team1.clone(),
                            on_change: move |value: String| draft.write().team1 = value,
                            multiple: false,
                            placeholder: "Team or match result".to_string(),
                            wrapper_class: Some("mb-2".to_string()),
                        }
                    }
                    div { class: "col-md-6",
                        TeamSelectionField {
                            label: "Team 2".to_string(),
                            team_options: options.clone(),
                            tags: vec![],
                            matches: targets.clone(),
                            value: current.team2.clone(),
                            on_change: move |value: String| draft.write().team2 = value,
                            multiple: false,
                            placeholder: "Team or match result".to_string(),
                            wrapper_class: Some("mb-2".to_string()),
                        }
                    }
                }
                div { class: "mb-2",
                    TeamSelectionField {
                        label: "Referees".to_string(),
                        team_options: options.clone(),
                        tags: vec![],
                        matches: targets.clone(),
                        value: current.referees.clone(),
                        on_change: move |value: String| draft.write().referees = value,
                        multiple: true,
                        placeholder: "Teams or match results".to_string(),
                        help_text: Some("Optional.".to_string()),
                        wrapper_class: Some("mb-0".to_string()),
                    }
                }
                div { class: "mb-3",
                    h5 { class: "mt-0 mb-0", "Scores" }
                    div { class: "row g-2 mb-1",
                        div { class: "col-auto", style: "width: 4.5rem;" }
                        div { class: "col small text-muted text-truncate", "{team1_label}" }
                        div { class: "col small text-muted text-truncate", "{team2_label}" }
                    }
                    for (index, set) in score_rows.iter().enumerate() {
                        {
                            let team1_score = set.team1.clone();
                            let team2_score = set.team2.clone();
                            rsx! {
                                div { class: "row g-2 align-items-center mb-2", key: "{index}",
                                    div { class: "col-auto text-muted", style: "width: 4.5rem;", "{index + 1}" }
                                    div { class: "col",
                                        input {
                                            class: "form-control",
                                            r#type: "text",
                                            inputmode: "numeric",
                                            autocomplete: "off",
                                            value: "{team1_score}",
                                            oninput: move |ev| draft.write().set_score(index, true, ev.value()),
                                        }
                                    }
                                    div { class: "col",
                                        input {
                                            class: "form-control",
                                            r#type: "text",
                                            inputmode: "numeric",
                                            autocomplete: "off",
                                            value: "{team2_score}",
                                            oninput: move |ev| draft.write().set_score(index, false, ev.value()),
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
		       div { class: "d-flex flex-wrap align-items-center gap-2 mb-3",
			     span { class: "small text-muted", "Scoring" }
			     div { class: "btn-group btn-group-sm",
				   button {
				       class: if stones { "btn btn-outline-secondary" } else { "btn btn-secondary" },
				       r#type: "button",
				       tabindex: "-1",
				       onclick: move |_| draft.write().set_type = SETS.to_string(),
				       "Sets"
				   }
				   button {
				       class: if stones { "btn btn-secondary" } else { "btn btn-outline-secondary" },
				       r#type: "button",
				       tabindex: "-1",
				       onclick: move |_| draft.write().set_type = STONES.to_string(),
				       "Stones"
				   }
			     }
			     span { class: "small text-muted",
				    if stones {
					"Winner follows total points."
				    } else {
					"Winner follows sets won."
				    }
			     }
                       }
                       div { class: "d-flex flex-wrap align-items-center gap-2 mb-3",
			     span { class: "fw-semibold me-1", "Winner" }
			     div { class: "btn-group",
				   button {
				       class: if winner.as_deref() == Some(TEAM1) { "btn btn-success" } else { "btn btn-outline-success" },
				       onclick: move |_| draft.write().chosen_winner = Some(TEAM1.to_string()),
				       i { class: "fas fa-trophy me-1" }
				       "{team1_label}"
				   }
				   button {
				       class: if winner.as_deref() == Some(TEAM2) { "btn btn-success" } else { "btn btn-outline-success" },
				       onclick: move |_| draft.write().chosen_winner = Some(TEAM2.to_string()),
				       i { class: "fas fa-trophy me-1" }
				       "{team2_label}"
				   }
			     }
			     if current.chosen_winner.is_none() && inferred.is_some() {
				 span { class: "small text-muted", "from the score" }
			     }
			     if winner_disagrees {
				 span { class: "small text-warning",
					i { class: "fas fa-exclamation-triangle me-1" }
					"Doesn't match the score."
				 }
				 button { class: "btn btn-sm btn-link p-0", onclick: move |_| draft.write().chosen_winner = None, "Use score" }
			     }
                       }

                div { class: "d-flex gap-2 col-md-6",
                    button {
                        class: "btn btn-primary",
                        disabled: saving(),
                        onclick: move |_| save.call(()),
                        if saving() {
                            "Saving..."
                        } else if editing_name.is_some() {
                            "Save changes"
                        } else {
                            "Save match"
                        }
                    }
                    if editing_name.is_some() {
                        button {
                            class: "btn btn-outline-secondary",
                            onclick: move |_| {
                                let matches = records().map(|response| response.matches).unwrap_or_default();
                                if has_unsaved_changes(&draft(), editing().as_deref(), &matches)
                                    && !confirm("Discard your changes to this match?")
                                {
                                    return;
                                }
                                draft.set(MatchDraft::new(next_match_name(&matches), carried_set_type(&matches)));
                                editing.set(None);
                                error.set(None);
                            },
                            "Cancel"
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn MatchListCard(
    url: String,
    records: Records,
    draft: Signal<MatchDraft>,
    editing: Signal<Option<String>>,
) -> Element {
    let mut records = records;
    let mut draft = draft;
    let mut editing = editing;
    let mut filter = use_signal(String::new);
    let mut error = use_signal(|| None::<String>);

    let Some(data) = records() else {
        return rsx! {};
    };
    let needle = filter().trim().to_lowercase();
    let label_for = |team: &Option<String>| {
        team.as_deref()
            .map(|id| team_name(id, &data.teams))
            .unwrap_or_else(|| "?".to_string())
    };
    let visible: Vec<&HistoricalMatch> = data
        .matches
        .iter()
        .rev()
        .filter(|existing| {
            needle.is_empty()
                || existing.name.to_lowercase().contains(&needle)
                || label_for(&existing.team1).to_lowercase().contains(&needle)
                || label_for(&existing.team2).to_lowercase().contains(&needle)
        })
        .collect();
    let total = data.matches.len();

    rsx! {
        div { class: "edit-section",
            div { class: "edit-section-heading d-flex flex-wrap justify-content-between align-items-center gap-2",
                h5 { class: "mb-0", "Matches ({total})" }
                if total > 0 {
                    input {
                        class: "form-control form-control-sm",
                        style: "max-width: 16rem;",
                        placeholder: "Filter by match or team",
                        value: "{filter}",
                        oninput: move |ev| filter.set(ev.value()),
                    }
                }
            }
            if let Some(message) = error() {
                div { class: "alert alert-danger m-3 mb-0 py-2", "{message}" }
            }
            if total == 0 {
                div { class: "edit-section-body text-muted", "Matches you save appear here, newest first." }
            } else {
                div { class: "table-responsive",
                    table { class: "table table-hover align-middle mb-0",
                        thead {
                            tr {
                                th { "Match" }
                                th { class: "text-end", "Team 1" }
                                th { class: "text-center", "Score" }
                                th { "Team 2" }
                                th { "Referees" }
                                th {}
                            }
                        }
                        tbody {
                            for existing in visible {
                                {
                                    let uuid = existing.uuid.clone();
                                    let uuid_for_delete = existing.uuid.clone();
                                    let match_name = existing.name.clone();
                                    let url = url.clone();
                                    let is_editing = editing().as_deref() == Some(existing.uuid.as_str());
                                    let team1_won = existing.winner.as_deref() == Some(TEAM1);
                                    let team2_won = existing.winner.as_deref() == Some(TEAM2);
                                    let team1_reference = existing.team1_initial.clone().filter(|initial| parse_reference(initial).is_some());
                                    let team2_reference = existing.team2_initial.clone().filter(|initial| parse_reference(initial).is_some());
                                    let referee_names = existing
                                        .referees
                                        .iter()
                                        .filter_map(|referee| referee.team_id.as_deref())
                                        .map(|id| team_name(id, &data.teams))
                                        .collect::<Vec<_>>()
                                        .join(", ");
                                    rsx! {
                                        tr {
                                            key: "{existing.uuid}",
                                            class: if is_editing { "table-primary" } else { "" },
                                            style: "cursor: pointer;",
                                            title: "Edit this match",
                                            onclick: move |_| {
                                                let Some(snapshot) = records() else { return };
                                                if has_unsaved_changes(&draft(), editing().as_deref(), &snapshot.matches)
                                                    && !confirm("Discard the match you're entering?")
                                                {
                                                    return;
                                                }
                                                if let Some(selected) = snapshot.matches.iter().find(|candidate| candidate.uuid == uuid) {
                                                    draft.set(MatchDraft::from_match(selected));
                                                    editing.set(Some(uuid.clone()));
                                                    focus_and_select(MATCH_NAME_INPUT_ID);
                                                }
                                            },
                                            td { class: "fw-semibold", "{existing.name}" }
                                            td { class: "text-end",
                                                span { class: if team1_won { "fw-bold" } else { "" },
                                                    if team1_won {
                                                        i { class: "fas fa-trophy text-success me-1" }
                                                    }
                                                    "{label_for(&existing.team1)}"
                                                }
                                                if let Some(reference) = team1_reference {
                                                    div { class: "small text-muted", "{reference}" }
                                                }
                                            }
                                            td { class: "text-center text-nowrap", "{score_summary(&existing.points)}" }
                                            td {
                                                span { class: if team2_won { "fw-bold" } else { "" },
                                                    "{label_for(&existing.team2)}"
                                                    if team2_won {
                                                        i { class: "fas fa-trophy text-success ms-1" }
                                                    }
                                                }
                                                if let Some(reference) = team2_reference {
                                                    div { class: "small text-muted", "{reference}" }
                                                }
                                            }
                                            td { class: "small text-muted", "{referee_names}" }
                                            td { class: "text-end",
                                                button {
                                                    class: "btn btn-sm btn-outline-danger",
                                                    title: "Delete match",
                                                    onclick: move |ev| {
                                                        ev.stop_propagation();
                                                        if !confirm(&format!("Delete {match_name}? This cannot be undone.")) {
                                                            return;
                                                        }
                                                        let url = url.clone();
                                                        let uuid = uuid_for_delete.clone();
                                                        spawn(async move {
                                                            match api::historical_delete_match(&url, &uuid).await {
                                                                Ok(response) => {
                                                                    error.set(None);
                                                                    if editing().as_deref() == Some(uuid.as_str()) {
                                                                        draft.set(MatchDraft::new(next_match_name(&response.matches), carried_set_type(&response.matches)));
                                                                        editing.set(None);
                                                                    }
                                                                    records.set(Some(response));
                                                                }
                                                                Err(message) => error.set(Some(message)),
                                                            }
                                                        });
                                                    },
                                                    i { class: "fas fa-trash" }
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

#[component]
fn TeamsCard(url: String, records: Records) -> Element {
    let mut records = records;
    let mut new_team_name = use_signal(String::new);
    let mut new_team_shortname = use_signal(String::new);
    let renaming = use_signal(|| None::<String>);
    let inviting = use_signal(|| None::<String>);
    let mut error = use_signal(|| None::<String>);

    let url_for_add = url.clone();
    let add_team = use_callback(move |_: ()| {
        let name = new_team_name().trim().to_string();
        if name.is_empty() {
            return;
        }
        let shortname = new_team_shortname().trim().to_string();
        let url = url_for_add.clone();
        spawn(async move {
            match api::historical_add_teams(
                &url,
                &[serde_json::json!({ "name": name, "shortname": shortname })],
            )
            .await
            {
                Ok(response) => {
                    records.set(Some(response));
                    new_team_name.set(String::new());
                    new_team_shortname.set(String::new());
                    error.set(None);
                }
                Err(message) => error.set(Some(message)),
            }
        });
    });

    let Some(data) = records() else {
        return rsx! {};
    };
    let unclaimed = data.teams.iter().filter(|team| team.placeholder).count();

    rsx! {
        div { class: "edit-section",
            div { class: "edit-section-heading",
                h5 { class: "mb-0", "Teams ({data.teams.len()})" }
                if unclaimed > 0 {
                    div { class: "small text-muted", "{unclaimed} unclaimed" }
                }
            }
            div { class: "edit-section-body pb-2",
                if let Some(message) = error() {
                    div { class: "alert alert-danger py-2", "{message}" }
                }
                div { class: "input-group input-group-sm",
                    input {
                        class: "form-control",
                        r#type: "text",
                        placeholder: "Team name",
                        value: "{new_team_name}",
                        oninput: move |ev| new_team_name.set(ev.value()),
                        onkeydown: move |ev: Event<KeyboardData>| {
                            if ev.key().to_string() == "Enter" {
                                ev.prevent_default();
                                add_team.call(());
                            }
                        },
                    }
                    input {
                        class: "form-control",
                        r#type: "text",
                        placeholder: "Short name",
                        maxlength: "8",
                        style: "max-width: 8rem;",
                        value: "{new_team_shortname}",
                        oninput: move |ev| new_team_shortname.set(ev.value()),
                        onkeydown: move |ev: Event<KeyboardData>| {
                            if ev.key().to_string() == "Enter" {
                                ev.prevent_default();
                                add_team.call(());
                            }
                        },
                    }
                    button {
                        class: "btn btn-outline-primary",
                        disabled: new_team_name().trim().is_empty(),
                        onclick: move |_| add_team.call(()),
                        "Add"
                    }
                }
                div { class: "form-text", "Short name is optional." }
            }
            ul { class: "list-group list-group-flush",
                for team in data.teams.iter() {
                    TeamRow {
                        key: "{team.id}",
                        url: url.clone(),
                        team: team.clone(),
                        records,
                        renaming,
                        inviting,
                        error,
                    }
                }
            }
        }
    }
}

#[component]
fn TeamRow(
    url: String,
    team: HistoricalTeam,
    records: Records,
    renaming: Signal<Option<String>>,
    inviting: Signal<Option<String>>,
    error: Signal<Option<String>>,
) -> Element {
    let mut records = records;
    let mut renaming = renaming;
    let mut inviting = inviting;
    let mut error = error;
    let mut rename_text = use_signal(|| team.name.clone());
    let mut rename_shortname = use_signal(|| team.shortname.clone().unwrap_or_default());

    let url_for_rename = url.clone();
    let team_id_for_rename = team.id.clone();
    let save_rename = use_callback(move |_: ()| {
        let name = rename_text().trim().to_string();
        if name.is_empty() {
            return;
        }
        let shortname = rename_shortname().trim().to_string();
        let url = url_for_rename.clone();
        let team_id = team_id_for_rename.clone();
        spawn(async move {
            match api::historical_rename_team(&url, &team_id, &name, &shortname).await {
                Ok(response) => {
                    records.set(Some(response));
                    renaming.set(None);
                    error.set(None);
                }
                Err(message) => error.set(Some(message)),
            }
        });
    });

    let is_renaming = renaming().as_deref() == Some(team.id.as_str());
    let is_inviting = inviting().as_deref() == Some(team.id.as_str());
    let team_id = team.id.clone();
    let team_id_for_invite = team.id.clone();
    let team_id_for_cancel = team.id.clone();
    let team_id_for_remove = team.id.clone();
    let url_for_cancel = url.clone();
    let url_for_remove = url.clone();
    let url_for_unclaim = url.clone();
    let team_id_for_unclaim = team.id.clone();
    let team_name_for_rename = team.name.clone();
    let team_shortname_for_rename = team.shortname.clone().unwrap_or_default();
    let team_name_for_remove = team.name.clone();
    let team_name_for_unclaim = team.name.clone();
    let can_remove = team.match_count == 0;
    let claimed_under_other_name = team
        .account_name
        .as_ref()
        .filter(|account_name| **account_name != team.name)
        .cloned();

    rsx! {
        li { class: "list-group-item",
            if is_renaming {
                div { class: "input-group input-group-sm",
                    input {
                        class: "form-control",
                        placeholder: "Team name",
                        value: "{rename_text}",
                        autofocus: true,
                        oninput: move |ev| rename_text.set(ev.value()),
                        onkeydown: move |ev: Event<KeyboardData>| match ev.key().to_string().as_str() {
                            "Enter" => {
                                ev.prevent_default();
                                save_rename.call(());
                            }
                            "Escape" => renaming.set(None),
                            _ => {}
                        },
                    }
                    input {
                        class: "form-control",
                        placeholder: "Short name",
                        maxlength: "8",
                        style: "max-width: 8rem;",
                        value: "{rename_shortname}",
                        oninput: move |ev| rename_shortname.set(ev.value()),
                        onkeydown: move |ev: Event<KeyboardData>| match ev.key().to_string().as_str() {
                            "Enter" => {
                                ev.prevent_default();
                                save_rename.call(());
                            }
                            "Escape" => renaming.set(None),
                            _ => {}
                        },
                    }
                    button { class: "btn btn-primary", title: "Save", onclick: move |_| save_rename.call(()), i { class: "fas fa-check" } }
                    button { class: "btn btn-outline-secondary", title: "Cancel", onclick: move |_| renaming.set(None), i { class: "fas fa-times" } }
                }
            } else {
                div { class: "d-flex justify-content-between align-items-start gap-2",
                    div { style: "min-width: 0;",
                        div { class: "fw-semibold text-truncate",
                            if team.placeholder {
                                "{team.name}"
                            } else {
                                Link { to: Route::TeamProfilePage { id: team.id.clone() }, class: "text-decoration-none", "{team.name}" }
                            }
                            if let Some(shortname) = team.shortname.as_deref().filter(|shortname| !shortname.is_empty()) {
                                span { class: "text-muted fw-normal", " ({shortname})" }
                            }
                        }
                        div { class: "small d-flex flex-wrap align-items-center gap-1",
                            if !team.placeholder {
                                span { class: "badge arctos-badge-success", "Claimed" }
                                if let Some(account_name) = claimed_under_other_name {
                                    span { class: "text-muted", "by {account_name}" }
                                }
                            } else if let Some(invitation) = team.claim_invitation.clone() {
                                span { class: "badge arctos-badge-warning", title: "Waiting for {invitation.team_name} to accept",
                                    "Invited: {invitation.team_name}"
                                }
                                button {
                                    class: "btn btn-link btn-sm p-0 small",
                                    onclick: move |_| {
                                        let url = url_for_cancel.clone();
                                        let team_id = team_id_for_cancel.clone();
                                        spawn(async move {
                                            match api::historical_cancel_claim_invitation(&url, &team_id).await {
                                                Ok(response) => records.set(Some(response)),
                                                Err(message) => error.set(Some(message)),
                                            }
                                        });
                                    },
                                    "cancel"
                                }
                            } else {
                                span { class: "badge arctos-badge-neutral", "Unclaimed" }
                            }
                            span { class: "text-muted", "{team.match_count} matches • {team.roster_size} players" }
                        }
                    }
                    div { class: "btn-group btn-group-sm flex-shrink-0",
                        button {
                            class: "btn btn-outline-secondary",
                            title: "Edit",
                            onclick: move |_| {
                                rename_text.set(team_name_for_rename.clone());
                                rename_shortname.set(team_shortname_for_rename.clone());
                                renaming.set(Some(team_id.clone()));
                            },
                            i { class: "fas fa-pen" }
                        }
                        if !team.placeholder {
                            button {
                                class: "btn btn-outline-warning",
                                title: "Put this spot back on an unclaimed team. Results and the roster stay with the spot.",
                                onclick: move |_| {
                                    if !confirm(&format!(
                                        "Un-claim {team_name_for_unclaim}? The results stay, listed again as an unclaimed team."
                                    )) {
                                        return;
                                    }
                                    let url = url_for_unclaim.clone();
                                    let team_id = team_id_for_unclaim.clone();
                                    spawn(async move {
                                        match api::historical_unclaim_team(&url, &team_id).await {
                                            Ok(response) => {
                                                records.set(Some(response));
                                                error.set(None);
                                            }
                                            Err(message) => error.set(Some(message)),
                                        }
                                    });
                                },
                                i { class: "fas fa-unlink" }
                            }
                        }
                        if team.placeholder {
                            button {
                                class: if is_inviting { "btn btn-secondary" } else { "btn btn-outline-secondary" },
                                title: "Invite a team account to claim these results",
                                onclick: move |_| {
                                    if is_inviting {
                                        inviting.set(None);
                                    } else {
                                        inviting.set(Some(team_id_for_invite.clone()));
                                    }
                                },
                                i { class: "fas fa-user-plus" }
                            }
                        }
                        Link {
                            to: Route::HistoricalRoster { url: url.clone(), team_id: team.id.clone() },
                            class: "btn btn-outline-secondary",
                            title: "Roster",
                            i { class: "fas fa-users" }
                        }
                        button {
                            class: "btn btn-outline-danger",
                            disabled: !can_remove,
                            title: if can_remove { "Remove from tournament" } else { "Used in matches; delete those first" },
                            onclick: move |_| {
                                if !confirm(&format!("Remove {team_name_for_remove} from this tournament?")) {
                                    return;
                                }
                                let url = url_for_remove.clone();
                                let team_id = team_id_for_remove.clone();
                                spawn(async move {
                                    match api::historical_remove_team(&url, &team_id).await {
                                        Ok(response) => {
                                            records.set(Some(response));
                                            error.set(None);
                                        }
                                        Err(message) => error.set(Some(message)),
                                    }
                                });
                            },
                            i { class: "fas fa-trash" }
                        }
                    }
                }
                if !team.claim_requests.is_empty() {
                    ul { class: "list-unstyled small mb-0 mt-2",
                        for request in team.claim_requests.iter() {
                            {
                                let request_id = request.id;
                                let url_for_approve = url.clone();
                                let url_for_decline = url.clone();
                                rsx! {
                                    li { key: "{request.id}", class: "d-flex flex-wrap align-items-center gap-2",
                                        span { "{request.team_name} asked to claim this spot" }
                                        button {
                                            class: "btn btn-sm btn-success",
                                            onclick: move |_| {
                                                let url = url_for_approve.clone();
                                                spawn(async move {
                                                    match api::historical_decide_claim_request(&url, request_id, true).await {
                                                        Ok(response) => {
                                                            records.set(Some(response));
                                                            error.set(None);
                                                        }
                                                        Err(message) => error.set(Some(message)),
                                                    }
                                                });
                                            },
                                            "Approve"
                                        }
                                        button {
                                            class: "btn btn-sm btn-outline-secondary",
                                            onclick: move |_| {
                                                let url = url_for_decline.clone();
                                                spawn(async move {
                                                    match api::historical_decide_claim_request(&url, request_id, false).await {
                                                        Ok(response) => {
                                                            records.set(Some(response));
                                                            error.set(None);
                                                        }
                                                        Err(message) => error.set(Some(message)),
                                                    }
                                                });
                                            },
                                            "Decline"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if team.roster_request_count > 0 {
                    div { class: "small text-muted mt-1",
                        "{team.roster_request_count} roster requests waiting. Open the roster to approve them."
                    }
                }
                if is_inviting {
                    ClaimInvitePicker {
                        url: url.clone(),
                        placeholder_id: team.id.clone(),
                        placeholder_name: team.name.clone(),
                        records,
                        inviting,
                    }
                }
            }
        }
    }
}

#[component]
fn ClaimInvitePicker(
    url: String,
    placeholder_id: String,
    placeholder_name: String,
    records: Records,
    inviting: Signal<Option<String>>,
) -> Element {
    let mut records = records;
    let mut inviting = inviting;
    let mut query = use_signal(|| placeholder_name.clone());
    let mut error = use_signal(|| None::<String>);
    let results = use_resource(move || {
        let search = query().trim().to_string();
        async move {
            if search.chars().count() < 2 {
                return Ok(Vec::new());
            }
            api::teams_list(&search)
                .await
                .map(|response| response.teams)
        }
    });

    let attending: Vec<String> = records()
        .map(|response| response.teams.into_iter().map(|team| team.id).collect())
        .unwrap_or_default();

    rsx! {
        div { class: "mt-2 p-2 border rounded bg-light",
            div { class: "small text-muted mb-1",
                "Invite a team account to claim the results of {placeholder_name}. Once they accept, the tournament shows on their profile."
            }
            if let Some(message) = error() {
                div { class: "alert alert-danger py-1 px-2 small mb-1", "{message}" }
            }
            input {
                class: "form-control form-control-sm",
                placeholder: "Search team accounts",
                autofocus: true,
                value: "{query}",
                oninput: move |ev| query.set(ev.value()),
                onkeydown: move |ev: Event<KeyboardData>| {
                    if ev.key().to_string() == "Escape" {
                        inviting.set(None);
                    }
                },
            }
            match results.read().as_ref() {
                Some(Ok(teams)) => {
                    let candidates: Vec<_> = teams.iter().filter(|team| !attending.contains(&team.id)).take(8).cloned().collect();
                    if candidates.is_empty() {
                        rsx! {
                            if query().trim().chars().count() >= 2 {
                                div { class: "small text-muted mt-1", "No team accounts found." }
                            }
                        }
                    } else {
                        rsx! {
                            div { class: "list-group list-group-flush mt-1",
                                for candidate in candidates {
                                    {
                                        let url = url.clone();
                                        let placeholder_id = placeholder_id.clone();
                                        let invited_id = candidate.id.clone();
                                        rsx! {
                                            button {
                                                key: "{candidate.id}",
                                                class: "list-group-item list-group-item-action d-flex justify-content-between align-items-center px-2 py-1 small",
                                                onclick: move |_| {
                                                    let url = url.clone();
                                                    let placeholder_id = placeholder_id.clone();
                                                    let invited_id = invited_id.clone();
                                                    spawn(async move {
                                                        match api::historical_invite_team_to_claim(&url, &placeholder_id, &invited_id).await {
                                                            Ok(response) => {
                                                                records.set(Some(response));
                                                                inviting.set(None);
                                                            }
                                                            Err(message) => error.set(Some(message)),
                                                        }
                                                    });
                                                },
                                                span {
                                                    "{candidate.name} "
                                                    span { class: "text-muted", "({candidate.id})" }
                                                }
                                                span { class: "text-primary", "Invite" }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                Some(Err(message)) => rsx! {
                    div { class: "small text-danger mt-1", "{message}" }
                },
                None => rsx! {},
            }
        }
    }
}
