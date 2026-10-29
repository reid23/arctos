use crate::api;
use crate::components::{InvitationActions, RequestEpoch, PaymentStatusBadge, RegistrationStatusBadge};
use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn LeagueInvitations(league_url: String) -> Element {
    let url_for_data = league_url.clone();
    let mut refresh = use_signal(|| 0u32);
    let data = use_resource(use_reactive(&(url_for_data, refresh), |(u, r)| {
        let u = u.clone();
        let _ = r();
        async move { api::league_invitations(&u).await.map_err(|e| e.to_string()) }
    }));
    let mut confirmed = use_signal(|| None::<crate::types::TournamentInvitationsResponse>);
    let refresh_epoch = use_signal(RequestEpoch::default);
    let snapshot = confirmed().map(Ok).or_else(|| data.read().clone());
    let backend = api::base_url();
    rsx! {
        if let Some(Ok(d)) = snapshot.as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h2 { "{d.team_registration.pseudonym} roster" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item", Link { to: Route::LeagueHome { league_url: league_url.clone() }, "{d.tournament.name}" } }
                            li { class: "breadcrumb-item active", "Roster" }
                        }
                    }
                }
            }

            div { class: "row",
                div { class: "col-12",
                    div { class: "card data-section",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Pending Player Requests" }
                        }
                        div { class: "card-body",
                            p { class: "small text-muted", "Declining leaves the player confirmed for the league and unattached to a team." }
                            if d.invitations.is_empty() {
                                p { class: "text-muted", "No pending requests." }
                            } else {
                                p { class: "data-section-summary",
                                    strong { "Current Team Size: " }
                                    "{d.current_team_size}"
                                    if let Some(max) = d.tournament.max_team_size_roster.filter(|max| *max > 0) {
                                        " / {max} (max)"
                                    }
                                }
                                div { class: "table-responsive",
                                    table { class: "table roster-table",
                                        thead {
                                            tr {
                                                th { class: "table-nowrap", "Player" }
                                                th { class: "table-nowrap", "Jersey Name" }
                                                th { class: "table-number", "Jersey Number" }
                                                th { class: "table-nowrap", "Actions" }
                                            }
                                        }
                                        tbody {
                                            for invitation in d.invitations.iter() {
                                                {
                                                    let inv_id = invitation.registration.id;
                                                    let at_cap = d.tournament
                                                        .max_team_size_roster
                                                        .filter(|max| *max > 0)
                                                        .is_some_and(|max| d.current_team_size >= max);
                                                    rsx! {
                                                        tr { key: "{invitation.registration.id}",
                                                            td { class: "table-copy",
                                                                div { class: "d-flex align-items-center",
                                                                    div { class: "flex-shrink-0 me-2",
                                                                        if let Some(photo) = &invitation.player.profile_photo {
                                                                            img { src: "{backend}/static/{photo}", alt: "{invitation.player.name}", class: "rounded-circle", style: "width: 32px; height: 32px; object-fit: cover;" }
                                                                        } else {
                                                                            div { class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle", style: "width: 32px; height: 32px;",
                                                                                i { class: "fas fa-user text-white" }
                                                                            }
                                                                        }
                                                                    }
                                                                    div { class: "flex-grow-1",
                                                                        Link { to: Route::PlayerProfilePage { id: invitation.player.id.clone() }, class: "roster-name", "{invitation.player.name}" }
                                                                    }
                                                                }
                                                            }
                                                            td { class: "table-copy", "data-label": "Jersey name", "{invitation.registration.jersey_name.as_deref().unwrap_or(\"N/A\")}" }
                                                            td { class: "table-number", "data-label": "Jersey number", "{invitation.registration.jersey_number.as_deref().unwrap_or(\"N/A\")}" }
                                                            td { class: "roster-actions", "data-label": "Actions",
                                                                InvitationActions {
                                                                    scope_url: league_url.clone(), league: true,
                                                                    registration_id: inv_id, player_name: invitation.player.name.clone(), at_capacity: at_cap,
                                                                    refresh_epoch, on_refreshed: move |roster| confirmed.set(Some(roster)),
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

                    if !d.team_roster.is_empty() {
                        div { class: "card data-section mt-3",
                            div { class: "card-header",
                                h5 { class: "mb-0", "Team Roster" }
                            }
                            div { class: "card-body",
                                div { class: "table-responsive",
                                    table { class: "table roster-table",
                                        thead {
                                            tr {
                                                th { class: "table-nowrap", "Player" }
                                                th { class: "table-nowrap", "Jersey Name" }
                                                th { class: "table-number", "Jersey Number" }
                                                th { class: "table-nowrap", "Status" }
                                                th { class: "table-nowrap", "Payment" }
                                            }
                                        }
                                        tbody {
                                            for roster_item in d.team_roster.iter() {
                                                tr { key: "{roster_item.registration.id}",
                                                    td { class: "table-copy",
                                                        div { class: "d-flex align-items-center",
                                                            div { class: "flex-shrink-0 me-2",
                                                                if let Some(photo) = &roster_item.player.profile_photo {
                                                                    img { src: "{backend}/static/{photo}", alt: "{roster_item.player.name}", class: "rounded-circle", style: "width: 32px; height: 32px; object-fit: cover;" }
                                                                } else {
                                                                    div { class: "d-flex align-items-center justify-content-center bg-secondary rounded-circle", style: "width: 32px; height: 32px;",
                                                                        i { class: "fas fa-user text-white" }
                                                                    }
                                                                }
                                                            }
                                                            div { class: "flex-grow-1",
                                                                Link { to: Route::PlayerProfilePage { id: roster_item.player.id.clone() }, class: "roster-name", "{roster_item.player.name}" }
                                                            }
                                                        }
                                                    }
                                                    td { class: "table-copy", "data-label": "Jersey name", "{roster_item.registration.jersey_name.as_deref().unwrap_or(\"N/A\")}" }
                                                    td { class: "table-number", "data-label": "Jersey number", "{roster_item.registration.jersey_number.as_deref().unwrap_or(\"N/A\")}" }
                                                    td { class: "roster-wide", "data-label": "Status",
                                                        RegistrationStatusBadge { status: roster_item.registration.status.clone() }
                                                    }
                                                    td { "data-label": "Payment",
                                                        if roster_item.registration.paid {
                                                            PaymentStatusBadge { paid: true }
                                                            if roster_item.registration.amount_paid > 0.0 {
                                                                {
                                                                    let paid_amount = format!("${:.2}", roster_item.registration.amount_paid);
                                                                    rsx! { small { class: "text-muted ms-1 table-number", "{paid_amount}" } }
                                                                }
                                                            }
                                                        } else {
                                                            PaymentStatusBadge { paid: false }
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
        } else if let Some(Err(e)) = snapshot.as_ref() {
            p { class: "text-danger", role: "alert", "Could not load roster: {e}" }
            button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| refresh.set(refresh().wrapping_add(1)), "Retry loading roster" }
        } else {
            p { role: "status", "Loading roster…" }
        }
    }
}
