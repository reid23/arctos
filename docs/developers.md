# For Developers

Arctos has a comprehensive API for programmatic access to data and
some functionality. This page explains how to register an app, how to
let people "Sign in with Arctos", and how to call Arctos endpoints
with the resulting tokens.

Arctos is an OAuth 2.0 authorization server and an OpenID Connect
provider. If you have used "Sign in with Google" or GitHub OAuth apps
before, this works the same way. OpenID Connect clients discover the
provider at
`https://events.californiajugger.org/.well-known/openid-configuration`.
That origin is the issuer: discovery and ID tokens name it. The site
is also served at `https://arctos.usnja.org`, and ordinary API calls
work on either host, but that host is not the issuer.

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
libraries handle all of this for you. On 
`https://events.californiajugger.org` the endpoints are:

| Endpoint | Path |
|----------|------|
| Discovery | `/.well-known/openid-configuration` |
| Authorization | `/oauth/authorize` |
| Token | `/api/oauth/token` |
| JSON Web Key Set | `/api/oauth/jwks` |
| Revocation | `/api/oauth/revoke` |
| User info | `/api/oauth/userinfo` |

### 1. Send the user to Arctos

Generate a random `code_verifier` (43 to 128 characters from
`A-Z a-z 0-9 - . _ ~`) and a random `state`, then open:

```
https://events.californiajugger.org/oauth/authorize
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

The user logs in if needed. The first time, they see which app is
asking and what it will be able to do, and choose **Allow** or
**Cancel**. If they already allowed this app every scope in the
request, Arctos only asks them to confirm signing in as the current
account. Either screen can switch to a different Arctos account before
approving. Include `scope=openid` to receive an ID token. `nonce` is
copied into that token. `prompt=none` returns to your app without a
page (`login_required` or `consent_required` when Arctos would
otherwise have to ask). `prompt=consent` always shows the permission
list again.

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
  "scope": "...",
  "id_token": "..."
}
```

`id_token` is present when the grant includes `openid`. It is an
RS256 JWT. Verify it with the keys from the JSON Web Key Set. The
issuer (`iss`) is `https://events.californiajugger.org`, the audience
is your client id (`aud`), and `sub` is the account's username. `nonce` is present when
you sent one. A refreshed token includes a new ID token too, without
the original nonce.

Codes expire after 5 minutes and work once.

### 4. Find out who signed in

```
GET /api/oauth/userinfo
Authorization: Bearer <access token>
```

returns the account. `sub` and `id` are both the username, which is
the stable identifier. `preferred_username` is the same value, `name`
is the display name, and `type` is `"player"` or `"team"`. `picture`
is included for the `profile` scope when the account has a photo.
`email`, when requested, is that same username. It is not a mailbox.
The ID token carries the same identity claims, without `id` and `type`.

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
| `openid` | Sign in with OpenID Connect and receive an ID token |
| `profile` | See the account's profile photo |
| `email` | Receive the account id in the `email` claim |
| `injuries` | Read all of the user's injury records, including private ones |
| `footage` | See tournaments the user organizes, including unpublished ones, and upload match footage to them |
| `manage` | Create tournaments and leagues, and [manage](#managing-tournaments) the ones the user organizes, including unpublished ones |

Every user grant can see the account's name and username, with or
without `profile`. `email` is granted whenever a client asks for it.
Arctos does not show that scope on the consent screen or in Connected
apps. The claim is the account id from the profile URL, the same value
as `sub`. It is not an email address, and it is not the account's
contact address.

## Errors

Token and revocation errors follow
[RFC 6749 section 5.2](https://www.rfc-editor.org/rfc/rfc6749#section-5.2):
a `400` or `401` with `{"error": "...", "error_description": "..."}`.
API requests with a missing, expired, or revoked access token get `401`;
tokens without a needed scope get `403` (see [API errors](#api-errors)).

## API reference

The Arctos API gives apps read access to everything a signed-out visitor
of the Arctos website can see (tournaments, schedules, live scores,
rosters, results), plus what a user can grant with [scopes](#scopes),
including organizing tournaments: creating them, changing their
settings, and building their schedules.

### Quick start

Register a **confidential** app, get a token with the client credentials
grant, and call the API:

```sh
TOKEN=$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d grant_type=client_credentials \
  https://events.californiajugger.org/api/oauth/token | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" \
  "https://events.californiajugger.org/api/v1/tournaments?when=upcoming"
```

To act for a user (to read their private injuries, upload footage, or set
up and run their tournaments), get a token through [Sign in with Arctos](#sign-in-with-arctos)
instead, asking for the scopes you need. Public apps (desktop, mobile,
browser) must do this, since they can't use client credentials.

### Conventions

- **Base URL**: `https://events.californiajugger.org/api/v1`.
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
| `409` | The change conflicts with the resource's state, such as editing a match that has started: `{"error": "..."}` |

### Visibility

- Unpublished tournaments and leagues act as if they don't exist (`404`,
  left out of lists), except for a token with the `footage` or `manage`
  scope whose user organizes them.
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
- `set_type` is `SETS`, `STONES`, or `TIMED`. `sets` counts points won
  per set, leaving out rerolled points.

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
      "track_matchups": false,
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
- `track_matchups` is available when loss tracking is on, including chain
  and breaker loss tracking. Scoring then records two people at a time: a
  win for one and a loss for the other. Either person may be unknown.
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
`LONG`, `QTIP`, `STAFF`, `BOARD`, `FLORENTINE`, `QWIK`. `QWIK` uses the
unarmed symbol.
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

### Managing tournaments

A token with the `manage` scope can do what an organizer does in a
tournament's settings and schedule editor. Every route in this and the
following sections needs that scope and a user (client credentials get
`403`); all but creation also need the user to organize the tournament
or league. Other users get `403`, or `404` when the tournament isn't
published. Changes return `200` with the changed resource, creations
`201`, and deletions `204` with no body.

To set up a tournament from scratch (for LLM agents, [For AI
Agents](agents.md) walks through this with a tested example and
checks):

1. `POST /tournaments` to create it.
2. `PATCH /tournaments/{tournament_id}` with dates, location, and
   registration settings.
3. Register teams ([Registrations](#registrations)), or let them register
   on the website.
4. `PUT /tournaments/{tournament_id}/schedule` with a schedule file that
   has the fields, tags, and matches.
5. Check `GET .../schedule/warnings` and `GET .../matches`, fix problems
   (in the file or match by match), then publish the tournament and its
   schedule with `PATCH /tournaments/{tournament_id}`.

#### `POST /tournaments`

```json
{"id": "fall-open", "name": "Fall Open", "league": "metro-league"}
```

`id` is the URL slug (letters, digits, `-`, `_`). `league` is optional;
the user must organize that league. The user becomes the tournament's
organizer. Returns `201` with `{"tournament": {...}}`, in the shape of
`GET /tournaments/{tournament_id}`. New tournaments are unpublished and
start and end today.

#### `GET /tournaments/{tournament_id}/settings`

Every setting, under the keys `PATCH` takes:

```json
{
  "settings": {
    "id": "fall-open",
    "name": "Fall Open",
    "location": "Central Park",
    "about": "Markdown description.",
    "start_date": "2026-10-03",
    "end_date": "2026-10-04",
    "published": false,
    "schedule_published": false,
    "bracket_published": false,
    "historical": false,
    "league": null,
    "head_refs_allow_reffing_teams": false,
    "head_refs_allow_anyone": false,
    "head_refs_allowed_list": "jsmith,kjones",
    "registration": {
      "team_reg_fee": 40.0,
      "player_reg_fee": 0.0,
      "team_registration_open": true,
      "player_registration_open": false,
      "terms_link": null,
      "n_max_teams": 12,
      "max_team_size_roster": null,
      "max_team_size_field": null,
      "require_waiver_signature": false
    },
    "organizers": [{"id": "jsmith", "name": "Jane Smith", "type": "player"}]
  }
}
```

`head_refs_allowed_list` is comma-separated player ids. `registration` is
`null` for a tournament in a league, whose registration settings are the
league's. `bracket_published`, `historical`, `league`, and `organizers`
are read-only here.

#### `PATCH /tournaments/{tournament_id}`

Send only the settings to change, in the shape above (registration
settings may be nested under `registration` or not). Dates are
`YYYY-MM-DD`; the end can't be before the start. Booleans must be JSON
booleans. Fees are amounts of at least 0, caps are whole numbers or
`null`. `require_waiver_signature` can only be turned off here; upload
a waiver on the website. Returns `{"settings": {...}}`. Historical
tournaments can't be changed.

#### `DELETE /tournaments/{tournament_id}?confirm={tournament_id}`

Permanently deletes the tournament with its matches, results,
registrations, and side competitions. `confirm` must repeat the id.

#### `POST /tournaments/{tournament_id}/organizers`

`{"user_id": "kjones", "user_type": "player"}` (`player` or `team`)
adds an organizer and returns `201` with `{"organizers": [...]}`.
`DELETE /tournaments/{tournament_id}/organizers/{user_type}/{user_id}`
removes one; users can't remove themselves (or a team they manage).
Organizers of a tournament in a league are the league's; manage them on
the league.

#### Fields

- `POST /tournaments/{tournament_id}/fields` with `{"name": "Field 3"}`
  returns `201` with `{"field": {"id": 14, "name": "Field 3"}}`.
- `PATCH /tournaments/{tournament_id}/fields/{field_id}` with
  `{"name": "North"}` renames it; its matches move with it.
- `DELETE /tournaments/{tournament_id}/fields/{field_id}` deletes a
  field that has no matches.

#### Tags

Tags are named slots for a team (`tag::Seed 1` in a match), filled by an
[Arctos Schedule Script](arctos-schedule-script.md) expression that
evaluates to a team, or set by hand.

- `GET /tournaments/{tournament_id}/tags` returns
  `{"tags": [{"id": 3, "name": "Seed 1", "expression": "(get 0 standings)", "team": null}]}`.
- `POST /tournaments/{tournament_id}/tags` with `{"name", "expression"}`
  (`expression` optional) returns `201` with `{"tag": {...}}`.
- `PUT /tournaments/{tournament_id}/tags/{tag_id}` with
  `{"name", "expression"}` renames the tag and, if `expression` is
  present, replaces it. Tags in use can't be renamed.
- `PUT /tournaments/{tournament_id}/tags/{tag_id}/team` with
  `{"team_id": "jsmith-team"}` sets the tag's team by hand (`null` goes
  back to the expression). Matches that haven't started pick it up.
- `DELETE /tournaments/{tournament_id}/tags/{tag_id}` deletes a tag
  nothing refers to.

#### Script variables

Named expressions that tags, skip conditions, and other variables can
use.

- `GET /tournaments/{tournament_id}/script-variables` returns
  `{"script_variables": [{"id": 1, "name": "rounds", "expression": "3"}]}`.
- `POST /tournaments/{tournament_id}/script-variables` with
  `{"name", "expression"}` returns `201` with `{"script_variable": {...}}`.
- `PATCH /tournaments/{tournament_id}/script-variables/{variable_id}`
  changes `name` and/or `expression`.
- `DELETE /tournaments/{tournament_id}/script-variables/{variable_id}`
  deletes a variable nothing refers to.

### Building the schedule

The easiest way to build a whole schedule is to write a schedule file
and `PUT` it; single matches can then be adjusted one at a time. These
routes need the `manage` scope and an organizer, as above.

#### `GET /tournaments/{tournament_id}/schedule/script`

Returns `{"script": "..."}`: the tournament's schedule file (TOML), with
its script variables, tags, fields, matches, and schedule items. Export
one from a tournament set up on the website to see every option.

#### `PUT /tournaments/{tournament_id}/schedule`

`{"script": "..."}` replaces the schedule with the file's contents:
variables, tags, fields, and matches the file leaves out are deleted
(schedule items only when the file has `schedule_items`). The whole file
is checked before anything changes, so an invalid file changes nothing
(`400` listing the problems). A short file looks like:

```toml
[[variables]]
name = "final_length"
expression = "40"

[[tags]]
name = "Champion"

[[fields]]
name = "North"

[[matches]]
name = "Semi 1"
field = "North"
schedule_type = "STATIC"
set_type = "SETS"
nsets = 3
nominal_length = 30
team1_initial = "bears"
team2_initial = "wolves"
refs_initial = "otters"
scheduled_start_time = 2026-07-04T16:00:00

[[matches]]
name = "Final"
field = "North"
schedule_type = "FAST"
set_type = "SETS"
nominal_length = 40
previous_match = "Semi 1"
team1_initial = "Semi 1::winner"
team2_initial = "tag::Champion"
```

A match's teams and referees (`refs_initial`, comma-separated) are team
ids, registered team names, `tag::Name`, or `Match Name::winner` /
`::loser`. `schedule_type` is `STATIC` (starts at
`scheduled_start_time`, UTC), `FAST` or `SAFE` (follows
`previous_match` on the same field), `BREAK` or `JOIN` (a pause or a
point where fields wait for each other, same-named rows across fields
start together), or `STATBREAK` (a break at a fixed time). `set_type` is
`SETS` (with `nsets`), `STONES` (with `stones_per_set`), or `TIMED`
(with `nsets`, and `stones_per_set` holding each set's length in
minutes). Lengths are
in minutes. Exported files also carry each row's `uuid` / `id`, which
update that row in place (a match can be renamed this way). A match row
without a `uuid` updates the match with the same name (and field, for
breaks and joins) or creates one; tags, fields, and variables are matched
by name. A file that leaves out a match that has started is rejected.
Names that aren't registered teams become tags, with a warning.

Returns the counts and warnings:

```json
{
  "dry_run": false,
  "variables_created": 1, "variables_updated": 0,
  "tags_created": 1, "tags_updated": 0,
  "fields_created": 1, "fields_updated": 0,
  "matches_created": 2, "matches_updated": 0,
  "warnings": []
}
```

With `?dry_run=true`, the file gets every check an import runs, including
tag and variable expressions checked against the file's own matches, tags,
and variables, and the response reports what the import would do. Nothing
is saved. `warnings` here only covers names the import turned into tags;
conflicts in the schedule itself come from
[`GET .../schedule/warnings`](#get-tournamentstournament_idschedulewarnings)
after a real import.

#### `POST /tournaments/{tournament_id}/schedule/validate`

Checks an [Arctos Schedule Script](arctos-schedule-script.md) expression
(a skip condition, tag, or variable) against the tournament's schedule:
`{"expression": "(+ 1 2)"}` returns
`{"valid": true, "value": 3, "simplified": "3", "error": null, "warnings": [], "result_type": ["INT"]}`,
or `"valid": false` with an `error`. Unknown team and match names make an
expression invalid.

#### `GET /tournaments/{tournament_id}/schedule/warnings`

Problems in the whole schedule that don't block saving:

```json
{"warnings": [{"kind": "unknown_team", "message": "...", "matches": ["Semi 1"]}]}
```

`kind` is `unknown_team`, `missing_team`, `duplicate_team`, `cycle`,
`unknown_match_ref`, or `double_booked`. `double_booked` means two
matches that overlap on the planned schedule share a team or referee; a
slot with no team yet counts by its placeholder (`tag::A1`,
`Semi 1::winner`). Read the schedule itself with
[`GET /tournaments/{tournament_id}/matches`](#get-tournamentstournament_idmatches);
organizers see it before it's published.

#### `POST /tournaments/{tournament_id}/matches`

```json
{
  "name": "Semi 3",
  "field": "South",
  "schedule_type": "FAST",
  "previous_match_id": "5b1e...",
  "length": 30,
  "team1": "bears",
  "team2": "tag::Seed 4",
  "refs": ["otters"],
  "set_type": "SETS",
  "nsets": 3,
  "ribbon": false,
  "skip_condition": null
}
```

`STATIC` and `STATBREAK` matches take `start_time` (ISO 8601) instead of
`previous_match_id`, which `FAST`, `SAFE`, `BREAK`, and `JOIN` need (on
the same field). Names are unique, except that breaks and joins share a
name across fields. Breaks and joins have no teams. `set_type`, `nsets`,
and `stones_per_set` work as in a [schedule file](#put-tournamentstournament_idschedule).
Returns `201` with `{"match": {...}}`, in the shape of
`GET /tournaments/{tournament_id}/matches/{match_id}`.

#### `PATCH /tournaments/{tournament_id}/matches/{match_id}`

Send the properties to change, as above. A match's name can't change;
`previous_match_id` is needed again for matches that follow another, and
changing a `STATIC` match detaches it from its field's chain. Matches
that have started can't change (`409`). Allowed type changes are
`STATIC` to `SAFE`/`FAST`, `SAFE` to `FAST`, `BREAK`/`JOIN` to each
other (single-field ones only; use break groups otherwise), and
`STATBREAK` to `BREAK`. Returns `{"match": {...}}`.

#### `DELETE /tournaments/{tournament_id}/matches/{match_id}`

Deletes the match; the matches around it on its field close up.

#### `POST /tournaments/{tournament_id}/matches/bulk-length`

`{"match_ids": [...], "length": 25}` sets many lengths at once and
returns
`{"updated": 2, "results": [{"match_id": "5b1e...", "status": "updated"}]}`.
`status` is `updated`, `not_found`, `skipped_join`, or `skipped_locked`
(already started). A break stands for its whole group.

#### Break groups

A break or join across several fields, edited as one:

- `POST /tournaments/{tournament_id}/break-groups` with
  `{"name": "Lunch", "schedule_type": "BREAK", "fields": ["North", "South"], "length": 60}`
  returns `201` with
  `{"break_group": {"name": "Lunch", "schedule_type": "BREAK", "match_ids": [...]}}`.
  Each row goes at the end of its field unless `previous_match` maps a
  field name to the match it follows. `STATBREAK` groups need
  `start_time`; `JOIN` groups have no length.
- `PATCH /tournaments/{tournament_id}/break-groups/{name}` changes
  `length`, `start_time` (static breaks), `fields`, or `schedule_type`
  (`BREAK` and `JOIN` only).
- `DELETE /tournaments/{tournament_id}/break-groups/{name}`.

#### `POST /tournaments/{tournament_id}/schedule/push-back`

`{"minutes": 15, "day": "2026-07-04", "tz_offset_minutes": -420}`
delays the `STATIC` matches and static breaks that haven't started on
that local day (`tz_offset_minutes` is added to UTC to get local time).
Returns `{"moved": 6}`.

#### `POST /tournaments/{tournament_id}/schedule/update-references`

Fills `Match::winner` / `::loser` slots again from every finished
match, in case they got out of sync.

### Managing leagues

Like tournaments, these need the `manage` scope; all but creation need a
user who organizes the league.

- `POST /leagues` with `{"id": "metro-league", "name": "Metro League"}`
  returns `201` with `{"league": {...}}`; the user becomes its organizer.
  Add rounds with `POST /tournaments` and `"league": "metro-league"`.
- `GET /leagues/{league_id}/settings` returns
  `{"settings": {"id", "name", "about", "published", "registration", "organizers"}}`;
  `registration` has a tournament's keys plus `payment_info`.
- `PATCH /leagues/{league_id}` changes `about`, `published`, and
  registration settings, as for tournaments.
- `DELETE /leagues/{league_id}?confirm={league_id}` deletes a league
  with no tournaments left.
- `POST /leagues/{league_id}/organizers` and
  `DELETE /leagues/{league_id}/organizers/{user_type}/{user_id}` work
  like a tournament's.

### Registrations

Organizers can see every registration with its payment and waiver
status, register teams and players, record payments, and cancel
registrations. These need a token with the `manage` scope whose user
organizes the tournament or league (otherwise `403`). A tournament in a
league shares the league's registrations.

#### `GET /tournaments/{tournament_id}/registrations` and `GET /leagues/{league_id}/registrations`

Team registrations that aren't cancelled, and pending, confirmed, and
rejected player registrations:

```json
{
  "teams": [
    {
      "id": 31,
      "team": {"id": "jsmith-team", "name": "Smith Team Account"},
      "name": "Smith & Co",
      "shortname": "SMC",
      "status": "CONFIRMED",
      "registered_at": "2026-03-02T18:21:00Z",
      "payment": {
        "paid": true,
        "amount_paid": 40.0,
        "paid_at": "2026-03-03T10:00:00Z",
        "payment_method": "venmo",
        "payment_reference": "8812",
        "payment_notes": null
      }
    }
  ],
  "players": [
    {
      "id": 207,
      "player": {"id": "jsmith", "name": "Jane Smith"},
      "team": {"id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"},
      "jersey_name": "SMITH",
      "jersey_number": "7",
      "status": "PENDING_TEAM_APPROVAL",
      "registered_at": "2026-03-04T08:00:00Z",
      "waiver_status": "NOT_SIGNED",
      "payment": {"paid": false, "...": "..."}
    }
  ]
}
```

`id` is the registration id. A player's `status` is
`PENDING_TEAM_APPROVAL` (waiting for their team), `CONFIRMED`, or
`REJECTED`. `waiver_status` is `VALID`, `NOT_SIGNED`, `OUT_OF_DATE`
(signed an earlier waiver), or `null` when no waiver is required.

#### `POST /tournaments/{tournament_id}/registrations/teams`

Register a team, confirmed and marked paid:

```json
{"team_id": "jsmith-team", "name": "Smith & Co", "shortname": "SMC"}
```

`name` defaults to the team account's name; `shortname` is optional.
Returns `201` with `{"registration": {...}}` in the shape above. `400` when the
team is already registered or the tournament is full.

#### `POST /tournaments/{tournament_id}/registrations/players`

Register a player, confirmed and marked paid:

```json
{
  "player_id": "jsmith",
  "team_id": "jsmith-team",
  "jersey_name": "SMITH",
  "jersey_number": "7",
  "waiver_legal_name_signature": "Jane Smith"
}
```

Only `player_id` is required. `waiver_legal_name_signature` is required
when the tournament has a waiver. Returns `201` with `{"registration": {...}}`.

#### `PATCH .../registrations/teams/{registration_id}` and `PATCH .../registrations/players/{registration_id}`

Under `/tournaments/{tournament_id}` or `/leagues/{league_id}`. Replaces
the registration's payment details:

```json
{"paid": true, "amount_paid": 40, "payment_method": "venmo", "payment_reference": "8812", "payment_notes": ""}
```

`paid` is required; the other fields default to `0` and empty. Marking a
registration unpaid also voids its paid entry, including one bought
online. `amount_paid` in responses adds online payments to the recorded
amount, and the `payment_*` fields are only the recorded ones. Returns
`{"registration": {...}}`.

#### `DELETE .../registrations/teams/{team_id}` and `DELETE .../registrations/players/{player_id}`

Under `/tournaments/{tournament_id}` or `/leagues/{league_id}`. Cancels a
confirmed team registration, or a pending or confirmed player
registration, even when registration is closed. A cancelled team's
players stay registered without a team. A cancelled player's side
competition registrations in a standalone tournament are cancelled too.
The team or player keeps their paid entry, so they can register again
without paying. Returns `204`, or `404` when there is nothing to
cancel.

### Penalty types

The penalty categories referees attach to match notes. A tournament in
a league uses the league's penalty types. Listing them needs no scope;
changes need a token with the `manage` scope whose user organizes the
tournament or league.

#### `GET /tournaments/{tournament_id}/penalty-types` and `GET /leagues/{league_id}/penalty-types`

```json
{"penalty_types": [{"id": 4, "name": "Shield foul", "color": "e6194b", "description": "Rule 4.2"}]}
```

`color` is six hex digits.

#### `POST .../penalty-types`

```json
{"name": "Shield foul", "description": "Rule 4.2", "color": "#e6194b"}
```

`name` (up to 50 characters) is required. Without `color`, Arctos picks
one not yet used. Returns `201` with `{"penalty_type": {...}}`.

#### `PATCH .../penalty-types/{penalty_type_id}` and `DELETE .../penalty-types/{penalty_type_id}`

`PATCH` changes the fields present in the body (an invalid `color` is
ignored) and returns `{"penalty_type": {...}}`. `DELETE` returns `204`,
or `409` while a match note uses the penalty type.

### Side competitions (organizers)

Set up side competitions. These need a token with the `manage` scope
whose user organizes the tournament. Creating returns `201`. Responses are
`{"side_competition": {...}}` in the shape of
[`GET .../side-competitions/{side_competition_id}`](#get-tournamentstournament_idside-competitionsside_competition_id),
which also has `only_show_top_n_results`.

#### `POST /tournaments/{tournament_id}/side-competitions`

```json
{
  "name": "Dueling",
  "type": "DUELING",
  "description": "Best of three.",
  "allowed_weapons": ["LONG", "STAFF"],
  "only_show_top_n_results": 10,
  "schedule": {"start_time": "2026-05-01T12:00:00Z", "end_time": "2026-05-01T13:00:00Z", "field": "Field 1"}
}
```

`name` and `type` (`DUELING`, `CHAIN_BREAKING`, or `OTHER`) are
required. `allowed_weapons` defaults to every weapon.
`only_show_top_n_results` limits the public results (`null` shows all).
`schedule` (optional) also puts the side competition on the schedule,
with the fields of a [schedule item](#schedule-items) other than `title`
and `description`.

#### `PATCH /tournaments/{tournament_id}/side-competitions/{side_competition_id}`

Changes the fields present in the body: `name`, `type`, `description`,
`registration_open`, `active`, `track_losses`, `track_chain_losses`,
`track_break_losses`, `track_matchups`, `allowed_weapons`, and
`only_show_top_n_results`. Registrants whose weapon is no longer allowed
are deregistered.

#### `DELETE /tournaments/{tournament_id}/side-competitions/{side_competition_id}`

Deletes the side competition with its registrations and results.
Returns `204`.

#### `POST /tournaments/{tournament_id}/side-competitions/{side_competition_id}/finalize`

Permanently finalizes the results.

### Schedule items

Activities on the schedule other than matches (ceremonies, meals,
clinics). Listing them follows the schedule's visibility, like
[`/matches`](#get-tournamentstournament_idmatches); changes need a token
with the `manage` scope whose user organizes the tournament.

#### `GET /tournaments/{tournament_id}/schedule-items`

By start time:

```json
{
  "schedule_items": [
    {
      "id": 9,
      "sidecomp_id": null,
      "title": "Opening ceremony",
      "description": "All teams on Field 1.",
      "start_time": "2026-05-01T13:00:00Z",
      "end_time": "2026-05-01T13:30:00Z",
      "field": "Field 1",
      "location": null
    }
  ]
}
```

An item with a `sidecomp_id` schedules that side competition and shows
its name and description.

#### `POST /tournaments/{tournament_id}/schedule-items`

```json
{
  "title": "Opening ceremony",
  "description": "All teams on Field 1.",
  "start_time": "2026-05-01T09:00:00-04:00",
  "end_time": "2026-05-01T09:30:00-04:00",
  "field": "Field 1",
  "location": null,
  "sidecomp_id": null,
  "request_id": "6f1c3e1a-8a8c-4a52-9a43-2b1f0b0c7c11"
}
```

Times need a timezone, and an item lasts at most 31 days. `title` is
required unless `sidecomp_id` names one of the tournament's side
competitions. `field` must be one of the tournament's fields. Returns
`201` with `{"schedule_item": {...}}`. `request_id` (optional, a UUID)
makes retries safe: sending the same item again returns `200` with the
saved one, and different details with a used `request_id` get `409`.

#### `PUT /tournaments/{tournament_id}/schedule-items/{item_id}` and `DELETE /tournaments/{tournament_id}/schedule-items/{item_id}`

`PUT` replaces the item's details (same body; `request_id` is ignored)
and returns `{"schedule_item": {...}}`. `DELETE` returns `204`.

### Brackets

The bracket diagram: matches placed on a canvas and connected by wires,
plus text, labeled teams, and images. Reading it needs the bracket to be
published (otherwise `403`), except for a token with the `footage` or
`manage` scope whose user organizes the tournament. Changes need the
`manage` scope and an organizer.

#### `GET /tournaments/{tournament_id}/bracket`

```json
{
  "bracket": {
    "published": true,
    "placements": [
      {
        "match": "5b1e...",
        "placed": true,
        "x_pos": 300.0,
        "y_pos": 40.0,
        "width": 280.0,
        "height": 100.0,
        "team1": "NET",
        "team2": "LABEL",
        "inputs_flipped": false
      }
    ],
    "texts": [{"id": "title", "text": "Finals", "x_pos": 10.0, "y_pos": 10.0, "size": 18.0}],
    "labeled_teams": [
      {"id": "seed-1", "label": "1st seed", "team": "tag::Seed 1", "kind": "LABEL", "x_pos": 10.0, "y_pos": 80.0}
    ],
    "images": [
      {"id": "logo", "image": "uploads/brackets/...", "x_pos": 0.0, "y_pos": 0.0, "width": 200.0, "height": 120.0}
    ]
  }
}
```

`match` is a match id. A placement with `placed: false` is off the
canvas but keeps its settings. `team1` and `team2` are `NET` (a wire
from the match a `Match::winner` or `Match::loser` slot comes from) or
`LABEL` (a text label). A labeled team's `team` is a team id, a `tag::`
reference, or a `Match::winner` reference. `image` is a path on the
Arctos host under `/static/`.

#### `PATCH /tournaments/{tournament_id}/bracket`

`{"published": true}` shows the bracket to everyone; `false` hides it.
Returns `{"bracket": {...}}`.

#### `PUT /tournaments/{tournament_id}/bracket/layout`

Saves the canvas, in the shape `GET` returns:

```json
{
  "placements": [{"match": "5b1e...", "x_pos": 300, "y_pos": 40, "team1": "NET", "team2": "LABEL"}],
  "texts": [{"id": "title", "text": "Finals", "x_pos": 10, "y_pos": 10, "size": 18}],
  "labeled_teams": [],
  "images": [],
  "clear_missing": false
}
```

`placements` is required; `texts`, `labeled_teams`, and `images` are
optional. Rows are matched by `match` or `id`, then created or updated;
choose your own ids for new text and labeled teams. Breaks and joins
can't be placed. A match without both `x_pos` and `y_pos` is unplaced.
`NET` on a slot that doesn't come from another match becomes `LABEL`.
With `clear_missing: true`, rows left out of a list you sent are
deleted. Images can only refer to files already uploaded on the
website. Returns `{"bracket": {...}}`.
