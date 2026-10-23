//! Local image drafts shared by the profile preview and its final Save action.

use super::image_crop::crop_rect;
#[cfg(target_arch = "wasm32")]
use super::image_crop::focus_crop;
use dioxus::prelude::*;
use std::rc::Rc;

#[derive(Debug, PartialEq)]
struct ObjectUrl(String);

impl Drop for ObjectUrl {
    fn drop(&mut self) {
        #[cfg(target_arch = "wasm32")]
        let _ = web_sys::Url::revoke_object_url(&self.0);
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct CropImage {
    pub url: String,
    pub width: f64,
    pub height: f64,
    pub mime: String,
    pub zoom: f64,
    pub x: f64,
    pub y: f64,
    // Cloning a draft keeps its URL alive while Save asynchronously encodes it.
    owner: Rc<ObjectUrl>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ImageDraft {
    pub existing: Option<String>,
    pub selected: Option<CropImage>,
    pub removed: bool,
}

impl ImageDraft {
    pub fn new(existing: Option<String>) -> Self {
        Self {
            existing,
            ..Self::default()
        }
    }

    pub fn src(&self) -> Option<String> {
        if self.removed {
            return None;
        }
        self.selected
            .as_ref()
            .map(|image| image.url.clone())
            .or_else(|| {
                self.existing
                    .as_ref()
                    .map(|path| format!("{}/static/{path}", crate::api::base_url()))
            })
    }

    pub fn style(&self, avatar: bool) -> String {
        self.selected
            .as_ref()
            .map(|image| {
                let crop = crop_rect(
                    image.width,
                    image.height,
                    if avatar { 1.0 } else { 3.0 },
                    image.zoom,
                    image.x,
                    image.y,
                );
                format!(
                    "width:{}%;height:{}%;left:{}%;top:{}%;",
                    image.width / crop.width * 100.0,
                    image.height / crop.height * 100.0,
                    -crop.x / crop.width * 100.0,
                    -crop.y / crop.height * 100.0
                )
            })
            .unwrap_or_else(|| "width:100%;height:100%;left:0;top:0;object-fit:cover;".into())
    }

    pub fn changed(&self) -> bool {
        self.selected.is_some() || self.is_removed()
    }

    pub fn is_removed(&self) -> bool {
        self.removed && self.existing.is_some()
    }

    /// Return staged image bytes only. The parent decides when and how to save them.
    pub async fn encode(&self, avatar: bool) -> Result<Option<bytes::Bytes>, String> {
        if self.removed {
            return Ok(None);
        }
        let Some(image) = self.selected.as_ref() else {
            return Ok(None);
        };
        #[cfg(target_arch = "wasm32")]
        {
            let crop = crop_rect(
                image.width,
                image.height,
                if avatar { 1.0 } else { 3.0 },
                image.zoom,
                image.x,
                image.y,
            );
            super::image_crop::encode_crop(&image.url, crop, avatar, &image.mime)
                .await
                .map(Some)
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            let _ = (image, avatar);
            Err("Image editing is available in the browser.".into())
        }
    }

    fn remove(&mut self) {
        self.selected = None;
        self.removed = self.existing.is_some();
    }
}

#[cfg(target_arch = "wasm32")]
async fn prepare_image(bytes: &[u8]) -> Result<CropImage, String> {
    let mime = super::image_crop::validate_source(bytes)?;
    let parts = js_sys::Array::new();
    parts.push(&js_sys::Uint8Array::from(bytes));
    let blob = web_sys::Blob::new_with_u8_array_sequence(&parts)
        .map_err(|_| "Could not open this image.")?;
    let url = web_sys::Url::create_object_url_with_blob(&blob)
        .map_err(|_| "Could not open this image.")?;
    let owner = Rc::new(ObjectUrl(url.clone()));
    let image = super::image_crop::load_image(&url).await?;
    Ok(CropImage {
        url,
        width: image.natural_width() as f64,
        height: image.natural_height() as f64,
        mime: mime.into(),
        zoom: 1.0,
        x: 50.0,
        y: 50.0,
        owner,
    })
}

#[cfg(target_arch = "wasm32")]
async fn prepare_existing(url: &str) -> Result<CropImage, String> {
    let response = reqwest::get(url)
        .await
        .map_err(|_| "Could not load this image. Try replacing it.")?;
    if !response.status().is_success() {
        return Err("Could not load this image. Try replacing it.".into());
    }
    if response
        .content_length()
        .is_some_and(|size| size > 8 * 1024 * 1024)
    {
        return Err("This image is too large to adjust. Choose a replacement up to 8 MB.".into());
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|_| "Could not read this image. Try replacing it.")?;
    prepare_image(&bytes).await
}

fn focus_after_render(id: &str) {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        let id = id.to_string();
        let callback = wasm_bindgen::closure::Closure::once_into_js(move || {
            crate::appearance::focus_control(&id)
        });
        if let Some(window) = web_sys::window() {
            let _ = window
                .set_timeout_with_callback_and_timeout_and_arguments_0(callback.unchecked_ref(), 0);
        }
    }
}

fn click_file(id: &str) {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        if let Some(input) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id(id))
            .and_then(|el| el.dyn_into::<web_sys::HtmlInputElement>().ok())
        {
            input.set_value("");
            input.click();
        }
    }
}

fn capture_pointer(id: &str, pointer: i32, capture: bool) {
    #[cfg(target_arch = "wasm32")]
    if let Some(element) = web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id(id))
    {
        if capture {
            let _ = element.set_pointer_capture(pointer);
        } else {
            let _ = element.release_pointer_capture(pointer);
        }
    }
}

fn nudge(mut draft: Signal<ImageDraft>, x: f64, y: f64) {
    if let Some(image) = draft.write().selected.as_mut() {
        image.x = (image.x + x).clamp(0.0, 100.0);
        image.y = (image.y + y).clamp(0.0, 100.0);
    }
}

#[component]
pub fn ProfileImageEditor(
    mut draft: Signal<ImageDraft>,
    avatar: bool,
    mut active: Signal<Option<bool>>,
    mut busy: Signal<bool>,
    #[props(default)] disabled: bool,
) -> Element {
    let mut menu = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut loading = use_signal(|| false);
    let mut drag = use_signal(|| None::<(i32, f64, f64, f64, f64, bool)>);
    let noun = if avatar { "photo" } else { "banner" };
    let label = if avatar {
        "Profile photo"
    } else {
        "Banner image"
    };
    let frame_id = if avatar {
        "image-crop-avatar"
    } else {
        "image-crop-banner"
    };
    let file_id = if avatar {
        "profile-photo-file"
    } else {
        "profile-banner-file"
    };
    let trigger_id = if avatar {
        "profile-photo-options-trigger"
    } else {
        "profile-banner-options-trigger"
    };
    let menu_id = if avatar {
        "profile-photo-options"
    } else {
        "profile-banner-options"
    };
    let remove_id = if avatar {
        "profile-photo-remove"
    } else {
        "profile-banner-remove"
    };
    let adjusting = active() == Some(avatar) && draft.read().selected.is_some();
    let locked = disabled || busy();
    let editor_class = format!(
        "profile-image-editor {}{}",
        if avatar { "is-avatar" } else { "is-banner" },
        if menu() { " has-open-menu" } else { "" }
    );
    use_drop(move || {
        if loading() {
            busy.set(false);
        }
    });
    rsx! {
        style { {include_str!("profile_image_editor.css")} }
        div {
            class: editor_class,
            onkeydown: move |event| {
                if event.key() == Key::Escape && menu() {
                    event.prevent_default(); event.stop_propagation(); menu.set(false);
                    crate::appearance::focus_control(trigger_id);
                }
            },
            div {
                id: frame_id, class: if adjusting { "profile-image-viewport is-adjusting" } else { "profile-image-viewport" },
                tabindex: if draft.read().selected.is_some() && !locked { "0" } else { "-1" },
                role: "group", aria_label: "{label} preview", aria_describedby: adjusting.then_some("profile-image-position-help"),
                onfocus: move |_| { if draft.read().selected.is_some() && !locked { active.set(Some(avatar)); } },
                onkeydown: move |event| {
                    if locked || draft.read().selected.is_none() { return; }
                    let delta = match event.key() {
                        Key::ArrowLeft => Some((5.0, 0.0)), Key::ArrowRight => Some((-5.0, 0.0)),
                        Key::ArrowUp => Some((0.0, 5.0)), Key::ArrowDown => Some((0.0, -5.0)), _ => None,
                    };
                    if let Some((x, y)) = delta { event.prevent_default(); nudge(draft, x, y); }
                },
                onpointerdown: move |event: Event<PointerData>| {
                    if locked || !adjusting || drag().is_some() { return; }
                    if event.trigger_button() != Some(dioxus::html::input_data::MouseButton::Primary) { return; }
                    event.prevent_default(); active.set(Some(avatar));
                    crate::appearance::focus_control(frame_id);
                    capture_pointer(frame_id, event.pointer_id(), true);
                    if let Some(image) = draft.read().selected.as_ref() {
                        drag.set(Some((event.pointer_id(), event.client_coordinates().x, event.client_coordinates().y, image.x, image.y, false)));
                    }
                },
                onpointermove: move |event: Event<PointerData>| {
                    if locked { return; }
                    if let Some((pointer, start_x, start_y, initial_x, initial_y, moved)) = drag() {
                        if pointer != event.pointer_id() { return; }
                        if !moved {
                            if (event.client_coordinates().x - start_x).hypot(event.client_coordinates().y - start_y) < 4.0 { return; }
                            drag.set(Some((pointer, start_x, start_y, initial_x, initial_y, true)));
                        }
                        #[cfg(target_arch = "wasm32")]
                        if let Some(element) = web_sys::window().and_then(|w| w.document()).and_then(|d| d.get_element_by_id(frame_id)) {
                            let bounds = element.get_bounding_client_rect();
                            if let Some(image) = draft.write().selected.as_mut() {
                                let crop = crop_rect(image.width, image.height, if avatar { 1.0 } else { 3.0 }, image.zoom, initial_x, initial_y);
                                if image.width > crop.width && bounds.width() > 0.0 {
                                    image.x = (initial_x - (event.client_coordinates().x - start_x) / bounds.width() * crop.width / (image.width - crop.width) * 100.0).clamp(0.0, 100.0);
                                }
                                if image.height > crop.height && bounds.height() > 0.0 {
                                    image.y = (initial_y - (event.client_coordinates().y - start_y) / bounds.height() * crop.height / (image.height - crop.height) * 100.0).clamp(0.0, 100.0);
                                }
                            }
                        }
                    }
                },
                onpointerup: move |event: Event<PointerData>| {
                    if let Some((pointer, _, _, _, _, moved)) = drag() {
                        if pointer != event.pointer_id() { return; }
                        if !moved && !locked {
                            #[cfg(target_arch = "wasm32")]
                            if let Some(element) = web_sys::window().and_then(|w| w.document()).and_then(|d| d.get_element_by_id(frame_id)) {
                                let bounds = element.get_bounding_client_rect();
                                if bounds.width() > 0.0 && bounds.height() > 0.0 {
                                    if let Some(image) = draft.write().selected.as_mut() {
                                        let crop = crop_rect(image.width, image.height, if avatar { 1.0 } else { 3.0 }, image.zoom, image.x, image.y);
                                        (image.x, image.y) = focus_crop(image.width, image.height, crop,
                                            (event.client_coordinates().x - bounds.left()) / bounds.width(),
                                            (event.client_coordinates().y - bounds.top()) / bounds.height());
                                    }
                                }
                            }
                        }
                        drag.set(None); capture_pointer(frame_id, event.pointer_id(), false);
                    }
                },
                onpointercancel: move |event: Event<PointerData>| { drag.set(None); capture_pointer(frame_id, event.pointer_id(), false); },
                onlostpointercapture: move |_| drag.set(None),
                if let Some(src) = draft.read().src() {
                    img { src, style: draft.read().style(avatar), alt: "", draggable: "false" }
                } else if avatar {
                    span { class: "profile-image-placeholder", aria_hidden: "true", i { class: "fas fa-user" } }
                }
            }
            if adjusting {
                button {
                    class: "profile-image-reset", r#type: "button", disabled: locked,
                    aria_label: "Reset position", title: "Reset position",
                    onclick: move |_| {
                        // Reset pan only: keep the user's chosen zoom level.
                        if let Some(image) = draft.write().selected.as_mut() { image.x = 50.0; image.y = 50.0; }
                    },
                    i { class: "fas fa-rotate-left", aria_hidden: "true" }
                }
            }
            input {
                id: file_id, class: "visually-hidden", tabindex: "-1", r#type: "file",
                aria_label: "Choose {noun}", accept: "image/jpeg,image/png,image/webp", disabled: locked,
                onchange: move |event| {
                    if disabled || busy() { return; }
                    #[cfg(target_arch = "wasm32")]
                    {
                        if let Some(file) = event.files().into_iter().next() {
                            error.set(None); menu.set(false);
                            let mime = file.content_type().unwrap_or_default();
                            let extension = file.name().rsplit('.').next().unwrap_or("").to_lowercase();
                            if !matches!(mime.as_str(), "image/jpeg" | "image/png" | "image/webp")
                                && !(mime.is_empty() && matches!(extension.as_str(), "jpg" | "jpeg" | "png" | "webp")) {
                                error.set(Some("Choose a JPEG, PNG, or WebP image.".into())); return;
                            }
                            if file.size() == 0 || file.size() > 8 * 1024 * 1024 {
                                error.set(Some("Choose a non-empty image no larger than 8 MB.".into())); return;
                            }
                            busy.set(true); loading.set(true);
                            spawn(async move {
                                let result = match file.read_bytes().await {
                                    Ok(bytes) => prepare_image(&bytes).await,
                                    Err(_) => Err("Could not read this image. Try again.".into()),
                                };
                                busy.set(false); loading.set(false);
                                match result {
                                    Ok(image) => {
                                        let mut state = draft.write(); state.selected = Some(image); state.removed = false;
                                        drop(state); active.set(Some(avatar)); focus_after_render(frame_id);
                                    }
                                    Err(message) => error.set(Some(message)),
                                }
                            });
                        }
                    }
                },
            }
            button {
                id: trigger_id, class: "profile-image-camera", r#type: "button", disabled: locked,
                aria_label: "Edit {noun} image", aria_expanded: "{menu}", aria_controls: menu_id,
                onclick: move |_| menu.toggle(),
                i { class: "fas fa-camera", aria_hidden: "true" }
            }
            if menu() {
                button { class: "profile-image-menu-dismiss", r#type: "button", tabindex: "-1", aria_label: "Dismiss image menu",
                    onclick: move |_| { menu.set(false); crate::appearance::focus_control(trigger_id); } }
                div { id: menu_id, class: "profile-image-options",
                    button { r#type: "button", class: "dropdown-item", disabled: locked,
                        onclick: move |_| { menu.set(false); click_file(file_id); },
                        if draft.read().src().is_some() { "Replace image" } else { "Choose image" }
                    }
                    if draft.read().src().is_some() {
                        button { r#type: "button", class: "dropdown-item", disabled: locked,
                            onclick: move |_| {
                                if disabled || busy() { return; }
                                menu.set(false); error.set(None);
                                if draft.read().selected.is_some() { active.set(Some(avatar)); focus_after_render(frame_id); return; }
                                #[cfg(target_arch = "wasm32")]
                                if let Some(src) = draft.read().src() {
                                    busy.set(true); loading.set(true);
                                    spawn(async move {
                                        let result = prepare_existing(&src).await;
                                        busy.set(false); loading.set(false);
                                        match result {
                                            Ok(image) => { draft.write().selected = Some(image); active.set(Some(avatar)); focus_after_render(frame_id); }
                                            Err(message) => error.set(Some(message)),
                                        }
                                    });
                                }
                            }, "Adjust position"
                        }
                        button { id: remove_id, r#type: "button", class: "dropdown-item", disabled: locked,
                            onclick: move |_| {
                                draft.write().remove(); menu.set(false); error.set(None); drag.set(None);
                                if active() == Some(avatar) { active.set(None); }
                                crate::appearance::focus_control(trigger_id);
                            }, "Remove {noun}"
                        }
                    }
                }
            }
            if loading() { span { class: "profile-image-message", role: "status", "Preparing image…" } }
            if let Some(message) = error() { p { class: "profile-image-message is-error", role: "alert", "{message}" } }
        }
    }
}

#[component]
pub fn ProfileImageControls(
    banner: Signal<ImageDraft>,
    photo: Signal<ImageDraft>,
    mut active: Signal<Option<bool>>,
    busy: Signal<bool>,
    #[props(default)] disabled: bool,
) -> Element {
    let Some(avatar) = active() else {
        return rsx! {};
    };
    let mut draft = if avatar { photo } else { banner };
    let Some(image) = draft.read().selected.clone() else {
        return rsx! {};
    };
    let noun = if avatar { "photo" } else { "banner" };
    let locked = disabled || busy();
    rsx! {
        div { class: "profile-image-controls", aria_label: "Position {noun}",
            p { id: "profile-image-position-help", class: "form-text", "Drag or tap to position."
                span { class: "visually-hidden", " You can also focus the image and use arrow keys. Reset position centers the image without changing zoom." }
            }
            div { class: "profile-image-zoom-row",
                label { r#for: "profile-image-zoom", "Zoom" }
                input { id: "profile-image-zoom", class: "form-range", r#type: "range", min: "1", max: "3", step: "0.01",
                    value: "{image.zoom}", disabled: locked,
                    oninput: move |event| { if let Some(image) = draft.write().selected.as_mut() { image.zoom = event.value().parse::<f64>().unwrap_or(1.0).clamp(1.0, 3.0); } }
                }
                button { r#type: "button", class: "btn btn-outline-secondary", disabled: locked,
                    onclick: move |_| { active.set(None); crate::appearance::focus_control(if avatar { "profile-photo-options-trigger" } else { "profile-banner-options-trigger" }); }, "Done" }
            }
            span { class: "visually-hidden", aria_live: "polite", "Editing {noun}. Changes are saved with your profile." }
        }
    }
}
