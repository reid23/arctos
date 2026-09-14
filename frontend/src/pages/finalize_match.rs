use crate::api;
use crate::Route;
use dioxus::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;
use wasm_bindgen::closure::Closure;
use wasm_bindgen::JsCast as _;
#[cfg(target_arch = "wasm32")]
use js_sys;

#[component]
pub fn FinalizeMatch(url: String, match_id: String) -> Element {
    let match_id_for_data = match_id.clone();
    let url_for_data = url.clone();
    let data = use_resource(move || {
        let u = url_for_data.clone();
        let id = match_id_for_data.clone();
        async move {
            api::finalize_match_data(&u, &id).await.map_err(|e| e.to_string())
        }
    });
    let val = data.value();
    let mut match_winner = use_signal(|| None::<String>);
    let mut final_notes = use_signal(String::new);
    let mut submit_error = use_signal(|| None::<String>);
    let team1_canvas_id = "team1_signature_canvas";
    let team2_canvas_id = "team2_signature_canvas";
    let navigator = use_navigator();
    let data_snapshot = val.read().clone();
    let schedule_url = url.clone();
    let home_url = url.clone();
    let submit_url = url.clone();
    let mut signature_setup_done = use_signal(|| false);

    use_effect(move || {
        let snapshot = val.read().clone();
        if snapshot.is_some() && !signature_setup_done() {
            signature_setup_done.set(true);
            setup_signature_canvas_by_id(team1_canvas_id);
            setup_signature_canvas_by_id(team2_canvas_id);
        }
    });

    match data_snapshot {
        Some(Ok(d)) => rsx! {
            div { class: "row",
                div { class: "col-12",
                    h1 { "Finalize Match" }
                    nav { aria_label: "breadcrumb",
                        ol { class: "breadcrumb",
                            li { class: "breadcrumb-item",
                                Link {
                                    to: Route::TournamentHome {
                                        url: home_url.clone(),
                                    },
                                    "{d.tournament.name}"
                                }
                            }
                            li { class: "breadcrumb-item",
                                Link {
                                    to: Route::Schedule {
                                        url: schedule_url.clone(),
                                        view: String::new(),
                                        team: String::new(),
                                        field: String::new(),
                                    },
                                    "Schedule"
                                }
                            }
                            li { class: "breadcrumb-item active", "Finalize Match" }
                        }
                    }
                }
            }

            div { class: "row",
                div { class: "col-md-8",
                    div { class: "card",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Points Summary" }
                        }
                        div { class: "card-body",
                            div { class: "table-responsive",
                                table { class: "table table-sm",
                                    thead {
                                        tr {
                                            th { "Set" }
                                            th { "Point" }
                                            th { "Winner" }
                                            th { "🪨" }
                                            th { "Rerun?" }
                                            th { "Notes" }
                                        }
                                    }
                                    tbody {
                                        for (idx , point) in d.points.iter().enumerate() {
                                            {
                                                let stones = d.stones_elapsed_map.get(&point.uuid).copied().unwrap_or(0);
                                                let notes = d.point_notes_map.get(&point.uuid).cloned().unwrap_or_default();
                                                rsx! {
                                                    tr { key: "{point.uuid}",
                                                        td { "{point.set_number.unwrap_or(1)}" }
                                                        td { "{idx + 1}" }
                                                        td {
                                                            if point.winner.as_deref() == Some("TEAM1") {
                                                                "{d.match_info.team1_name}"
                                                            } else if point.winner.as_deref() == Some("TEAM2") {
                                                                "{d.match_info.team2_name}"
                                                            } else {
                                                                "None"
                                                            }
                                                        }
                                                        td { "{stones}" }
                                                        td {
                                                            if point.rerolled {
                                                                span { class: "badge bg-warning", "Rerun" }
                                                            } else {
                                                                span { class: "badge bg-success", "Valid" }
                                                            }
                                                        }
                                                        td {
                                                            if notes.is_empty() {
                                                                span { class: "text-muted", "—" }
                                                            } else {
                                                                for note in notes.iter() {
                                                                    {
                                                                        let target_display = note
                                                                            .player_display
                                                                            .clone()
                                                                            .or(note.player_name.clone())
                                                                            .or(note.target.clone())
                                                                            .unwrap_or_else(|| "Match".to_string());
                                                                        rsx! {
                                                                            div { class: "small text-muted border-start border-3 ps-2 mb-1",
                                                                                "{target_display}: {note.text.clone().unwrap_or_default()}"
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
                        }
                    }

                    div { class: "card mt-3",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Score by Set" }
                        }
                        div { class: "card-body",
                            if d.points.is_empty() {
                                div { class: "text-muted text-center", "No points yet" }
                            } else {
                                {
                                    let mut set_scores: std::collections::HashMap<u32, (u32, u32)> = std::collections::HashMap::new();
                                    for p in d.points.iter() {
                                        let set_num = p.set_number.unwrap_or(1);
                                        let entry = set_scores.entry(set_num).or_insert((0, 0));
                                        if p.winner.as_deref() == Some("TEAM1") && !p.rerolled {
                                            entry.0 += 1;
                                        }
                                        if p.winner.as_deref() == Some("TEAM2") && !p.rerolled {
                                            entry.1 += 1;
                                        }
                                    }
                                    let mut sets: Vec<u32> = set_scores.keys().copied().collect();
                                    sets.sort();
                                    rsx! {
                                        div { class: "row mb-2",
                                            div { class: "col-5 text-center",
                                                small { class: "text-muted", "{d.match_info.team1_name}" }
                                            }
                                            div { class: "col-2" }
                                            div { class: "col-5 text-center",
                                                small { class: "text-muted", "{d.match_info.team2_name}" }
                                            }
                                        }
                                        for set_num in sets.iter() {
                                            {
                                                let (t1, t2) = set_scores.get(set_num).copied().unwrap_or((0, 0));
                                                rsx! {
                                                    div { class: "row mb-1",
                                                        div { class: "col-5 text-center",
                                                            strong { "{t1}" }
                                                        }
                                                        div { class: "col-2 text-center",
                                                            small { class: "text-muted", "Set {set_num}" }
                                                        }
                                                        div { class: "col-5 text-center",
                                                            strong { "{t2}" }
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

                    div { class: "card mt-4",
                        div { class: "card-header",
                            h5 { class: "mb-0", "Match Results" }
                        }
                        div { class: "card-body",
                            form {
                                onsubmit: move |ev| {
                                    ev.prevent_default();
                                    submit_error.set(None);
                                    let winner = match_winner().clone();
                                    if winner.is_none() {
                                        submit_error.set(Some(
                                            "Please select the match winner first.".to_string(),
                                        ));
                                        return;
                                    }
                                    if !both_canvases_signed(team1_canvas_id, team2_canvas_id) {
                                        submit_error.set(Some(
                                            "Both team captains must sign before submitting the match."
                                                .to_string(),
                                        ));
                                        return;
                                    }
                                    let winner = winner.unwrap();
                                    let notes = final_notes().clone();
                                    let u = submit_url.clone();
                                    let match_id = d.match_info.uuid.clone();
                                    let team1_sig = canvas_data_url(team1_canvas_id);
                                    let team2_sig = canvas_data_url(team2_canvas_id);
                                    let nav = navigator.clone();
                                    spawn(async move {
                                        let req = crate::types::FinalizeMatchRequest {
                                            match_id,
                                            match_winner: winner,
                                            final_notes: notes,
                                            team1_signature: team1_sig,
                                            team2_signature: team2_sig,
                                        };
                                        if api::finalize_match(&u, &req).await.is_ok() {
                                            let _ = nav.push(format!("/{}/schedule", u));
                                        }
                                    });
                                },
                                div { class: "mb-4",
                                    h6 { class: "form-label", "Match Winner" }
                                    div { class: "row",
                                        div { class: "col-md-6",
                                            div { class: "form-check form-check-inline",
                                                input {
                                                    class: "form-check-input",
                                                    r#type: "radio",
                                                    name: "match_winner",
                                                    id: "winner-team1",
                                                    value: "TEAM1",
                                                    onchange: move |_| match_winner.set(Some("TEAM1".to_string())),
                                                }
                                                label {
                                                    class: "form-check-label",
                                                    r#for: "winner-team1",
                                                    strong { "{d.match_info.team1_name}" }
                                                }
                                            }
                                        }
                                        div { class: "col-md-6",
                                            div { class: "form-check form-check-inline",
                                                input {
                                                    class: "form-check-input",
                                                    r#type: "radio",
                                                    name: "match_winner",
                                                    id: "winner-team2",
                                                    value: "TEAM2",
                                                    onchange: move |_| match_winner.set(Some("TEAM2".to_string())),
                                                }
                                                label {
                                                    class: "form-check-label",
                                                    r#for: "winner-team2",
                                                    strong { "{d.match_info.team2_name}" }
                                                }
                                            }
                                        }
                                    }
                                }

                                div { class: "row mb-4",
                                    div { class: "col-md-6",
                                        h6 { "{d.match_info.team1_name} Captain Signature" }
                                        div {
                                            class: "border p-3",
                                            style: "height: 150px;",
                                            canvas {
                                                id: "{team1_canvas_id}",
                                                style: "width: 100%; height: 100%;",
                                            }
                                        }
                                        div { class: "mt-2",
                                            button {
                                                r#type: "button",
                                                class: "btn btn-sm btn-outline-secondary",
                                                onclick: move |_| clear_canvas_by_id(team1_canvas_id),
                                                "Clear"
                                            }
                                        }
                                    }
                                    div { class: "col-md-6",
                                        h6 { "{d.match_info.team2_name} Captain Signature" }
                                        div {
                                            class: "border p-3",
                                            style: "height: 150px;",
                                            canvas {
                                                id: "{team2_canvas_id}",
                                                style: "width: 100%; height: 100%;",
                                            }
                                        }
                                        div { class: "mt-2",
                                            button {
                                                r#type: "button",
                                                class: "btn btn-sm btn-outline-secondary",
                                                onclick: move |_| clear_canvas_by_id(team2_canvas_id),
                                                "Clear"
                                            }
                                        }
                                    }
                                }

                                div { class: "mb-3",
                                    label {
                                        r#for: "final_notes",
                                        class: "form-label",
                                        "Final Notes"
                                    }
                                    textarea {
                                        class: "form-control",
                                        id: "final_notes",
                                        name: "final_notes",
                                        rows: "3",
                                        value: "{final_notes()}",
                                        oninput: move |ev| final_notes.set(ev.value().clone()),
                                    }
                                }

                                if let Some(ref err) = submit_error() {
                                    div { class: "alert alert-warning mb-3", "{err}" }
                                }
                                div { class: "d-grid",
                                    button {
                                        r#type: "submit",
                                        class: "btn btn-primary",
                                        "Finalize Match"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        Some(Err(e)) => rsx! {
            p { class: "text-danger", "{e}" }
        },
        None => rsx! {
            p { "Loading…" }
        },
    }
}

fn setup_signature_canvas_by_id(canvas_id: &str) {
    let window = match web_sys::window() {
        Some(w) => w,
        None => return,
    };
    let document = match window.document() {
        Some(d) => d,
        None => return,
    };
    let element = match document.get_element_by_id(canvas_id) {
        Some(el) => el,
        None => return,
    };
    let canvas = match element.dyn_into::<web_sys::HtmlCanvasElement>() {
        Ok(c) => c,
        Err(_) => return,
    };

    let rect = canvas.get_bounding_client_rect();
    canvas.set_width(rect.width() as u32);
    canvas.set_height(rect.height() as u32);
    let ctx = canvas
        .get_context("2d")
        .ok()
        .flatten()
        .and_then(|c| c.dyn_into::<web_sys::CanvasRenderingContext2d>().ok());
    if ctx.is_none() {
        return;
    }
    let ctx = ctx.unwrap();
    #[cfg(target_arch = "wasm32")]
    ctx.set_stroke_style(&js_sys::JsString::from("#000000").into());
    #[cfg(not(target_arch = "wasm32"))]
    let _ = ctx;
    ctx.set_line_width(2.0);
    ctx.set_line_cap("round");
    ctx.set_line_join("round");
    let drawing = Rc::new(RefCell::new(false));
    let drawing_move = drawing.clone();
    let ctx_move = ctx.clone();
    let canvas_move = canvas.clone();
    let on_mousedown = Closure::<dyn FnMut(_)>::new(move |e: web_sys::MouseEvent| {
        *drawing_move.borrow_mut() = true;
        let _ = canvas_move.set_attribute("data-signed", "true");
        let rect = canvas_move.get_bounding_client_rect();
        ctx_move.begin_path();
        ctx_move
            .move_to(e.client_x() as f64 - rect.left(), e.client_y() as f64 - rect.top());
    });
    canvas
        .add_event_listener_with_callback("mousedown", on_mousedown.as_ref().unchecked_ref())
        .ok();
    on_mousedown.forget();

    let drawing_move = drawing.clone();
    let ctx_move = ctx.clone();
    let canvas_move = canvas.clone();
    let on_mousemove = Closure::<dyn FnMut(_)>::new(move |e: web_sys::MouseEvent| {
        if !*drawing_move.borrow() {
            return;
        }
        let rect = canvas_move.get_bounding_client_rect();
        ctx_move.line_to(
            e.client_x() as f64 - rect.left(),
            e.client_y() as f64 - rect.top(),
        );
        ctx_move.stroke();
    });
    canvas
        .add_event_listener_with_callback("mousemove", on_mousemove.as_ref().unchecked_ref())
        .ok();
    on_mousemove.forget();

    let drawing_move = drawing.clone();
    let on_mouseup = Closure::<dyn FnMut(_)>::new(move |_e: web_sys::MouseEvent| {
        *drawing_move.borrow_mut() = false;
    });
    canvas
        .add_event_listener_with_callback("mouseup", on_mouseup.as_ref().unchecked_ref())
        .ok();
    canvas
        .add_event_listener_with_callback("mouseleave", on_mouseup.as_ref().unchecked_ref())
        .ok();
    on_mouseup.forget();

    let drawing_move = drawing.clone();
    let ctx_move = ctx.clone();
    let canvas_move = canvas.clone();
    let on_touchstart = Closure::<dyn FnMut(_)>::new(move |e: web_sys::TouchEvent| {
        e.prevent_default();
        if let Some(touch) = e.touches().get(0) {
            *drawing_move.borrow_mut() = true;
            let _ = canvas_move.set_attribute("data-signed", "true");
            let rect = canvas_move.get_bounding_client_rect();
            ctx_move.begin_path();
            ctx_move.move_to(
                touch.client_x() as f64 - rect.left(),
                touch.client_y() as f64 - rect.top(),
            );
        }
    });
    canvas
        .add_event_listener_with_callback("touchstart", on_touchstart.as_ref().unchecked_ref())
        .ok();
    on_touchstart.forget();

    let drawing_move = drawing.clone();
    let ctx_move = ctx.clone();
    let canvas_move = canvas.clone();
    let on_touchmove = Closure::<dyn FnMut(_)>::new(move |e: web_sys::TouchEvent| {
        e.prevent_default();
        if !*drawing_move.borrow() {
            return;
        }
        if let Some(touch) = e.touches().get(0) {
            let rect = canvas_move.get_bounding_client_rect();
            ctx_move.line_to(
                touch.client_x() as f64 - rect.left(),
                touch.client_y() as f64 - rect.top(),
            );
            ctx_move.stroke();
        }
    });
    canvas
        .add_event_listener_with_callback("touchmove", on_touchmove.as_ref().unchecked_ref())
        .ok();
    on_touchmove.forget();

    let drawing_move = drawing.clone();
    let on_touchend = Closure::<dyn FnMut(_)>::new(move |e: web_sys::TouchEvent| {
        e.prevent_default();
        *drawing_move.borrow_mut() = false;
    });
    canvas
        .add_event_listener_with_callback("touchend", on_touchend.as_ref().unchecked_ref())
        .ok();
    canvas
        .add_event_listener_with_callback("touchcancel", on_touchend.as_ref().unchecked_ref())
        .ok();
    on_touchend.forget();
}

fn clear_canvas_by_id(canvas_id: &str) {
    let window = match web_sys::window() {
        Some(w) => w,
        None => return,
    };
    let document = match window.document() {
        Some(d) => d,
        None => return,
    };
    let element = match document.get_element_by_id(canvas_id) {
        Some(el) => el,
        None => return,
    };
    let canvas = match element.dyn_into::<web_sys::HtmlCanvasElement>() {
        Ok(c) => c,
        Err(_) => return,
    };
    if let Ok(Some(ctx)) = canvas.get_context("2d") {
        if let Ok(ctx) = ctx.dyn_into::<web_sys::CanvasRenderingContext2d>() {
            ctx.clear_rect(0.0, 0.0, canvas.width() as f64, canvas.height() as f64);
        }
    }
    if let Some(el) = document.get_element_by_id(canvas_id) {
        let _ = el.remove_attribute("data-signed");
    }
}

fn canvas_data_url(canvas_id: &str) -> Option<String> {
    let window = web_sys::window()?;
    let document = window.document()?;
    let element = document.get_element_by_id(canvas_id)?;
    let canvas: web_sys::HtmlCanvasElement = element.dyn_into().ok()?;
    canvas.to_data_url().ok()
}

fn both_canvases_signed(team1_id: &str, team2_id: &str) -> bool {
    let doc = match web_sys::window().and_then(|w| w.document()) {
        Some(d) => d,
        None => return false,
    };
    let signed = |id: &str| -> bool {
        doc.get_element_by_id(id)
            .and_then(|el| el.dyn_into::<web_sys::HtmlElement>().ok())
            .and_then(|el| el.get_attribute("data-signed"))
            .as_deref()
            == Some("true")
    };
    signed(team1_id) && signed(team2_id)
}
