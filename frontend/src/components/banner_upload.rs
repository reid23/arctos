use crate::api;
use super::image_crop::crop_rect;
#[cfg(target_arch = "wasm32")]
use super::image_crop::focus_crop;
use dioxus::prelude::*;
use std::{cell::RefCell, rc::Rc};

#[derive(Clone, Copy, PartialEq)]
pub enum BannerAccountKind { Tournament, League }

#[derive(Clone, PartialEq)]
struct SelectedImage { url: String, width: f64, height: f64, mime: String }

type OwnedUrl = Rc<RefCell<Option<String>>>;

fn clear_url(owner: &OwnedUrl) {
    if let Some(url) = owner.borrow_mut().take() {
        #[cfg(target_arch = "wasm32")]
        let _ = web_sys::Url::revoke_object_url(&url);
    }
}

#[cfg(target_arch = "wasm32")]
fn clear_file_input(id: &str) {
    use wasm_bindgen::JsCast;
    if let Some(input) = web_sys::window().and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id(id)).and_then(|el| el.dyn_into::<web_sys::HtmlInputElement>().ok()) {
        input.set_value("");
    }
}

fn pointer_capture(event: &Event<PointerData>, capture: bool) {
    #[cfg(target_arch = "wasm32")]
    {
        use dioxus_web::WebEventExt;
        use wasm_bindgen::JsCast;
        if let Some(target) = event.data().try_as_web_event().and_then(|e| e.target())
            .and_then(|target| target.dyn_into::<web_sys::Element>().ok()) {
            if capture { let _ = target.set_pointer_capture(event.pointer_id()); }
            else { let _ = target.release_pointer_capture(event.pointer_id()); }
        }
    }
}

fn focus_after_render(id: &str) {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        let id = id.to_string();
        let callback = wasm_bindgen::closure::Closure::once_into_js(move || crate::appearance::focus_control(&id));
        if let Some(window) = web_sys::window() {
            let _ = window.set_timeout_with_callback_and_timeout_and_arguments_0(callback.unchecked_ref(), 0);
        }
    }
}

async fn upload(kind: BannerAccountKind, id: &str, bytes: bytes::Bytes) -> Result<String, String> {
    match kind {
        BannerAccountKind::Tournament => api::upload_tournament_banner_photo(id, bytes).await,
        BannerAccountKind::League => api::upload_league_banner_photo(id, bytes).await,
    }
}

async fn remove(kind: BannerAccountKind, id: &str) -> Result<(), String> {
    match kind {
        BannerAccountKind::Tournament => api::delete_tournament_banner_photo(id).await,
        BannerAccountKind::League => api::delete_league_banner_photo(id).await,
    }
}

/// Event and league banners save independently of their settings forms.
/// Profile images instead use the atomic profile editor's local image drafts.
#[component]
pub fn BannerUpload(kind: BannerAccountKind, account_id: String, mut photo: Signal<Option<String>>, mut busy: Signal<bool>) -> Element {
    let mut working = use_signal(|| false);
    let mut selection = use_signal(|| None::<SelectedImage>);
    let mut error = use_signal(|| None::<String>);
    let mut status = use_signal(String::new);
    let mut zoom = use_signal(|| 1.0_f64);
    let mut horizontal = use_signal(|| 50.0_f64);
    let mut vertical = use_signal(|| 50.0_f64);
    let mut drag = use_signal(|| None::<(i32, f64, f64, f64, f64, bool)>);
    let urls: OwnedUrl = use_hook(|| Rc::new(RefCell::new(None)));
    let dropped_urls = urls.clone();
    use_drop(move || clear_url(&dropped_urls));
    let input_urls = urls.clone();
    let cancel_urls = urls.clone();
    let escape_urls = urls.clone();
    let save_urls = urls.clone();
    let upload_id = account_id.clone();
    let input_id = "profile-banner-file";
    let remove_id = "profile-banner-remove";
    let crop_id = "image-crop-banner";
    let aspect = 3.0;
    let preview_style = selection().map(|image| {
        let crop = crop_rect(image.width, image.height, aspect, zoom(), horizontal(), vertical());
        format!("width:{}%;height:{}%;left:{}%;top:{}%;", image.width / crop.width * 100.0,
            image.height / crop.height * 100.0, -crop.x / crop.width * 100.0, -crop.y / crop.height * 100.0)
    }).unwrap_or_default();
    rsx! {
        style { {include_str!("image_upload.css")} }
        section { class: "image-upload", aria_label: "Banner image",
            onkeydown: move |event| {
                if event.key() == Key::Escape && selection().is_some() && !working() {
                    event.prevent_default(); event.stop_propagation();
                    clear_url(&escape_urls); selection.set(None); drag.set(None); error.set(None); busy.set(false);
                    focus_after_render(input_id);
                }
            },
            label { class: "form-label", r#for: input_id, "Banner image" }
            p { class: "form-text", "JPEG, PNG, or WebP, up to 8 MB. Choose a wide crop for your banner." }
            p { class: "form-text", "Banner changes save separately from settings. Save banner and Remove banner take effect immediately." }
            if let Some(path) = photo() {
                img { class: "banner-upload-preview", src: "{api::base_url()}/static/{path}", alt: "Current banner" }
            }
            if let Some(message) = error() { p { class: "text-danger", role: "alert", "{message}" } }
            div { class: "image-upload-actions",
                input { id: input_id, class: "form-control", r#type: "file", accept: "image/jpeg,image/png,image/webp", disabled: busy(),
                    onchange: move |event| {
                        if busy() { return; }
                        #[cfg(target_arch = "wasm32")]
                        {
                            if let Some(file) = event.files().into_iter().next() {
                                clear_file_input(input_id); drag.set(None); error.set(None); status.set(String::new());
                                let content_type = file.content_type().unwrap_or_default();
                                let extension = file.name().rsplit('.').next().unwrap_or("").to_lowercase();
                                if !matches!(content_type.as_str(), "image/jpeg" | "image/png" | "image/webp")
                                    && !(content_type.is_empty() && matches!(extension.as_str(), "jpg" | "jpeg" | "png" | "webp")) {
                                    error.set(Some("Choose a JPEG, PNG, or WebP image.".into())); return;
                                }
                                if file.size() == 0 || file.size() > 8 * 1024 * 1024 {
                                    error.set(Some("Choose a non-empty image no larger than 8 MB.".into())); return;
                                }
                                busy.set(true); working.set(true);
                                let owned = input_urls.clone();
                                spawn(async move {
                                    let result: Result<SelectedImage, String> = async {
                                        let bytes = file.read_bytes().await.map_err(|_| "Could not read this image. Try again.")?;
                                        let mime = super::image_crop::validate_source(&bytes)?;
                                        let parts = js_sys::Array::new();
                                        parts.push(&js_sys::Uint8Array::from(bytes.as_ref()));
                                        let blob = web_sys::Blob::new_with_u8_array_sequence(&parts).map_err(|_| "Could not open this image.")?;
                                        let url = web_sys::Url::create_object_url_with_blob(&blob).map_err(|_| "Could not open this image.")?;
                                        clear_url(&owned); *owned.borrow_mut() = Some(url.clone());
                                        let image = super::image_crop::load_image(&url).await?;
                                        Ok(SelectedImage { url, width: image.natural_width() as f64, height: image.natural_height() as f64, mime: mime.into() })
                                    }.await;
                                    working.set(false);
                                    match result {
                                        Ok(image) => { zoom.set(1.0); horizontal.set(50.0); vertical.set(50.0); selection.set(Some(image)); focus_after_render(&format!("{crop_id}-zoom")); }
                                        Err(message) => { clear_url(&owned); error.set(Some(message)); busy.set(false); }
                                    }
                                });
                            }
                        }
                    },
                }
                if photo().is_some() {
                    button { id: remove_id, r#type: "button", class: "btn btn-outline-secondary", disabled: busy(),
                        onclick: move |_| {
                            if busy() { return; }
                            busy.set(true); working.set(true); error.set(None); status.set(String::new());
                            let id = account_id.clone();
                            spawn(async move {
                                match remove(kind, &id).await {
                                    Ok(()) => { photo.set(None); status.set("Banner removed.".into()); }
                                    Err(message) => error.set(Some(message)),
                                }
                                working.set(false); busy.set(false);
                            });
                        },
                        "Remove banner"
                    }
                }
            }
            if let Some(image) = selection() {
                div { class: "image-crop-editor",
                    p { class: "form-text", id: "{crop_id}-help", "Drag or tap to position."
                        span { class: "visually-hidden", " You can also focus the image and use arrow keys. Reset position centers the image without changing zoom." }
                    }
                    div { class: "image-crop-frame",
                    div { id: crop_id, class: "image-crop-viewport",
                        style: "aspect-ratio:{aspect};", tabindex: "0", role: "group", aria_label: "Crop preview", aria_describedby: "{crop_id}-help",
                        onkeydown: move |event| {
                            if working() { return; }
                            let delta = match event.key() {
                                Key::ArrowLeft => Some((5.0, 0.0)), Key::ArrowRight => Some((-5.0, 0.0)),
                                Key::ArrowUp => Some((0.0, 5.0)), Key::ArrowDown => Some((0.0, -5.0)), _ => None,
                            };
                            if let Some((x, y)) = delta {
                                event.prevent_default(); horizontal.set((horizontal() + x).clamp(0.0, 100.0)); vertical.set((vertical() + y).clamp(0.0, 100.0));
                            }
                        },
                        onpointerdown: move |event: Event<PointerData>| {
                            if working() || drag().is_some() { return; }
                            if event.trigger_button() != Some(dioxus::html::input_data::MouseButton::Primary) { return; }
                            event.prevent_default(); pointer_capture(&event, true);
                            crate::appearance::focus_control(crop_id);
                            drag.set(Some((event.pointer_id(), event.client_coordinates().x, event.client_coordinates().y, horizontal(), vertical(), false)));
                        },
                        onpointermove: move |event: Event<PointerData>| {
                            if working() { return; }
                            if let Some((pointer, start_x, start_y, initial_x, initial_y, moved)) = drag() {
                                if pointer != event.pointer_id() { return; }
                                if !moved {
                                    if (event.client_coordinates().x - start_x).hypot(event.client_coordinates().y - start_y) < 4.0 { return; }
                                    drag.set(Some((pointer, start_x, start_y, initial_x, initial_y, true)));
                                }
                                #[cfg(target_arch = "wasm32")]
                                if let (Some(image), Some(element)) = (selection(), web_sys::window().and_then(|w| w.document()).and_then(|d| d.get_element_by_id(crop_id))) {
                                    let bounds = element.get_bounding_client_rect();
                                    let crop = crop_rect(image.width, image.height, aspect, zoom(), initial_x, initial_y);
                                    if image.width > crop.width && bounds.width() > 0.0 {
                                        horizontal.set((initial_x - (event.client_coordinates().x - start_x) / bounds.width() * crop.width / (image.width - crop.width) * 100.0).clamp(0.0, 100.0));
                                    }
                                    if image.height > crop.height && bounds.height() > 0.0 {
                                        vertical.set((initial_y - (event.client_coordinates().y - start_y) / bounds.height() * crop.height / (image.height - crop.height) * 100.0).clamp(0.0, 100.0));
                                    }
                                }
                            }
                        },
                        onpointerup: move |event: Event<PointerData>| {
                            if let Some((pointer, _, _, _, _, moved)) = drag() {
                                if pointer != event.pointer_id() { return; }
                                if !moved && !working() {
                                    #[cfg(target_arch = "wasm32")]
                                    if let (Some(image), Some(element)) = (selection(), web_sys::window().and_then(|w| w.document()).and_then(|d| d.get_element_by_id(crop_id))) {
                                        let bounds = element.get_bounding_client_rect();
                                        if bounds.width() > 0.0 && bounds.height() > 0.0 {
                                            let crop = crop_rect(image.width, image.height, aspect, zoom(), horizontal(), vertical());
                                            let (x, y) = focus_crop(image.width, image.height, crop,
                                                (event.client_coordinates().x - bounds.left()) / bounds.width(),
                                                (event.client_coordinates().y - bounds.top()) / bounds.height());
                                            horizontal.set(x); vertical.set(y);
                                        }
                                    }
                                }
                                drag.set(None); pointer_capture(&event, false);
                            }
                        },
                        onpointercancel: move |event: Event<PointerData>| { drag.set(None); pointer_capture(&event, false); },
                        onlostpointercapture: move |_| drag.set(None),
                        img { src: "{image.url}", style: "{preview_style}", alt: "", draggable: "false" }
                    }
                    button { class: "image-position-reset", r#type: "button", disabled: working(),
                        aria_label: "Reset position", title: "Reset position",
                        onclick: move |_| {
                            // Reset pan only: keep the user's chosen zoom level.
                            horizontal.set(50.0); vertical.set(50.0);
                        },
                        i { class: "fas fa-rotate-left", aria_hidden: "true" }
                    }
                    }
                    label { class: "form-label", r#for: "{crop_id}-zoom", "Zoom" }
                    input { id: "{crop_id}-zoom", class: "form-range", r#type: "range", min: "1", max: "3", step: "0.01", value: "{zoom}", disabled: working(),
                        oninput: move |event| zoom.set(event.value().parse::<f64>().unwrap_or(1.0)) }
                    div { class: "image-crop-actions",
                        button { r#type: "button", class: "btn btn-outline-secondary", disabled: working(),
                            onclick: move |_| { clear_url(&cancel_urls); selection.set(None); drag.set(None); error.set(None); busy.set(false); focus_after_render(input_id); }, "Cancel" }
                        button { r#type: "button", class: "btn btn-primary", disabled: working(),
                            onclick: move |_| {
                                if working() { return; }
                                #[cfg(target_arch = "wasm32")]
                                if let Some(image) = selection() {
                                    working.set(true); error.set(None);
                                    let id = upload_id.clone(); let owned = save_urls.clone();
                                    let crop = crop_rect(image.width, image.height, aspect, zoom(), horizontal(), vertical());
                                    spawn(async move {
                                        let result = match super::image_crop::encode_crop(&image.url, crop, false, &image.mime).await {
                                            Ok(bytes) => upload(kind, &id, bytes).await,
                                            Err(message) => Err(message),
                                        };
                                        working.set(false);
                                        match result {
                                            Ok(path) => {
                                                photo.set(Some(path)); selection.set(None); drag.set(None); clear_url(&owned); busy.set(false);
                                                status.set("Banner updated.".into());
                                                focus_after_render(input_id);
                                            }
                                            Err(message) => error.set(Some(message)),
                                        }
                                    });
                                }
                            },
                            if working() { "Saving…" } else { "Save banner" }
                        }
                    }
                }
            }
            if working() { p { class: "form-text", role: "status", "Preparing image…" } }
            else if !status().is_empty() { p { class: "form-text", role: "status", "{status()}" } }
        }
    }
}
