use crate::api;
use dioxus::prelude::*;

#[component]
pub fn Markdown(page: String) -> Element {
  let page_state = use_signal(|| page);
  
    let data = use_resource(move || async move {
        api::markdown_page(&page_state()).await.map_err(|e| e.to_string())
    });
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
