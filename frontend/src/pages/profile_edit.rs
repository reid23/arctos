use crate::api;
use crate::components::{ChangePasswordCard, ConnectedAppsCard, ImageDraft, PlayerPompfen, ProfileBio, ProfileImageControls, ProfileImageEditor};
use crate::Route;
use dioxus::prelude::*;

#[derive(Clone, Default, PartialEq)]
struct ProfileFields {
    name: String,
    location: String,
    about: String,
    pronouns: String,
    pompfen: Vec<String>,
    phone: String,
    email: String,
    website: String,
}

#[derive(Clone, PartialEq)]
struct ProfileEditData {
    fields: ProfileFields,
    photo: Option<String>,
    banner: Option<String>,
    past_count: u32,
    has_password: bool,
}

fn invalid_field() -> Option<(String, String)> {
    #[cfg(target_arch = "wasm32")]
    {
        use wasm_bindgen::JsCast;
        let document = web_sys::window()?.document()?;
        let field = document.query_selector("#profile-edit-form input:invalid").ok()??
            .dyn_into::<web_sys::HtmlInputElement>().ok()?;
        return Some((field.id(), field.validation_message().unwrap_or_else(|_| "Check this field.".into())));
    }
    #[cfg(not(target_arch = "wasm32"))]
    None
}

fn focus_field(id: String) {
    #[cfg(target_arch = "wasm32")]
    spawn(async move {
        gloo_timers::future::TimeoutFuture::new(0).await;
        crate::appearance::focus_control(&id);
    });
}

#[component]
pub fn ProfileEditPage(account_id: String, team: bool) -> Element {
    let data = use_resource(use_reactive(&(account_id.clone(), team), move |(id, team)| async move {
        let user = api::me().await.map_err(|_| "Log in to edit your profile.".to_string())?;
        if user.id != id || user.user_type != if team { "team" } else { "player" } {
            return Err("You can only edit your own profile.".to_string());
        }
        if team {
            let response = api::team_profile(&id).await?;
            let value = response.team;
            Ok(ProfileEditData {
                fields: ProfileFields { name: value.name, location: value.location.unwrap_or_default(),
                    about: value.about.unwrap_or_default(), email: value.email.unwrap_or_default(),
                    website: value.website.unwrap_or_default(), ..Default::default() },
                photo: value.profile_photo, banner: value.banner_photo,
                past_count: response.past_event_count, has_password: user.has_password,
            })
        } else {
            let response = api::player_profile(&id).await?;
            let value = response.player;
            Ok(ProfileEditData {
                fields: ProfileFields { name: value.name, location: value.location.unwrap_or_default(),
                    about: value.bio.unwrap_or_default(), phone: value.phone.unwrap_or_default(),
                    pronouns: value.pronouns.unwrap_or_default(), pompfen: value.pompfen, ..Default::default() },
                photo: value.profile_photo, banner: value.banner_photo,
                past_count: response.past_event_count, has_password: user.has_password,
            })
        }
    }));
    let result = data.read();
    match &*result {
        None => rsx! { p { role: "status", "Loading profile…" } },
        Some(Err(message)) => rsx! {
            h1 { "Edit profile" }
            p { role: "alert", "{message}" }
            Link { to: Route::Login {}, "Log in" }
        },
        Some(Ok(data)) => rsx! { ProfileEditForm { key: "{team}-{account_id}", account_id, team, data: data.clone() } },
    }
}

#[component]
fn ProfileEditForm(account_id: String, team: bool, data: ProfileEditData) -> Element {
    let nav = use_navigator();
    let mut auth_version = super::layout::use_auth_invalidate();
    let mut fields = use_signal(|| data.fields.clone());
    let photo = use_signal(|| ImageDraft::new(data.photo.clone()));
    let banner = use_signal(|| ImageDraft::new(data.banner.clone()));
    let active = use_signal(|| None::<bool>);
    let busy = use_signal(|| false);
    let mut saving = use_signal(|| false);
    let mut error = use_signal(|| None::<String>);
    let mut preview_only = use_signal(|| false);
    let changed = fields() != data.fields || photo().changed() || banner().changed();
    let profile_route = if team { Route::TeamProfilePage { id: account_id.clone() } }
        else { Route::PlayerProfilePage { id: account_id.clone() } };
    let save_route = profile_route.clone();
    let save_id = account_id.clone();
    let onsubmit = move |event: Event<FormData>| {
        event.prevent_default();
        if saving() || busy() { return; }
        if fields().name.trim().is_empty() {
            error.set(Some("Enter a display name.".into()));
            preview_only.set(false);
            focus_field("profile-edit-name".into());
            return;
        }
        if let Some((id, message)) = invalid_field() {
            error.set(Some(message));
            preview_only.set(false);
            focus_field(id);
            return;
        }
        saving.set(true);
        error.set(None);
        let draft = fields();
        let photo = photo();
        let banner = banner();
        let id = save_id.clone();
        let route = save_route.clone();
        spawn(async move {
            let result: Result<(), String> = async {
                let avatar_bytes = photo.encode(true).await?;
                let banner_bytes = banner.encode(false).await?;
                let details = if team {
                    serde_json::json!({"name": draft.name, "location": draft.location, "about": draft.about,
                        "email": draft.email, "website": draft.website})
                } else {
                    serde_json::json!({"name": draft.name, "location": draft.location, "bio": draft.about, "phone": draft.phone,
                        "pronouns": draft.pronouns, "pompfen": draft.pompfen})
                };
                api::save_profile_edit(if team { "teams" } else { "players" }, &id, &details,
                    avatar_bytes, banner_bytes, photo.is_removed(), banner.is_removed()).await
            }.await;
            match result {
                Ok(()) => { auth_version += 1; nav.push(route); }
                Err(message) => { error.set(Some(message)); saving.set(false); }
            }
        });
    };
    rsx! {
        style { {include_str!("../components/profile_details.css")} }
        style { {include_str!("profile_edit.css")} }
        div { class: if preview_only() { "profile-editor is-preview-only" } else { "profile-editor" },
            form { id: "profile-edit-form", novalidate: true, onsubmit,
                oninput: move |_| { if !saving() { error.set(None); } },
                header { class: "profile-editor-toolbar",
                    button { r#type: "button", class: "btn btn-outline-secondary", disabled: saving() || busy(),
                        onclick: move |_| { nav.push(profile_route.clone()); }, "Cancel" }
                    h1 { "Edit profile" }
                    button { r#type: "submit", class: "btn btn-primary", disabled: saving() || busy() || !changed,
                        if saving() { "Saving…" } else { "Save" } }
                }
                if let Some(message) = error() { p { class: "alert alert-danger profile-edit-error", role: "alert", "{message}" } }
                div { class: "profile-editor-mobile-view",
                    button { r#type: "button", class: "btn btn-outline-secondary", aria_pressed: preview_only(),
                        onclick: move |_| preview_only.toggle(),
                        if preview_only() { "Edit details" } else { "Preview profile" }
                    }
                }
                div { class: "profile-editor-grid",
                    section { id: "profile-edit-preview", class: "profile-edit-preview", aria_label: "Profile preview",
                        p { class: "profile-preview-label", "Preview" }
                        div { class: "profile-page",
                            div { class: "profile-detail-header",
                                ProfileImageEditor { draft: banner, avatar: false, active, busy, disabled: saving() }
                                div { class: "profile-header-body",
                                    div { class: "profile-avatar-row",
                                        div { class: "profile-edit-avatar", ProfileImageEditor { draft: photo, avatar: true, active, busy, disabled: saving() } }
                                    }
                                    ProfileImageControls { banner, photo, active, busy, disabled: saving() }
                                    div { class: "profile-identity",
                                        h2 { id: "profile-preview-name", class: "profile-preview-name", if fields().name.trim().is_empty() { "Your name" } else { "{fields().name}" } }
                                        p { class: "profile-account-name", "@{account_id}"
                                            if !team && !fields().pronouns.trim().is_empty() {
                                                span { class: "profile-pronouns", " · {fields().pronouns}" }
                                            }
                                        }
                                    }
                                    div { class: "profile-meta",
                                        if !fields().location.trim().is_empty() {
                                            p { class: "profile-location", i { class: "fas fa-map-marker-alt", aria_hidden: "true" } "{fields().location}" }
                                        }
                                        dl { class: "profile-event-stat", dt { "Past events" } dd { "{data.past_count}" } }
                                        if !team { PlayerPompfen { selected: fields().pompfen } }
                                    }
                                }
                            }
                            div { class: "profile-preview-tabs", aria_hidden: "true", span { "Information" } span { "History" } }
                            div { class: "profile-preview-content",
                                if team && !fields().website.trim().is_empty() { p { strong { "Website: " } span { class: "profile-preview-link", "{fields().website}" } } }
                                if team && !fields().email.trim().is_empty() { p { strong { "Email: " } "{fields().email}" } }
                                if !fields().about.trim().is_empty() {
                                    ProfileBio { source: fields().about, preview: true }
                                } else { p { class: "text-muted", "No profile details added yet." } }
                            }
                        }
                    }
                    section { class: "profile-edit-fields", aria_label: "Profile details",
                        ProfileField { state: fields, field: Field::Name, label: if team { "Team name" } else { "Display name" }, required: true, disabled: saving() }
                        if !team { ProfileField { state: fields, field: Field::Pronouns, label: "Pronouns", help: "Optional. Shown on your profile.", disabled: saving() } }
                        ProfileField { state: fields, field: Field::Location, label: "Location", disabled: saving() }
                        if !team { PlayerPompfen { selected: fields().pompfen, disabled: saving(), onchange: move |value| fields.write().pompfen = value } }
                        ProfileField { state: fields, field: Field::About, label: if team { "About" } else { "Bio" }, multiline: true,
                            help: "Markdown supported: **bold**, *italic*, headings, lists, and [links](https://example.com).", disabled: saving() }
                        if team {
                            ProfileField { state: fields, field: Field::Website, label: "Website", input_type: "url", disabled: saving() }
                            ProfileField { state: fields, field: Field::Email, label: "Email", input_type: "email", disabled: saving() }
                        } else {
                            ProfileField { state: fields, field: Field::Phone, label: "Phone number", input_type: "tel", help: "Only you can see this.", disabled: saving() }
                        }
                    }
                }
            }
            details { class: "profile-account-settings",
                summary { "Account settings" }
                if data.has_password { ChangePasswordCard {} }
                ConnectedAppsCard {}
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Field { Name, Location, About, Pronouns, Phone, Email, Website }

impl Field {
    fn id(self) -> &'static str {
        match self { Self::Name => "profile-edit-name", Self::Location => "profile-edit-location",
            Self::About => "profile-edit-about", Self::Pronouns => "profile-edit-pronouns", Self::Phone => "profile-edit-phone", Self::Email => "profile-edit-email", Self::Website => "profile-edit-website" }
    }
    fn get(self, fields: &ProfileFields) -> &str {
        match self { Self::Name => &fields.name, Self::Location => &fields.location, Self::About => &fields.about, Self::Pronouns => &fields.pronouns,
            Self::Phone => &fields.phone, Self::Email => &fields.email, Self::Website => &fields.website }
    }
    fn set(self, fields: &mut ProfileFields, value: String) {
        *match self { Self::Name => &mut fields.name, Self::Location => &mut fields.location, Self::About => &mut fields.about, Self::Pronouns => &mut fields.pronouns,
            Self::Phone => &mut fields.phone, Self::Email => &mut fields.email, Self::Website => &mut fields.website } = value;
    }
    fn limit(self) -> usize {
        match self { Self::Name | Self::Location => 100, Self::Pronouns => 64, Self::Phone => 20, Self::Email => 255, Self::Website => 200, Self::About => 20000 }
    }
}

#[component]
fn ProfileField(mut state: Signal<ProfileFields>, field: Field, label: String,
    #[props(default = "text".to_string())] input_type: String,
    #[props(default)] multiline: bool, #[props(default)] required: bool,
    #[props(default)] disabled: bool, #[props(default)] help: String) -> Element {
    let id = field.id();
    let value = field.get(&state()).to_string();
    rsx! {
        div { class: "profile-edit-field",
            label { class: "form-label", r#for: id, "{label}" }
            if multiline {
                textarea { id, class: "form-control", rows: "5", value, disabled,
                    aria_describedby: (!help.is_empty()).then(|| format!("{id}-help")),
                    oninput: move |event| field.set(&mut state.write(), event.value()) }
            } else {
                input { id, class: "form-control", r#type: input_type, value, required, disabled, maxlength: field.limit(),
                    aria_describedby: (!help.is_empty()).then(|| format!("{id}-help")),
                    oninput: move |event| field.set(&mut state.write(), event.value()) }
            }
            if !help.is_empty() { p { id: "{id}-help", class: "form-text", "{help}" } }
        }
    }
}
