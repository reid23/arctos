//! Server-owned event discovery. Do not download the full tournament dataset
//! and reapply publication, search, dates or pagination in the browser.

use crate::types::PublicEventsPage;

pub async fn directory(query: &str, past: bool, page: u32) -> Result<PublicEventsPage, String> {
    let page = page.max(1).to_string();
    let response = super::with_credentials(
        super::client()
            .get(format!("{}/_api/public/events", super::base()))
            .query(&[
                ("q", query),
                ("view", if past { "past" } else { "upcoming" }),
                ("page", page.as_str()),
            ]),
    )
    .send()
    .await
    .map_err(|error| error.to_string())?;
    super::response_json(response).await
}
