use crate::api;
use dioxus::prelude::*;

#[component]
pub fn Markdown(page: String) -> Element {
    let data = use_resource(use_reactive(&page, move |page| {
        let value = page.clone();
        async move { api::markdown_page(&value).await.map_err(|e| e.to_string()) }
    }));
    let val = data.value();

    rsx! {
        if let Some(Ok(d)) = val.read().as_ref() {
            div { class: "row",
                div { class: "col-lg-6 mx-auto",
                    div { dangerous_inner_html: "{d.html}" }
                }
            }
        } else if let Some(Err(e)) = val.read().as_ref() {
            p { class: "text-danger", "{e}" }
        } else {
            p { "Loading…" }
        }
    }
}
