use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct User {
    pub id: String,
    pub name: String,
    #[serde(rename = "type")]
    pub user_type: String,
    /// True if the account has a password set (can change password). False for Google-only accounts.
    #[serde(default)]
    pub has_password: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Tournament {
    pub url: String,
    pub name: String,
    pub start_date: String,
    pub end_date: Option<String>,
    pub location: Option<String>,
    pub published: bool,
    pub n_max_teams: Option<u32>,
    #[serde(default)]
    pub schedule_published: bool,
    #[serde(default)]
    pub registration_open: bool,
    #[serde(default)]
    pub team_registration_open: bool,
    #[serde(default)]
    pub player_registration_open: bool,
    #[serde(default)]
    pub bracket: bool,
    pub about: Option<String>,
    pub team_reg_fee: Option<f64>,
    pub player_reg_fee: Option<f64>,
    pub max_team_size_roster: Option<u32>,
    pub max_team_size_field: Option<u32>,
    pub terms_link: Option<String>,
    #[serde(default)]
    pub waiver_required: bool,
    pub waiver_filepath: Option<String>,
    pub waiver_sha256: Option<String>,
    pub head_refs_allowed_list: Option<String>,
    #[serde(default)]
    pub head_refs_allow_reffing_teams: bool,
    #[serde(default)]
    pub head_refs_allow_anyone: bool,
    /// When set, this tournament is part of a league; registration is via the league.
    pub league: Option<LeagueRef>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LeagueRef {
    pub league_url: String,
    pub name: String,
    #[serde(default)]
    pub registration_open: bool,
    #[serde(default)]
    pub team_registration_open: bool,
    #[serde(default)]
    pub player_registration_open: bool,
    pub team_reg_fee: Option<f64>,
    pub player_reg_fee: Option<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentsResponse {
    pub tournaments: Vec<Tournament>,
    pub team_counts: std::collections::HashMap<String, u32>,
    pub user_reg_status: std::collections::HashMap<String, UserRegStatus>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct UserRegStatus {
    #[serde(rename = "type")]
    pub reg_type: String,
    pub status: String,
    pub paid: bool,
    pub amount_paid: f64,
    #[serde(default)]
    pub waiver_required: bool,
    #[serde(default)]
    pub waiver_status: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CheckUsernameResponse {
    pub available: bool,
    pub message: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct TeamWithCount {
    pub team_id: String,
    pub team_name: String,
    pub pseudonym: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
    pub player_count: u32,
    pub registered_at: Option<String>,
    pub profile_photo: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UnattachedPlayer {
    pub player_id: String,
    pub player_name: String,
    pub jersey_number: Option<String>,
    pub jersey_name: Option<String>,
    pub registered_at: Option<String>,
    pub profile_photo: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct LeagueInfo {
    pub league_url: String,
    pub name: String,
    pub about: Option<String>,
    pub team_reg_fee: Option<f64>,
    pub player_reg_fee: Option<f64>,
    #[serde(default)]
    pub registration_open: bool,
    #[serde(default)]
    pub team_registration_open: bool,
    #[serde(default)]
    pub player_registration_open: bool,
    #[serde(default)]
    pub published: bool,
    pub terms_link: Option<String>,
    #[serde(default)]
    pub waiver_required: bool,
    pub waiver_filepath: Option<String>,
    pub waiver_sha256: Option<String>,
    pub n_max_teams: Option<u32>,
    pub max_team_size_roster: Option<u32>,
    pub max_team_size_field: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LeaguesListResponse {
    pub leagues: Vec<LeagueInfo>,
    pub team_counts: std::collections::HashMap<String, u32>,
    pub user_reg_status: std::collections::HashMap<String, UserRegStatus>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LeagueDetailResponse {
    pub league: LeagueInfo,
    pub events: Vec<Tournament>,
    pub teams_with_counts: Vec<TeamWithCount>,
    pub unattached_players: Vec<UnattachedPlayer>,
    pub to_entries: Vec<ToEntry>,
    pub is_current_team_registered: bool,
    pub is_current_player_registered: bool,
    #[serde(default)]
    pub penalty_types: Vec<PenaltyType>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LeagueResultsResponse {
    pub league: LeagueInfo,
    pub teams: Vec<TeamResultRow>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentDetailResponse {
    pub tournament: Tournament,
    pub teams_with_counts: Vec<TeamWithCount>,
    pub unattached_players: Vec<UnattachedPlayer>,
    pub to_entries: Vec<ToEntry>,
    pub is_current_team_registered: bool,
    pub is_current_player_registered: bool,
    #[serde(default)]
    pub penalty_types: Vec<PenaltyType>,
    #[serde(default)]
    pub manual_footage_uploads_enabled: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentManageResponse {
    pub tournament: Tournament,
    pub search_query: String,
    pub search_type: String,
    pub team_registrations: Vec<ManageTeamRegistration>,
    pub player_registrations: Vec<ManagePlayerRegistration>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ManageTeamRegistration {
    pub registration: ManageTeamRegistrationData,
    pub team: ManageTeamInfo,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ManageTeamRegistrationData {
    pub id: u32,
    pub team: String,
    pub pseudonym: String,
    #[serde(default)]
    pub shortname: Option<String>,
    pub status: String,
    pub paid: bool,
    pub amount_paid: f64,
    pub registered_at: Option<String>,
    pub paid_at: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ManageTeamInfo {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ManagePlayerRegistration {
    pub registration: ManagePlayerRegistrationData,
    pub player: ManagePlayerInfo,
    pub team: Option<ManageTeamInfo>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ManagePlayerRegistrationData {
    pub id: u32,
    pub player: String,
    pub team: Option<String>,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
    pub status: String,
    pub paid: bool,
    pub amount_paid: f64,
    pub registered_at: Option<String>,
    pub paid_at: Option<String>,
    #[serde(default)]
    pub waiver_required: bool,
    #[serde(default)]
    pub waiver_status: Option<String>,
    pub waiver_legal_name_signature: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ManagePlayerInfo {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentInvitationsResponse {
    pub tournament: Tournament,
    pub team_registration: InvitationTeamRegistration,
    pub current_team_size: u32,
    pub invitations: Vec<InvitationItem>,
    pub team_roster: Vec<RosterItem>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InvitationTeamRegistration {
    pub id: u32,
    pub pseudonym: String,
    #[serde(default)]
    pub shortname: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InvitationItem {
    pub registration: InvitationRegistration,
    pub player: InvitationPlayer,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InvitationRegistration {
    pub id: u32,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InvitationPlayer {
    pub id: String,
    pub name: String,
    pub profile_photo: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RosterItem {
    pub registration: RosterRegistration,
    pub player: InvitationPlayer,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RosterRegistration {
    pub id: u32,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
    pub status: String,
    pub paid: bool,
    pub amount_paid: f64,
}

/// Canvas bracket payload returned by ``GET /tournaments/:url/bracket``.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct BracketResponse {
    pub tournament: Tournament,
    #[serde(default)]
    pub is_to: bool,
    #[serde(default)]
    pub team_options: Vec<TeamOption>,
    #[serde(default)]
    pub tags: Vec<TagSetupData>,
    #[serde(default)]
    pub matches: Vec<BracketMatchData>,
    #[serde(default)]
    pub texts: Vec<BracketTextData>,
    #[serde(default)]
    pub labeled_teams: Vec<BracketLabeledTeamData>,
    #[serde(default)]
    pub images: Vec<BracketImageData>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct BracketTextData {
    pub id: String,
    #[serde(default)]
    pub text: String,
    pub x_pos: f64,
    pub y_pos: f64,
    #[serde(default = "default_text_size")]
    pub size: f64,
}

fn default_text_size() -> f64 {
    18.0
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct BracketLabeledTeamData {
    pub id: String,
    /// Short caption (max 50). Shown alone until the team resolves.
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub team: String,
    /// ``"LABEL"`` or ``"NET"``.
    #[serde(default = "default_port_label")]
    pub kind: String,
    pub x_pos: f64,
    pub y_pos: f64,
    #[serde(default)]
    pub team_id: Option<String>,
    #[serde(default)]
    pub display_text: String,
    #[serde(default)]
    pub profile_photo: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
    /// True when ``team`` has resolved to a concrete registered team.
    #[serde(default)]
    pub resolved: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct BracketImageData {
    pub id: String,
    pub image: String,
    pub x_pos: f64,
    pub y_pos: f64,
    pub width: f64,
    pub height: f64,
}

/// One playable match plus optional canvas placement.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct BracketMatchData {
    pub uuid: String,
    pub name: String,
    pub team1: Option<String>,
    pub team2: Option<String>,
    pub team1_name: String,
    pub team2_name: String,
    #[serde(default)]
    pub team1_shortname: Option<String>,
    #[serde(default)]
    pub team2_shortname: Option<String>,
    #[serde(default)]
    pub team1_photo: Option<String>,
    #[serde(default)]
    pub team2_photo: Option<String>,
    pub team1_initial: Option<String>,
    pub team2_initial: Option<String>,
    pub status: String,
    pub match_winner: Option<String>,
    #[serde(default)]
    pub schedule_type: Option<String>,
    pub placement: Option<BracketPlacementData>,
}

/// Layout + port mode for a match on the bracket canvas.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct BracketPlacementData {
    pub x_pos: Option<f64>,
    pub y_pos: Option<f64>,
    #[serde(default = "default_bracket_width")]
    pub width: f64,
    #[serde(default = "default_bracket_height")]
    pub height: f64,
    /// ``"LABEL"`` or ``"NET"``.
    #[serde(default = "default_port_label")]
    pub team1: String,
    /// ``"LABEL"`` or ``"NET"``.
    #[serde(default = "default_port_label")]
    pub team2: String,
    /// When true, team2 is drawn above team1 (inputs swapped vertically).
    #[serde(default)]
    pub inputs_flipped: bool,
    #[serde(default)]
    pub placed: bool,
}

fn default_bracket_width() -> f64 {
    280.0
}
fn default_bracket_height() -> f64 {
    100.0
}
fn default_port_label() -> String {
    "LABEL".to_string()
}

impl BracketPlacementData {
    pub fn is_placed(&self) -> bool {
        self.placed || (self.x_pos.is_some() && self.y_pos.is_some())
    }

    pub fn is_net_team1(&self) -> bool {
        self.team1.eq_ignore_ascii_case("NET")
    }

    pub fn is_net_team2(&self) -> bool {
        self.team2.eq_ignore_ascii_case("NET")
    }
}

// Legacy image-bracket types (bracket-setup page)
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BracketItem {
    pub name: String,
    pub image: String,
    pub teams: Vec<BracketTeamEntry>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BracketTeamEntry {
    pub team_info: Option<BracketTeamInfo>,
    pub x: i32,
    pub y: i32,
    pub halign: String,
    pub valign: String,
    pub size: i32,
    pub is_reference: bool,
    pub is_tag: bool,
    pub match_name: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BracketTeamInfo {
    pub id: Option<String>,
    pub pseudonym: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
    pub profile_photo: Option<String>,
    pub display_text: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ToEntry {
    pub id: i32,
    pub user_id: String,
    pub user_type: String,
    #[serde(default)]
    pub user_name: String,
    #[serde(default)]
    pub is_current_user: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct TeamOption {
    pub id: String,
    pub pseudonym: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
    #[serde(default)]
    pub profile_photo: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PointData {
    pub uuid: String,
    pub set_number: Option<u32>,
    pub winner: Option<String>,
    pub rerolled: bool,
    pub stamp: Option<String>,
    pub end_stamp: Option<String>,
    pub stones_at_start: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamResultRow {
    pub id: String,
    pub pseudonym: String,
    #[serde(default)]
    pub shortname: Option<String>,
    pub profile_photo: Option<String>,
    pub matches_won: u32,
    pub matches_lost: u32,
    pub points_won: u32,
    pub points_lost: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResultsResponse {
    pub tournament: Tournament,
    pub teams: Vec<TeamResultRow>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SetScore {
    pub set_number: u32,
    pub team1_points: u32,
    pub team2_points: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamMatchDetail {
    pub uuid: String,
    pub name: String,
    pub team1_name: String,
    pub team2_name: String,
    pub match_winner: Option<String>,
    /// Which side this team played: "TEAM1" or "TEAM2"
    pub your_side: Option<String>,
    pub sets: Vec<SetScore>,
    #[serde(default)]
    pub ribbon: bool,
    /// Tournament URL (event) for this match; present when matches span multiple events (e.g. league).
    #[serde(default)]
    pub event: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamMatchesResponse {
    pub matches: Vec<TeamMatchDetail>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BracketTeamConfig {
    pub team: String,
    pub x: i32,
    pub y: i32,
    pub halign: Option<String>,
    pub valign: Option<String>,
    pub size: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BracketConfig {
    pub name: String,
    pub image: String,
    #[serde(default)]
    pub teams: Vec<BracketTeamConfig>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BracketSetupResponse {
    pub tournament: Tournament,
    #[serde(default)]
    pub brackets: Vec<BracketConfig>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct MatchDetailData {
    pub uuid: String,
    pub name: String,
    pub field: Option<String>,
    pub team1: Option<String>,
    pub team2: Option<String>,
    pub team1_name: String,
    pub team2_name: String,
    #[serde(default)]
    pub team1_shortname: Option<String>,
    #[serde(default)]
    pub team2_shortname: Option<String>,
    #[serde(default)]
    pub team1_photo: Option<String>,
    #[serde(default)]
    pub team2_photo: Option<String>,
    pub team1_initial: Option<String>,
    pub team2_initial: Option<String>,
    pub status: String,
    pub nominal_start_time: Option<String>,
    pub confirmed_start_time: Option<String>,
    pub completed_time: Option<String>,
    pub set_type: Option<String>,
    pub stones_per_set: Option<u32>,
    pub stones_remaining: Option<u32>,
    pub match_winner: Option<String>,
    pub schedule_type: Option<String>,
    pub nominal_length: Option<u32>,
    pub previous_match: Option<String>,
    #[serde(rename = "refs", default)]
    pub r#refs: Option<String>,
    #[serde(rename = "refs_initial", default)]
    pub r#refs_initial: Option<String>,
    /// Refs by display name (pseudonym), like team1_name/team2_name.
    #[serde(rename = "refs_display", default)]
    pub refs_display: Option<String>,
    pub ribbon: bool,
    pub skip_condition: Option<String>,
    pub nsets: Option<u32>,
    pub initial_notes: Option<String>,
    pub final_notes: Option<String>,
}

/// Per-point in-video start time. Includes point_uuid so cameras can have different point sets.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PointTimestamp {
    /// Point UUID; None for legacy data (array of numbers).
    pub point_uuid: Option<String>,
    pub in_video_start: f64,
}

fn deserialize_point_timestamps<'de, D>(d: D) -> Result<Option<Vec<PointTimestamp>>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    use serde::Deserialize;
    let opt: Option<serde_json::Value> = Option::deserialize(d)?;
    let Some(val) = opt else {
        return Ok(None);
    };
    let arr = match &val {
        serde_json::Value::Array(a) => a.clone(),
        _ => return Ok(None),
    };
    let mut out = Vec::new();
    for item in arr {
        match item {
            serde_json::Value::Number(n) => {
                let t = n.as_f64().unwrap_or(0.0);
                out.push(PointTimestamp {
                    point_uuid: None,
                    in_video_start: t,
                });
            }
            serde_json::Value::Object(o) => {
                let pt: PointTimestamp = serde_json::from_value(serde_json::Value::Object(o))
                    .map_err(serde::de::Error::custom)?;
                out.push(pt);
            }
            _ => {}
        }
    }
    Ok(Some(out))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CameraData {
    pub index: u32,
    pub url: Option<String>,
    pub stream_start_time: Option<String>,
    #[serde(rename = "type")]
    pub camera_type: String,
    pub video_path: Option<String>,
    pub camera_id: Option<String>,
    pub session_id: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub source_type: Option<String>,
    #[serde(default)]
    pub time_world: Option<Vec<String>>,
    #[serde(default)]
    pub time_video: Option<Vec<f64>>,
    #[serde(default, deserialize_with = "deserialize_point_timestamps")]
    pub point_timestamps: Option<Vec<PointTimestamp>>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FieldOption {
    pub id: u32,
    pub name: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct UserUploadPlanningField {
    pub id: u32,
    pub name: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct UserUploadPlanningPoint {
    pub uuid: String,
    pub index: u32,
    pub stamp: Option<String>,
    pub end_stamp: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct UserUploadPlanningMatch {
    pub uuid: String,
    pub name: String,
    pub field_name: String,
    #[serde(default)]
    pub points: Vec<UserUploadPlanningPoint>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct UserUploadPlanningResponse {
    pub field: UserUploadPlanningField,
    #[serde(default)]
    pub matches: Vec<UserUploadPlanningMatch>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UserUploadedCameraRow {
    pub uuid: String,
    pub match_uuid: String,
    pub match_name: String,
    pub field_name: String,
    pub camera_name: String,
    pub status: String,
    pub user: Option<String>,
    pub world_start_timestamp: Option<String>,
    pub link: Option<String>,
    pub file: Option<String>,
    pub uploaded_by_user_id: Option<String>,
    pub uploaded_by_user_type: Option<String>,
    pub manifest_only: Option<bool>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UserUploadedCamerasResponse {
    pub cameras: Vec<UserUploadedCameraRow>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PenaltyType {
    pub id: i32,
    pub name: String,
    pub color: String,
    /// Backend may send null when not set.
    #[serde(default)]
    pub desc: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerPenaltyHistoryItem {
    pub penalty_type_name: String,
    pub match_name: String,
    pub point_label: String,
    pub date: String,
    pub is_current_match: bool,
    #[serde(default)]
    pub is_current_point: bool,
    pub note_uuid: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerPenaltyHistoryResponse {
    pub penalties: Vec<PlayerPenaltyHistoryItem>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MatchNoteData {
    pub text: String,
    pub target: String,
    pub player_id: Option<String>,
    pub player_name: Option<String>,
    pub player_display: Option<String>,
    pub team_id: Option<String>,
    pub created_at: Option<String>,
    pub penalty_type_id: Option<i32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MatchPlayerForNotes {
    pub player_id: String,
    pub name: String,
    pub display: String,
    #[serde(default)]
    pub profile_photo: Option<String>,
    #[serde(default)]
    pub team_side: Option<String>,
    #[serde(default)]
    pub in_this_match: bool,
    #[serde(default)]
    pub penalty_counts: std::collections::HashMap<String, i32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MatchDetailResponse {
    #[serde(rename = "match")]
    pub match_data: MatchDetailData,
    pub points: Vec<PointData>,
    pub available_cameras: Vec<CameraData>,
    pub camera_url: Option<String>,
    pub match_notes: Vec<MatchNoteData>,
    pub point_notes_map: std::collections::HashMap<String, Vec<MatchNoteData>>,
    pub is_head_ref: bool,
    #[serde(default)]
    pub can_retry_finalization: bool,
    #[serde(default)]
    pub can_start: bool,
    #[serde(default)]
    pub block_reasons: Vec<String>,
    #[serde(default)]
    pub why_sections: Option<WhySections>,
    #[serde(default)]
    pub conflicting_match: Option<ConflictingMatchInfo>,
    #[serde(default)]
    pub match_players: Vec<MatchPlayerForNotes>,
    #[serde(default)]
    pub penalty_types: Vec<PenaltyType>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct WhySections {
    #[serde(default)]
    pub match_ready: MatchReadySection,
    #[serde(default)]
    pub conflicts: Vec<String>,
    #[serde(default)]
    pub ref_permissions: RefPermissionsSection,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct MatchReadySection {
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub reasons: Vec<String>,
    #[serde(default)]
    pub blocks_start: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ConflictingMatchInfo {
    pub uuid: String,
    pub name: String,
    pub team1_name: String,
    pub team2_name: String,
    #[serde(default)]
    pub team1_shortname: Option<String>,
    #[serde(default)]
    pub team2_shortname: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ForceStartMatchRequest {
    pub team1: String,
    pub team2: String,
    pub refs: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflicting_match_action: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflicting_match_winner: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RefPermissionsSection {
    #[serde(default)]
    pub who_allowed: Vec<String>,
    #[serde(default)]
    pub current_user: Vec<String>,
    #[serde(default)]
    pub is_ok: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StartMatchResponse {
    pub tournament: Tournament,
    pub match_info: StartMatchInfo,
    pub team1_players: Vec<StartMatchPlayer>,
    pub team2_players: Vec<StartMatchPlayer>,
    pub all_players: Vec<StartMatchPlayer>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StartMatchInfo {
    pub uuid: String,
    pub name: String,
    pub field: Option<String>,
    pub set_type: Option<String>,
    pub refs: Option<String>,
    pub team1_name: String,
    pub team2_name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct StartMatchPlayer {
    pub id: String,
    pub name: String,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
    pub team: Option<String>,
    pub paid: bool,
    #[serde(default)]
    pub injuries: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StartMatchRequest {
    pub match_id: String,
    pub team1_players: Vec<String>,
    pub team2_players: Vec<String>,
    pub match_notes: String,
    pub stones_per_set: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StartMatchPostResponse {
    pub match_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FinalizeMatchResponse {
    pub tournament: Tournament,
    pub match_info: FinalizeMatchInfo,
    pub points: Vec<FinalizePoint>,
    pub point_notes_map: std::collections::HashMap<String, Vec<FinalizeNote>>,
    pub stones_elapsed_map: std::collections::HashMap<String, u32>,
    pub team1_score: u32,
    pub team2_score: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FinalizeMatchInfo {
    pub uuid: String,
    pub name: String,
    pub team1_name: String,
    pub team2_name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FinalizePoint {
    pub uuid: String,
    pub set_number: Option<u32>,
    pub winner: Option<String>,
    pub rerolled: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FinalizeNote {
    pub text: Option<String>,
    pub target: Option<String>,
    pub player_id: Option<String>,
    pub player_name: Option<String>,
    pub player_display: Option<String>,
    pub team_id: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FinalizeMatchRequest {
    pub match_id: String,
    pub match_winner: String,
    pub final_notes: String,
    pub team1_signature: Option<String>,
    pub team2_signature: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FinalizeMatchPostResponse {
    pub ok: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerListItem {
    pub id: String,
    pub name: String,
    pub profile_photo: Option<String>,
    pub location: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayersListResponse {
    pub players: Vec<PlayerListItem>,
    pub page: u32,
    pub total_pages: u32,
    pub total: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerProfileData {
    pub id: String,
    pub name: String,
    pub profile_photo: Option<String>,
    pub phone: Option<String>,
    pub location: Option<String>,
    pub bio: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerInjury {
    pub id: u32,
    pub message: String,
    pub stamp: Option<String>,
    pub active: bool,
    pub show: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerProfileResponse {
    pub player: PlayerProfileData,
    pub registrations: Vec<PlayerRegItem>,
    #[serde(default)]
    pub injuries: Vec<PlayerInjury>,
    #[serde(default)]
    pub player_notes: Vec<PlayerNoteItem>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerRegItem {
    pub event: String,
    pub team: Option<String>,
    pub team_pseudonym: Option<String>,
    #[serde(default)]
    pub team_shortname: Option<String>,
    pub status: String,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
    #[serde(default)]
    pub paid: bool,
    #[serde(default)]
    pub amount_paid: f64,
    #[serde(default)]
    pub waiver_required: bool,
    #[serde(default)]
    pub waiver_status: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerNoteItem {
    pub created_at: Option<String>,
    pub text: String,
    pub point_index: String,
    #[serde(rename = "match")]
    pub match_info: Option<NoteMatchInfo>,
    #[serde(default)]
    pub penalty_type_name: Option<String>,
    #[serde(default)]
    pub penalty_type_color: Option<String>,
    #[serde(default)]
    pub penalty_type_desc: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NoteMatchInfo {
    pub event: String,
    pub uuid: String,
    pub name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamListItem {
    pub id: String,
    pub name: String,
    pub profile_photo: Option<String>,
    pub location: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamsListResponse {
    pub teams: Vec<TeamListItem>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamProfileData {
    pub id: String,
    pub name: String,
    pub profile_photo: Option<String>,
    pub location: Option<String>,
    pub email: Option<String>,
    pub website: Option<String>,
    pub about: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamProfileResponse {
    pub team: TeamProfileData,
    pub registrations: Vec<TeamRegItem>,
    #[serde(default)]
    pub team_notes: Vec<TeamNoteItem>,
    #[serde(default)]
    pub tournament_players: std::collections::HashMap<String, Vec<TournamentPlayerItem>>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TeamRegItem {
    pub event: String,
    pub pseudonym: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
    pub status: String,
    pub paid: bool,
    pub amount_paid: f64,
    pub start_date: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamNoteItem {
    pub created_at: Option<String>,
    pub text: String,
    pub point_index: String,
    #[serde(rename = "match")]
    pub match_info: NoteMatchInfo,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentPlayerItem {
    pub registration: TournamentPlayerRegistration,
    pub player: Option<TournamentPlayerInfo>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentPlayerRegistration {
    pub player: String,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TournamentPlayerInfo {
    pub id: String,
    pub name: String,
    pub profile_photo: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StoneFile {
    pub filename: String,
    pub filename_encoded: String,
    pub display_name: String,
    pub sort_order: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StonesResponse {
    pub stones: Vec<StoneFile>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ServerTimeResponse {
    pub server_time: f64,
    pub timestamp: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ScoreboardStateResponse {
    pub has_active_match: bool,
    pub match_id: Option<String>,
    pub team1_name: Option<String>,
    pub team2_name: Option<String>,
    pub team1_photo: Option<String>,
    pub team2_photo: Option<String>,
    pub scores_by_set:
        Option<std::collections::HashMap<String, std::collections::HashMap<String, u32>>>,
    pub sets: Option<Vec<u32>>,
    pub stones_info: Option<StonesInfo>,
    #[serde(default)]
    pub points_for_stones: Option<Vec<ScoreboardPointForStones>>,
    pub prev_match: Option<PrevNextMatch>,
    pub next_match: Option<PrevNextMatch>,
    pub timestamp: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StonesInfo {
    pub stones_per_set: u32,
    pub stones_remaining: Option<u32>,
}

/// Point data for live stones computation on scoreboard (STONES matches).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ScoreboardPointForStones {
    pub stamp: Option<String>,
    pub end_stamp: Option<String>,
    pub stones_at_start: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PrevNextMatch {
    pub team1_name: String,
    pub team2_name: String,
    pub team1_photo: Option<String>,
    pub team2_photo: Option<String>,
    pub winner: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TagListItem {
    pub id: u32,
    pub name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TagsListResponse {
    pub tags: Vec<TagListItem>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MarkdownPageResponse {
    pub title: String,
    pub html: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RenderMarkdownResponse {
    pub html: String,
    #[serde(default)]
    pub css: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GoogleChooseAccountTypeResponse {
    pub email: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GoogleCompleteProfileResponse {
    pub email: String,
    pub user_type: String,
    pub suggested_name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GoogleChooseAccountTypeRequest {
    pub user_type: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GoogleCompleteProfileRequest {
    pub username: String,
    pub display_name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdateFieldRequest {
    pub name: String,
    pub camera_urls: Vec<String>,
    /// Per-camera stream start times (ISO UTC). None = no change; Some(None) = clear; Some(Some(s)) = set.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream_start_times: Option<Vec<Option<String>>>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdateMatchRequest {
    // `name` is intentionally absent: match names are immutable after creation.
    pub field: Option<String>,
    pub schedule_type: Option<String>,
    pub length: Option<u32>,
    pub start_time: Option<String>,
    pub previous_match_id: Option<String>,
    pub refs: Option<Vec<String>>,
    pub team1: Option<String>,
    pub team2: Option<String>,
    pub set_type: Option<String>,
    pub nsets: Option<u32>,
    pub stones_per_set: Option<u32>,
    pub ribbon: Option<bool>,
    pub skip_condition: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdatePlayerProfileRequest {
    pub name: Option<String>,
    pub phone: Option<String>,
    pub location: Option<String>,
    pub bio: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdateTeamProfileRequest {
    pub name: Option<String>,
    pub location: Option<String>,
    pub email: Option<String>,
    pub website: Option<String>,
    pub about: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerRegistrationData {
    pub id: u32,
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
    pub team: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MyPlayerRegistrationResponse {
    pub registration: PlayerRegistrationData,
    pub current_team: Option<TeamOption>,
    #[serde(default)]
    pub waiver_required: bool,
    #[serde(default)]
    pub waiver_signature_valid: bool,
    pub waiver_filepath: Option<String>,
    pub waiver_sha256: Option<String>,
    pub waiver_legal_name_signature: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdatePlayerRegistrationRequest {
    pub jersey_name: Option<String>,
    pub jersey_number: Option<String>,
    pub team: Option<String>,
    pub waiver_legal_name_signature: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamRegistrationData {
    pub id: u32,
    pub pseudonym: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MyTeamRegistrationResponse {
    pub registration: TeamRegistrationData,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdateTeamRegistrationRequest {
    pub pseudonym: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shortname: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ScheduleSetupResponse {
    pub tournament: Tournament,
    pub matches: Vec<MatchSetupData>,
    pub fields: Vec<FieldSetupData>,
    pub tags: Vec<TagSetupData>,
    pub team_options: Vec<TeamOption>,
    pub is_to: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct MatchSetupData {
    pub uuid: String,
    pub name: String,
    pub field: Option<String>,
    pub team1: Option<String>,
    pub team2: Option<String>,
    pub team1_initial: Option<String>,
    pub team2_initial: Option<String>,
    pub status: String,
    pub scheduled_start_time: Option<String>,
    pub nominal_start_time: Option<String>,
    pub confirmed_start_time: Option<String>,
    pub completed_time: Option<String>,
    pub schedule_type: Option<String>,
    pub set_type: Option<String>,
    pub nominal_length: Option<u32>,
    pub previous_match: Option<String>,
    pub next_match: Option<String>,
    pub refs: Option<String>,
    pub refs_initial: Option<String>,
    pub ribbon: bool,
    pub skip_condition: Option<String>,
    pub nsets: Option<u32>,
    pub stones_per_set: Option<u32>,
    pub stones_remaining: Option<u32>,
    pub match_winner: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct FieldSetupData {
    pub id: u32,
    pub name: String,
    pub camera_urls: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct TagSetupData {
    pub id: u32,
    pub name: String,
    #[serde(default)]
    pub team: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CreateMatchRequest {
    pub name: String,
    pub field: Option<String>,
    pub length: Option<u32>,
    pub schedule_type: Option<String>,
    pub start_time: Option<String>,
    pub previous_match_id: Option<String>,
    pub team1: Option<String>,
    pub team2: Option<String>,
    pub refs: Option<Vec<String>>,
    pub set_type: Option<String>,
    pub nsets: Option<u32>,
    pub stones_per_set: Option<u32>,
    pub ribbon: Option<bool>,
    pub skip_condition: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CreateMatchResponse {
    pub success: bool,
    pub uuid: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ScheduleWarning {
    /// One of "unknown_team", "unknown_match_ref", "cycle", "double_booked".
    pub kind: String,
    pub message: String,
    #[serde(default)]
    pub matches: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ScheduleWarningsResponse {
    pub success: bool,
    #[serde(default)]
    pub warnings: Vec<ScheduleWarning>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ValidateDslResponse {
    pub valid: bool,
    #[serde(default)]
    pub simplified: Option<String>,
    pub error: Option<String>,
    /// Possible types of the simplified result, e.g. ["BOOL"], ["TEAM"], ["UNKNOWN"].
    #[serde(default)]
    pub result_type: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CreateFieldRequest {
    pub name: String,
    pub camera_urls: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CreateFieldResponse {
    pub success: bool,
    pub id: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CreateTagRequest {
    pub name: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CreateTagResponse {
    pub success: bool,
    pub id: u32,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PushBackRequest {
    pub minutes: i32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdateTagsRequest {
    pub tag_id: u32,
    pub team_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExportScheduleResponse {
    pub toml: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ImportScheduleRequest {
    pub toml: String,
}

// Record page API
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RecordMatchStatusResponse {
    pub hasActiveMatch: bool,
    pub match_id: Option<String>,
    pub match_name: Option<String>,
    pub start_time: Option<String>,
    pub status: Option<String>,
    pub points: Option<Vec<RecordPointData>>,
    pub reason: Option<String>,
    /// True when a TO has requested preview for this field; record page should send preview frames.
    #[serde(default)]
    pub preview_requested: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RecordPointData {
    pub uuid: String,
    pub stamp: Option<String>,
    pub end_stamp: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RegisterPlayerAsToResponse {
    pub success: bool,
    pub message: Option<String>,
    pub error: Option<String>,
    pub player_id: Option<String>,
    pub player_name: Option<String>,
    pub team: Option<String>,
    pub jersey_number: Option<String>,
    pub jersey_name: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RegisterTeamAsToResponse {
    pub success: bool,
    pub message: Option<String>,
    pub error: Option<String>,
    pub team_id: Option<String>,
    pub team_name: Option<String>,
    pub pseudonym: Option<String>,
    #[serde(default)]
    pub shortname: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SideCompSummary {
    pub id: i32,
    pub name: String,
    #[serde(rename = "type")]
    pub type_: String,
    pub registrant_count: i64,
    #[serde(default)]
    pub registration_open: bool,
    pub created_at: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SideCompRegistrant {
    pub player_id: String,
    pub player_name: String,
    #[serde(default)]
    pub entry_number: i32,
    pub registered_at: Option<String>,
    pub registered_by_to: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SideCompDetail {
    pub id: i32,
    pub event: String,
    pub name: String,
    #[serde(rename = "type")]
    pub type_: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub registration_open: bool,
    pub created_at: Option<String>,
    pub registrants: Vec<SideCompRegistrant>,
    #[serde(default)]
    pub viewer_is_to: bool,
    #[serde(default)]
    pub viewer_can_register: bool,
    #[serde(default)]
    pub viewer_is_registered_in_comp: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EligiblePlayer {
    pub player_id: String,
    pub player_name: String,
    pub team_id: Option<String>,
    pub team_pseudonym: Option<String>,
    #[serde(default)]
    pub team_shortname: Option<String>,
    pub jersey_name: Option<String>,
    #[serde(default)]
    pub sidecomp_registered: bool,
    pub entry_number: Option<i32>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SideCompRegisterPlayerResponse {
    pub player_id: String,
    pub player_name: String,
    pub entry_number: i32,
    pub registered_at: Option<String>,
}
