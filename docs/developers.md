# For Developers

Arctos has a comprehensive API for programmatic access to data and
some functionality. This page explains how to register an app, how to
let people "Sign in with Arctos", and how to call Arctos endpoints
with the resulting tokens.

Arctos is an OAuth 2.0 authorization server. If you have used "Sign in
with Google" or GitHub OAuth apps before, this works the same way.

## Registering an app

Anyone with an Arctos account can register apps on the
[Your apps](/developers/apps) page. You'll choose:

- **Name** and optional **homepage**: shown to people on the consent
  screen when they sign in.
- **Redirect URIs**: where Arctos sends people back to after they sign in.
  Each must be one of:
  - an `https://` URL,
  - an `http://127.0.0.1/...` (or `http://[::1]/...`, `http://localhost/...`)
    loopback URL, for apps running on the user's own computer. Any port
    matches, so your app can listen on whichever port is free, or
  - a reverse-domain private-use scheme like `com.example.app:/callback`
    for mobile apps.
- **Type**:
  - **Public** apps run on the user's device (desktop, mobile, and
    single-page browser apps). They can't keep a secret, so they must use
    PKCE.
  - **Confidential** apps run on a server you control. They get a
    **client secret**, which is shown once when you create the app (or
    generate a new one). Arctos only stores a hash of it.

Every app gets a **client ID**, which is not secret.

## Sign in with Arctos

Arctos implements the
[authorization code flow](https://www.rfc-editor.org/rfc/rfc6749#section-4.1)
with [PKCE](https://www.rfc-editor.org/rfc/rfc7636). Native apps should
follow [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252) (open the
system browser and listen on a loopback redirect URI). Most OAuth client
libraries handle all of this for you; the endpoints are:

| Endpoint | URL |
|----------|-----|
| Authorization | `https://<arctos host>/oauth/authorize` |
| Token | `https://<arctos host>/api/oauth/token` |
| Revocation | `https://<arctos host>/api/oauth/revoke` |
| User info | `https://<arctos host>/api/oauth/userinfo` |

### 1. Send the user to Arctos

Generate a random `code_verifier` (43 to 128 characters from
`A-Z a-z 0-9 - . _ ~`) and a random `state`, then open:

```
https://<arctos host>/oauth/authorize
    ?response_type=code
    &client_id=<client id>
    &redirect_uri=<one of your redirect URIs>
    &scope=<space-separated scopes, optional>
    &state=<state>
    &code_challenge=<BASE64URL(SHA256(code_verifier)), no padding>
    &code_challenge_method=S256
```

Only `S256` is accepted as the challenge method. PKCE is required for
public apps and recommended for confidential ones.

The user logs in if needed, then sees which app is asking and what it
will be able to do, and chooses **Allow** or **Cancel**.

### 2. Handle the redirect

Arctos redirects to your `redirect_uri` with either `?code=...&state=...`
or `?error=access_denied&state=...`. Check that `state` matches what you
sent.

### 3. Exchange the code for tokens

```
POST /api/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<code>
&redirect_uri=<the same redirect URI>
&client_id=<client id>
&code_verifier=<code_verifier>
```

Confidential apps also authenticate, with HTTP Basic auth
(`client_id:client_secret`) or `client_secret` in the form. The response
is:

```json
{
  "access_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "...",
  "scope": "..."
}
```

Codes expire after 5 minutes and work once.

### 4. Find out who signed in

```
GET /api/oauth/userinfo
Authorization: Bearer <access token>
```

returns `{"id": "<username>", "name": "<display name>", "type": "player" | "team"}`.
`id` is the stable identifier for the account.

## Tokens

- **Access tokens** last one hour. Send them as
  `Authorization: Bearer <access token>`.
- **Refresh tokens** get a new access token when the old one expires:

```
POST /api/oauth/token

grant_type=refresh_token&refresh_token=<refresh token>&client_id=<client id>
```

  Every refresh returns a **new refresh token**; store it and throw the old
  one away. Using an old refresh token again is treated as theft and
  signs the user out of your app entirely. Refresh tokens expire if unused
  for 90 days.
- An access token you hand to another service of yours (for example, a
  server doing work on the user's behalf) keeps working until it expires,
  even after you refresh, so you can give out short-lived access tokens
  without ever sharing the refresh token.
- **Revoke** tokens when the user signs out:

```
POST /api/oauth/revoke

token=<refresh or access token>&client_id=<client id>
```

Users can see every app they've signed in to in the **Connected apps**
section of their profile, and disconnect any of them, which revokes all
of that app's tokens.

### Tokens without a user

Confidential apps that only need public data can skip the user entirely
with the client credentials grant:

```
POST /api/oauth/token
Authorization: Basic <base64(client_id:client_secret)>

grant_type=client_credentials
```

This returns an access token (no refresh token) that can read public data
but can't request any scopes.

## Scopes

Reading public Arctos data needs no scope; every token can do it. Ask
only for the scopes you need:

| Scope | Lets the app |
|-------|--------------|
| `injuries` | Read all of the user's injury records, including private ones |
| `footage` | See tournaments the user organizes, including unpublished ones, and upload match footage to them |

## Errors

Token and revocation errors follow
[RFC 6749 section 5.2](https://www.rfc-editor.org/rfc/rfc6749#section-5.2):
a `400` or `401` with `{"error": "...", "error_description": "..."}`.
API requests with a missing, expired, or revoked access token get `401`;
tokens without a needed scope get `403` (see [API errors](#api-errors)).

## API reference

The Arctos API gives apps read access to everything a signed-out visitor
of the Arctos website can see (tournaments, schedules, live scores,
rosters, results), plus a few things a user can grant with
[scopes](#scopes).

### Quick start

Register a **confidential** app, get a token with the client credentials
grant, and call the API:

```sh
TOKEN=$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d grant_type=client_credentials \
  https://<arctos host>/api/oauth/token | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" \
  "https://<arctos host>/api/v1/tournaments?when=upcoming"
```

To act for a user (to read their private injuries, or upload footage to
their tournaments), get a token through [Sign in with Arctos](#sign-in-with-arctos)
instead, asking for the scopes you need. Public apps (desktop, mobile,
browser) must do this, since they can't use client credentials.

### Conventions

- **Base URL**: `https://<arctos host>/api/v1`.
- **Authentication**: every request needs `Authorization: Bearer <access token>`.
  Any valid token can read public data. The Arctos session cookie is not
  accepted.
- **JSON**: keys are `snake_case`. Timestamps are ISO 8601 in UTC ending
  in `Z` (`"2026-05-01T12:00:00Z"`); missing values are `null`.
- **IDs** are the ones Arctos uses in its own URLs: tournament and league
  ids are URL slugs (`"spring-open"`), player and team ids are usernames,
  match ids are UUIDs, and field ids are integers.
- **Lists** are wrapped in an object (`{"tournaments": [...]}`). Player
  and team directories are paginated 50 to a page with `?page=` (from 1)
  and also return `page`, `total_pages`, and `total`.
- **Teams** in a tournament or league show under the name they
  registered with. A team reference is `{"id", "name", "shortname"}`;
  bracket slots not yet filled have `"id": null` and a placeholder name
  such as `"Winner of Game 3"`.
- **Profile photos** are paths on the Arctos host (`"/static/uploads/profiles/..."`).
- **Stability**: fields may be added to responses, but are never
  renamed, retyped, or removed within `/v1`. Ignore fields you don't know.
- **CORS**: responses carry `Access-Control-Allow-Origin: *`, so
  browser apps can call the API directly.

### API errors

Errors are JSON with an `error` message and an HTTP status:

| Status | Meaning |
|--------|---------|
| `400` | The request is invalid: `{"error": "..."}` |
| `401` | Missing, expired, or revoked token: `{"error": "invalid_token", "error_description": "..."}` |
| `403` | The token lacks a needed scope or user (`{"error": "insufficient_scope", ...}`), or the user isn't allowed (`{"error": "..."}`) |
| `404` | No such resource, or it isn't visible to you: `{"error": "..."}` |

### Visibility

- Unpublished tournaments and leagues act as if they don't exist (`404`,
  left out of lists), except for a token with the `footage` scope whose
  user organizes them.
- A tournament's schedule (`/matches`) is `403` until the organizers
  publish it, with the same exception. Single matches, results, and
  scoreboards follow the website and are always available for visible
  tournaments.
- Contact details, payments, waivers, and private injuries are never
  returned.

### Me

#### `GET /me`

The user the token acts for. `403` for client credentials tokens.

```json
{"id": "jsmith", "name": "Jane Smith", "type": "player"}
```

### Tournaments

#### `GET /tournaments`

Visible tournaments, by start date. Query parameters:

- `league`: only tournaments in this league.
- `when`: `upcoming` (not yet over) or `past`.

```json
{
  "tournaments": [
    {
      "id": "spring-open",
      "name": "Spring Open",
      "start_date": "2026-05-01T14:00:00Z",
      "end_date": "2026-05-02T23:00:00Z",
      "location": "Central Park",
      "about": "Our annual open.",
      "published": true,
      "historical": false,
      "schedule_published": true,
      "bracket_published": false,
      "league": {"id": "metro-league", "name": "Metro League"},
      "registration": {
        "team_registration_open": false,
        "player_registration_open": true,
        "max_teams": 16,
        "max_roster_size": 12,
        "max_field_size": 7,
        "team_fee": 40.0,
        "player_fee": 10.0
      }
    }
  ]
}
```

`league` is `null` for standalone tournaments. `historical` is `true` for
records of past tournaments whose results were entered after the event;
their matches carry no real start or end times. An unpublished historical
tournament is visible only to its organizers.

#### `GET /tournaments/{tournament_id}`

One tournament, as above plus its organizers:

```json
{
  "tournament": {
    "id": "spring-open",
    "...": "...",
    "organizers": [{"id": "jsmith", "name": "Jane Smith", "type": "player"}]
  }
}
```

#### `GET /tournaments/{tournament_id}/fields`

```json
{"fields": [{"id": 12, "name": "Field 1"}, {"id": 13, "name": "Field 2"}]}
```

#### `GET /tournaments/{tournament_id}/teams`

Confirmed teams, by name. For a tournament in a league, these are the
league's teams.

```json
{
  "teams": [
    {
      "id": "jsmith-team",
      "name": "Smith & Co",
      "shortname": "SMC",
      "team_name": "Smith Team Account",
      "profile_photo": null,
      "player_count": 9,
      "registered_at": "2026-03-02T18:21:00Z"
    }
  ]
}
```

`name` is the name the team registered under; `team_name` is the team
account's own name.

#### `GET /tournaments/{tournament_id}/players`

Confirmed players, by name (league-wide for league tournaments).
`?team={team_id}` returns one team's roster.

```json
{
  "players": [
    {
      "id": "jsmith",
      "name": "Jane Smith",
      "jersey_name": "SMITH",
      "jersey_number": "7",
      "profile_photo": "/static/uploads/profiles/jsmith.jpg",
      "team": {"id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"}
    }
  ]
}
```

`team` is `null` for players without a team.

#### `GET /tournaments/{tournament_id}/matches`

Matches and match-timing entries (breaks and joins), with scores, by start time.
Event activities and scheduled side competitions are not included in this
endpoint. `403` while the schedule is unpublished. Query parameters:

- `field`: field name.
- `team`: team id.
- `status`: `NOT_STARTED`, `TIME_FINALIZED`, `READY_TO_START`,
  `IN_PROGRESS`, `COMPLETED`, or `SKIPPED`.

```json
{
  "matches": [
    {
      "id": "6f1c...",
      "name": "Game 4",
      "field": "Field 1",
      "status": "COMPLETED",
      "schedule_type": "STATIC",
      "set_type": "SETS",
      "ribbon": false,
      "team1": {"id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"},
      "team2": {"id": null, "name": "Winner of Game 2", "shortname": null},
      "winner": "TEAM1",
      "scheduled_start_time": "2026-05-01T14:00:00Z",
      "start_time": "2026-05-01T14:05:00Z",
      "started_at": "2026-05-01T14:06:12Z",
      "completed_at": "2026-05-01T14:41:40Z",
      "length_minutes": 30,
      "sets": [
        {"number": 1, "team1_score": 5, "team2_score": 3},
        {"number": 2, "team1_score": 5, "team2_score": 4}
      ]
    }
  ]
}
```

- `start_time` is the current planned start; `scheduled_start_time` is
  the one originally published. `started_at` and `completed_at` are when
  the match actually started and finished.
- `winner` is `TEAM1`, `TEAM2`, or `null`.
- `schedule_type` says how the start time is planned; `STATBREAK`
  entries are breaks in the schedule rather than games.
- `set_type` is `SETS` or `STONES`. `sets` counts points won per set,
  leaving out rerolled points.

#### `GET /tournaments/{tournament_id}/matches/{match_id}`

One match, as above plus its referees, published footage, and every
point in order:

```json
{
  "match": {
    "id": "6f1c...",
    "...": "...",
    "sets_to_win": 2,
    "stones": null,
    "referees": [{"id": "ref-team", "name": "Ref Team", "shortname": null}],
    "footage": [
      {
        "id": "a8d2...",
        "name": "Sideline",
        "youtube_url": "https://www.youtube.com/watch?v=...",
        "anchors": [{"world_time": "2026-05-01T14:06:00Z", "video_offset": 12.5}]
      }
    ],
    "points": [
      {
        "id": "c0ff...",
        "stamp": "2026-05-01T14:06:12Z",
        "end_stamp": "2026-05-01T14:07:01Z",
        "set_number": 1,
        "winner": "TEAM1",
        "rerolled": false,
        "stones_at_start": null
      }
    ]
  }
}
```

Each point's `stamp` is when it started and `end_stamp` when it ended
(`null` while it's being played). `stones` is
`{"per_set", "remaining"}` for stones matches, where `stones_at_start` is
also set on each point.

#### `GET /tournaments/{tournament_id}/fields/{field_name}/scoreboard`

What's happening on a field right now, for scoreboards and overlays.
URL-encode the field name (`Field%201`). While a match is in progress:

```json
{
  "match": {
    "id": "6f1c...",
    "team1": {"name": "Smith & Co", "shortname": "SMC"},
    "team2": {"name": "The Rivals", "shortname": null},
    "set_type": "SETS",
    "sets": [{"number": 1, "team1_score": 2, "team2_score": 1}],
    "stones": null,
    "points": [
      {
        "stamp": "2026-05-01T14:06:12Z",
        "end_stamp": "2026-05-01T14:07:01Z",
        "set_number": 1,
        "winner": "TEAM1",
        "rerolled": false,
        "stones_at_start": null
      }
    ]
  }
}
```

`points` is ordered by `stamp` and includes the point being played.
When the field is idle, `match` is `null` and the previous and next
matches (either may be `null`) are included instead. `previous` is the
field's most recently completed match and `next` is the match scheduled
after it; breaks, joins, and skipped matches are never included. Pass
`?tz_offset_minutes=` (minutes to add to UTC for local time, e.g. `-420`
for UTC-7) to leave out matches that aren't on the current day in that
timezone (`previous` by when it ended, `next` by its expected start):

```json
{
  "match": null,
  "previous": {
    "id": "6f1c...",
    "name": "Game 4",
    "team1": {"name": "Smith & Co", "shortname": "SMC"},
    "team2": {"name": "The Rivals", "shortname": null},
    "start_time": "2026-05-01T14:05:00Z",
    "winner": "TEAM1"
  },
  "next": {"id": "9b2e...", "name": "Game 7", "...": "...", "winner": null}
}
```

#### `GET /tournaments/{tournament_id}/results`

Standings from completed matches, by team name. Add
`?include_ribbon=true` to count ribbon matches.

```json
{
  "standings": [
    {
      "team": {"id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"},
      "matches_won": 4,
      "matches_lost": 1,
      "points_won": 31,
      "points_lost": 18
    }
  ]
}
```

#### `GET /tournaments/{tournament_id}/side-competitions`

Side competitions (duels, chains and breaks, and the like), oldest first.

```json
{
  "side_competitions": [
    {
      "id": 3,
      "name": "Duels",
      "type": "DUELING",
      "description": "Single elimination.",
      "registration_open": false,
      "active": true,
      "finalized": false,
      "allowed_weapons": ["LONG", "QTIP", "STAFF"],
      "track_losses": true,
      "track_chain_losses": false,
      "track_break_losses": false,
      "results_available": true,
      "registrant_count": 12,
      "created_at": "2026-04-20T10:00:00Z"
    }
  ]
}
```

- `type` is `DUELING`, `CHAIN_BREAKING`, or `OTHER`.
- `active` means results are being logged right now. `finalized` means the
  organizers have locked the results as final; until then they are
  unofficial.
- `allowed_weapons` are the weapons players may register with: `CHAIN`,
  `LONG`, `QTIP`, `STAFF`, `BOARD`, `FLORENTINE`, `SKULL`, or `UNARMED`.
- Whether losses count is `track_losses`, except in `CHAIN_BREAKING`, where
  chains follow `track_chain_losses` and everyone else follows
  `track_break_losses`.
- `results_available` is `false` when the organizers have hidden the
  results.

#### `GET /tournaments/{tournament_id}/side-competitions/{side_competition_id}`

One side competition, with the fields above plus its registrants in
registration order.

```json
{
  "id": 3,
  "name": "Duels",
  "...": "...",
  "registrants": [
    {
      "entry_number": 1,
      "player": {"id": "jsmith", "name": "Jane Smith"},
      "weapon": "LONG",
      "registered_at": "2026-04-21T09:12:00Z"
    }
  ]
}
```

A player keeps the same `entry_number` in every side competition of the
tournament, or of the league when the tournament belongs to one.

#### `GET /tournaments/{tournament_id}/side-competitions/{side_competition_id}/results`

Standings. `CHAIN_BREAKING` has two tables, `chains` and `breaks`; every
other type has one, `all`. Rows are ranked by wins, or by win percentage
when the table tracks losses. This endpoint returns the public summary: a
positive public result limit selects that many rows per table. Its payload
contains the published tables. Personal results are provided by the application
endpoint. `404` when the organizers have hidden
the results.

```json
{
  "finalized": false,
  "tables": [
    {
      "id": "all",
      "title": "Standings",
      "track_losses": true,
      "rows": [
        {
          "rank": 1,
          "entry_number": 1,
          "weapon": "LONG",
          "player": {"id": "jsmith", "name": "Jane Smith"},
          "team": {"id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"},
          "wins": 9,
          "losses": 3,
          "win_percentage": 75.0
        }
      ]
    }
  ]
}
```

`team` is the player's team at the tournament, or `null`. `losses` and
`win_percentage` are `null` when the table doesn't track losses, and
`win_percentage` is also `null` before a player's first result.

### Footage

Organizers can attach match footage, as on the match page. These need a
token with the `footage` scope whose user organizes the tournament
(otherwise `403`).

#### `POST /tournaments/{tournament_id}/matches/{match_id}/footage/links`

Attach a YouTube video:

```json
{
  "youtube_link": "https://youtu.be/...",
  "camera_name": "Sideline",
  "anchors": [{"point_index": 0, "video_offset": 12.5}]
}
```

returns `{"camera_uuid": "a8d2..."}`.

`anchors` (optional) line the video up with the match: each is either
`{"point_index", "video_offset"}` (the 0-based point, in time order,
that starts `video_offset` seconds into the video) or
`{"world_time", "video_offset"}` (an ISO 8601 time).

#### Uploading a video file

Arctos uploads the file to YouTube for you. Send it in chunks of up to
16 MiB (at most 2048 chunks, 32 GiB in total; MP4 or WebM):

1. `POST /tournaments/{tournament_id}/matches/{match_id}/footage/uploads`

   ```json
   {
     "camera_name": "Sideline",
     "filename": "game4.mp4",
     "content_type": "video/mp4",
     "total_chunks": 3,
     "anchors": [{"point_index": 0, "video_offset": 12.5}]
   }
   ```

   returns `{"upload_id": "4c1e..."}`.

2. For each chunk, in any order:
   `PUT /tournaments/{tournament_id}/matches/{match_id}/footage/uploads/{upload_id}/chunks/{index}`
   with `Content-Type: application/octet-stream` and the raw bytes as the
   body (`index` from 0). Returns `{"received": 0}`. Re-sending a chunk
   replaces it.

3. `POST /tournaments/{tournament_id}/matches/{match_id}/footage/uploads/{upload_id}/complete`
   returns `{"camera_uuid": "a8d2..."}`. Only the user who started the
   upload can complete it. The footage appears on the match once YouTube
   has processed it.

Unfinished uploads are discarded after 24 hours.

### Leagues

#### `GET /leagues`

Visible leagues, by name:

```json
{
  "leagues": [
    {
      "id": "metro-league",
      "name": "Metro League",
      "about": "Weekly play.",
      "published": true,
      "registration": {"team_registration_open": true, "...": "..."}
    }
  ]
}
```

`registration` has the same fields as a tournament's.

#### `GET /leagues/{league_id}`

```json
{"league": {"id": "metro-league", "...": "..."}, "tournaments": [{"id": "metro-week-1", "...": "..."}]}
```

`tournaments` are the league's visible tournaments, by start date.

#### `GET /leagues/{league_id}/teams` and `GET /leagues/{league_id}/players`

The league's confirmed teams and players, in the same shape as a
tournament's. `players` takes `?team={team_id}`.

#### `GET /leagues/{league_id}/results`

Standings across all of the league's visible tournaments, in the same
shape as a tournament's.

### Players

#### `GET /players`

All players, by name, 50 to a page. `?search=` matches names and ids.

```json
{
  "players": [
    {"id": "jsmith", "name": "Jane Smith", "location": "Boston", "profile_photo": null}
  ],
  "page": 1,
  "total_pages": 4,
  "total": 187
}
```

#### `GET /players/{player_id}`

```json
{
  "player": {
    "id": "jsmith",
    "name": "Jane Smith",
    "location": "Boston",
    "profile_photo": null,
    "bio": "Captain of Smith & Co.",
    "pronouns": "she / her",
    "pompfen": ["CHAIN", "STAFF"],
    "registrations": [
      {
        "tournament": {"id": "spring-open", "name": "Spring Open"},
        "league": null,
        "team": {"id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"},
        "jersey_name": "SMITH",
        "jersey_number": "7"
      }
    ]
  }
}
```

`registrations` lists confirmed registrations in published tournaments
and leagues; exactly one of `tournament` and `league` is set.

`pronouns` is optional public text (up to 64 characters), or `null`.
`pompfen` lists the player's chosen roles in canonical order: `CHAIN`,
`LONG`, `QTIP`, `STAFF`, `BOARD`, `FLORENTINE`, `SKULL`, `UNARMED`.
It is an empty list when no choices are set. These profile preferences
do not change a player's weapon selection for any side competition.

#### `GET /players/{player_id}/injuries`

The player's public injury records, newest first. A token with the
`injuries` scope acting for this player also gets the private ones.

```json
{
  "injuries": [
    {"id": 41, "message": "Sprained ankle", "date": "2026-04-02T19:30:00Z", "active": true, "public": true}
  ]
}
```

### Teams

#### `GET /teams`

All team accounts, by name, 50 to a page, in the same shape as
`/players`. `?search=` matches names and ids.

#### `GET /teams/{team_id}`

```json
{
  "team": {
    "id": "jsmith-team",
    "name": "Smith Team Account",
    "location": "Boston",
    "profile_photo": null,
    "website": "https://smith.example",
    "about": "Est. 2019.",
    "registrations": [
      {
        "tournament": {"id": "spring-open", "name": "Spring Open"},
        "league": null,
        "name": "Smith & Co",
        "shortname": "SMC",
        "players": [{"id": "jsmith", "name": "Jane Smith", "...": "..."}]
      }
    ]
  }
}
```

Each registration's `players` is its confirmed roster, in the same shape
as a tournament's `/players`.
