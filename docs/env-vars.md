# Environment Variables

This is a list of all environment variables that can be used to
configure Arctos. For local development, copy `.env.example` to `.env`
and fill only the values you need.

`SECRET_KEY`
: Flask session signing secret. Format: any long random string. Generate
  one with `python -c "import os; print(os.urandom(32).hex())"`.

`ARCTOS_CORS_DEV`
: (default `0`) Set to `1` when the frontend and backend run on
  different local origins during development. Leave unset or `0` for the
  normal Dioxus development proxy and in production.

`ARCTOS_API_BASE`
: Optional frontend build-time API origin. The Rust frontend uses its own
  browser origin by default; Dioxus proxies `/_api/`, `/api/`, and `/static/`
  to Flask on port 5006 in development. Export an absolute URL before
  building only when intentionally using a separate API server.

`ARCTOS_PORT`
: (default `5006`) Backend port when running `python run_app.py`.

`SQLALCHEMY_DATABASE_URI`
: SQLAlchemy database URI. Format: `sqlite:///tournament.db` for a local
  SQLite file, or any SQLAlchemy-supported database URI.

`SCRIPT_NAME`
: URL path prefix for subpath deployments. Format: leading slash, no
  trailing slash; e.g. `/arctos`.

`EXTERNAL_BASE_URL`
: Public URL where this Arctos instance is reachable. Format: absolute
  URL without a trailing slash; e.g. `https://arctos.example.org`.

`ARCTOS_LOG_LEVEL`
: (default `INFO`) Python logging level; e.g. `DEBUG`, `INFO`,
  `WARNING`, `ERROR`.

`MAX_CONTENT_LENGTH_BYTES`
: (default `104857600`) Max upload/request size in bytes.

`SILLY_USERS`
: Enables the additional built-in Stones sounds for the listed accounts.
  Use exact, case-sensitive usernames separated by colons; for example,
  `alice:team-slug`. These are the `Player.id` or `Team.id` values used to
  sign in. Unlisted accounts and signed-out visitors receive Classic and
  Snare. Custom MP3 playback is available to everyone.

  Set this variable in the backend process environment and restart the
  backend after changing it. `just dev` and `just run` load `.env`; the
  isolated demo launcher uses the environment passed to its command.
  To enable the extra sounds for the demo organizer:

  ```sh
  SILLY_USERS=demo-organizer uv run python -m scripts.dev_demo
  ```

  Sign in as that account and open or reload Stones. The same environment
  prefix works with the demo launcher's `--snapshot` option.


## Scoreboard overlay rendering

The scoreboard overlay endpoint
(`GET /_api/tournaments/<url>/matches/<match_id>/scoreboard-overlay`)
renders the SPA's `/scoreboard-render` page in headless Chromium. See
[DEPLOY.md](DEPLOY.md#scoreboard-overlay-rendering) for server setup.

`ARCTOS_FRONTEND_URL`
: Origin serving the SPA, as reachable from the backend host. Format:
  absolute URL without a trailing slash; e.g. `https://arctos.example.org`
  in production (the same public origin), or `http://127.0.0.1:8080` in
  development. When unset the overlay endpoint returns 503.

`ARCTOS_CHROMIUM_PATH`
: Optional path to a system Chromium/Chrome executable; e.g.
  `/usr/bin/chromium`. When unset, Playwright's bundled Chromium is used
  (install it with `uv run playwright install chromium`).


## Registration forms

`GOOGLE_FORMS_SERVICE_ACCOUNT_FILE`
: Absolute path to a Google service-account JSON key. The file must be
  readable by the Arctos server and kept outside the repository and web root.
  When unset or unreadable, organizers cannot enable a response requirement.
  Events with an enabled requirement return a temporary error when responses
  cannot be checked.

To configure the server:

1. Enable the Google Sheets API in your Google Cloud project and create a
   service account. Domain-wide delegation is not required.
2. Place its JSON key on the server, restrict file access to the account
   running Arctos, set the variable above, and restart the server.

To connect an organizer's form:

1. Add a required short-answer question named **Arctos registration code**.
   Explain that Arctos fills it automatically and players should leave it unchanged.
2. In Google Forms, choose **More → Pre-fill form**, enter `ARCTOS_CODE` in
   that question, and copy the full generated link. Arctos uses this placeholder
   to identify the question; shortened links are not supported.
3. Publish the form for its intended audience. Share the response spreadsheet
   with the service-account email shown in Arctos registration settings, using
   the **Viewer** role.
4. In event or league settings, enter the prefilled form link, response
   spreadsheet link, and exact heading of the registration-code column.
   A spreadsheet link containing `gid` selects that tab; otherwise the first
   tab is used.

Each signed-in player opens a personalized link from Arctos. The link fills
the registration-code question; players submit the form and return to Arctos
to register. Arctos checks the player's code in the response sheet before
creating or confirming a registration. Organizers adding players and team
managers confirming pending requests use the same check.

Saving settings checks Sheet access and the column heading. Use a player
account to open its personalized link, submit the form, and verify registration
to check the complete setup. See Google's [prefilled-link and publishing
instructions](https://support.google.com/docs/answer/2839588?hl=en).

The integration requests Google's `spreadsheets.readonly` scope. Arctos reads
the selected tab's header and registration-code column. It stores the connection
settings and a private signing key for each registration configuration, not
form responses. Cached response codes are reused for up to
ten seconds; tab and column mappings are reused for up to one minute. Each
memory cache keeps at most 32 sheet configurations per worker. Every response
read rechecks the selected column's heading.

Codes are stable for each player and registration configuration. Changing the
form or its code question creates new codes; correcting the Sheet, tab, or
heading preserves them. Disabling the requirement clears its configuration.
Already-confirmed registrations remain confirmed.

A matching code associates a response row with the player's personalized
link. Google allows prefilled answers to be edited, and another person can use
a shared personalized link. Response-sheet editors can also alter its rows.

The Google service account has access to every spreadsheet shared with it.
Only share sheets intended for Arctos, and restrict response-sheet sharing
to authorized people. A separate spreadsheet containing only the registration-code
column can further limit the data available to the service account.

Google applies per-minute API quotas, including a per-account read limit.
The integration uses bounded requests and reports a temporary error when
Google is unavailable or a quota is exceeded. See Google's
[service-account sharing instructions](https://docs.cloud.google.com/integration-connectors/docs/connectors/gsc_google_sheets/configure),
[Sheets authorization scopes](https://developers.google.com/workspace/sheets/api/scopes),
and [usage limits](https://developers.google.com/workspace/sheets/api/limits).

## Google Oauth2 info

go to google cloud console for these

`GOOGLE_CLIENT_ID`
: OAuth client id from Google Cloud Console.

`GOOGLE_CLIENT_SECRET`
: OAuth client secret from Google Cloud Console.

`GOOGLE_REDIRECT_URI`
: Optional exact public callback URL, such as
  `http://localhost:8080/_api/auth/google/callback` for Dioxus development or
  `https://arctos.example.org/_api/auth/google/callback` in production.
  Must be an absolute HTTP(S) URL without credentials, query, or fragment.
  Include any deployment path prefix. Invalid values cause Google sign-in
  to fail with a configuration error.

When testing Google sign-in through `dx serve`, set `GOOGLE_REDIRECT_URI` to
the frontend's callback URL because the Dioxus proxy replaces the request's
Host header with Flask's address. Register that exact URL in Google Cloud
Console and open Arctos at the same origin: switching between `localhost`
and `127.0.0.1`, or between desktop and phone addresses, loses the session
cookie needed to verify the OAuth callback. A phone needs an accessible
HTTPS hostname accepted by Google for its callback; a desktop loopback URL
points at the phone itself. The callback must pass through the same proxy
as the initial login request so its state and session cookies are preserved.

When `GOOGLE_REDIRECT_URI` is unset, callback URLs are derived from the
incoming request host. If the same server
is reachable on multiple public domains, add each domain's
`/_api/auth/google/callback` URL to the authorized redirect URIs in Google
Cloud Console. Forwarded host headers are not trusted to choose this URL.


## Sign in with Arctos

Arctos's own OAuth provider (see [For Developers](developers.md)) refuses
requests that don't arrive over HTTPS. Production gets HTTPS from
gunicorn's TLS (`just run`) or from a proxy that forwards to it over
HTTPS. The reverse proxy must also forward `/api/` to gunicorn, like
`/_api/`.

`AUTHLIB_INSECURE_TRANSPORT`
: Set to `1` to allow the OAuth endpoints over plain HTTP, e.g. with
  `just dev`. Never set it in production.


## Youtube config

`YOUTUBE_UPLOAD_CLIENT_ID`
: Optional OAuth client id for upload. If unset, Arctos uses
  `GOOGLE_CLIENT_ID`.

`YOUTUBE_UPLOAD_CLIENT_SECRET`
: Optional OAuth client secret for upload. If unset, Arctos uses
  `GOOGLE_CLIENT_SECRET`.

`YOUTUBE_UPLOAD_REFRESH_TOKEN`
: OAuth refresh token for the upload account. Generate one with the
  Google OAuth playground or your preferred OAuth flow using the upload
  client id and secret.

`YOUTUBE_UPLOAD_PRIVACY_STATUS`
: (default `unlisted`) YouTube privacy status, usually `private`,
  `unlisted`, or `public`.

`YOUTUBE_UPLOAD_CATEGORY_ID`
: (default `22`) Numeric YouTube category id. The local example uses
  `17` for Sports.
