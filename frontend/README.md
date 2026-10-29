# Rust frontend

Arctos uses [Dioxus](https://dioxuslabs.com/) compiled to WebAssembly for
interactive workflows. The browser calls Flask under `/_api/`. Flask also owns
the initial document: public routing serves Events, Leagues, and their Info
pages as HTML. Interactive destinations display startup feedback before Dioxus.
See [entry architecture](../docs/public-html-architecture.md) and
[deployment](../docs/DEPLOY.md).

## Run locally

From the repository root:

```bash
uv sync --group test
just setup-frontend
just demo
```

In another terminal, run `just frontend`, then open `http://127.0.0.1:8080`.
Use `just dev` instead of `just demo` for your configured local database.
The [root README](../README.md#running-the-app-locally) explains demo accounts,
snapshot copies, migrations, and phone access.

`Dioxus.toml` proxies `/_api/`, `/api/`, and `/static/` to Flask on port 5006.
Keep the browser and API on this shared origin for normal development.
`ARCTOS_API_BASE` is an optional **build-time** override for an intentionally
separate backend; it also requires compatible CORS and cookie settings.

For a production bundle:

```bash
cd frontend
dx bundle --release
```

Deploy the output path reported by Dioxus. The frontend builds with Rust and
serves bundled CSS and fonts locally. Dependencies and licenses are in
[`assets/vendor/`](assets/vendor/README.md).

Exercise Flask public entry on port 5006 with public routing enabled and a
prepared release bundle. After bundling, run `scripts/prepare_public_assets.py`
from the repository root with `--dist` set to Dioxus's public output directory.
Set `ARCTOS_FRONTEND_DIST` to the same directory. The
[script guide](../scripts/README.md#public-html-assets-and-browser-checks)
contains preparation and browser commands. `dx serve` on port 8080 serves the
application directly.

## Code map

| File or directory | Responsibility |
| --- | --- |
| `src/main.rs` | Route declarations and application startup |
| `src/public_navigation.rs` | Persistent app history and the parked public-document navigation bridge |
| `src/api.rs`, `src/api/public_events.rs` | HTTP requests, credentials, response errors, and the paginated Events API |
| `src/league_cache.rs` | Account/date-scoped Leagues cache and invalidation |
| `src/types.rs` | Shared API response and request types |
| `src/auth.rs` | Shared account loading and retry state |
| `src/pages/` | Route views and their styles |
| `src/components/` | Reusable controls, headers, tables, dialogs, and image editors |
| `src/theme.css` | Semantic colors, Bootstrap mappings, navigation, and common controls |
| `src/appearance.rs` | Theme/mode preferences and browser listeners |
| `src/viewport.rs` | Measured header and available viewport height |
| `src/time_format.rs` | Display formatting for dates and timestamps |
| `src/time_sync.rs`, `src/pages/stones.rs` | Clock synchronization and playback counter logic |

## Routes and data

Use the `Route` enum in `src/main.rs` as the source of truth. Some routes have
query variants for filters or sections; preserve those parameters and browser
Back/Forward behavior when adding navigation. Event sections use real links,
with `aria-current="page"` on the current destination.

With public routing enabled, Flask serves `/` and `/leagues` (including
`q`, `view`, and `page`) and published event/league Info as Flask documents.
Other tabs and workflows, authorized drafts, and explicit `?app=1` requests
use the application.

Documents marked `data-public-background="true"` initialize a parked runtime.
`public_navigation.rs` activates its Router through the cancelable navigation
bridge. While startup is pending or failed, marked reading links
and GET forms replace the public document with fresh Flask HTML, preserving
the single runtime. Once ready, navigation activates the application.
Subsequent public reading and workflows stay in the running app. History
preserves raw queries, page numbers, fragments, return parameters, and native
modified/external/download behavior. Dedicated output routes retain their own
documents.

League events register through their league. A direct event registration URL
redirects to the league registration page before displaying a form. The
same registration URLs dispatch already-registered players and teams to the
existing registration editor, preserving save, cancel, withdrawal, and roster
access. The backend's `can_view_schedule` flag controls schedule access. The tab remains
named **Schedule**; it is disabled without access, and authorized organizers
can open an unpublished schedule. Editing is a separate route.

Pages use functions in `src/api.rs` rather than constructing requests directly.
The wrapper includes session credentials. Update the matching Rust type when a
backend response changes. Keep authentication failure distinct from a temporary
account-service failure: `auth::use_account()` exposes that distinction and
Layout provides the shared retry notice.

`PublicPageService` selects request-scoped view models for Jinja. Dioxus Events
uses the same paginated `DirectoryPage` through `/_api/public/events`.
`PublicLeagueService` selects the bounded server directory. Dioxus applies
24-item pagination and query-filtered counts to its account/date-scoped Leagues
response, which it reuses for up to 60 seconds. Writes, account changes, expiry,
and Retry refresh data.

Public CSS is generated from the frontend sources by `just public-assets` and
by release preparation. Restart Flask after regeneration to refresh its cached
asset versions and compressed responses. Generated files are ignored by Git. `public.css`
contains native-control adapters. Review shared component changes in both the
Flask documents and the running application.

`boot.js` displays startup feedback for interactive destinations;
`background.js` keeps public reading available until an application route and
its styles are ready. Both preserve appearance preferences and a single
visible header. See [architecture](../docs/public-html-architecture.md) and
[verification](../docs/public-html-verification.md) for the handoff contract
and browser checks.

## UI conventions

The normal app wrapper owns the theme through `data-palette` and
`data-bs-theme`. California Jugger, Neutral, and Monokai each support Light, Dark,
and System mode. Preferences are local to the browser and synchronize across
its tabs. OBS and fixed-size output routes keep their own presentation.

Use semantic color variables rather than hardcoded brand colors:

- `--ui-bg`, `--ui-surface`, and `--ui-border` for containers.
- `--ui-text` and `--ui-muted` for copy.
- `--ui-link` for links and focus indicators.
- `--ui-accent` with `--ui-accent-contrast` for filled actions and selected states.
- `--ui-primary-*` overrides for primary buttons when they need a different fill
  from other indicators, as in Monokai Dark.
- Status tokens for success, warning, and error states.

Keep primary actions visible, give icon buttons accessible names, and use
44px touch targets. Use ordinary links for navigation and buttons for actions.
More and Organize are disclosures with normal link tab order; Appearance is a
settings dialog. Only the top-level Events, Leagues, Players, Teams, and Stones
introductions can be collapsed. The current page's Appearance setting restores
its introduction. Detail headers remain visible.

Use flat `edit-section` groups for forms and native `details` for secondary
settings. Label each input and associate hints/errors with it. Keep drafts
mounted during saves and errors. Explain disabled actions near the control,
and distinguish a validation error from a failed service request.

Historical records use native tables with visible column headings and a named,
focusable scroll container on narrow screens. Compact dates, numbers, and
status cells use the shared table classes; long names and notes must still wrap.
Do not apply global no-wrap rules to every cell.

Use `components::Modal` for blocking dialogs. Supply a unique ID and visible
labelled heading, an appropriate initial focus target, and a return-focus target
when the opener can disappear. Pending writes must guard their handlers and
set `dismissible: false`. Use `dismiss_on_backdrop: false` when a background tap
should not dismiss an editor. Docked schedule editors and anchored menus are
separate patterns, not dialogs.

`viewport.rs` measures the normal header and unzoomed visual viewport, with
listener cleanup on unmount. CSS fallbacks and safe-area insets keep dialogs
scrollable in short space. Do not disable browser zoom. Actual virtual keyboard
and Safari behavior still require device checks.

## Profiles and images

Profiles show a 3:1 cover, overlapping avatar, identity, and URL-based sections.
Changing sections keeps mounted injury drafts and roster expansion state;
server permissions determine which records are available. Past-event counts
represent confirmed registration history in published events, not verified
attendance.

Profile editing stages text, avatar, and cover changes locally. Save sends one
multipart request; failed saves retain the draft. Preview renders Markdown
through the shared backend sanitizer and shows escaped text while formatting is
unavailable. Player phone numbers are excluded from the public preview.

Image editors support drag/tap positioning, arrow keys, Zoom, and Reset position.
Reset centers the pan without changing zoom. Profile images save with the
profile; tournament and league banner controls save independently in Settings.
The settings form waits for a pending image edit to be saved or cancelled.

The server validates still JPEG, PNG, and WebP uploads, limits file size and
pixel dimensions, and removes metadata. Files live under `static/uploads`;
models store relative paths. See [migration and storage notes](../migrations/README.md#profile-and-event-banners)
before deploying against an existing database.

## Specialist tools

Keep bracket and schedule geometry in their dedicated modules/stylesheets.
Do not alter saved coordinates or dependency semantics for a visual change.
The Stones counter uses the synchronized 1.5-second beat grid. Pause retains
its count, Reset stops playback and clears it, and a sound preview does not
advance it. Calibration cancellation restores the saved timing snapshot.

`manage_footage.rs` lists, filters, opens, and removes footage records.
The match page plays linked footage and seeks to recorded points. Upload/link
endpoints are implemented in `app/routes/tournaments/footage.py`; do not assume
that every API capability has a management-page button.

## Checks and troubleshooting

Run `just frontend-test` for host-side Rust tests. Check the browser target with:

```bash
cd frontend
cargo check --locked --target wasm32-unknown-unknown
```

See [TESTING.md](../TESTING.md#mobile-browser-verification) for browser checks
and device coverage limits. Choose checks for the changed behavior instead of
repeating every browser suite after a cosmetic edit.

If requests fail, confirm Flask is listening on `127.0.0.1:5006` and that the
browser requests `/_api/tournaments` through the frontend origin. Restart
`dx serve` after proxy changes. For the normal proxy, leave `ARCTOS_CORS_DEV`
unset or `0`; an HTTP LAN address cannot use secure cross-origin cookies.
