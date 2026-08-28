use crate::Route;
use crate::api;
use crate::components::{
    EditRegistrationContext, EditRegistrationModal, EventHeader, LeagueRegistrationButtons,
};
use crate::types::{ToEntry, User};
use dioxus::prelude::*;


fn is_current_user_to(me: Option<&Result<User, String>>, to_entries: &[ToEntry]) -> bool {
    me.and_then(|r| r.as_ref().ok()).map_or(false, |u| {
        to_entries
            .iter()
            .any(|e| e.user_id == u.id && e.user_type == u.user_type)
    })
}

fn format_date(iso: &str) -> String {
    iso.split('T').next().unwrap_or(iso).to_string()
}

fn format_date_display(start: &str, end: Option<&String>) -> String {
    let start_fmt = format_date(start);
    match end {
        None => start_fmt,
        Some(e) if e.as_str() == start => start_fmt,
        Some(e) => format!("{} - {}", start_fmt, format_date(e)),
    }
}

#[component]
pub fn TournamentHome(url: String) -> Element {
    rsx! {
        TournamentHomeContent { url, initial_tab: None::<String> }
    }
}

#[component]
pub fn TournamentHomeWithTab(url: String, tab: String) -> Element {
    rsx! {
        TournamentHomeContent { url, initial_tab: Some(tab) }
    }
}

#[component]
fn TournamentHomeContent(url: String, initial_tab: Option<String>) -> Element {
    let url_for_data = url.clone();
    let navigator = use_navigator();
    let mut refresh = use_signal(|| 0u32);
    let data = use_resource(move || {
        let _ = refresh();
        let u = url_for_data.clone();
        async move { api::tournament_detail(&u).await.map_err(|e| e.to_string()) }
    });
    let me_res = use_resource(move || async move { api::me().await });
    let val = data.value();
    let backend = api::base_url();
    let mut delete_modal_open = use_signal(|| false);
    let mut delete_confirm_url = use_signal(|| String::new());
    let mut delete_error = use_signal(|| None::<String>);
    let mut show_edit_player_modal = use_signal(|| false);
    let mut show_edit_team_modal = use_signal(|| false);
    let mut show_league_edit_modal = use_signal(|| false);
    let url_for_delete_confirm = url.clone();
    let mut about_markdown = use_signal(|| Option::<String>::None);
    let mut delete_redirect_league = use_signal(|| None as Option<String>);
    use_effect(move || {
        let v = val.read();
        if let Some(Ok(d)) = v.as_ref() {
            about_markdown.set(d.tournament.about.clone());
            delete_redirect_league.set(d.tournament.league.as_ref().map(|l| l.league_url.clone()));
        } else {
            about_markdown.set(None);
            delete_redirect_league.set(None);
        }
    });
    let about_html = use_resource(use_reactive(&about_markdown, move |md| {
        let md = md().clone();
        async move {
            match md.as_deref() {
                Some(m) if !m.is_empty() => api::render_markdown(m).await,
                _ => Ok(String::new()),
            }
        }
    }));

    let url_for_sidecomps = url.clone();
    let sidecomps = use_resource(move || {
        let u = url_for_sidecomps.clone();
        async move { api::sidecomps_list(&u).await }
    });

    let mut info_tab = use_signal(|| None::<String>);

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            {{
                let teams_count = d.teams_with_counts.len();
                let unattached_count = d.unattached_players.len();
                rsx! {
            EventHeader {
                title: d.tournament.name.clone(),
                subtitle: format!("{} • {}", d.tournament.location.as_deref().unwrap_or("Location TBA"), format_date_display(&d.tournament.start_date, d.tournament.end_date.as_ref())),
                badge_league_url: d.tournament.league.as_ref().map(|l| l.league_url.clone()),
                badge_season: None,
                badge_name: d.tournament.league.as_ref().map(|l| l.name.clone()),
            }

            div { class: "row mb-3",
                div { class: "col-12 d-flex flex-wrap gap-2",
                    if d.tournament.schedule_published {
                        Link { to: Route::Schedule { url: url.clone() }, class: "btn btn-primary", "Schedule" }
                    } else {
                        Link { to: Route::Schedule { url: url.clone() }, class: "btn btn-outline-secondary", "Schedule (Not Published)" }
                    }
                    Link { to: Route::Results { url: url.clone() }, class: "btn btn-outline-primary", "Results" }
                    if d.tournament.bracket_published {
                        Link { to: Route::Bracket { url: url.clone() }, class: "btn btn-outline-primary", "Bracket" }
                    } else if is_current_user_to(me_res.read().as_ref(), &d.to_entries) {
                        Link { to: Route::Bracket { url: url.clone() }, class: "btn btn-outline-secondary", "Bracket (Not Published)" }
                    }
                    if let Some(ref l) = d.tournament.league {
                        LeagueRegistrationButtons {
                            league_url: l.league_url.clone(),
                            registration_open: l.registration_open,
                            team_registration_open: Some(l.team_registration_open),
                            player_registration_open: Some(l.player_registration_open),
                            current_user: me_res.read().as_ref().cloned(),
                            is_team_registered: d.is_current_team_registered,
                            is_player_registered: d.is_current_player_registered,
                            use_edit_modal: true,
                            on_edit_registration: move |_| show_league_edit_modal.set(true),
                            register_label: String::from("Register (league)"),
                        }
                    } else {
                        if let Some(current_user) = me_res.read().as_ref().and_then(|r| r.as_ref().ok()) {
                            if current_user.user_type == "team" {
                                if d.is_current_team_registered {
                                    a { href: "{backend}/{url}/invitations", class: "btn btn-outline-secondary", "Manage Roster" }
                                    button {
                                        class: "btn btn-outline-secondary",
                                        onclick: move |_| show_edit_team_modal.set(true),
                                        "Edit Registration"
                                    }
                                } else if d.tournament.team_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-success", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Team registration closed"
                                    }
                                }
                            } else if current_user.user_type == "player" {
                                if d.is_current_player_registered {
                                    button {
                                        class: "btn btn-outline-secondary",
                                        onclick: move |_| show_edit_player_modal.set(true),
                                        "Edit Registration"
                                    }
                                } else if d.tournament.player_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-success", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Player registration closed"
                                    }
                                }
                            } else {
                                if d.tournament.team_registration_open || d.tournament.player_registration_open {
                                    Link { to: Route::TournamentRegister { url: url.clone() }, class: "btn btn-success", "Register" }
                                } else {
                                    button {
                                        r#type: "button",
                                        class: "btn btn-secondary disabled",
                                        disabled: true,
                                        "Registration closed"
                                    }
                                }
                            }
                        } else {
                            button {
                                r#type: "button",
                                class: "btn btn-secondary disabled",
                                disabled: true,
                                "Sign in to register"
                            }
                        }
                    }
                }
            }

            {
                let viewer_is_to = is_current_user_to(me_res.read().as_ref(), &d.to_entries);
                let active_info_tab = info_tab().unwrap_or_else(|| {
                    if initial_tab.as_deref() == Some("sidecomps") {
                        "sidecomps".to_string()
                    } else {
                        "info".to_string()
                    }
                });
                rsx! {
            div { class: "row",
                div { class: "col-md-8",
                    div { class: "card",
                        div { class: "card-header",
                            ul { class: "nav nav-tabs card-header-tabs",
                                li { class: "nav-item",
                                    a {
                                        class: if active_info_tab == "info" { "nav-link active" } else { "nav-link" },
                                        href: "#",
                                        onclick: move |evt| { evt.prevent_default(); info_tab.set(Some("info".to_string())); },
                                        "Tournament Information"
                                    }
                                }
                                li { class: "nav-item",
                                    a {
                                        class: if active_info_tab == "sidecomps" { "nav-link active" } else { "nav-link" },
                                        href: "#",
                                        onclick: move |evt| { evt.prevent_default(); info_tab.set(Some("sidecomps".to_string())); },
                                        "Side Competitions"
                                    }
                                }
                            }
                        }
                        div { class: "card-body",
                            if active_info_tab == "info" {
                                div { class: "row mb-3",
                                    div { class: "col-md-6",
                                        p { strong { "Start Date: " } "{format_date(&d.tournament.start_date)}" }
                                        p { strong { "End Date: " } "{d.tournament.end_date.as_ref().map(|e| format_date(e)).unwrap_or_else(|| \"TBA\".into())}" }
                                    }
                                    div { class: "col-md-6",
                                        if let Some(max) = d.tournament.n_max_teams {
                                            p { strong { "Max Teams: " } "{max}" }
                                        }
                                        if let Some(roster) = d.tournament.max_team_size_roster {
                                            p { strong { "Max Team Size (Roster): " } "{roster}" }
                                        }
                                        if let Some(field) = d.tournament.max_team_size_field {
                                            p { strong { "Max Team Size (Field): " } "{field}" }
                                        }
                                    }
                                }
                                if d.tournament.league.is_none() && {
                                    let ro = d.tournament.team_registration_open || d.tournament.player_registration_open;
                                    let tf = d.tournament.team_reg_fee.unwrap_or(0.0);
                                    let pf = d.tournament.player_reg_fee.unwrap_or(0.0);
                                    ro && (tf > 0.0 || pf > 0.0)
                                } {
                                    div { class: "alert alert-info mb-3",
                                        h6 { class: "mb-2", "Registration Fees" }
                                        {
                                            let tf = d.tournament.team_reg_fee.unwrap_or(0.0);
                                            let pf = d.tournament.player_reg_fee.unwrap_or(0.0);
                                            let tf_str = format!("${:.2}", tf);
                                            let pf_str = format!("${:.2}", pf);
                                            rsx! {
                                                if tf > 0.0 {
                                                    p { class: "mb-1", strong { "Team Registration: " } "{tf_str}" }
                                                }
                                                if pf > 0.0 {
                                                    p { class: "mb-0", strong { "Player Registration: " } "{pf_str}" }
                                                }
                                            }
                                        }
                                    }
                                }
                                if let Some(about) = &d.tournament.about {
                                    if !about.is_empty() {
                                        hr {}
                                        if let Some(Ok(html)) = about_html.value().read().as_ref() {
                                            if html.is_empty() {
                                                div { class: "markdown-content", style: "white-space: pre-wrap;", "{about}" }
                                            } else {
                                                div { dangerous_inner_html: "{html}" }
                                            }
                                        } else {
                                            div { class: "markdown-content", style: "white-space: pre-wrap;", "{about}" }
                                        }
                                    }
                                } else {
                                    p { class: "text-muted", "Tournament details coming soon!" }
                                }
                            } else {
                                if viewer_is_to {
                                    div { class: "mb-3 d-flex justify-content-end",
                                        Link {
                                            to: Route::SideCompNew { url: url.clone() },
                                            class: "btn btn-sm btn-outline-success",
                                            title: "Add side competition",
                                            i { class: "fas fa-plus" }
                                        }
                                    }
                                }
                                match sidecomps.read().as_ref() {
                                    Some(Ok(rows)) => {
                                        if rows.is_empty() {
                                            rsx! { p { class: "text-muted mb-0", "No side competitions for this tournament." } }
                                        } else {
                                            rsx! {
                                                ul { class: "list-group",
                                                    for row in rows.iter() {
                                                        {
                                                            let comp_id = row.id;
                                                            let row_name = row.name.clone();
                                                            let mut sidecomps_for_row = sidecomps;
                                                            rsx! {
                                                                li {
                                                                    key: "{comp_id}",
                                                                    class: "list-group-item d-flex justify-content-between align-items-center p-0",
                                                                    Link {
                                                                        to: Route::SideCompDetail { url: url.clone(), comp_id },
                                                                        class: "text-decoration-none text-reset p-3 flex-grow-1",
                                                                        div {
                                                                            strong { "{row.name}" }
                                                                            span { class: "badge bg-secondary ms-2", "{row.type_}" }
                                                                            if row.registration_open {
                                                                                span { class: "badge bg-success ms-2", "Open" }
                                                                            } else {
                                                                                span { class: "badge bg-secondary ms-2", "Closed" }
                                                                            }
                                                                            span { class: "text-muted ms-2", "({row.registrant_count} registered)" }
                                                                        }
                                                                    }
                                                                    if viewer_is_to {
                                                                        div { class: "d-flex gap-1 px-3",
                                                                            Link {
                                                                                to: Route::SideCompEdit { url: url.clone(), comp_id },
                                                                                class: "btn btn-sm btn-outline-secondary",
                                                                                title: "Edit",
                                                                                i { class: "fas fa-pen" }
                                                                            }
                                                                            button {
                                                                                class: "btn btn-sm btn-outline-danger",
                                                                                title: "Delete",
                                                                                onclick: move |_| {
                                                                                    let confirmed = web_sys::window()
                                                                                        .and_then(|w| w
                                                                                            .confirm_with_message(&format!(
                                                                                                "Delete '{}'? This cannot be undone.",
                                                                                                row_name
                                                                                            ))
                                                                                            .ok())
                                                                                        .unwrap_or(false);
                                                                                    if !confirmed {
                                                                                        return;
                                                                                    }
                                                                                    spawn(async move {
                                                                                        match api::sidecomp_delete(comp_id).await {
                                                                                            Ok(_) => {
                                                                                                sidecomps_for_row.restart();
                                                                                            }
                                                                                            Err(e) => {
                                                                                                web_sys::console::error_1(
                                                                                                    &format!("sidecomp_delete failed: {}", e).into(),
                                                                                                );
                                                                                            }
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
                                    _ => rsx! {},
                                }
                            }
                        }
                    }
                    // Upload Footage is now a modal triggered from the button row.
                }
                                if is_current_user_to(me_res.read().as_ref(), &d.to_entries) {
                                    div { class: "col-md-4",
                                        div { class: "card",
                                            div { class: "card-header", h5 { class: "mb-0", "Admin" } }
                                            div { class: "card-body",
                                                div { class: "d-grid gap-2",
                                                    Link { to: Route::TournamentSettings { url: url.clone() }, class: "btn btn-outline-secondary", "Settings" }
                                                    if let Some(ref l) = d.tournament.league {
                                                        Link { to: Route::LeagueManage { league_url: l.league_url.clone() }, class: "btn btn-outline-warning", "Registration Management" }
                                                    } else {
                                                        Link { to: Route::Manage { url: url.clone() }, class: "btn btn-outline-warning", "Registration Management" }
                                                    }
                                                    Link {
                                                        to: Route::ManageFootage { url: url.clone() },
                                                        class: "btn btn-outline-secondary",
                                                        "Manage Footage"
                                                    }
                                    button {
                                        class: "btn btn-outline-danger",
                                        onclick: move |_| {
                                            delete_modal_open.set(true);
                                            delete_confirm_url.set(String::new());
                                            delete_error.set(None);
                                        },
                                        "Delete Tournament"
                                    }
                                }
                            }
                        }
                    }
                }
            }
                }
            }

            if !d.teams_with_counts.is_empty() {
                div { class: "row mt-4",
                    div { class: "col-12",
                        div { class: "card",
                            div { class: "card-header", h5 { class: "mb-0", "Registered Teams ({teams_count})" } }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table table-striped",
                                        thead {
                                            tr {
                                                th { "Team Name" }
                                                th { "Players" }
                                                th { "Registration Date" }
                                            }
                                        }
                                        tbody {
                                            for team in d.teams_with_counts.iter() {
                                                tr { key: "{team.team_id}",
                                                    td {
                                                        div { class: "d-flex align-items-center",
                                                            div { class: "flex-shrink-0 me-2",
                                                                if let Some(photo) = &team.profile_photo {
                                                                    img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle", style: "width: 40px; height: 40px; object-fit: cover;" }
                                                                } else {
                                                                    div { class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle", style: "width: 40px; height: 40px;",
                                                                        span { class: "text-white", "👥" }
                                                                    }
                                                                }
                                                            }
                                                            div {
                                                                Link { to: Route::TeamProfilePage { id: team.team_id.clone() }, class: "text-decoration-none",
                                                                    strong { "{team.pseudonym.as_deref().unwrap_or(&team.team_name)}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    td {
                                                        span { class: "badge bg-primary", "{team.player_count}" }
                                                        if let Some(max) = d.tournament.max_team_size_roster {
                                                            span { " / {max}" }
                                                        }
                                                    }
                                                    td { "{team.registered_at.as_deref().unwrap_or(\"-\")}" }
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

            if !d.unattached_players.is_empty() {
                div { class: "row mt-4",
                    div { class: "col-12",
                        div { class: "card",
                            div { class: "card-header", h5 { class: "mb-0", "Unattached Players ({unattached_count})" } }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table table-striped",
                                        thead {
                                            tr {
                                                th { "Player Name" }
                                                th { "Jersey" }
                                                th { "Registration Date" }
                                            }
                                        }
                                        tbody {
                                            for p in d.unattached_players.iter() {
                                                tr { key: "{p.player_id}",
                                                    td {
                                                        div { class: "d-flex align-items-center",
                                                            div { class: "flex-shrink-0 me-2",
                                                                if let Some(photo) = &p.profile_photo {
                                                                    img { src: "{backend}/static/{photo}", alt: "", class: "rounded-circle", style: "width: 40px; height: 40px; object-fit: cover;" }
                                                                } else {
                                                                    div { class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle", style: "width: 40px; height: 40px;",
                                                                        span { class: "text-white", "👤" }
                                                                    }
                                                                }
                                                            }
                                                            div {
                                                                Link { to: Route::PlayerProfilePage { id: p.player_id.clone() }, class: "text-decoration-none",
                                                                    strong { "{p.player_name}" }
                                                                }
                                                            }
                                                        }
                                                    }
                                                    td {
                                                        if let (Some(num), Some(name)) = (&p.jersey_number, &p.jersey_name) {
                                                            "#{num} {name}"
                                                        } else if let Some(name) = &p.jersey_name {
                                                            "{name}"
                                                        } else if let Some(num) = &p.jersey_number {
                                                            "#{num}"
                                                        } else {
                                                            span { class: "text-muted", "No jersey info" }
                                                        }
                                                    }
                                                    td { "{p.registered_at.as_deref().unwrap_or(\"-\")}" }
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


            if show_edit_player_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::Tournament { tournament_url: url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_player_modal.set(false),
                        on_success: move |_| {
                            show_edit_player_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }

            if show_edit_team_modal() {
                if let Some(Ok(me)) = me_res.read().as_ref() {
                    EditRegistrationModal {
                        context: EditRegistrationContext::Tournament { tournament_url: url.clone() },
                        user_type: me.user_type.clone(),
                        on_close: move |_| show_edit_team_modal.set(false),
                        on_success: move |_| {
                            show_edit_team_modal.set(false);
                            refresh.set(refresh() + 1);
                        },
                    }
                }
            }

            if show_league_edit_modal() {
                if let Some(ref l) = d.tournament.league {
                    if let Some(Ok(me)) = me_res.read().as_ref() {
                        EditRegistrationModal {
                            context: EditRegistrationContext::League { league_url: l.league_url.clone() },
                            user_type: me.user_type.clone(),
                            on_close: move |_| show_league_edit_modal.set(false),
                            on_success: move |_| {
                                show_league_edit_modal.set(false);
                                refresh.set(refresh() + 1);
                            },
                        }
                    }
                }
            }

            if is_current_user_to(me_res.read().as_ref(), &d.to_entries) && delete_modal_open() {
                div { class: "modal d-block", tabindex: -1, style: "background: rgba(0,0,0,0.5)",
                    div { class: "modal-dialog modal-dialog-centered",
                        div { class: "modal-content",
                            div { class: "modal-header",
                                h5 { class: "modal-title", "Delete Tournament" }
                                button { class: "btn-close", onclick: move |_| delete_modal_open.set(false) }
                            }
                            div { class: "modal-body",
                                if let Some(ref err) = delete_error() {
                                    div { class: "alert alert-danger mb-3", "{err}" }
                                }
                                div { class: "alert alert-danger",
                                    strong { "Warning: " }
                                    "This action cannot be undone. All matches, registrations, and data will be permanently removed."
                                }
                                p { "To confirm, type the tournament URL exactly:" }
                                p { class: "text-center mb-2", strong { "{url}" } }
                                form {
                                    id: "delete-tournament-form",
                                    onsubmit: move |ev| {
                                        ev.prevent_default();
                                        if delete_confirm_url() != url_for_delete_confirm {
                                            return;
                                        }
                                        delete_error.set(None);
                                        let nav = navigator.clone();
                                        let url_submit = url_for_delete_confirm.clone();
                                        let confirm = delete_confirm_url();
                                        let redirect_league = delete_redirect_league();
                                        spawn(async move {
                                            match api::delete_tournament(&url_submit, &confirm).await {
                                                Ok(res) if res.success => {
                                                    if let Some(lu) = redirect_league {
                                                        let _ = nav.push(Route::LeagueHome { league_url: lu });
                                                    } else {
                                                        let _ = nav.push(Route::Index {});
                                                    }
                                                }
                                                Ok(res) => {
                                                    delete_error.set(Some(res.error.unwrap_or_else(|| "Delete failed.".to_string())));
                                                }
                                                Err(e) => {
                                                    delete_error.set(Some(e));
                                                }
                                            }
                                        });
                                    },
                                    div { class: "mb-3",
                                        label { class: "form-label", "Tournament URL:" }
                                        input {
                                            class: "form-control",
                                            name: "confirm_url",
                                            "type": "text",
                                            placeholder: "{url}",
                                            value: "{delete_confirm_url()}",
                                            oninput: move |e| delete_confirm_url.set(e.value()),
                                        }
                                    }
                                }
                            }
                            div { class: "modal-footer",
                                button { class: "btn btn-secondary", onclick: move |_| delete_modal_open.set(false), "Cancel" }
                                button {
                                    class: "btn btn-danger",
                                    "type": "submit",
                                    form: "delete-tournament-form",
                                    disabled: delete_confirm_url() != url,
                                    "Delete Tournament"
                                }
                            }
                        }
                    }
                }
            }
            }
            }}
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { class: "text-muted", "Loading…" }
        }
    }
}
