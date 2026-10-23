use crate::api;
use dioxus::prelude::*;

/// Render only HTML returned by the shared sanitizing Markdown endpoint.
#[component]
pub fn ProfileBio(source: String, #[props(default)] preview: bool) -> Element {
    let mut rendered = use_resource(use_reactive(
        &(source.clone(), preview),
        move |(source, preview)| async move {
            let result = if source.trim().is_empty() {
                Ok(String::new())
            } else {
                if preview {
                    #[cfg(target_arch = "wasm32")]
                    gloo_timers::future::TimeoutFuture::new(250).await;
                }
                api::render_markdown(&source).await
            };
            (source, result)
        },
    ));
    if source.trim().is_empty() {
        return rsx! {};
    }
    let result = rendered.read();
    // Resources may retain their previous value while a new request is pending.
    // Never show that previous bio beneath the new profile or edited text.
    let current = result
        .as_ref()
        .filter(|(text, _)| text == &source)
        .map(|(_, result)| result);
    rsx! {
        div { class: "profile-bio", aria_busy: current.is_none(),
            if let Some(Ok(html)) = current {
                div { class: "markdown-content", dangerous_inner_html: "{html}",
                    onclick: move |event| { if preview { event.prevent_default(); } }
                }
            } else {
                div { class: "markdown-content", style: "white-space: pre-wrap;", "{source}" }
                if matches!(current, Some(Err(_))) {
                    p { class: "form-text", role: "status", "Formatting unavailable." }
                    button { r#type: "button", class: "btn btn-sm btn-outline-secondary",
                        onclick: move |_| rendered.restart(), "Retry formatting" }
                }
            }
        }
    }
}
