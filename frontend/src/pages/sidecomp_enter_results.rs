use crate::api;
use crate::components::PompfenIcon;
use crate::types::{SideCompEnterRoster, SideCompEnterRosterPlayer, SideCompResultEntry};
use crate::Route;
use dioxus::prelude::*;
use std::cell::Cell;
use std::rc::Rc;

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

fn entry_number_matches(entry_number: i32, query: &str) -> bool {
    entry_number.to_string().contains(query)
}

fn merge_roster_lookup(
    current: &SideCompEnterRoster,
    query: &str,
    update: &SideCompEnterRoster,
) -> SideCompEnterRoster {
    let mut next = current.clone();
    next.active = update.active;
    next.name = update.name.clone();
    next.type_ = update.type_.clone();
    next.id = update.id;
    next.registrants
        .retain(|player| !entry_number_matches(player.entry_number, query));
    next.registrants.extend(update.registrants.clone());
    next.registrants.sort_by(|left, right| {
        right
            .registered
            .cmp(&left.registered)
            .then_with(|| left.entry_number.cmp(&right.entry_number))
            .then_with(|| left.player_id.cmp(&right.player_id))
    });
    next
}

fn apply_roster_response(mut roster: Signal<Option<SideCompEnterRoster>>, query: &str, data: SideCompEnterRoster) {
    let next = if query.is_empty() {
        data
    } else if let Some(current) = roster() {
        merge_roster_lookup(&current, query, &data)
    } else {
        data
    };
    if roster().as_ref() != Some(&next) {
        roster.set(Some(next));
    }
}

fn queue_roster_refresh(
    comp_id: i32,
    query: String,
    request_serial: Rc<Cell<u32>>,
    roster: Signal<Option<SideCompEnterRoster>>,
    mut roster_error: Signal<Option<String>>,
) {
    let serial = request_serial.get().wrapping_add(1);
    request_serial.set(serial);
    spawn(async move {
        let lookup = if query.is_empty() { None } else { Some(query.as_str()) };
        match api::sidecomp_enter_results_roster(comp_id, lookup).await {
            Ok(data) => {
                if request_serial.get() != serial {
                    return;
                }
                apply_roster_response(roster, &query, data);
                if roster_error().is_some() {
                    roster_error.set(None);
                }
            }
            Err(err) => {
                if request_serial.get() == serial && roster().is_none() {
                    roster_error.set(Some(err));
                }
            }
        }
    });
}

#[derive(Clone, Copy, PartialEq)]
enum MatchSide {
    Left,
    Right,
}

impl MatchSide {
    fn other(self) -> Self {
        match self {
            Self::Left => Self::Right,
            Self::Right => Self::Left,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Left => "Left",
            Self::Right => "Right",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct MatchupParty {
    entry_number: i32,
    display_name: String,
    profile_photo: Option<String>,
    team_profile_photo: Option<String>,
    team_known: bool,
    weapon: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
struct MatchupEvent {
    winner: Option<MatchupParty>,
    loser: Option<MatchupParty>,
    result_uuids: Vec<String>,
    flagged: bool,
}

fn party_from_player(player: &SideCompEnterRosterPlayer) -> MatchupParty {
    MatchupParty {
        entry_number: player.entry_number,
        display_name: player.display_name.clone(),
        profile_photo: player.profile_photo.clone(),
        team_profile_photo: player.team_profile_photo.clone(),
        team_known: player.team_id.is_some() || player.team_shortname.is_some() || player.team_profile_photo.is_some(),
        weapon: player.weapon.clone().filter(|weapon| !weapon.is_empty()),
    }
}

fn party_class(side: MatchSide, selected: bool, known: bool) -> String {
    let side_name = match side {
        MatchSide::Left => "left",
        MatchSide::Right => "right",
    };
    let selected_class = if selected { " selected" } else { "" };
    let known_class = if known { "" } else { " unknown" };
    format!("sc-party {side_name}{selected_class}{known_class}")
}

fn place_party(
    side: MatchSide,
    player: Option<SideCompEnterRosterPlayer>,
    mut left_party: Signal<Option<SideCompEnterRosterPlayer>>,
    mut right_party: Signal<Option<SideCompEnterRosterPlayer>>,
    mut selected_side: Signal<MatchSide>,
    mut error: Signal<Option<String>>,
) -> bool {
    let other = match side {
        MatchSide::Left => right_party(),
        MatchSide::Right => left_party(),
    };
    if let (Some(next), Some(existing)) = (&player, other.as_ref()) {
        if next.player_id == existing.player_id {
            error.set(Some("Pick two different people".into()));
            return false;
        }
    }
    error.set(None);
    match side {
        MatchSide::Left => left_party.set(player.clone()),
        MatchSide::Right => right_party.set(player.clone()),
    }
    if player.is_some() {
        let other_empty = match side {
            MatchSide::Left => right_party().is_none(),
            MatchSide::Right => left_party().is_none(),
        };
        if other_empty {
            selected_side.set(side.other());
        }
    }
    true
}

fn log_side_win(
    comp_id: i32,
    winner: Option<SideCompEnterRosterPlayer>,
    loser: Option<SideCompEnterRosterPlayer>,
    mut matchup_history: Signal<Vec<MatchupEvent>>,
    mut error: Signal<Option<String>>,
    mut matchup_busy: Signal<bool>,
) {
    if winner.is_none() && loser.is_none() {
        error.set(Some("Pick at least one person".into()));
        return;
    }
    if matchup_busy() {
        return;
    }
    matchup_busy.set(true);
    error.set(None);
    let winner_party = winner.as_ref().map(party_from_player);
    let loser_party = loser.as_ref().map(party_from_player);
    let winner_registration_id = winner.as_ref().and_then(|player| player.registration_id);
    let winner_player_id = winner.as_ref().map(|player| player.player_id.clone());
    let loser_registration_id = loser.as_ref().and_then(|player| player.registration_id);
    let loser_player_id = loser.as_ref().map(|player| player.player_id.clone());
    spawn(async move {
        match api::sidecomp_log_matchup(
            comp_id,
            winner_registration_id,
            winner_player_id.as_deref(),
            loser_registration_id,
            loser_player_id.as_deref(),
        )
        .await
        {
            Ok(results) if !results.is_empty() => {
                let flagged = results.iter().any(|result| result.flagged);
                matchup_history.write().insert(
                    0,
                    MatchupEvent {
                        winner: winner_party,
                        loser: loser_party,
                        result_uuids: results.into_iter().map(|result| result.uuid).collect(),
                        flagged,
                    },
                );
            }
            Ok(_) => error.set(Some("Could not record that win".into())),
            Err(message) => error.set(Some(message)),
        }
        matchup_busy.set(false);
    });
}

fn team_label(player: &SideCompEnterRosterPlayer) -> String {
    player.team_shortname.clone().unwrap_or_else(|| "merc".to_string())
}

#[component]
fn MatchupBoard(
    comp_id: i32,
    left_party: Signal<Option<SideCompEnterRosterPlayer>>,
    right_party: Signal<Option<SideCompEnterRosterPlayer>>,
    selected_side: Signal<MatchSide>,
    matchup_history: Signal<Vec<MatchupEvent>>,
    error: Signal<Option<String>>,
    matchup_busy: Signal<bool>,
) -> Element {
    let left = left_party();
    let right = right_party();
    let selected = selected_side();
    let busy = matchup_busy();

    rsx! {
        div { class: "sc-matchup",
            for (side, player) in [(MatchSide::Left, left.clone()), (MatchSide::Right, right.clone())] {
                {
                    let known = player.is_some();
                    let card_class = party_class(side, selected == side, known);
                    let name = player.as_ref().map(|person| person.display_name.clone()).unwrap_or_else(|| "Unknown".into());
                    let entry_label = player.as_ref().map(|person| format!("#{}", person.entry_number));
                    let photo = player.as_ref().and_then(|person| person.profile_photo.clone());
                    let team_photo = player.as_ref().and_then(|person| person.team_profile_photo.clone());
                    let team_known = player.as_ref().is_some_and(|person| {
                        person.team_id.is_some() || person.team_shortname.is_some() || person.team_profile_photo.is_some()
                    });
                    let weapon = player.as_ref().and_then(|person| person.weapon.clone()).unwrap_or_default();
                    let side_key = side.label().to_string();
                    let clear_label = format!("Clear {}", side.label());
                    rsx! {
                        div {
                            class: "{card_class}",
                            key: "{side_key}",
                            onclick: move |_| selected_side.set(side),
                            if known {
                                button {
                                    class: "sc-party-clear",
                                    r#type: "button",
                                    aria_label: "{clear_label}",
                                    onclick: move |event: Event<MouseData>| {
                                        event.stop_propagation();
                                        place_party(side, None, left_party, right_party, selected_side, error);
                                        selected_side.set(side);
                                    },
                                    "×"
                                }
                            }
                            if known {
                                AvatarCircle { photo: photo.clone(), size: "2.4rem".to_string(), is_team: false }
                            }
                            div { class: "sc-party-name", "{name}" }
                            if let Some(entry_label) = entry_label {
                                div { class: "sc-party-meta", "{entry_label}" }
                            }
                            if team_known || !weapon.is_empty() {
                                div { class: "sc-party-marks",
                                    if team_known {
                                        AvatarCircle { photo: team_photo.clone(), size: "1.2rem".to_string(), is_team: true }
                                    }
                                    if !weapon.is_empty() {
                                        PompfenIcon { weapon: weapon.clone(), size: "1.2em".to_string() }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        div { class: "sc-wins",
            for (side, player, opponent) in [
                (MatchSide::Left, left.clone(), right.clone()),
                (MatchSide::Right, right.clone(), left.clone()),
            ] {
                {
                    let side_class = match side {
                        MatchSide::Left => "sc-win-btn left",
                        MatchSide::Right => "sc-win-btn right",
                    };
                    let winner_for_click = player.clone();
                    let side_key = side.label().to_string();
                    rsx! {
                        button {
                            class: "{side_class}",
                            key: "{side_key}",
                            r#type: "button",
                            disabled: busy,
                            onclick: move |_| {
                                log_side_win(
                                    comp_id,
                                    winner_for_click.clone(),
                                    opponent.clone(),
                                    matchup_history,
                                    error,
                                    matchup_busy,
                                );
                            },
                            "Log win"
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn MatchupPicker(
    registrants: Vec<SideCompEnterRosterPlayer>,
    query: Signal<String>,
    selected_side: Signal<MatchSide>,
    left_party: Signal<Option<SideCompEnterRosterPlayer>>,
    right_party: Signal<Option<SideCompEnterRosterPlayer>>,
    error: Signal<Option<String>>,
    matchup_busy: Signal<bool>,
) -> Element {
    let entered = query();
    let side = selected_side();
    let matches: Vec<SideCompEnterRosterPlayer> = if entered.is_empty() {
        Vec::new()
    } else {
        registrants
            .into_iter()
            .filter(|player| player.entry_number.to_string().contains(&entered))
            .collect()
    };
    rsx! {
        if entered.is_empty() {
            p { class: "sc-matchup-hint", "Type a number" }
        } else if matches.is_empty() {
            p { class: "text-muted text-center mt-3", "No matching players" }
        }
        for player in matches {
            {
                let chosen = player.clone();
                let shortname = team_label(&player);
                let weapon = player.weapon.clone().unwrap_or_default();
                let photo = player.profile_photo.clone();
                let team_photo = player.team_profile_photo.clone();
                let team_known = player.team_id.is_some() || player.team_shortname.is_some() || player.team_profile_photo.is_some();
                rsx! {
                    button {
                        class: "sc-pick-row",
                        key: "{player.player_id}",
                        r#type: "button",
                        disabled: matchup_busy(),
                        onclick: move |_| {
                            if place_party(
                                side,
                                Some(chosen.clone()),
                                left_party,
                                right_party,
                                selected_side,
                                error,
                            ) {
                                query.set(String::new());
                            }
                        },
                        AvatarCircle { photo: photo, size: "2.2rem".to_string(), is_team: false }
                        div { class: "sc-pick-num", "#{player.entry_number}" }
                        div { class: "sc-pick-text",
                            div { class: "sc-display-name", "{player.display_name}" }
                            div { class: "sc-meta-text", "{shortname}" }
                        }
                        if team_known {
                            AvatarCircle { photo: team_photo, size: "1.25rem".to_string(), is_team: true }
                        }
                        if !weapon.is_empty() {
                            PompfenIcon { weapon: weapon, size: "1.4em".to_string() }
                        }
                    }
                }
            }
        }
    }
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
    let roster = use_signal(|| None::<SideCompEnterRoster>);
    let mut roster_error = use_signal(|| None::<String>);
    let mut initial_finished = use_signal(|| false);
    let initial_started = use_hook(|| Rc::new(Cell::new(false)));
    let request_serial = use_hook(|| Rc::new(Cell::new(0u32)));
    let mut query = use_signal(String::new);

    use_effect(move || {
        if initial_started.get() {
            return;
        }
        initial_started.set(true);
        spawn(async move {
            match api::sidecomp_enter_results_roster(comp_id, None).await {
                Ok(data) => {
                    apply_roster_response(roster, "", data);
                    roster_error.set(None);
                }
                Err(err) => {
                    if roster().is_none() {
                        roster_error.set(Some(err));
                    }
                }
            }
            initial_finished.set(true);
        });
    });
    {
        let request_serial = request_serial.clone();
        use_effect(move || {
            let entered = query();
            if entered.is_empty() || !initial_finished() {
                return;
            }
            queue_roster_refresh(comp_id, entered, request_serial.clone(), roster, roster_error);
        });
    }
    let mut history = use_signal(Vec::<HistoryEvent>::new);
    let mut matchup_history = use_signal(Vec::<MatchupEvent>::new);
    let mut left_party = use_signal(|| None::<SideCompEnterRosterPlayer>);
    let mut right_party = use_signal(|| None::<SideCompEnterRosterPlayer>);
    let mut selected_side = use_signal(|| MatchSide::Left);
    let mut error = use_signal(|| None::<String>);
    let mut busy_player = use_signal(|| None::<String>);
    let mut matchup_busy = use_signal(|| false);
    let mut undo_busy = use_signal(|| false);

    // Keep the newest history entry visible on the right; older entries overflow left.
    use_effect(move || {
        let _len = history().len() + matchup_history().len();
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
                box-sizing: border-box;
                display: flex;
                flex-direction: column;
                width: 100%;
                min-width: 0;
                max-width: min(560px, 100%);
                height: 100vh;
                height: 100dvh;
                margin: 0 auto;
                padding: env(safe-area-inset-top, 0px) env(safe-area-inset-right, 0px) env(safe-area-inset-bottom, 0px) env(safe-area-inset-left, 0px);
                background: var(--ui-bg);
                color: var(--ui-text);
                overflow: hidden;
            }}
            .sc-enter-top {{
                min-width: 0;
                padding: 0.5rem 0.5rem 0.15rem;
                flex-shrink: 0;
                overflow: hidden;
            }}
            .sc-enter-top > .btn,
            .sc-enter-top > a {{
                flex: 0 0 auto;
            }}
            .sc-search-display {{
                min-width: 0;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                font-size: 1.35rem;
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
                min-width: 0;
                overflow-y: auto;
                overflow-x: hidden;
                padding: 0.4rem 0.5rem;
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
                min-width: 0;
                max-width: 100%;
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
                min-width: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.2rem;
                white-space: nowrap;
                overflow: hidden;
            }}
            .sc-hist-text {{
                min-width: 0;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-hist-person {{
                width: 100%;
                min-width: 0;
            }}
            .sc-hist-marks {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.2rem;
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
                min-width: 0;
                width: 100%;
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 0.35rem;
                padding: 0.45rem;
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
            .sc-matchup {{
                flex-shrink: 0;
                min-width: 0;
                width: 100%;
                display: grid;
                grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
                gap: 0.4rem;
                padding: 0.15rem 0.5rem 0;
            }}
            .sc-party {{
                position: relative;
                min-width: 0;
                min-height: 6.4rem;
                box-sizing: border-box;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 0.15rem;
                border-radius: 0.9rem;
                border: 3px solid var(--ui-border);
                background: var(--ui-surface);
                padding: 0.4rem 0.35rem 0.45rem;
                text-align: center;
                overflow: hidden;
            }}
            .sc-party.left.selected {{ border-color: #1565c0; }}
            .sc-party.right.selected {{ border-color: #ef6c00; }}
            .sc-party-name {{
                width: 100%;
                min-width: 0;
                font-size: 1rem;
                font-weight: 800;
                line-height: 1.15;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .sc-party.unknown .sc-party-name {{
                color: var(--ui-muted);
                font-weight: 600;
            }}
            .sc-party-meta {{
                max-width: 100%;
                font-size: 0.82rem;
                color: var(--ui-muted);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .sc-party-marks {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.35rem;
                max-width: 100%;
                min-width: 0;
            }}
            .sc-party-clear {{
                position: absolute;
                top: 0.2rem;
                right: 0.2rem;
                width: 2.25rem;
                height: 2.25rem;
                border: none;
                border-radius: 0.55rem;
                background: var(--ui-surface-muted);
                color: var(--ui-text);
                font-size: 1.15rem;
                line-height: 1;
            }}
            .sc-wins {{
                flex-shrink: 0;
                min-width: 0;
                width: 100%;
                display: grid;
                grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
                gap: 0.4rem;
                padding: 0.4rem 0.5rem 0.3rem;
            }}
            .sc-win-btn {{
                min-width: 0;
                min-height: 3.6rem;
                box-sizing: border-box;
                border: none;
                border-radius: 0.9rem;
                color: #fff;
                background: #2e7d32;
                font-size: 1rem;
                font-weight: 800;
                line-height: 1.15;
                padding: 0.4rem 0.4rem;
                white-space: normal;
                overflow-wrap: anywhere;
            }}
            .sc-win-btn.left {{ box-shadow: inset 0 7px 0 #1565c0; }}
            .sc-win-btn.right {{ box-shadow: inset 0 7px 0 #ef6c00; }}
            .sc-win-btn:disabled {{ opacity: 0.38; }}
            .sc-matchup-hint {{
                margin: 0.35rem 0 0;
                text-align: center;
                color: var(--ui-muted);
                font-size: 0.95rem;
            }}
            .sc-pick-row {{
                width: 100%;
                max-width: 100%;
                min-width: 0;
                box-sizing: border-box;
                display: flex;
                align-items: center;
                gap: 0.5rem;
                text-align: left;
                border: none;
                border-radius: 0.8rem;
                background: var(--ui-surface);
                color: inherit;
                padding: 0.55rem 0.6rem;
                margin-bottom: 0.45rem;
                min-height: 3.6rem;
                overflow: hidden;
            }}
            .sc-pick-num {{
                font-weight: 800;
                font-size: 1.2rem;
                min-width: 2.6rem;
            }}
            .sc-pick-text {{
                min-width: 0;
                flex: 1;
            }}
            .sc-hist-card.matchup {{ width: 7.8rem; }}
            .sc-hist-beat {{
                color: var(--ui-muted);
                font-size: 0.62rem;
                font-weight: 700;
            }}
            /* A short landscape viewport must not clip the nonshrinking keypad. */
            @media (max-height: 500px) {{
                .sc-enter {{
                    height: auto;
                    min-height: 100vh;
                    min-height: 100dvh;
                    overflow-x: hidden;
                    overflow-y: visible;
                }}
                .sc-results {{
                    flex: 0 1 auto;
                    min-height: 6rem;
                    max-height: 40vh;
                    max-height: 40dvh;
                }}
            }}
        "# }

        div { class: "sc-enter",
            div { class: "sc-enter-top d-flex align-items-center justify-content-between gap-2",
                Link {
                    to: Route::SideCompDetail { url: url_back, comp_id },
                    class: "btn btn-sm btn-outline-secondary",
                    "Back"
                }
                {
                    let matchup_mode = roster().as_ref().is_some_and(|data| data.track_matchups);
                    let placeholder = if matchup_mode { "number" } else { "type to search" };
                    let undo_disabled = undo_busy() || if matchup_mode {
                        matchup_history().is_empty()
                    } else {
                        history().is_empty()
                    };
                    rsx! {
                        div {
                            class: if query().is_empty() { "sc-search-display is-placeholder flex-grow-1 text-center" } else { "sc-search-display flex-grow-1 text-center" },
                            if query().is_empty() { "{placeholder}" } else { "{query}" }
                        }
                        button {
                            class: "btn btn-sm btn-outline-secondary",
                            disabled: undo_disabled,
                            onclick: move |_| {
                                if roster().as_ref().is_some_and(|data| data.track_matchups) {
                                    let Some(last) = matchup_history().first().cloned() else {
                                        return;
                                    };
                                    let Some(uuid) = last.result_uuids.first().cloned() else {
                                        return;
                                    };
                                    undo_busy.set(true);
                                    error.set(None);
                                    spawn(async move {
                                        match api::sidecomp_delete_result(&uuid).await {
                                            Ok(_) => {
                                                matchup_history.write().retain(|event| event.result_uuids.first() != Some(&uuid));
                                            }
                                            Err(e) => error.set(Some(e)),
                                        }
                                        undo_busy.set(false);
                                    });
                                    return;
                                }
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
                }
            }

            if let Some(err) = error() {
                div { class: "alert alert-danger mx-2 py-1 mb-1", "{err}" }
            }

            match roster() {
                Some(data) if !data.active => {
                    rsx! {
                        div { class: "alert alert-warning mx-2 mt-2",
                            "This side competition is not active; results cannot be entered."
                        }
                    }
                }
                _ => rsx! {}
            }

            if roster().as_ref().is_some_and(|data| data.active && data.track_matchups) {
                MatchupBoard {
                    comp_id,
                    left_party,
                    right_party,
                    selected_side,
                    matchup_history,
                    error,
                    matchup_busy,
                }
            }

            div { class: "sc-results",
                match roster() {
                    Some(data) if data.active && data.track_matchups => {
                        rsx! {
                            MatchupPicker {
                                registrants: data.registrants.clone(),
                                query,
                                selected_side,
                                left_party,
                                right_party,
                                error,
                                matchup_busy,
                            }
                        }
                    }
                    Some(data) if data.active => {
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
                    None => {
                        if let Some(err) = roster_error() {
                            rsx! { div { class: "alert alert-danger", "{err}" } }
                        } else {
                            rsx! { div { class: "text-center mt-4", div { class: "spinner-border" } } }
                        }
                    }
                    _ => rsx! {},
                }
            }

            {
                if roster().as_ref().is_some_and(|data| data.track_matchups) {
                    let events: Vec<MatchupEvent> = matchup_history().into_iter().rev().collect();
                    rsx! {
                        div { class: "sc-history",
                            if events.is_empty() {
                                span { class: "text-muted align-self-center px-2", "History" }
                            }
                            for event in events {
                                {
                                    let uuids = event.result_uuids.clone();
                                    let currently_flagged = event.flagged;
                                    let card_class = if event.flagged {
                                        "sc-hist-card matchup flagged"
                                    } else {
                                        "sc-hist-card matchup"
                                    };
                                    let winner_line = match &event.winner {
                                        Some(party) => format!("#{} {}", party.entry_number, party.display_name),
                                        None => "Unknown".to_string(),
                                    };
                                    let winner_photo = event.winner.as_ref().and_then(|party| party.profile_photo.clone());
                                    let winner_team_known = event.winner.as_ref().is_some_and(|party| party.team_known);
                                    let winner_team_photo = event.winner.as_ref().and_then(|party| party.team_profile_photo.clone());
                                    let winner_weapon = event.winner.as_ref().and_then(|party| party.weapon.clone()).unwrap_or_default();
                                    let loser_photo = event.loser.as_ref().and_then(|party| party.profile_photo.clone());
                                    let loser_team_known = event.loser.as_ref().is_some_and(|party| party.team_known);
                                    let loser_team_photo = event.loser.as_ref().and_then(|party| party.team_profile_photo.clone());
                                    let loser_weapon = event.loser.as_ref().and_then(|party| party.weapon.clone()).unwrap_or_default();
                                    let loser_line = match &event.loser {
                                        Some(party) => format!("#{} {}", party.entry_number, party.display_name),
                                        None => "Unknown".to_string(),
                                    };
                                    let card_key = uuids.first().cloned().unwrap_or_default();
                                    rsx! {
                                        div {
                                            class: "{card_class}",
                                            key: "{card_key}",
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
                                                        let mut hist = matchup_history();
                                                        for recorded in hist.iter_mut() {
                                                            if recorded.result_uuids.iter().any(|uuid| uuids.iter().any(|flagged| flagged == uuid)) {
                                                                recorded.flagged = new_flag;
                                                            }
                                                        }
                                                        matchup_history.set(hist);
                                                    }
                                                });
                                            },
                                            if event.flagged {
                                                i { class: "fas fa-flag sc-hist-flag" }
                                            }
                                            div { class: "sc-hist-person",
                                                div { class: "sc-hist-line sc-hist-points win",
                                                    if event.winner.is_some() {
                                                        AvatarCircle { photo: winner_photo, size: "1rem".to_string(), is_team: false }
                                                    }
                                                    span { class: "sc-hist-text", "{winner_line}" }
                                                }
                                                if winner_team_known || !winner_weapon.is_empty() {
                                                    div { class: "sc-hist-marks",
                                                        if winner_team_known {
                                                            AvatarCircle { photo: winner_team_photo, size: "0.85rem".to_string(), is_team: true }
                                                        }
                                                        if !winner_weapon.is_empty() {
                                                            PompfenIcon { weapon: winner_weapon, size: "0.85em".to_string() }
                                                        }
                                                    }
                                                }
                                            }
                                            div { class: "sc-hist-beat", "beat" }
                                            div { class: "sc-hist-person",
                                                div { class: "sc-hist-line sc-hist-points loss",
                                                    if event.loser.is_some() {
                                                        AvatarCircle { photo: loser_photo, size: "1rem".to_string(), is_team: false }
                                                    }
                                                    span { class: "sc-hist-text", "{loser_line}" }
                                                }
                                                if loser_team_known || !loser_weapon.is_empty() {
                                                    div { class: "sc-hist-marks",
                                                        if loser_team_known {
                                                            AvatarCircle { photo: loser_team_photo, size: "0.85rem".to_string(), is_team: true }
                                                        }
                                                        if !loser_weapon.is_empty() {
                                                            PompfenIcon { weapon: loser_weapon, size: "0.85em".to_string() }
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
                } else {
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

#[cfg(test)]
mod tests {
    use super::merge_roster_lookup;
    use crate::types::{SideCompEnterRoster, SideCompEnterRosterPlayer};

    fn player(player_id: &str, entry_number: i32, display_name: &str) -> SideCompEnterRosterPlayer {
        SideCompEnterRosterPlayer {
            registration_id: Some(entry_number),
            registered: true,
            entry_number,
            weapon: None,
            player_id: player_id.to_string(),
            display_name: display_name.to_string(),
            profile_photo: None,
            jersey_name: None,
            jersey_number: None,
            team_id: None,
            team_shortname: None,
            team_profile_photo: None,
            track_losses: false,
        }
    }

    fn roster(registrants: Vec<SideCompEnterRosterPlayer>) -> SideCompEnterRoster {
        SideCompEnterRoster {
            id: 1,
            name: "Duel".to_string(),
            type_: "DUELING".to_string(),
            active: true,
            track_matchups: false,
            registrants,
        }
    }

    #[test]
    fn lookup_replaces_matching_cached_registrants_and_keeps_the_rest() {
        let cached = roster(vec![player("old", 12, "Old"), player("keep", 7, "Keep")]);
        let mut refreshed = player("fresh", 12, "Fresh");
        refreshed.registered = true;
        let update = roster(vec![refreshed, player("new", 120, "New")]);
        let merged = merge_roster_lookup(&cached, "12", &update);
        assert_eq!(
            merged
                .registrants
                .iter()
                .map(|registrant| registrant.player_id.as_str())
                .collect::<Vec<_>>(),
            vec!["keep", "fresh", "new"]
        );
    }
}
