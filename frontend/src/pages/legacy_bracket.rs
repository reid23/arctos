//! Standalone view of legacy image-overlay brackets.

use crate::api;
use crate::display::short_or_truncate;
use crate::types::BracketItem;
use crate::Route;
use dioxus::prelude::*;

/// Full-page viewer for legacy image brackets.
#[component]
pub fn LegacyBracket(url: String) -> Element {
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        async move { api::tournament_bracket(&u).await.map_err(|e| e.to_string()) }
    });
    let val = data.value();

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-12",
                    h1 { "{d.tournament.name} - Legacy Bracket" }
                    div { class: "d-flex flex-wrap gap-2 mb-3",
                        Link {
                            to: Route::TournamentHome { url: url.clone() },
                            class: "btn btn-outline-secondary btn-sm",
                            "Back to Tournament"
                        }
                        Link {
                            to: Route::Bracket { url: url.clone() },
                            class: "btn btn-outline-primary btn-sm",
                            "Back to Bracket"
                        }
                    }
                }
            }

            if d.legacy_brackets.is_empty() {
                p { class: "text-muted", "No legacy brackets are configured for this tournament." }
            } else {
                LegacyBracketDiagrams {
                    url: url.clone(),
                    brackets: d.legacy_brackets.clone(),
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { "Loading…" }
        }
    }
}

/// Render one or more legacy image brackets with team overlays.
#[component]
pub fn LegacyBracketDiagrams(url: String, brackets: Vec<BracketItem>) -> Element {
    let backend = api::base_url();
    rsx! {
        for (idx, bracket) in brackets.iter().enumerate() {
            div { class: "row mb-5", key: "{idx}-{bracket.name}",
                div { class: "col-12",
                    div { class: "card",
                        div { class: "card-header",
                            h3 { class: "mb-0",
                                {
                                    if bracket.name.is_empty() {
                                        "Bracket".to_string()
                                    } else {
                                        bracket.name.clone()
                                    }
                                }
                            }
                        }
                        div { class: "card-body",
                            div { class: "position-relative", style: "display: inline-block;",
                                img {
                                    src: "{backend}/static/{bracket.image}",
                                    alt: "{bracket.name}",
                                    class: "img-fluid",
                                    style: "max-width: none; height: none;"
                                }
                                for team_entry in bracket.teams.iter() {
                                    if let Some(team_info) = &team_entry.team_info {
                                        {
                                            let mut style_parts = vec!["position: absolute".to_string()];
                                            let mut transform_parts: Vec<String> = vec![];
                                            if team_entry.halign == "left" {
                                                style_parts.push(format!("left: {}px", team_entry.x));
                                            } else if team_entry.halign == "right" {
                                                style_parts.push(format!("left: {}px", team_entry.x));
                                                transform_parts.push("translateX(-100%)".to_string());
                                            } else {
                                                style_parts.push(format!("left: {}px", team_entry.x));
                                                transform_parts.push("translateX(-50%)".to_string());
                                            }
                                            if team_entry.valign == "top" {
                                                style_parts.push(format!("top: {}px", team_entry.y));
                                            } else if team_entry.valign == "bottom" {
                                                style_parts.push(format!("top: {}px", team_entry.y));
                                                transform_parts.push("translateY(-100%)".to_string());
                                            } else {
                                                style_parts.push(format!("top: {}px", team_entry.y));
                                                transform_parts.push("translateY(-50%)".to_string());
                                            }
                                            if !transform_parts.is_empty() {
                                                style_parts.push(format!("transform: {}", transform_parts.join(" ")));
                                            }
                                            style_parts.push(format!("font-size: {}px", team_entry.size));
                                            style_parts.push("line-height: 1.2".to_string());
                                            let style_str = style_parts.join("; ");
                                            let match_ref = team_entry.match_name.clone().unwrap_or_default();
                                            let bracket_label = short_or_truncate(
                                                team_info.pseudonym.as_deref().unwrap_or(&team_info.display_text),
                                                team_info.shortname.as_deref(),
                                            );
                                            let size = team_entry.size;
                                            rsx! {
                                                div { class: "bracket-team-overlay", style: "{style_str}",
                                                    if team_entry.is_tag {
                                                        span { "{team_info.display_text}" }
                                                    } else if let Some(team_id) = &team_info.id {
                                                        Link {
                                                            to: Route::TeamProfilePage { id: team_id.clone() },
                                                            class: "text-decoration-none text-dark d-inline-flex align-items-center",
                                                            if let Some(photo) = &team_info.profile_photo {
                                                                img {
                                                                    src: "{backend}/static/{photo}",
                                                                    alt: "{bracket_label}",
                                                                    class: "rounded-circle me-1",
                                                                    style: "width: {size}px; height: {size}px; object-fit: cover;"
                                                                }
                                                            }
                                                            span { "{bracket_label}" }
                                                        }
                                                    } else if team_entry.is_reference {
                                                        a {
                                                            href: "/{url}/match?name={match_ref}",
                                                            class: "text-decoration-none text-dark",
                                                            {team_info.display_text.replace("::", " ")}
                                                        }
                                                    } else {
                                                        span { "{team_info.display_text}" }
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
        style { r#"
        .bracket-team-overlay {{
            white-space: nowrap;
            z-index: 10;
        }}
        .bracket-team-overlay a {{
            display: inline-flex;
            align-items: center;
        }}
        .bracket-team-overlay img {{
            flex-shrink: 0;
        }}
        "# }
    }
}
