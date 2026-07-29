use crate::types::*;
use dioxus::prelude::*;
use reqwest::Client;
use serde_json::Value;

pub fn base_url() -> String {
    if cfg!(debug_assertions) {
        "http://127.0.0.1:5006".to_string()
    } else {
        let window = web_sys::window().unwrap();
        let location = window.location();
        let origin = location.origin().unwrap();
        origin
    }
}

fn base() -> String {
    base_url()
}

fn client() -> Client {
    Client::new()
}

fn with_credentials(req: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
    #[cfg(target_arch = "wasm32")]
    {
        req.fetch_credentials_include()
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        req
    }
}

fn truncate_error_body(text: &str, max_len: usize) -> String {
    let t = text.trim();
    if t.len() <= max_len {
        t.to_string()
    } else {
        format!(
            "{}... ({} bytes total). Check server logs for full error.",
            &t[..max_len],
            t.len()
        )
    }
}

#[derive(serde::Deserialize)]
pub struct StatusResponse {
    pub success: bool,
    #[allow(dead_code)]
    pub message: Option<String>,
    pub error: Option<String>,
}

#[derive(serde::Deserialize)]
pub struct CreateTournamentResponse {
    pub success: bool,
    #[allow(dead_code)]
    pub message: Option<String>,
    pub error: Option<String>,
    pub url: Option<String>,
}

async fn response_json<T: serde::de::DeserializeOwned>(
    resp: reqwest::Response,
) -> Result<T, String> {
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        // If the body is JSON with an "error" field, use that for a friendlier message
        if let Ok(v) = serde_json::from_str::<Value>(&text) {
            if let Some(msg) = v.get("error").and_then(|e| e.as_str()) {
                return Err(msg.to_string());
            }
        }
        let truncated = truncate_error_body(&text, 200);
        return Err(format!("Server returned error {}: {}", status, truncated));
    }

    // Check content type
    let ct_str = resp
        .headers()
        .get("content-type")
        .and_then(|ct| ct.to_str().ok())
        .map(|s| s.to_string());
    if let Some(ct) = ct_str.as_deref() {
        if !ct.contains("application/json") {
            let text = resp.text().await.unwrap_or_default();
            let truncated = truncate_error_body(&text, 200);
            return Err(format!(
                "Server returned non-JSON (content-type: {}). Maybe the backend is not running or /_api/ routes are missing. Body: {}",
                ct,
                truncated
            ));
        }
    }

    resp.json::<T>().await.map_err(|e| e.to_string())
}

pub async fn me() -> Result<User, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/me", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn login(username: &str, password: &str) -> Result<User, String> {
    let c = client();
    let body = serde_json::json!({ "username": username, "password": password });
    let r = with_credentials(c.post(format!("{}/_api/login", base())).json(&body))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn logout() -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.post(format!("{}/_api/logout", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err("Logout failed".to_string());
    }
    Ok(())
}

pub async fn change_password(current_password: &str, new_password: &str) -> Result<(), String> {
    let c = client();
    let body = serde_json::json!({
        "current_password": current_password,
        "new_password": new_password
    });
    let r = with_credentials(
        c.post(format!("{}/_api/change-password", base()))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let status = r.status();
        let text = r.text().await.unwrap_or_default();
        if let Ok(v) = serde_json::from_str::<Value>(&text) {
            if let Some(msg) = v.get("error").and_then(|e| e.as_str()) {
                return Err(msg.to_string());
            }
        }
        return Err(format!("Server returned error {}: {}", status, text));
    }
    Ok(())
}

pub async fn register(
    username: &str,
    password: &str,
    name: &str,
    user_type: &str,
) -> Result<User, String> {
    let c = client();
    let body = serde_json::json!({
        "username": username,
        "password": password,
        "name": name,
        "user_type": user_type
    });
    let r = with_credentials(c.post(format!("{}/_api/register", base())).json(&body))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn check_username(username: &str) -> Result<CheckUsernameResponse, String> {
    let c = client();
    let r = c
        .get(format!("{}/_api/check-username", base()))
        .query(&[("username", username)])
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn tournaments() -> Result<TournamentsResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/tournaments", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn tournament_detail(tournament_url: &str) -> Result<TournamentDetailResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/tournaments/{}", base(), tournament_url)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

pub async fn tournament_fields(tournament_url: &str) -> Result<Vec<FieldOption>, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/fields",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    let fields_val = data
        .get("fields")
        .cloned()
        .unwrap_or_else(|| Value::Array(vec![]));
    serde_json::from_value::<Vec<FieldOption>>(fields_val).map_err(|e| e.to_string())
}

pub async fn user_uploaded_cameras_list(
    tournament_url: &str,
) -> Result<UserUploadedCamerasResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/user-uploaded-cameras",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_user_uploaded_camera(
    tournament_url: &str,
    camera_uuid: &str,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/tournaments/{}/user-upload/delete-camera/{}",
        base(),
        tournament_url,
        camera_uuid
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Delete failed")
            .to_string())
    }
}

pub async fn user_upload_planning(
    tournament_url: &str,
    field_id: u32,
) -> Result<UserUploadPlanningResponse, String> {
    let c = client();
    let r = with_credentials(
        c.get(format!(
            "{}/_api/tournaments/{}/user-upload/planning",
            base(),
            tournament_url
        ))
        .query(&[("field_id", field_id)]),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

#[cfg(target_arch = "wasm32")]
pub async fn user_upload_video_footage(
    tournament_url: &str,
    upload_mode: &str,
    field_id: Option<u32>,
    match_uuid: Option<&str>,
    file: dioxus::html::FileData,
    start_world_override: Option<String>,
    camera_name: Option<String>,
    batch_id: &str,
    batch_index: u32,
    batch_total: u32,
) -> Result<String, String> {
    user_upload_video_footage_with_progress(
        tournament_url,
        upload_mode,
        field_id,
        match_uuid,
        file,
        start_world_override,
        camera_name,
        batch_id,
        batch_index,
        batch_total,
        |_, _| {},
    )
    .await
}

/// `on_progress(bytes_sent, total_bytes)` after each chunk succeeds and after finalize completes.
#[cfg(target_arch = "wasm32")]
pub async fn user_upload_video_footage_with_progress<F>(
    tournament_url: &str,
    upload_mode: &str,
    field_id: Option<u32>,
    match_uuid: Option<&str>,
    file: dioxus::html::FileData,
    start_world_override: Option<String>,
    camera_name: Option<String>,
    batch_id: &str,
    batch_index: u32,
    batch_total: u32,
    mut on_progress: F,
) -> Result<String, String>
where
    F: FnMut(u64, u64),
{
    use gloo_timers::future::TimeoutFuture;
    use wasm_bindgen::JsCast;

    let window = web_sys::window().ok_or("no window")?;
    const CHUNK_SIZE_BYTES: u64 = 20 * 1024 * 1024;
    const CHUNK_UPLOAD_MAX_ATTEMPTS: u32 = 3;

    let web_file = file
        .inner()
        .downcast_ref::<web_sys::File>()
        .cloned()
        .ok_or_else(|| "Could not access browser file handle".to_string())?;
    let file_size = web_file.size() as u64;
    on_progress(0, file_size);

    let total_chunks = ((file_size + CHUNK_SIZE_BYTES - 1) / CHUNK_SIZE_BYTES).max(1);
    let upload_id = format!("u{}", uuid::Uuid::new_v4().simple());
    let content_type = file
        .content_type()
        .unwrap_or_else(|| "application/octet-stream".to_string());
    let filename = file.name();

    let chunk_url = format!(
        "{}/_api/tournaments/{}/user-upload/chunk",
        base(),
        tournament_url
    );
    for idx in 0..total_chunks {
        let start = idx * CHUNK_SIZE_BYTES;
        let end = std::cmp::min(start + CHUNK_SIZE_BYTES, file_size);
        let blob = web_file
            .slice_with_f64_and_f64(start as f64, end as f64)
            .map_err(|_| "Failed to slice file chunk")?;

        let form = web_sys::FormData::new().map_err(|_| "FormData::new failed")?;
        form.append_with_str("upload_mode", upload_mode)
            .map_err(|_| "append upload_mode failed")?;
        if let Some(field_id) = field_id {
            form.append_with_str("field_id", &field_id.to_string())
                .map_err(|_| "append field_id failed")?;
        }
        if let Some(match_uuid) = match_uuid {
            if !match_uuid.trim().is_empty() {
                form.append_with_str("match_uuid", match_uuid.trim())
                    .map_err(|_| "append match_uuid failed")?;
            }
        }
        form.append_with_str("upload_id", &upload_id)
            .map_err(|_| "append upload_id failed")?;
        form.append_with_str("chunk_index", &idx.to_string())
            .map_err(|_| "append chunk_index failed")?;
        form.append_with_str("total_chunks", &total_chunks.to_string())
            .map_err(|_| "append total_chunks failed")?;
        form.append_with_str("batch_id", batch_id)
            .map_err(|_| "append batch_id failed")?;
        form.append_with_str("batch_index", &batch_index.to_string())
            .map_err(|_| "append batch_index failed")?;
        form.append_with_str("batch_total", &batch_total.to_string())
            .map_err(|_| "append batch_total failed")?;
        form.append_with_str("filename", &filename)
            .map_err(|_| "append filename failed")?;
        form.append_with_str("content_type", &content_type)
            .map_err(|_| "append content_type failed")?;
        if let Some(sw) = start_world_override.as_deref() {
            if !sw.trim().is_empty() {
                form.append_with_str("start_world", sw.trim())
                    .map_err(|_| "append start_world failed")?;
            }
        }
        if let Some(name) = camera_name.as_deref() {
            if !name.trim().is_empty() {
                form.append_with_str("camera_name", name.trim())
                    .map_err(|_| "append camera_name failed")?;
            }
        }
        form.append_with_blob("chunk", &blob)
            .map_err(|_| "append chunk failed")?;

        let opts = web_sys::RequestInit::new();
        opts.set_method("POST");
        let form_js = wasm_bindgen::JsValue::from(form);
        opts.set_body(form_js.as_ref());
        #[allow(deprecated)]
        opts.set_credentials(web_sys::RequestCredentials::Include);
        let request = web_sys::Request::new_with_str_and_init(&chunk_url, &opts)
            .map_err(|_| "Request::new failed")?;
        let mut fetch_err: Option<String> = None;
        let mut resp_opt: Option<web_sys::Response> = None;
        for attempt in 1..=CHUNK_UPLOAD_MAX_ATTEMPTS {
            match wasm_bindgen_futures::JsFuture::from(window.fetch_with_request(&request)).await {
                Ok(resp) => {
                    let resp: web_sys::Response =
                        resp.dyn_into().map_err(|_| "response cast failed")?;
                    resp_opt = Some(resp);
                    break;
                }
                Err(e) => {
                    fetch_err = Some(format!("{:?}", e));
                    if attempt < CHUNK_UPLOAD_MAX_ATTEMPTS {
                        TimeoutFuture::new(750 * attempt).await;
                    }
                }
            }
        }
        let resp = resp_opt.ok_or_else(|| {
            format!(
                "Chunk upload failed due to a network/load error after {} attempts: {}. This usually means the server or proxy dropped the connection during upload.",
                CHUNK_UPLOAD_MAX_ATTEMPTS,
                fetch_err.unwrap_or_else(|| "unknown fetch failure".to_string())
            )
        })?;
        if !resp.ok() {
            let text = wasm_bindgen_futures::JsFuture::from(
                resp.text()
                    .map_err(|e: wasm_bindgen::JsValue| format!("{:?}", e))?,
            )
            .await
            .map_err(|e| format!("{:?}", e))?;
            let msg = text
                .as_string()
                .unwrap_or_else(|| "Unknown error".to_string());
            return Err(format!("Chunk upload failed: {}", msg));
        }
        on_progress(end, file_size);
    }

    let complete_url = format!(
        "{}/_api/tournaments/{}/user-upload/complete",
        base(),
        tournament_url
    );
    let body = serde_json::json!({
        "upload_id": upload_id,
        "batch_index": batch_index,
    });
    let c = client();
    let r = with_credentials(c.post(complete_url).json(&body))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let v: Value = response_json(r).await?;
    on_progress(file_size, file_size);
    Ok(v.get("upload_group_name")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string())
}

pub async fn start_match_data(
    tournament_url: &str,
    match_id: &str,
) -> Result<StartMatchResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/start-match?match_id={}",
        base(),
        tournament_url,
        match_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn start_match(
    tournament_url: &str,
    req: &StartMatchRequest,
) -> Result<StartMatchPostResponse, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/start-match",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn finalize_match_data(
    tournament_url: &str,
    match_id: &str,
) -> Result<FinalizeMatchResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/finalize-match?match_id={}",
        base(),
        tournament_url,
        match_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn finalize_match(
    tournament_url: &str,
    req: &FinalizeMatchRequest,
) -> Result<FinalizeMatchPostResponse, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/finalize-match",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

async fn post_form_status(
    url: &str,
    params: &[(String, String)],
) -> Result<StatusResponse, String> {
    let c = client();
    let r = with_credentials(c.post(url).form(params))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_tournament_settings(
    tournament_url: &str,
    params: &[(String, String)],
) -> Result<StatusResponse, String> {
    let url = format!("{}/_api/{}/update-settings", base(), tournament_url);
    post_form_status(&url, params).await
}

pub async fn add_tournament_to(
    tournament_url: &str,
    user_type: &str,
    user_id: &str,
) -> Result<StatusResponse, String> {
    let params = vec![
        ("user_type".into(), user_type.to_string()),
        ("user_id".into(), user_id.to_string()),
    ];
    let url = format!("{}/_api/{}/add-to", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn remove_tournament_to(
    tournament_url: &str,
    to_id: i32,
) -> Result<StatusResponse, String> {
    let params = vec![("to_id".into(), to_id.to_string())];
    let url = format!("{}/_api/{}/remove-to", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn mark_team_paid(
    tournament_url: &str,
    registration_id: u32,
    amount_paid: f64,
    paid: bool,
    payment_method: &str,
    payment_reference: &str,
    payment_notes: &str,
) -> Result<StatusResponse, String> {
    let mut params = vec![
        ("registration_id".into(), registration_id.to_string()),
        ("amount_paid".into(), amount_paid.to_string()),
        ("payment_method".into(), payment_method.to_string()),
        ("payment_reference".into(), payment_reference.to_string()),
        ("payment_notes".into(), payment_notes.to_string()),
    ];
    if paid {
        params.push(("paid".into(), "on".to_string()));
    }
    let url = format!("{}/_api/{}/mark-team-paid", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn mark_player_paid(
    tournament_url: &str,
    registration_id: u32,
    amount_paid: f64,
    paid: bool,
    payment_method: &str,
    payment_reference: &str,
    payment_notes: &str,
) -> Result<StatusResponse, String> {
    let mut params = vec![
        ("registration_id".into(), registration_id.to_string()),
        ("amount_paid".into(), amount_paid.to_string()),
        ("payment_method".into(), payment_method.to_string()),
        ("payment_reference".into(), payment_reference.to_string()),
        ("payment_notes".into(), payment_notes.to_string()),
    ];
    if paid {
        params.push(("paid".into(), "on".to_string()));
    }
    let url = format!("{}/_api/{}/mark-player-paid", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn deregister_any_team(
    tournament_url: &str,
    team_id: &str,
) -> Result<StatusResponse, String> {
    let params = vec![("team_id".into(), team_id.to_string())];
    let url = format!("{}/_api/{}/deregister-any-team", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn deregister_any_player(
    tournament_url: &str,
    player_id: &str,
) -> Result<StatusResponse, String> {
    let params = vec![("player_id".into(), player_id.to_string())];
    let url = format!("{}/_api/{}/deregister-any-player", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn register_player(
    tournament_url: &str,
    jersey_name: &str,
    jersey_number: &str,
    team: &str,
    waiver_legal_name_signature: &str,
) -> Result<StatusResponse, String> {
    let params = vec![
        ("jersey_name".into(), jersey_name.to_string()),
        ("jersey_number".into(), jersey_number.to_string()),
        ("team".into(), team.to_string()),
        (
            "waiver_legal_name_signature".into(),
            waiver_legal_name_signature.to_string(),
        ),
    ];
    let url = format!("{}/_api/{}/register-player", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn register_team(
    tournament_url: &str,
    pseudonym: &str,
    shortname: Option<&str>,
) -> Result<StatusResponse, String> {
    let mut params: Vec<(String, String)> = vec![("pseudonym".into(), pseudonym.to_string())];
    if let Some(s) = shortname {
        let trimmed = s.trim();
        if !trimmed.is_empty() {
            params.push(("shortname".into(), trimmed.to_string()));
        }
    }
    let url = format!("{}/_api/{}/register-team", base(), tournament_url);
    post_form_status(&url, &params).await
}

pub async fn deregister_player(tournament_url: &str) -> Result<StatusResponse, String> {
    let url = format!("{}/_api/{}/deregister-player", base(), tournament_url);
    post_form_status(&url, &[]).await
}

pub async fn deregister_team(tournament_url: &str) -> Result<StatusResponse, String> {
    let url = format!("{}/_api/{}/deregister-team", base(), tournament_url);
    post_form_status(&url, &[]).await
}

pub async fn register_player_as_to(
    tournament_url: &str,
    player_id: &str,
    team_id: Option<&str>,
    jersey_number: &str,
    jersey_name: &str,
    waiver_legal_name_signature: &str,
) -> Result<crate::types::RegisterPlayerAsToResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "player_id": player_id,
        "team": team_id,
        "jersey_number": jersey_number,
        "jersey_name": jersey_name,
        "waiver_legal_name_signature": waiver_legal_name_signature,
    });
    let r = with_credentials(
        c.post(format!("{}/_api/{}/register-player-as-to", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn register_team_as_to(
    tournament_url: &str,
    team_id: &str,
    pseudonym: &str,
    shortname: Option<&str>,
) -> Result<crate::types::RegisterTeamAsToResponse, String> {
    let c = client();
    let mut body = serde_json::json!({
        "team_id": team_id,
        "pseudonym": pseudonym,
    });
    if let Some(s) = shortname {
        let trimmed = s.trim();
        if !trimmed.is_empty() {
            body["shortname"] = serde_json::Value::String(trimmed.to_string());
        }
    }
    let r = with_credentials(
        c.post(format!("{}/_api/{}/register-team-as-to", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn create_tournament(
    name: &str,
    url_slug: &str,
    league_id: Option<&str>,
) -> Result<CreateTournamentResponse, String> {
    let mut params: Vec<(String, String)> = vec![
        ("name".into(), name.to_string()),
        ("url".into(), url_slug.to_string()),
    ];
    if let Some(id) = league_id {
        params.push(("league_id".into(), id.to_string()));
    }
    let url = format!("{}/_api/create-tournament", base());
    let c = client();
    let r = with_credentials(c.post(&url).form(&params))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

#[derive(serde::Deserialize)]
pub struct CreateLeagueResponse {
    pub success: bool,
    pub message: Option<String>,
    pub error: Option<String>,
    pub league_url: Option<String>,
}

pub async fn create_league(
    league_name: &str,
    league_url: &str,
) -> Result<CreateLeagueResponse, String> {
    let params: Vec<(String, String)> = vec![
        ("league_name".into(), league_name.to_string()),
        ("league_url".into(), league_url.to_string()),
    ];
    let url = format!("{}/_api/create-league", base());
    let c = client();
    let r = with_credentials(c.post(&url).form(&params))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn leagues_list() -> Result<crate::types::LeaguesListResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/leagues", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

/// League seasons that the current user organizes (for tournament create/edit league selector).
pub async fn leagues_organized() -> Result<crate::types::LeaguesListResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/leagues/organized", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn league_detail(league_url: &str) -> Result<crate::types::LeagueDetailResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/leagues/{}", base(), league_url)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

pub async fn league_results(
    league_url: &str,
    include_ribbon: bool,
) -> Result<crate::types::LeagueResultsResponse, String> {
    let c = client();
    let inc = if include_ribbon { "true" } else { "false" };
    let r = with_credentials(c.get(format!(
        "{}/_api/leagues/{}/results?include_ribbon={}",
        base(),
        league_url,
        inc
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn league_results_team_matches(
    league_url: &str,
    team_id: &str,
) -> Result<crate::types::TeamMatchesResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/leagues/{}/results/team/{}",
        base(),
        league_url,
        team_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn league_register_team(
    league_url: &str,
    pseudonym: &str,
    shortname: Option<&str>,
) -> Result<StatusResponse, String> {
    let mut params: Vec<(String, String)> = vec![("pseudonym".into(), pseudonym.to_string())];
    if let Some(s) = shortname {
        let trimmed = s.trim();
        if !trimmed.is_empty() {
            params.push(("shortname".into(), trimmed.to_string()));
        }
    }
    let url = format!("{}/_api/leagues/{}/register-team", base(), league_url);
    post_form_status(&url, &params).await
}

pub async fn league_register_player(
    league_url: &str,
    team: Option<&str>,
    jersey_number: &str,
    jersey_name: &str,
    waiver_legal_name_signature: &str,
) -> Result<StatusResponse, String> {
    let mut params: Vec<(String, String)> = vec![
        ("jersey_number".into(), jersey_number.to_string()),
        ("jersey_name".into(), jersey_name.to_string()),
    ];
    if let Some(t) = team {
        params.push(("team".into(), t.to_string()));
    }
    params.push((
        "waiver_legal_name_signature".into(),
        waiver_legal_name_signature.to_string(),
    ));
    let url = format!("{}/_api/leagues/{}/register-player", base(), league_url);
    post_form_status(&url, &params).await
}

/// Upload or replace the tournament waiver PDF (organizers only). Call after saving settings.
pub async fn upload_waiver(
    tournament_url: &str,
    bytes: Vec<u8>,
    filename: &str,
) -> Result<(), String> {
    let form = reqwest::multipart::Form::new().part(
        "waiver",
        reqwest::multipart::Part::bytes(bytes).file_name(filename.to_string()),
    );
    let c = client();
    let url = format!("{}/_api/{}/upload-waiver", base(), tournament_url);
    let r = with_credentials(c.post(url).multipart(form))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let status = r.status();
        let text = r.text().await.unwrap_or_default();
        return Err(format!("{}: {}", status, text));
    }
    Ok(())
}

/// Upload or replace the league waiver PDF (organizers only).
pub async fn league_upload_waiver(
    league_url: &str,
    bytes: Vec<u8>,
    filename: &str,
) -> Result<(), String> {
    let form = reqwest::multipart::Form::new().part(
        "waiver",
        reqwest::multipart::Part::bytes(bytes).file_name(filename.to_string()),
    );
    let c = client();
    let url = format!("{}/_api/leagues/{}/upload-waiver", base(), league_url);
    let r = with_credentials(c.post(url).multipart(form))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let status = r.status();
        let text = r.text().await.unwrap_or_default();
        return Err(format!("{}: {}", status, text));
    }
    Ok(())
}

pub async fn league_deregister_team(league_url: &str) -> Result<StatusResponse, String> {
    let url = format!("{}/_api/leagues/{}/deregister-team", base(), league_url);
    post_form_status(&url, &[]).await
}

pub async fn league_deregister_player(league_url: &str) -> Result<StatusResponse, String> {
    let url = format!("{}/_api/leagues/{}/deregister-player", base(), league_url);
    post_form_status(&url, &[]).await
}

pub async fn get_my_player_registration_league(
    league_url: &str,
) -> Result<MyPlayerRegistrationResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/leagues/{}/registrations/player/me",
        base(),
        league_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_my_player_registration_league(
    league_url: &str,
    req: &UpdatePlayerRegistrationRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/leagues/{}/registrations/player/me",
            base(),
            league_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn get_my_team_registration_league(
    league_url: &str,
) -> Result<MyTeamRegistrationResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/leagues/{}/registrations/team/me",
        base(),
        league_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_my_team_registration_league(
    league_url: &str,
    req: &UpdateTeamRegistrationRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/leagues/{}/registrations/team/me",
            base(),
            league_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn add_league_to(
    league_url: &str,
    user_type: &str,
    user_id: &str,
) -> Result<StatusResponse, String> {
    let params = vec![
        ("user_type".into(), user_type.to_string()),
        ("user_id".into(), user_id.to_string()),
    ];
    let url = format!("{}/_api/leagues/{}/add-to", base(), league_url);
    post_form_status(&url, &params).await
}

pub async fn remove_league_to(league_url: &str, to_id: i32) -> Result<StatusResponse, String> {
    let params = vec![("to_id".into(), to_id.to_string())];
    let url = format!("{}/_api/leagues/{}/remove-to", base(), league_url);
    post_form_status(&url, &params).await
}

pub async fn league_manage(
    league_url: &str,
    search: &str,
    search_type: &str,
) -> Result<crate::types::TournamentManageResponse, String> {
    let c = client();
    let mut url = format!("{}/_api/leagues/{}/manage", base(), league_url);
    if !search.is_empty() || !search_type.is_empty() {
        let st = if search_type.is_empty() {
            "both"
        } else {
            search_type
        };
        url = format!("{}?search={}&type={}", url, urlencoding::encode(search), st);
    }
    let r = with_credentials(c.get(url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn league_invitations(
    league_url: &str,
) -> Result<crate::types::TournamentInvitationsResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/leagues/{}/invitations",
        base(),
        league_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn league_mark_team_paid(
    league_url: &str,
    registration_id: u32,
    amount: f64,
    paid: bool,
    payment_method: &str,
    payment_reference: &str,
    payment_notes: &str,
) -> Result<StatusResponse, String> {
    let mut params: Vec<(String, String)> = vec![
        ("registration_id".into(), registration_id.to_string()),
        ("amount_paid".into(), amount.to_string()),
        ("payment_method".into(), payment_method.to_string()),
        ("payment_reference".into(), payment_reference.to_string()),
        ("payment_notes".into(), payment_notes.to_string()),
    ];
    if paid {
        params.push(("paid".into(), "on".to_string()));
    }
    let url = format!("{}/_api/leagues/{}/mark-team-paid", base(), league_url);
    post_form_status(&url, &params).await
}

pub async fn league_mark_player_paid(
    league_url: &str,
    registration_id: u32,
    amount: f64,
    paid: bool,
    payment_method: &str,
    payment_reference: &str,
    payment_notes: &str,
) -> Result<StatusResponse, String> {
    let mut params: Vec<(String, String)> = vec![
        ("registration_id".into(), registration_id.to_string()),
        ("amount_paid".into(), amount.to_string()),
        ("payment_method".into(), payment_method.to_string()),
        ("payment_reference".into(), payment_reference.to_string()),
        ("payment_notes".into(), payment_notes.to_string()),
    ];
    if paid {
        params.push(("paid".into(), "on".to_string()));
    }
    let url = format!("{}/_api/leagues/{}/mark-player-paid", base(), league_url);
    post_form_status(&url, &params).await
}

pub async fn league_deregister_any_team(
    league_url: &str,
    team_id: &str,
) -> Result<StatusResponse, String> {
    let params = vec![("team_id".into(), team_id.to_string())];
    let url = format!("{}/_api/leagues/{}/deregister-any-team", base(), league_url);
    post_form_status(&url, &params).await
}

pub async fn league_deregister_any_player(
    league_url: &str,
    player_id: &str,
) -> Result<StatusResponse, String> {
    let params = vec![("player_id".into(), player_id.to_string())];
    let url = format!(
        "{}/_api/leagues/{}/deregister-any-player",
        base(),
        league_url
    );
    post_form_status(&url, &params).await
}

pub async fn league_accept_invitation(league_url: &str, invitation_id: u32) -> Result<(), String> {
    let c = client();
    let url = format!(
        "{}/_api/leagues/{}/invitation/{}/accept",
        base(),
        league_url,
        invitation_id
    );
    let r = with_credentials(c.post(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 403 {
        let err: serde_json::Value = response_json(r).await.unwrap_or_default();
        return Err(err
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Forbidden")
            .to_string());
    }
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    if !r.status().is_success() {
        return Err(format!("Request failed: {}", r.status()));
    }
    Ok(())
}

pub async fn league_decline_invitation(league_url: &str, invitation_id: u32) -> Result<(), String> {
    let c = client();
    let url = format!(
        "{}/_api/leagues/{}/invitation/{}/decline",
        base(),
        league_url,
        invitation_id
    );
    let r = with_credentials(c.post(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 403 {
        let err: serde_json::Value = response_json(r).await.unwrap_or_default();
        return Err(err
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Forbidden")
            .to_string());
    }
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    if !r.status().is_success() {
        return Err(format!("Request failed: {}", r.status()));
    }
    Ok(())
}

pub async fn league_update_settings(
    league_url: &str,
    params: &std::collections::HashMap<String, serde_json::Value>,
) -> Result<StatusResponse, String> {
    let c = client();
    let url = format!("{}/_api/leagues/{}/settings", base(), league_url);
    let r = with_credentials(c.post(&url).json(params))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn create_league_penalty_type(
    league_url: &str,
    name: &str,
    color: Option<&str>,
    desc: Option<&str>,
) -> Result<CreatePenaltyTypeResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "name": name,
        "color": color,
        "desc": desc
    });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/leagues/{}/penalty-types",
            base(),
            league_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_league_penalty_type(
    league_url: &str,
    pt_id: i32,
    name: Option<&str>,
    color: Option<&str>,
    desc: Option<&str>,
) -> Result<Value, String> {
    let c = client();
    let mut body = serde_json::Map::new();
    if let Some(n) = name {
        body.insert("name".to_string(), serde_json::json!(n));
    }
    if let Some(c_val) = color {
        body.insert("color".to_string(), serde_json::json!(c_val));
    }
    if let Some(d) = desc {
        body.insert("desc".to_string(), serde_json::json!(d));
    }
    let r = with_credentials(
        c.patch(format!(
            "{}/_api/leagues/{}/penalty-types/{}",
            base(),
            league_url,
            pt_id
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_league_penalty_type(league_url: &str, pt_id: i32) -> Result<Value, String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/leagues/{}/penalty-types/{}",
        base(),
        league_url,
        pt_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_tournament(
    tournament_url: &str,
    confirm_url: &str,
) -> Result<StatusResponse, String> {
    let params: Vec<(String, String)> = vec![("confirm_url".into(), confirm_url.to_string())];
    let url = format!("{}/_api/{}/delete", base(), tournament_url);
    let c = client();
    let r = with_credentials(c.post(&url).form(&params))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_league(league_url: &str, confirm_url: &str) -> Result<StatusResponse, String> {
    let params: Vec<(String, String)> = vec![("confirm_url".into(), confirm_url.to_string())];
    let url = format!("{}/_api/leagues/{}/delete", base(), league_url);
    let c = client();
    let r = with_credentials(c.post(&url).form(&params))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn tournament_manage(
    tournament_url: &str,
    search: &str,
    search_type: &str,
) -> Result<TournamentManageResponse, String> {
    let c = client();
    let mut url = format!("{}/_api/tournaments/{}/manage", base(), tournament_url);
    if !search.is_empty() || !search_type.is_empty() {
        let st = if search_type.is_empty() {
            "both"
        } else {
            search_type
        };
        url = format!("{}?search={}&type={}", url, urlencoding::encode(search), st);
    }
    let r = with_credentials(c.get(url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn tournament_invitations(
    tournament_url: &str,
) -> Result<TournamentInvitationsResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/invitations",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

/// Accept a pending player invitation (team only). Uses POST to _api route.
pub async fn accept_invitation(tournament_url: &str, invitation_id: u32) -> Result<(), String> {
    let c = client();
    let url = format!(
        "{}/_api/{}/invitation/{}/accept",
        base(),
        tournament_url,
        invitation_id
    );
    let r = with_credentials(c.post(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 403 {
        let err: serde_json::Value = response_json(r).await.unwrap_or_default();
        return Err(err
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Forbidden")
            .to_string());
    }
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    if !r.status().is_success() {
        return Err(format!("Request failed: {}", r.status()));
    }
    Ok(())
}

/// Decline a pending player invitation (team only). Uses POST to _api route.
pub async fn decline_invitation(tournament_url: &str, invitation_id: u32) -> Result<(), String> {
    let c = client();
    let url = format!(
        "{}/_api/{}/invitation/{}/decline",
        base(),
        tournament_url,
        invitation_id
    );
    let r = with_credentials(c.post(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 403 {
        let err: serde_json::Value = response_json(r).await.unwrap_or_default();
        return Err(err
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Forbidden")
            .to_string());
    }
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    if !r.status().is_success() {
        return Err(format!("Request failed: {}", r.status()));
    }
    Ok(())
}

pub async fn tournament_bracket(tournament_url: &str) -> Result<BracketResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/bracket",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 403 {
        return Err("Bracket is not available".to_string());
    }
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

/// Delete one legacy image-bracket by index (TO only). Returns updated bracket payload.
pub async fn delete_legacy_bracket(
    tournament_url: &str,
    index: usize,
) -> Result<BracketResponse, String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/tournaments/{}/legacy-brackets/{}",
        base(),
        tournament_url,
        index
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let msg = r
            .text()
            .await
            .unwrap_or_else(|_| "Failed to delete legacy bracket".to_string());
        return Err(msg);
    }
    response_json(r).await
}

/// Delete all legacy image-brackets for a tournament (TO only).
pub async fn clear_legacy_brackets(tournament_url: &str) -> Result<BracketResponse, String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/tournaments/{}/legacy-brackets",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let msg = r
            .text()
            .await
            .unwrap_or_else(|_| "Failed to clear legacy brackets".to_string());
        return Err(msg);
    }
    response_json(r).await
}

/// Persist bracket canvas state (match placements + annotations) for a tournament (TO only).
pub async fn save_bracket_placements(
    tournament_url: &str,
    placements: &[serde_json::Value],
    texts: &[serde_json::Value],
    labeled_teams: &[serde_json::Value],
    images: &[serde_json::Value],
    clear_missing: bool,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "placements": placements,
        "texts": texts,
        "labeled_teams": labeled_teams,
        "images": images,
        "clear_missing": clear_missing,
    });
    let r = with_credentials(c.put(format!(
        "{}/_api/tournaments/{}/bracket-placements",
        base(),
        tournament_url
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let msg = r
            .text()
            .await
            .unwrap_or_else(|_| "Failed to save bracket placements".to_string());
        return Err(msg);
    }
    response_json(r).await
}

pub async fn add_bracket_text(
    tournament_url: &str,
    x_pos: f64,
    y_pos: f64,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({ "text": "Text", "x_pos": x_pos, "y_pos": y_pos, "size": 18.0 });
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/bracket-elements/text",
        base(),
        tournament_url
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().await.unwrap_or_else(|_| "Failed to add text".into()));
    }
    response_json(r).await
}

pub async fn add_bracket_labeled_team(
    tournament_url: &str,
    x_pos: f64,
    y_pos: f64,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "label": "Label",
        "team": "",
        "kind": "LABEL",
        "x_pos": x_pos,
        "y_pos": y_pos
    });
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/bracket-elements/labeled-team",
        base(),
        tournament_url
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().await.unwrap_or_else(|_| "Failed to add team label".into()));
    }
    response_json(r).await
}

pub async fn add_bracket_image_element(
    tournament_url: &str,
    image_path: &str,
    x_pos: f64,
    y_pos: f64,
    width: f64,
    height: f64,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "image": image_path,
        "x_pos": x_pos,
        "y_pos": y_pos,
        "width": width,
        "height": height,
    });
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/bracket-elements/image",
        base(),
        tournament_url
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().await.unwrap_or_else(|_| "Failed to add image".into()));
    }
    response_json(r).await
}

pub async fn convert_labeled_team_port(
    tournament_url: &str,
    element_id: &str,
    mode: &str,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({ "mode": mode });
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/bracket-elements/labeled-team/{}/convert",
        base(),
        tournament_url,
        element_id
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().await.unwrap_or_else(|_| "Failed to convert port".into()));
    }
    response_json(r).await
}

/// Place a single match on the bracket canvas (TO only).
pub async fn add_bracket_placement(
    tournament_url: &str,
    match_uuid: &str,
    x_pos: f64,
    y_pos: f64,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "match": match_uuid,
        "x_pos": x_pos,
        "y_pos": y_pos,
    });
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/bracket-placements/add",
        base(),
        tournament_url
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let msg = r
            .text()
            .await
            .unwrap_or_else(|_| "Failed to add match to bracket".to_string());
        return Err(msg);
    }
    response_json(r).await
}

/// Toggle a bracket input between LABEL and NET (TO only).
pub async fn convert_bracket_port(
    tournament_url: &str,
    match_uuid: &str,
    side: &str,
    mode: &str,
) -> Result<BracketResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "match": match_uuid,
        "side": side,
        "mode": mode,
    });
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/bracket-placements/convert-port",
        base(),
        tournament_url
    )))
    .json(&body)
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let msg = r
            .text()
            .await
            .unwrap_or_else(|_| "Failed to convert bracket port".to_string());
        return Err(msg);
    }
    response_json(r).await
}

pub async fn results(
    tournament_url: &str,
    include_ribbon: bool,
) -> Result<ResultsResponse, String> {
    let c = client();
    let url = format!(
        "{}/_api/tournaments/{}/results?include_ribbon={}",
        base(),
        tournament_url,
        include_ribbon
    );
    let r = with_credentials(c.get(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

pub async fn results_team_matches(
    tournament_url: &str,
    team_id: &str,
) -> Result<crate::types::TeamMatchesResponse, String> {
    let c = client();
    let url = format!(
        "{}/_api/tournaments/{}/results/team/{}",
        base(),
        tournament_url,
        urlencoding::encode(team_id)
    );
    let r = with_credentials(c.get(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

pub async fn match_detail(
    tournament_url: &str,
    match_id: Option<&str>,
    match_name: Option<&str>,
) -> Result<MatchDetailResponse, String> {
    let c = client();
    let mut req = c.get(format!(
        "{}/_api/tournaments/{}/match",
        base(),
        tournament_url
    ));
    if let Some(id) = match_id {
        req = req.query(&[("id", id)]);
    } else if let Some(name) = match_name {
        req = req.query(&[("name", name)]);
    } else {
        return Err("id or name required".to_string());
    }
    let r = with_credentials(req)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Match not found".to_string());
    }
    response_json(r).await
}

pub async fn retry_match_finalization(
    tournament_url: &str,
    match_id: &str,
) -> Result<String, String> {
    let c = client();
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/matches/{}/retry-finalization",
        base(),
        tournament_url,
        match_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: StatusResponse = response_json(r).await?;
    if data.success {
        Ok(data
            .message
            .unwrap_or_else(|| "Finalization retry started.".to_string()))
    } else {
        Err(data
            .error
            .unwrap_or_else(|| "Failed to retry finalization.".to_string()))
    }
}

pub async fn players_list(search: &str, page: u32) -> Result<PlayersListResponse, String> {
    let c = client();
    let mut req = c
        .get(format!("{}/_api/players", base()))
        .query(&[("page", page)]);
    if !search.is_empty() {
        req = req.query(&[("search", search)]);
    }
    let r = with_credentials(req)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn player_profile(player_id: &str) -> Result<PlayerProfileResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/players/{}", base(), player_id)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

pub async fn teams_list(search: &str) -> Result<TeamsListResponse, String> {
    let c = client();
    let mut req = c.get(format!("{}/_api/teams", base()));
    if !search.is_empty() {
        req = req.query(&[("search", search)]);
    }
    let r = with_credentials(req)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn team_profile(team_id: &str) -> Result<TeamProfileResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/teams/{}", base(), team_id)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

/// Players registered for a team in an event (public). Load on demand for dropdown.
pub async fn team_registration_players(
    team_id: &str,
    event: &str,
) -> Result<Vec<crate::types::TournamentPlayerItem>, String> {
    let c = client();
    let url = format!(
        "{}/_api/teams/{}/players?event={}",
        base(),
        urlencoding::encode(team_id),
        urlencoding::encode(event)
    );
    let r = with_credentials(c.get(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Err("Not found".to_string());
    }
    response_json(r).await
}

pub async fn stones_list() -> Result<StonesResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/stones", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

#[allow(dead_code)]
pub async fn camera_url(tournament_url: &str, field_name: &str) -> Result<String, String> {
    let c = client();
    let r = with_credentials(
        c.get(format!("{}/_api/camera-url", base()))
            .query(&[("tournament", tournament_url), ("field", field_name)]),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err("Failed to get camera URL".to_string());
    }
    let data: Value = response_json(r).await?;
    data.get("url")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| "No URL in response".to_string())
}

pub async fn record_match_status(
    tournament_url: &str,
    field_name: &str,
    current_match_id: Option<&str>,
) -> Result<RecordMatchStatusResponse, String> {
    let c = client();
    let mut req = c
        .get(format!("{}/_api/record/match-status", base()))
        .query(&[("tournament", tournament_url), ("field", field_name)]);
    if let Some(mid) = current_match_id {
        req = req.query(&[("current_match_id", mid)]);
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    response_json(r).await
}

/// TO: request preview for a field (record pages will start sending frames).
pub async fn request_preview(tournament_url: &str, field_name: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/record/request-preview", base()))
            .json(&serde_json::json!({
                "tournament": tournament_url,
                "field": field_name,
            })),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to request preview")
            .to_string())
    }
}

/// TO: release preview for a field (record pages will stop sending frames).
pub async fn release_preview(tournament_url: &str, field_name: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/record/release-preview", base()))
            .json(&serde_json::json!({
                "tournament": tournament_url,
                "field": field_name,
            })),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to release preview")
            .to_string())
    }
}

/// Record page: upload a preview frame (JPEG bytes). Requires camera_key.
pub async fn upload_preview_frame(
    tournament_url: &str,
    field_name: &str,
    camera_key: &str,
    camera_name: &str,
    jpeg_bytes: bytes::Bytes,
) -> Result<(), String> {
    let c = client();
    let url = format!(
        "{}/_api/record/preview-frame?tournament={}&field={}&camera_name={}&camera_key={}",
        base(),
        urlencoding::encode(tournament_url),
        urlencoding::encode(field_name),
        urlencoding::encode(camera_name),
        urlencoding::encode(camera_key),
    );
    let r = with_credentials(c.post(&url).body(jpeg_bytes))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to upload preview frame")
            .to_string())
    }
}

/// Device metadata sent with preview (storage in bytes, battery 0–1). Used when uploading and when TO fetches.
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct PreviewMetadata {
    pub storage_usage: Option<f64>,
    pub storage_quota: Option<f64>,
    pub battery_level: Option<f64>,
}

/// Record page: upload device metadata (storage, battery) for the current camera. Requires camera_key.
pub async fn upload_preview_metadata(
    tournament_url: &str,
    field_name: &str,
    camera_key: &str,
    camera_name: &str,
    meta: &PreviewMetadata,
) -> Result<(), String> {
    let c = client();
    let url = format!(
        "{}/_api/record/preview-metadata?tournament={}&field={}&camera_name={}&camera_key={}",
        base(),
        urlencoding::encode(tournament_url),
        urlencoding::encode(field_name),
        urlencoding::encode(camera_name),
        urlencoding::encode(camera_key),
    );
    let r = with_credentials(c.post(&url).json(meta))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to upload preview metadata")
            .to_string())
    }
}

/// TO: fetch device metadata for a camera. Returns None on 204 or when no metadata.
pub async fn fetch_preview_metadata(
    tournament_url: &str,
    field_name: &str,
    camera_name: &str,
) -> Result<Option<PreviewMetadata>, String> {
    let c = client();
    let url = format!(
        "{}/_api/record/preview-metadata?tournament={}&field={}&camera_name={}",
        base(),
        urlencoding::encode(tournament_url),
        urlencoding::encode(field_name),
        urlencoding::encode(camera_name),
    );
    let r = with_credentials(c.get(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().as_u16() == 204 {
        return Ok(None);
    }
    let meta: PreviewMetadata = response_json(r).await?;
    Ok(Some(meta))
}

/// Record page: poll whether the pending frame was consumed (so we can send the next one).
pub async fn is_preview_frame_consumed(
    tournament_url: &str,
    field_name: &str,
    camera_name: &str,
    camera_key: &str,
) -> Result<bool, String> {
    let c = client();
    let r = with_credentials(
        c.get(format!("{}/_api/record/preview-frame-consumed", base()))
            .query(&[
                ("tournament", tournament_url),
                ("field", field_name),
                ("camera_name", camera_name),
                ("camera_key", camera_key),
            ]),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    data.get("consumed")
        .and_then(|v| v.as_bool())
        .ok_or_else(|| "Invalid response from preview-frame-consumed".to_string())
}

/// TO: list camera names that have a recent preview frame for this field.
pub async fn list_preview_cameras(
    tournament_url: &str,
    field_name: &str,
) -> Result<Vec<String>, String> {
    let c = client();
    let r = with_credentials(
        c.get(format!("{}/_api/record/preview-cameras", base()))
            .query(&[("tournament", tournament_url), ("field", field_name)]),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let arr = data
        .get("cameras")
        .and_then(|v| v.as_array())
        .ok_or("Invalid cameras response")?;
    let list: Vec<String> = arr
        .iter()
        .filter_map(|v| v.as_str().map(String::from))
        .collect();
    Ok(list)
}

/// TO: URL for the preview frame image (with cache-bust query). Use as <img src=...>.
pub fn preview_frame_url(
    tournament_url: &str,
    field_name: &str,
    camera_name: &str,
    cache_bust: &str,
) -> String {
    let base = base_url();
    format!(
        "{}/_api/record/preview-frame?tournament={}&field={}&camera_name={}&t={}",
        base,
        urlencoding::encode(tournament_url),
        urlencoding::encode(field_name),
        urlencoding::encode(camera_name),
        urlencoding::encode(cache_bust),
    )
}

/// TO: Fetch preview frame with credentials (for Safari compatibility). Returns Some(jpeg_bytes) on 200, None on 204, Err on failure.
pub async fn fetch_preview_frame(
    tournament_url: &str,
    field_name: &str,
    camera_name: &str,
    cache_bust: &str,
) -> Result<Option<Vec<u8>>, String> {
    let url = preview_frame_url(tournament_url, field_name, camera_name, cache_bust);
    let c = client();
    let r = with_credentials(c.get(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = r.status();
    if status.as_u16() == 204 {
        return Ok(None);
    }
    if !status.is_success() {
        let text = r.text().await.unwrap_or_default();
        return Err(format!("Preview frame: {} {}", status, text));
    }
    let bytes = r.bytes().await.map_err(|e| e.to_string())?.to_vec();
    Ok(Some(bytes))
}

/// Metadata for a single chunk upload (record page).
#[cfg(target_arch = "wasm32")]
#[derive(Clone)]
pub struct RecordChunkMeta {
    pub tournament_url: String,
    pub field: String,
    pub match_id: String,
    pub session_id: String,
    pub chunk_start_timestamp: f64,
    pub recording_session_start_time: f64,
    pub chunk_length_ms: u32,
    pub camera_name: String,
    pub key: Option<String>,
    /// Container for the chunk: "mp4" (H.265/HEVC) or "webm". Backend uses this for file extension and re-encode.
    pub container: String,
    /// `BlobEvent.timeStamp` (DOMHighResTimeStamp, ms).
    pub blob_event_timestamp_ms: f64,
    pub is_init_segment: bool,
}

/// Convert epoch milliseconds to ISO 8601 UTC string (e.g. "2025-02-23T19:30:00.123Z").
#[cfg(target_arch = "wasm32")]
fn epoch_ms_to_iso8601_utc(ms: f64) -> String {
    use chrono::TimeZone;
    chrono::Utc
        .timestamp_millis_opt(ms as i64)
        .single()
        .map(|dt| dt.to_rfc3339())
        .unwrap_or_else(|| ms.to_string())
}

#[cfg(target_arch = "wasm32")]
pub async fn record_upload_chunk(
    meta: &RecordChunkMeta,
    chunk_blob: &web_sys::Blob,
) -> Result<(), String> {
    use wasm_bindgen::JsCast;
    use web_sys::window;

    let form = web_sys::FormData::new().map_err(|_| "FormData::new failed")?;
    form.append_with_str("tournament", &meta.tournament_url)
        .map_err(|_| "append tournament failed")?;
    form.append_with_str("field", &meta.field)
        .map_err(|_| "append field failed")?;
    form.append_with_str("match_id", &meta.match_id)
        .map_err(|_| "append match_id failed")?;
    form.append_with_str("session_id", &meta.session_id)
        .map_err(|_| "append session_id failed")?;
    form.append_with_str(
        "chunk_start_timestamp",
        &epoch_ms_to_iso8601_utc(meta.chunk_start_timestamp),
    )
    .map_err(|_| "append chunk_start_timestamp failed")?;
    form.append_with_str(
        "recording_session_start_time",
        &epoch_ms_to_iso8601_utc(meta.recording_session_start_time),
    )
    .map_err(|_| "append recording_session_start_time failed")?;
    form.append_with_str("chunk_duration", &meta.chunk_length_ms.to_string())
        .map_err(|_| "append chunk_duration failed")?;
    form.append_with_str("camera_name", &meta.camera_name)
        .map_err(|_| "append camera_name failed")?;
    if let Some(ref k) = meta.key {
        form.append_with_str("camera_key", k)
            .map_err(|_| "append key failed")?;
    }
    form.append_with_str("container", &meta.container)
        .map_err(|_| "append container failed")?;
    form.append_with_str(
        "blob_event_timestamp_ms",
        &meta.blob_event_timestamp_ms.to_string(),
    )
    .map_err(|_| "append blob_event_timestamp_ms failed")?;
    form.append_with_str(
        "is_init_segment",
        if meta.is_init_segment { "1" } else { "0" },
    )
    .map_err(|_| "append is_init_segment failed")?;
    form.append_with_blob("chunk", chunk_blob)
        .map_err(|_| "append chunk failed")?;

    let window = window().ok_or("no window")?;
    let url = format!("{}/_api/record/upload-chunk", base());
    let opts = web_sys::RequestInit::new();
    opts.set_method("POST");
    let form_js = wasm_bindgen::JsValue::from(form);
    opts.set_body(form_js.as_ref());

    let request = web_sys::Request::new_with_str_and_init(&url, &opts)
        .map_err(|_| "Request::new_with_str_and_init failed")?;
    let resp = wasm_bindgen_futures::JsFuture::from(window.fetch_with_request(&request))
        .await
        .map_err(|_| "fetch failed")?;
    let resp: web_sys::Response = resp.dyn_into().map_err(|_| "response cast failed")?;
    if !resp.ok() {
        let status = resp.status();
        let text = wasm_bindgen_futures::JsFuture::from(resp.text().map_err(|_| "text() failed")?)
            .await
            .map_err(|_| "text await failed")?;
        let msg = text
            .as_string()
            .unwrap_or_else(|| "Unknown error".to_string());
        return Err(format!("Upload failed (HTTP {status}): {msg}"));
    }
    Ok(())
}

#[cfg(not(target_arch = "wasm32"))]
pub async fn record_upload_chunk(
    _meta: &crate::api::RecordChunkMeta,
    _chunk_blob: &[u8],
) -> Result<(), String> {
    Err("record_upload_chunk only supported on wasm".to_string())
}

#[cfg(not(target_arch = "wasm32"))]
pub struct RecordChunkMeta {
    pub tournament_url: String,
    pub field: String,
    pub match_id: String,
    pub session_id: String,
    pub chunk_start_timestamp: f64,
    pub recording_session_start_time: f64,
    pub chunk_length_ms: u32,
    pub camera_name: String,
    pub key: Option<String>,
    pub container: String,
    pub blob_event_timestamp_ms: f64,
    pub is_init_segment: bool,
}

pub async fn record_finalize(
    tournament_url: &str,
    field_name: &str,
    match_id: &str,
    camera_name: &str,
    key: Option<&str>,
) -> Result<(), String> {
    let c = client();
    let mut body = serde_json::json!({
        "tournament": tournament_url,
        "field": field_name,
        "match_id": match_id,
        "camera_name": camera_name,
    });
    if let Some(k) = key {
        body["camera_key"] = serde_json::json!(k);
    }
    let r = c
        .post(format!("{}/_api/record/finalize", base()))
        .json(&body)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        let text = r.text().await.unwrap_or_default();
        return Err(format!("Finalize failed: {}", text));
    }
    Ok(())
}

pub async fn server_time() -> Result<ServerTimeResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/server-time", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn bracket_setup_data(url: &str) -> Result<BracketSetupResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/bracket-setup-data",
        base(),
        url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn scoreboard_state(
    tournament_url: &str,
    field_name: &str,
) -> Result<ScoreboardStateResponse, String> {
    let c = client();
    let r = with_credentials(
        c.get(format!("{}/_api/scoreboard-state", base()))
            .query(&[("tournament", tournament_url), ("field", field_name)]),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn fetch_bytes(url: &str) -> Result<Vec<u8>, String> {
    let c = client();
    let r = with_credentials(c.get(url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(format!("Failed to fetch bytes: {}", r.status()));
    }
    let bytes = r.bytes().await.map_err(|e| e.to_string())?;
    Ok(bytes.to_vec())
}

pub async fn get_injury(player_id: &str, injury_id: u32) -> Result<PlayerInjury, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/players/{}/injuries/{}",
        base(),
        player_id,
        injury_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn create_injury(
    player_id: &str,
    req: &serde_json::Value,
) -> Result<PlayerInjury, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/players/{}/injuries", base(), player_id))
            .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    response_json(r).await
}

pub async fn update_injury(
    player_id: &str,
    injury_id: u32,
    req: &serde_json::Value,
) -> Result<PlayerInjury, String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/players/{}/injuries/{}",
            base(),
            player_id,
            injury_id
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    response_json(r).await
}

pub async fn delete_injury(player_id: &str, injury_id: u32) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/players/{}/injuries/{}",
        base(),
        player_id,
        injury_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn match_state(tournament_url: &str, match_id: &str) -> Result<Value, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/match-state?match_id={}",
        base(),
        tournament_url,
        match_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

#[allow(dead_code)]
pub async fn set_match_status(
    tournament_url: &str,
    match_id: &str,
    status: &str,
) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({ "match_id": match_id, "status": status });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/{}/match-actions/set-status",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let success = data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        return Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Request failed")
            .to_string());
    }
    Ok(data)
}

pub async fn add_point(
    tournament_url: &str,
    match_id: &str,
    set_number: u32,
    timestamp_ms: Option<u64>,
    stones_at_start: Option<u32>,
) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({
        "match_id": match_id,
        "set_number": set_number,
        "timestamp": timestamp_ms,
        "stones_at_start": stones_at_start,
    });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/{}/match-actions/add-point",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let success = data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        return Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Request failed")
            .to_string());
    }
    Ok(data)
}

pub async fn update_point(
    tournament_url: &str,
    point_id: &str,
    data: &serde_json::Value,
) -> Result<Value, String> {
    let c = client();
    let mut body = serde_json::Map::new();
    body.insert(
        "point_id".to_string(),
        serde_json::Value::String(point_id.to_string()),
    );
    if let Some(obj) = data.as_object() {
        for (k, v) in obj {
            body.insert(k.clone(), v.clone());
        }
    }
    let r = with_credentials(
        c.post(format!(
            "{}/_api/{}/match-actions/update-point",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let resp: Value = response_json(r).await?;
    let success = resp
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        return Err(resp
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Request failed")
            .to_string());
    }
    Ok(resp)
}

pub async fn delete_point(tournament_url: &str, point_id: &str) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({ "point_id": point_id });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/{}/match-actions/delete-point",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn get_point_notes(
    tournament_url: &str,
    match_id: &str,
    point_id: &str,
) -> Result<Value, String> {
    let c = client();
    let url = format!(
        "{}/_api/{}/get-point-notes?match_id={}&point_id={}",
        base(),
        tournament_url,
        urlencoding::encode(match_id),
        urlencoding::encode(point_id),
    );
    let r = with_credentials(c.get(url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

/// Get selection notes for a team and selected player IDs (start-match page).
/// Uses credentials so the backend @login_required sees the session.
pub async fn get_selection_notes(
    tournament_url: &str,
    match_id: &str,
    team: &str,
    player_ids: &str,
) -> Result<Value, String> {
    let c = client();
    let url = format!(
        "{}/_api/{}/get-selection-notes?match_id={}&team={}&player_ids={}",
        base(),
        tournament_url,
        urlencoding::encode(match_id),
        urlencoding::encode(team),
        urlencoding::encode(player_ids),
    );
    let r = with_credentials(c.get(url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn add_point_note(
    tournament_url: &str,
    match_id: &str,
    point_id: &str,
    text: &str,
    target: &str,
    player_id: Option<&str>,
    penalty_type_id: Option<i32>,
) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({
        "match_id": match_id,
        "point_id": point_id,
        "text": text,
        "target": target,
        "player_id": player_id,
        "penalty_type_id": penalty_type_id,
    });
    let r = with_credentials(
        c.post(format!("{}/_api/{}/add-point-note", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let success = data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        return Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Request failed")
            .to_string());
    }
    Ok(data)
}

/// Set the single point note (target=match) for a point. Replaces any existing one. Pass empty text to clear.
pub async fn set_point_note(
    tournament_url: &str,
    match_id: &str,
    point_id: &str,
    text: &str,
) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({
        "match_id": match_id,
        "point_id": point_id,
        "text": text,
    });
    let r = with_credentials(
        c.post(format!("{}/_api/{}/set-point-note", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let success = data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        return Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Request failed")
            .to_string());
    }
    Ok(data)
}

pub async fn update_stones(
    tournament_url: &str,
    match_id: &str,
    stones_remaining: u32,
) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({
        "match_id": match_id,
        "stones_remaining": stones_remaining,
    });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/{}/match-actions/update-stones",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let success = data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        return Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Request failed")
            .to_string());
    }
    Ok(data)
}

pub async fn save_bracket_setup(
    tournament_url: &str,
    brackets: &[crate::types::BracketConfig],
) -> Result<(), String> {
    let c = client();
    let body = serde_json::json!({ "brackets": brackets });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/bracket-setup",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let v: serde_json::Value = response_json(r).await?;
    if v.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
        Ok(())
    } else {
        Err(v
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to save bracket configuration")
            .to_string())
    }
}

/// Upload a single bracket image as raw bytes and return its relative static path.
#[cfg(target_arch = "wasm32")]
pub async fn upload_bracket_image_bytes(
    tournament_url: &str,
    bracket_index: u32,
    filename: &str,
    bytes: bytes::Bytes,
) -> Result<String, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/bracket-upload-bytes",
            base(),
            tournament_url
        ))
        .query(&[
            ("bracket_index", bracket_index.to_string()),
            ("filename", filename.to_string()),
        ])
        .body(bytes),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let v: serde_json::Value = response_json(r).await?;
    if v.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
        if let Some(path) = v.get("path").and_then(|v| v.as_str()) {
            Ok(path.to_string())
        } else {
            Err("Upload succeeded but no path returned".to_string())
        }
    } else {
        Err(v
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to upload bracket image")
            .to_string())
    }
}

/// Fetch YouTube stream start time (ISO UTC) for a video ID or URL. Returns Ok(None) if not available.
pub async fn youtube_stream_start(video_id_or_url: &str) -> Result<Option<String>, String> {
    let video_id =
        extract_youtube_video_id(video_id_or_url).unwrap_or_else(|| video_id_or_url.to_string());
    if video_id.is_empty() {
        return Ok(None);
    }
    let c = client();
    let url = format!("{}/youtube-stream-start", base());
    let r = with_credentials(c.get(&url).query(&[("video_id", video_id.as_str())]))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    let start = data.get("start_time");
    Ok(start.and_then(|v| v.as_str()).map(String::from))
}

fn extract_youtube_video_id(s: &str) -> Option<String> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    // youtu.be/ID
    if let Some(rest) = s.strip_prefix("https://youtu.be/") {
        let id = rest.split(&['?', '&', '#'][..]).next().unwrap_or(rest);
        if id.len() >= 11
            && id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Some(id[..11].to_string());
        }
    }
    if let Some(rest) = s.strip_prefix("http://youtu.be/") {
        let id = rest.split(&['?', '&', '#'][..]).next().unwrap_or(rest);
        if id.len() >= 11
            && id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Some(id[..11].to_string());
        }
    }
    // youtube.com/watch?v=ID or embed/ID or v/ID
    if s.contains("youtube.com") {
        if let Some(v) = s.find("v=") {
            let after = &s[v + 2..];
            let id = after.split(&['&', '#'][..]).next().unwrap_or(after);
            if id.len() >= 11
                && id
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
            {
                return Some(id[..11].to_string());
            }
        }
        for prefix in ["/embed/", "/v/"] {
            if let Some(i) = s.find(prefix) {
                let after = &s[i + prefix.len()..];
                let id = after.split(&['?', '&', '#'][..]).next().unwrap_or(after);
                if id.len() >= 11
                    && id
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
                {
                    return Some(id[..11].to_string());
                }
            }
        }
    }
    // Bare 11-char ID
    if s.len() == 11
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Some(s.to_string());
    }
    None
}

pub async fn update_field(
    tournament_url: &str,
    field_id: u32,
    req: &UpdateFieldRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/tournaments/{}/fields/{}",
            base(),
            tournament_url,
            field_id
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

#[allow(dead_code)]
pub async fn tags_list(tournament_url: &str) -> Result<TagsListResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/tags",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn markdown_page(slug: &str) -> Result<MarkdownPageResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/markdown/{}", base(), slug)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn render_markdown(markdown: &str) -> Result<String, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/render-markdown", base()))
            .json(&serde_json::json!({ "markdown": markdown })),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let res: RenderMarkdownResponse = response_json(r).await?;
    Ok(res.html)
}

pub async fn google_choose_account_type_info() -> Result<GoogleChooseAccountTypeResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/google/choose-account-type", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn google_choose_account_type(
    req: &GoogleChooseAccountTypeRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/google/choose-account-type", base()))
            .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("ok").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn google_complete_profile_info() -> Result<GoogleCompleteProfileResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/google/complete-profile", base())))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn google_complete_profile(req: &GoogleCompleteProfileRequest) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/google/complete-profile", base()))
            .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("ok").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn fetch_schedule_warnings(
    tournament_url: &str,
) -> Result<Vec<crate::types::ScheduleWarning>, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/{}/schedule-warnings",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: crate::types::ScheduleWarningsResponse = response_json(r).await?;
    if data.success {
        Ok(data.warnings)
    } else {
        Err(data
            .error
            .unwrap_or_else(|| "Failed to fetch schedule warnings".to_string()))
    }
}

pub async fn update_match(
    tournament_url: &str,
    match_id: &str,
    req: &UpdateMatchRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/tournaments/{}/matches/{}",
            base(),
            tournament_url,
            match_id
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn force_start_match(
    tournament_url: &str,
    match_id: &str,
    req: &crate::types::ForceStartMatchRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/matches/{}/force-start",
            base(),
            tournament_url,
            match_id
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn update_player_profile(
    player_id: &str,
    req: &UpdatePlayerProfileRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!("{}/_api/players/{}", base(), player_id))
            .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn update_team_profile(
    team_id: &str,
    req: &UpdateTeamProfileRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!("{}/_api/teams/{}", base(), team_id))
            .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

/// Upload player profile photo. Overwrites previous; backend uses predictable path.
pub async fn upload_player_profile_photo(
    player_id: &str,
    bytes: bytes::Bytes,
) -> Result<String, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/players/{}/profile-photo",
            base(),
            player_id
        ))
        .body(bytes),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        data.get("path")
            .and_then(|v| v.as_str())
            .map(String::from)
            .ok_or_else(|| "No path in response".to_string())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Upload failed")
            .to_string())
    }
}

/// Remove player profile photo.
pub async fn delete_player_profile_photo(player_id: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/players/{}/profile-photo",
        base(),
        player_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to remove photo")
            .to_string())
    }
}

/// Upload team profile photo. Overwrites previous; backend uses predictable path.
pub async fn upload_team_profile_photo(
    team_id: &str,
    bytes: bytes::Bytes,
) -> Result<String, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!("{}/_api/teams/{}/profile-photo", base(), team_id))
            .body(bytes),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        data.get("path")
            .and_then(|v| v.as_str())
            .map(String::from)
            .ok_or_else(|| "No path in response".to_string())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Upload failed")
            .to_string())
    }
}

/// Remove team profile photo.
pub async fn delete_team_profile_photo(team_id: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.delete(format!("{}/_api/teams/{}/profile-photo", base(), team_id)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Failed to remove photo")
            .to_string())
    }
}

pub async fn get_my_player_registration(
    tournament_url: &str,
) -> Result<MyPlayerRegistrationResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/registrations/player/me",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_my_player_registration(
    tournament_url: &str,
    req: &UpdatePlayerRegistrationRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/tournaments/{}/registrations/player/me",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn get_my_team_registration(
    tournament_url: &str,
) -> Result<MyTeamRegistrationResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/registrations/team/me",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_my_team_registration(
    tournament_url: &str,
    req: &UpdateTeamRegistrationRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.put(format!(
            "{}/_api/tournaments/{}/registrations/team/me",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn schedule_setup(tournament_url: &str) -> Result<ScheduleSetupResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/schedule-setup",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    if r.status().as_u16() == 403 {
        return Err("Schedule not published".to_string());
    }
    response_json(r).await
}

pub async fn create_match(
    tournament_url: &str,
    req: &CreateMatchRequest,
) -> Result<CreateMatchResponse, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/matches",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

/// Validate a DSL skip-condition expression. Uses the tournaments blueprint route.
pub async fn validate_dsl(
    tournament_url: &str,
    expression: &str,
) -> Result<ValidateDslResponse, String> {
    let c = client();
    let body = serde_json::json!({ "expression": expression });
    let r = with_credentials(
        c.post(format!("{}/_api/{}/validate-dsl", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_match(tournament_url: &str, match_id: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/tournaments/{}/matches/{}",
        base(),
        tournament_url,
        match_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn create_field(
    tournament_url: &str,
    req: &CreateFieldRequest,
) -> Result<CreateFieldResponse, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/fields",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_field(tournament_url: &str, field_id: u32) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/tournaments/{}/fields/{}",
        base(),
        tournament_url,
        field_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn create_tag(
    tournament_url: &str,
    req: &CreateTagRequest,
) -> Result<CreateTagResponse, String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/tags",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_tag(tournament_url: &str, tag_id: u32) -> Result<(), String> {
    let c = client();
    let resp = with_credentials(c.delete(format!(
        "{}/_api/tournaments/{}/tags/{}",
        base(),
        tournament_url,
        tag_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    if resp.status().is_success() {
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
            return Ok(());
        }
        return Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string());
    }
    let text = resp.text().await.unwrap_or_default();
    let err_msg = serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|v| v.get("error").cloned())
        .and_then(|v| v.as_str().map(String::from))
        .unwrap_or_else(|| format!("Delete failed: {}", truncate_error_body(&text, 200)));
    Err(err_msg)
}

pub async fn recompute_schedule(tournament_url: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/recompute-schedule",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

#[allow(dead_code)]
pub async fn update_all_references(tournament_url: &str) -> Result<(), String> {
    let c = client();
    let r = with_credentials(c.post(format!(
        "{}/_api/tournaments/{}/update-all-references",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

#[allow(dead_code)]
pub async fn push_back_matches(tournament_url: &str, req: &PushBackRequest) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/push-back-matches",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn update_tags(tournament_url: &str, req: &UpdateTagsRequest) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/update-tags",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

pub async fn export_schedule(tournament_url: &str) -> Result<ExportScheduleResponse, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/tournaments/{}/export-schedule",
        base(),
        tournament_url
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn import_schedule(
    tournament_url: &str,
    req: &ImportScheduleRequest,
) -> Result<(), String> {
    let c = client();
    let r = with_credentials(
        c.post(format!(
            "{}/_api/tournaments/{}/import-schedule",
            base(),
            tournament_url
        ))
        .json(req),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;

    let data: Value = response_json(r).await?;
    if data.get("success").and_then(|v| v.as_bool()) == Some(true) {
        Ok(())
    } else {
        Err(data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error")
            .to_string())
    }
}

#[derive(serde::Deserialize)]
pub struct GetPenaltyTypesResponse {
    pub penalty_types: Vec<PenaltyType>,
}

#[derive(serde::Deserialize)]
pub struct CreatePenaltyTypeResponse {
    pub success: bool,
    pub penalty_type: PenaltyType,
}

pub async fn get_penalty_types(tournament_url: &str) -> Result<GetPenaltyTypesResponse, String> {
    let c = client();
    let r = c
        .get(format!("{}/_api/{}/penalty-types", base(), tournament_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn create_penalty_type(
    tournament_url: &str,
    name: &str,
    color: Option<&str>,
    desc: Option<&str>,
) -> Result<CreatePenaltyTypeResponse, String> {
    let c = client();
    let body = serde_json::json!({
        "name": name,
        "color": color,
        "desc": desc
    });
    let r = with_credentials(
        c.post(format!("{}/_api/{}/penalty-types", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn update_penalty_type(
    tournament_url: &str,
    pt_id: i32,
    name: Option<&str>,
    color: Option<&str>,
    desc: Option<&str>,
) -> Result<Value, String> {
    let c = client();
    let mut body = serde_json::Map::new();
    if let Some(n) = name {
        body.insert("name".to_string(), serde_json::json!(n));
    }
    if let Some(c_val) = color {
        body.insert("color".to_string(), serde_json::json!(c_val));
    }
    if let Some(d) = desc {
        body.insert("desc".to_string(), serde_json::json!(d));
    }

    let r = with_credentials(
        c.patch(format!(
            "{}/_api/{}/penalty-types/{}",
            base(),
            tournament_url,
            pt_id
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_penalty_type(tournament_url: &str, pt_id: i32) -> Result<Value, String> {
    let c = client();
    let r = with_credentials(c.delete(format!(
        "{}/_api/{}/penalty-types/{}",
        base(),
        tournament_url,
        pt_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn get_player_penalty_history(
    tournament_url: &str,
    player_id: &str,
    match_id: &str,
    point_id: &str,
) -> Result<crate::types::PlayerPenaltyHistoryResponse, String> {
    let c = client();
    let url = format!(
        "{}/_api/{}/players/{}/penalty-history?match_id={}&point_id={}",
        base(),
        tournament_url,
        player_id,
        match_id,
        urlencoding::encode(point_id),
    );
    let r = with_credentials(c.get(&url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn delete_point_note(tournament_url: &str, note_id: &str) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({ "note_id": note_id });
    let r = with_credentials(
        c.post(format!(
            "{}/_api/{}/delete-point-note",
            base(),
            tournament_url
        ))
        .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomps_list(tournament_url: &str) -> Result<Vec<SideCompSummary>, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/{}/sidecomps", base(), tournament_url)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_detail(comp_id: i32) -> Result<SideCompDetail, String> {
    let c = client();
    let r = with_credentials(c.get(format!("{}/_api/sidecomps/{}", base(), comp_id)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_create(
    tournament_url: &str,
    name: &str,
    type_: &str,
    description: Option<&str>,
) -> Result<Value, String> {
    let c = client();
    let body = serde_json::json!({
        "name": name,
        "type": type_,
        "description": description.unwrap_or(""),
    });
    let r = with_credentials(
        c.post(format!("{}/_api/{}/sidecomps", base(), tournament_url))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_update(
    comp_id: i32,
    name: Option<&str>,
    type_: Option<&str>,
    description: Option<&str>,
    registration_open: Option<bool>,
) -> Result<Value, String> {
    let c = client();
    let mut body = serde_json::Map::new();
    if let Some(n) = name {
        body.insert("name".to_string(), serde_json::json!(n));
    }
    if let Some(t) = type_ {
        body.insert("type".to_string(), serde_json::json!(t));
    }
    if let Some(d) = description {
        body.insert("description".to_string(), serde_json::json!(d));
    }
    if let Some(open) = registration_open {
        body.insert("registration_open".to_string(), serde_json::json!(open));
    }
    let r = with_credentials(
        c.patch(format!("{}/_api/sidecomps/{}", base(), comp_id))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_delete(comp_id: i32) -> Result<Value, String> {
    let c = client();
    let r = with_credentials(c.delete(format!("{}/_api/sidecomps/{}", base(), comp_id)))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_register(comp_id: i32) -> Result<Value, String> {
    let c = client();
    let r = with_credentials(c.post(format!(
        "{}/_api/sidecomps/{}/register",
        base(),
        comp_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_deregister(comp_id: i32) -> Result<Value, String> {
    let c = client();
    let r = with_credentials(c.post(format!(
        "{}/_api/sidecomps/{}/deregister",
        base(),
        comp_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_to_register_player_as_to(
    comp_id: i32,
    player_id: &str,
) -> Result<SideCompRegisterPlayerResponse, String> {
    let c = client();
    let body = serde_json::json!({"player_id": player_id});
    let r = with_credentials(
        c.post(format!("{}/_api/sidecomps/{}/register-player-as-to", base(), comp_id))
            .json(&body),
    )
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}

pub async fn sidecomp_eligible_players(comp_id: i32) -> Result<Vec<EligiblePlayer>, String> {
    let c = client();
    let r = with_credentials(c.get(format!(
        "{}/_api/sidecomps/{}/eligible-players",
        base(),
        comp_id
    )))
    .send()
    .await
    .map_err(|e| e.to_string())?;
    response_json(r).await
}
