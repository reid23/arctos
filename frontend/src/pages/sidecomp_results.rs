use crate::api;
use crate::components::{pompfen_by_id, PompfenIcon};
use crate::types::{SideCompStandingRow, SideCompStandingTable, SideCompStandings};
use crate::Route;
use dioxus::prelude::*;
use wasm_bindgen::JsCast;
#[cfg(target_arch = "wasm32")]
use gloo_timers::future::TimeoutFuture;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum StandingSort {
    Wins,
    WinRatio,
}
impl StandingSort {
    fn default_for(track_losses: bool) -> Self {
        if track_losses { Self::WinRatio } else { Self::Wins }
    }
    fn query_value(self) -> &'static str {
        match self {
            Self::Wins => "wins",
            Self::WinRatio => "win_percentage",
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct StandingSelection {
    weapon: Option<String>,
    sort: StandingSort,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PublicationPolicy {
    showing_all_results: bool,
    public_limit: Option<i32>,
}
impl PublicationPolicy {
    fn from_standings(standings: &SideCompStandings) -> Self {
        Self {
            showing_all_results: standings.showing_all_results,
            public_limit: standings.only_show_top_n_results,
        }
    }
}
impl StandingSelection {
    fn is_default(&self, track_losses: bool) -> bool {
        self.weapon.is_none() && self.sort == StandingSort::default_for(track_losses)
    }
    fn for_loss_tracking(mut self, track_losses: bool) -> Self {
        if !track_losses {
            self.sort = StandingSort::Wins;
        }
        self
    }
}

fn focus_results_tab(comp_id: i32, table_id: &str) {
    let Some(document) = web_sys::window().and_then(|window| window.document()) else {
        return;
    };
    if let Some(tab) = document
        .get_element_by_id(&format!("sidecomp-{comp_id}-tab-{table_id}"))
        .and_then(|element| element.dyn_into::<web_sys::HtmlElement>().ok())
    {
        let options = web_sys::FocusOptions::new();
        options.set_prevent_scroll(true);
        let _ = tab.focus_with_options(&options);
    }
}

#[component]
fn StandingSortHeader(
    label: &'static str,
    active: bool,
    onsort: EventHandler<()>,
) -> Element {
    rsx! {
        th {
            scope: "col",
            class: "table-number",
            aria_sort: if active { "descending" } else { "none" },
            button {
                r#type: "button",
                class: "results-sort-button",
                onclick: move |_| onsort.call(()),
                span { class: "sidecomp-sort-label", "{label}" }
                span { aria_hidden: "true",
                    if active {
                        "↓"
                    } else {
                        "◇"
                    }
                }
            }
        }
    }
}
fn photo_src(path: &Option<String>) -> Option<String> {
    path.as_ref().map(|p| format!("{}/static/{}", api::base_url(), p))
}

#[component]
fn StandingAvatar(photo: Option<String>, is_team: bool) -> Element {
    if let Some(src) = photo_src(&photo) {
        rsx! {
            img {
                src: "{src}",
                alt: "",
                class: "rounded-circle me-2 sidecomp-standing-avatar",
                style: "width: 28px; height: 28px; object-fit: cover;",
            }
        }
    } else {
        let icon = if is_team { "fas fa-users" } else { "fas fa-user" };
        rsx! {
            span {
                aria_hidden: "true",
                class: "d-inline-flex align-items-center justify-content-center bg-secondary rounded-circle text-white me-2 sidecomp-standing-avatar",
                style: "width: 28px; height: 28px;",
                i { class: "{icon}", style: "font-size: 0.75rem;" }
            }
        }
    }
}

#[component]
fn StandingRowView(
    row: SideCompStandingRow,
    track_losses: bool,
    viewer_player_id: Option<String>,
) -> Element {
    rsx! {
        tr {
            td { class: "table-number", "{row.rank}" }
            td { class: "table-copy",
                div { class: "sidecomp-standing-player",
                    StandingAvatar { photo: row.profile_photo.clone(), is_team: false }
                    div { class: "sidecomp-standing-identity",
                        div { class: "fw-semibold",
                            "{row.display_name}"
                            if viewer_player_id.as_deref() == Some(row.player_id.as_str()) {
                                span { class: "sidecomp-standing-self", "You" }
                            }
                        }
                        div { class: "small text-muted sidecomp-standing-entry",
                            span { "Entry #{row.entry_number}" }
                            if let Some(weapon) = row.weapon.as_ref() {
                                span { class: "sidecomp-standing-weapon",
                                    span { aria_hidden: "true",
                                        PompfenIcon {
                                            weapon: weapon.clone(),
                                            size: "1.1em".to_string(),
                                        }
                                    }
                                    {pompfen_by_id(weapon).map_or(weapon.as_str(), |option| option.label)}
                                }
                            }
                        }
                        div { class: "small text-muted d-flex align-items-center",
                            StandingAvatar {
                                photo: row.team_profile_photo.clone(),
                                is_team: true,
                            }
                            span {
                                {
                                    let pseudo = row.jersey_name.clone().unwrap_or_else(|| "—".to_string());
                                    let num = row.jersey_number.clone().unwrap_or_default();
                                    let short = row.team_shortname.clone().unwrap_or_else(|| "merc".to_string());
                                    if num.is_empty() {
                                        format!("{pseudo} · {short}")
                                    } else {
                                        format!("{pseudo} ({num}) · {short}")
                                    }
                                }
                            }
                        }
                    }
                }
            }
            td { class: "table-number fw-bold", "{row.wins}" }
            if track_losses {
                td { class: "table-number", "{row.losses.unwrap_or(0)}" }
                td { class: "table-number fw-bold",
                    match row.win_percentage {
                        Some(percentage) => format!("{percentage:.0}%"),
                        None => "—".to_string(),
                    }
                }
            }
        }
    }
}

#[component]
fn PersonalResult(table: SideCompStandingTable, row: SideCompStandingRow) -> Element {
    let weapon = row
        .weapon
        .as_deref()
        .map(|weapon| pompfen_by_id(weapon).map_or(weapon, |option| option.label))
        .unwrap_or("—");
    let group = if table.id == "all" { "Overall" } else { table.title.as_str() };
    rsx! {
        section { class: "card mb-4", aria_label: "Your result",
            div { class: "card-body",
                h3 { class: "h5 mb-1", "Your result" }
                p { class: "small text-muted mb-2",
                    "{group} · {weapon}"
                }
                div { class: "d-flex flex-wrap gap-3",
                    strong { "Rank: {row.rank}" }
                    span { "Wins: {row.wins}" }
                    if table.track_losses {
                        span { "Losses: {row.losses.unwrap_or(0)}" }
                        span {
                            "Win percentage: "
                            {
                                row.win_percentage
                                    .map(|value| format!("{value:.0}%"))
                                    .unwrap_or_else(|| "—".to_string())
                            }
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn StandingsTableView(
    comp_id: i32,
    table: SideCompStandingTable,
    publication_policy: PublicationPolicy,
    viewer_player_id: Option<String>,
) -> Element {
    let track_losses = table.track_losses;
    let mut selection = use_signal(move || StandingSelection {
        weapon: None,
        sort: StandingSort::default_for(track_losses),
    });
    use_effect(
        use_reactive(
            &table.available_weapons,
            move |available_weapons| {
                let selected_weapon = selection.peek().weapon.clone();
                if selected_weapon
                    .is_some_and(|weapon| !available_weapons.contains(&weapon))
                {
                    selection.write().weapon = None;
                }
            },
        ),
    );
    let current_selection = selection().for_loss_tracking(track_losses);
    let sort = current_selection.sort;
    let mut selected_standings = use_resource(
        use_reactive(
            &(comp_id, table.id.clone(), track_losses, publication_policy),
            move |(comp_id, table_id, track_losses, publication_policy)| {
                let selected = selection().for_loss_tracking(track_losses);
                async move {
                    if selected.is_default(track_losses) {
                        return (selected, publication_policy, Ok(None));
                    }
                    let result = api::sidecomp_standings(
                            comp_id,
                            Some(&table_id),
                            selected.weapon.as_deref(),
                            Some(selected.sort.query_value()),
                        )
                        .await;
                    let response_policy = result
                        .as_ref()
                        .map(PublicationPolicy::from_standings)
                        .unwrap_or(publication_policy);
                    let result = result
                        .and_then(|data| {
                            if !data.results_page_enabled {
                                return Err(
                                    api::StandingsError::Request(
                                        "Results are not being shown for this side competition."
                                            .to_string(),
                                    ),
                                );
                            }
                            data.tables
                                .into_iter()
                                .find(|table| table.id == table_id)
                                .map(Some)
                                .ok_or_else(|| api::StandingsError::Request(
                                    "The standings table is unavailable.".to_string(),
                                ))
                        });
                    (selected, response_policy, result)
                }
            },
        ),
    );
    use_future(move || async move {
        #[cfg(target_arch = "wasm32")]
        loop {
            TimeoutFuture::new(1000).await;
            if selected_standings.finished() {
                selected_standings.restart();
            }
        }
    });
    let displayed_table = if current_selection.is_default(track_losses) {
        Some(Ok(table.clone()))
    } else {
        match selected_standings.read().as_ref() {
            Some(
                (loaded_selection, response_policy, result),
            ) if loaded_selection == &current_selection
                && response_policy == &publication_policy => {
                    match result {
                        Ok(Some(loaded)) if loaded.track_losses != track_losses => None,
                        _ => result.clone().transpose(),
                    }
                },
            _ => None,
        }
    };
    let rows = match &displayed_table {
        Some(Ok(loaded)) => loaded.rows.clone(),
        _ => Vec::new(),
    };
    let weapon_name = current_selection
        .weapon
        .as_ref()
        .map(|id| {
            pompfen_by_id(id).map_or(id.as_str(), |option| option.label).to_string()
        });
    let ranking_name = if sort == StandingSort::WinRatio {
        "win percentage"
    } else {
        "wins"
    };
    let summary = format!(
        "{} · Ranked by {ranking_name}",
        weapon_name.as_deref().unwrap_or("All weapons"),
    );
    let weapon_select_id = format!("sidecomp-{comp_id}-{}-weapon", table.id);
    rsx! {
        section { class: "mb-4", aria_label: "{table.title}",
            div { class: "d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2",
                h3 { class: "visually-hidden", "{table.title}" }
                if !table.available_weapons.is_empty() {
                    div { class: "sidecomp-weapon-filter",
                        label {
                            r#for: weapon_select_id.clone(),
                            class: "form-label mb-0",
                            "Weapon"
                        }
                        select {
                            id: weapon_select_id,
                            class: "form-select",
                            value: current_selection.weapon.clone().unwrap_or_default(),
                            onchange: move |event| {
                                let weapon = event.value();
                                selection.write().weapon = if weapon.is_empty() { None } else { Some(weapon) };
                            },
                            option { value: "", "All weapons" }
                            for weapon in table.available_weapons.iter() {
                                option { value: weapon.clone(),
                                    { pompfen_by_id(weapon).map_or(weapon.as_str(), | option | option
                                            .label) }
                                }
                            }
                        }
                    }
                }
            }
            if displayed_table.is_none() {
                p { role: "status", class: "text-muted", "Loading standings…" }
            }
            if let Some(Err(error)) = &displayed_table {
                div { role: "alert", class: "alert alert-danger",
                    p { class: "mb-2", "Could not load standings: {error}" }
                    button {
                        r#type: "button",
                        class: "btn btn-outline-secondary",
                        onclick: move |
                                _ | selected_standings.restart(),
                        "Retry standings"
                    }
                }
            }
            if matches!(&displayed_table, Some(Ok(_))) && rows.is_empty() {
                p { class: "text-muted",
                    "No results yet."
                }
            }
            div {
                class: "table-responsive",
                role: "region",
                "aria-label": "{table.title} standings",
                tabindex: "0",
                table { class: "table table-striped align-middle sidecomp-standings-table",
                    caption { class: "visually-hidden",
                        "{table.title} standings. {summary}. Rankings are highest first."
                        if table.track_losses {
                            " Select Wins or Win % to change the ranking."
                        }
                    }
                    thead {
                        tr {
                            th { scope: "col", class: "table-number", "Rank" }
                            th { scope: "col", "Player" }
                            StandingSortHeader {
                                label: "Wins",
                                active: sort == StandingSort::Wins,
                                onsort: move |_| {
                                    selection.write().sort = StandingSort::Wins;
                                },
                            }
                            if table.track_losses {
                                th { scope: "col", class: "table-number", "Losses" }
                                StandingSortHeader {
                                    label: "Win %",
                                    active: sort == StandingSort::WinRatio,
                                    onsort: move |_| {
                                        selection.write().sort = StandingSort::WinRatio;
                                    },
                                }
                            }
                        }
                    }
                    tbody {
                        for row in rows.iter() {
                            StandingRowView {
                                key: "{row.registration_id}",
                                row: row.clone(),
                                track_losses: table.track_losses,
                                viewer_player_id: viewer_player_id.clone(),
                            }
                        }
                    }
                }
            }

        }
    }
}

#[component]
pub fn SideCompResults(url: String, comp_id: i32) -> Element {
    let account = crate::auth::use_account();
    let mut selected_group = use_signal(String::new);
    let viewer_identity = account
        .read()
        .as_ref()
        .and_then(|result| result.as_ref().ok())
        .map(|user| (user.user_type.clone(), user.id.clone()));
    let viewer_player_id = viewer_identity
        .as_ref()
        .filter(|(user_type, _)| user_type == "player")
        .map(|(_, id)| id.clone());
    let mut standings = use_resource(
        use_reactive(
            &(comp_id, viewer_identity.clone()),
            move |(comp_id, identity)| async move {
                (identity, api::sidecomp_standings(comp_id, None, None, None).await)
            },
        ),
    );
    let mut last_standings = use_signal(|| {
        None::<(Option<(String, String)>, SideCompStandings)>
    });
    use_effect(move || {
        match standings.read().as_ref() {
            Some((identity, Ok(data))) => {
                last_standings.set(Some((identity.clone(), data.clone())))
            }
            Some((_, Err(api::StandingsError::NoAccess))) => last_standings.set(None),
            _ => {}
        }
    });
    use_future(move || async move {
        #[cfg(target_arch = "wasm32")]
        loop {
            TimeoutFuture::new(1000).await;
            if standings.finished() {
                standings.restart();
            }
        }
    });
    let url_back = url.clone();
    let displayed_standings = last_standings()
        .filter(|(identity, data)| identity == &viewer_identity && data.id == comp_id)
        .map(|(_, data)| data);
    let viewer_key = format!("{viewer_identity:?}");
    rsx! {
        style { {include_str!("sidecomp_results.css")} }
        div { class: "row",
            div { class: "col-12",
                Link {
                    to: Route::SideCompDetail {
                        url: url_back,
                        comp_id,
                    },
                    class: "btn btn-link",
                    "<- Back to side competition"
                }
                if let Some((_, Err(error))) = standings.read().as_ref() {
                    div { class: "alert alert-danger", role: "alert",
                        p { class: "mb-2", "Could not refresh results: {error}" }
                        button {
                            r#type: "button",
                            class: "btn btn-outline-secondary",
                            onclick: move | _ |
                                    standings.restart(),
                            "Retry results"
                        }
                    }
                }
                match displayed_standings {
                    Some(data) => {
                        if !data.results_page_enabled {
                            rsx! {
                                div { class: "alert alert-secondary", "Results are not being shown for this side competition." }
                            }
                        } else {
                            let publication_policy = PublicationPolicy::from_standings(&data);
                            let tables = data.tables.clone();
                            let selected_id = selected_group();
                            let active_id = tables.iter()
                                .find(|table| table.id == selected_id)
                                .or_else(|| tables.first())
                                .map(|table| table.id.clone())
                                .unwrap_or_default();
                            let name = data.name.clone();
                            let title = if data.finalized {
                                format!("{name} - Results (Final)")
                            } else {
                                format!("{name} - Results (Unofficial)")
                            };
                            rsx! {
                                h2 { "{title}" }
                                for table in &tables {
                                    if let Some(row) = table
                                        .personal_row
                                        .as_ref()
                                        .filter(|row| viewer_player_id.as_deref() == Some(row.player_id.as_str()))
                                    {
                                        PersonalResult { table: table.clone(), row: row.clone() }
                                    }
                                }
                                if tables.len() > 1 {
                                    div {
                                        class: "event-route-tabs sidecomp-results-tabs mb-3",
                                        role: "tablist",
                                        aria_label: "Results",
                                        for (index, table) in tables.iter().enumerate() {
                                            button {
                                                key: "{table.id}",
                                                id: "sidecomp-{comp_id}-tab-{table.id}",
                                                class: "event-route-tab",
                                                r#type: "button",
                                                role: "tab",
                                                aria_selected: active_id == table.id,
                                                aria_controls: "sidecomp-{comp_id}-panel-{table.id}",
                                                tabindex: if active_id == table.id { "0" } else { "-1" },
                                                onclick: {
                                                    let id = table.id.clone();
                                                    move |_| selected_group.set(id.clone())
                                                },
                                                onkeydown: {
                                                    let ids = tables.iter().map(|table| table.id.clone()).collect::<Vec<_>>();
                                                    move |event: KeyboardEvent| {
                                                        let next = match event.key() {
                                                            Key::ArrowRight => (index + 1) % ids.len(),
                                                            Key::ArrowLeft => (index + ids.len() - 1) % ids.len(),
                                                            Key::Home => 0,
                                                            Key::End => ids.len() - 1,
                                                            _ => return,
                                                        };
                                                        event.prevent_default();
                                                        selected_group.set(ids[next].clone());
                                                        focus_results_tab(comp_id, &ids[next]);
                                                    }
                                                },
                                                "{table.title}"
                                            }
                                        }
                                    }
                                }
                                for table in &tables {
                                    div {
                                        key: "{table.id}",
                                        id: "sidecomp-{comp_id}-panel-{table.id}",
                                        role: (tables.len() > 1).then_some("tabpanel"),
                                        aria_labelledby: (tables.len() > 1).then(|| format!("sidecomp-{comp_id}-tab-{}", table.id)),
                                        tabindex: (tables.len() > 1).then_some("0"),
                                        hidden: active_id != table.id,
                                        if active_id == table.id {
                                            StandingsTableView {
                                                key: "{table.id}-{viewer_key}",
                                                comp_id,
                                                table: table.clone(),
                                                publication_policy,
                                                viewer_player_id: viewer_player_id.clone(),
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    None => rsx! {
                        if !matches!(standings.read().as_ref(), Some((_, Err(_)))) {
                            p { role: "status", "Loading results…" }
                        }
                    },
                }
            }
        }
    }
}
