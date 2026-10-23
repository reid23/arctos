# `frontend/` - the Dioxus SPA

A single-page application written in Rust using
[Dioxus](https://dioxuslabs.com/) and compiled to WebAssembly. nginx
serves the built bundle at `/`; the SPA calls the Flask backend at
`/_api/...`.

## Layout

The crate is a standard Dioxus + WASM project (`Cargo.toml`,
`Dioxus.toml`, `rust-toolchain.toml`, `index.html` shell). All
application code lives under `src/`:

- `main.rs` - router definition and bootstrap.
- `api.rs` - reqwest-based wrapper for every `/_api/` endpoint.
- `types.rs` - serde structs mirroring API responses.
- `pages/` - one file per route (mirrors the SPA's URL structure).
- `components/` - shared widget library.
- `time_format.rs`, `stones_filter.rs` - smaller cross-cutting modules;
  see each file's top comment.

## Running

From the repository root:

```bash
uv sync --group test
just setup-frontend
just demo                    # isolated sample backend on 127.0.0.1:5006
# In a second terminal:
just frontend                # frontend on 127.0.0.1:8080
```

Use `just dev` instead of `just demo` for your normal local database. To
preview a versioned snapshot, start the backend with:

```bash
uv run python -m scripts.dev_demo --snapshot /absolute/path/to/snapshot.db
```

It creates a separate migrated copy at `instance/snapshot-demo/demo.db`, adds
demo accounts without replacing existing records, and preserves the working
copy across restarts. The source and ordinary demo database stay untouched.
Dioxus proxies API and static requests to Flask, so requests and cookies use
the same origin. For phone testing on your LAN:
`just frontend --addr 0.0.0.0`, then open the computer's LAN address at port
8080. Recording requires a browser secure context (localhost or HTTPS).

For production, `cd frontend && dx bundle --release`; follow the build output
path reported by Dioxus and the repository deployment guide.

## Design system

Open Appearance from More, alongside Help and About. The separate account menu
contains profile, developer apps, and logout actions. Choose
Light, Dark, or System and one of three themes: California Jugger, Field, or
Neutral. Themes coordinate page backgrounds, cards, navigation, and controls.
California Jugger and System are the defaults. Saved `field-chalk` preferences
use Field; removed or unknown themes fall back to California Jugger without
changing the saved display mode. Preferences apply across routes and browser
tabs. When storage is blocked, changes still work for the current visit.

Events and Leagues sit beside each other in the primary navigation. Their
date filters lead the discovery toolbar; a quieter Organize disclosure offers
creation for the current page only. Any signed-in account can create a private
draft, then continue directly to its Settings page. Organizers can find their
unpublished leagues in the directory; other visitors see only public leagues.
League date ranges and event counts use published rounds. A league moves to
Past after its last round's final day; leagues without dated rounds stay Active.

More and Organize keep focus on their trigger when opened. Tab reaches their
native links, and Escape closes the disclosure and returns focus. Appearance
is an unselected text entry in More, with no icon. These controls follow the
[W3C disclosure navigation pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/examples/disclosure-navigation-hybrid/).

`src/theme.css` styles the shared shell and existing Bootstrap components on
all routes. `src/pages/public_pages.css` contains event/discovery layouts;
`src/pages/operator.css` contains match and stones refinements. Colors, focus,
touch spacing, safe areas, forms, cards, tables, and dialogs share the same
mobile-first system. Use the `--ui-*` semantic color variables for app surfaces,
text, and accents; use `--ui-link` for text and focus indicators, and
`--ui-accent` with `--ui-accent-contrast` for filled actions. Status colors
retain their meaning across palettes.

Personal registration summaries use icons and outlined badges for pending
approval, unpaid registrations, and missing or outdated waivers. Keep status
labels explicit: team approval and recorded payment are independent states.

Player and team profiles and directory cards show a shared “Past events” count.
The API counts unique published events with confirmed registrations after their
end date, using the start date when no end is set. League registrations expand
to eligible rounds without counting the league itself. This is a registration
history statistic, not verified attendance; upcoming and cancelled
registrations are excluded.

Event and league detail pages share `EventHeader`, `EventAbout`, and `EventTabs`.
The header follows the profile layout: a 3:1 cover above left-aligned identity,
metadata, registration, and a compact Organizer tools menu. Registration appears
beside the identity on desktop and fills the content width below it on phones.
Embedded tools and side competitions omit parent registration controls, leaving
the current form or side competition as the action target. Organizers can upload a banner
image from Settings; profile owners can do the same from Edit profile.
Navigation tabs link to information, rosters, schedules, results, brackets,
league rounds, and side events using real URLs. The selected link uses
`aria-current="page"`; unpublished schedules remain disabled for visitors who
lack preview permission. Native links
preserve keyboard access, bookmarks, and browser history
([W3C link guidance](https://www.w3.org/WAI/ARIA/apg/patterns/link/)).

Use `data-section` for compact table and list spacing. Editing forms use flat
`edit-section` sections with headings and dividers instead of card frames.
Historical records remain tables with visible column headers and contained
horizontal scrolling on narrow screens. Use native `details.section-disclosure` for secondary records
and settings, with a labeled summary and a count where useful. Keep current
event information and essential actions visible. Collapsing a section keeps its
form state intact. Only the top-level Events, Leagues, Players, Teams, and
Stones introductions have a collapse chevron at their top-right.
Appearance settings offer “Show page introduction” on those pages. Their visibility is saved independently per page,
separately from appearance preferences. Player, team, tournament, and league
detail headers always remain visible, with no dismiss or restore control.
Older saved hidden-header preferences for individual profiles or events are
ignored.

Player and team profiles use a centered column up to 900px wide, with a 3:1
cover, an overlapping circular avatar, then left-aligned name, handle,
location, and past-event count. Information, tournament history, and authorized
infractions, injuries, or notes occupy flat panels below the URL navigation.
Profile panels use content and dividers rather than card frames. Switching
tabs keeps unsaved injury inputs and roster expansion intact, including browser
Back/Forward; server permissions still determine which records are visible.

Organizer settings use checkboxes for saved choices such as registration
availability. Head referee permissions and secondary side competition rules
are under Advanced options, with the current policy or restrictions visible in
the summary. Penalty types use one full-width disclosure, closed initially;
organizer lists start expanded.

Edit profile shows the cover and avatar where they appear on the public profile.
Camera controls select or remove an image. Drag or tap the image to position it and use
one Zoom control. Arrow keys also adjust position; Reset position centers the image
without changing the zoom. Profiles, avatars, and event covers share these controls. Player bios and team descriptions use the same Markdown renderer as event
information, with explicit formatting guidance beside the field. The preview
tracks the current draft and falls back to escaped text if rendering fails.
The desktop preview updates beside the fields; on phones, Preview
profile switches between the full preview and fields without losing changes.
The profile's Save action stores text and both images together in one multipart
request. Cancel leaves the stored profile unchanged, and failed saves retain all
drafts for retry. Account settings are in a separate disclosure below the editor.
Tournament and league Settings retain their individual banner crop and save controls;
form submission waits until those pending image edits are saved or cancelled.

Uploads accept still JPEG, PNG, or WebP up to 8 MiB, 8192 pixels per side, and
24 megapixels. File headers are checked before browser decoding. The editor
exports covers up to 1500×500 and avatars up to 512×512. The server separately
validates and normalizes images, caps uncropped uploads at 2400×1600 for banners
and 1024×1024 for avatars, and removes embedded metadata. Images are stored as
files; the database holds only their paths. Fresh filenames avoid stale caches,
and old files are removed only after a successful replacement. Avatar cleanup
also preserves legacy files still referenced by another account. Banners and
avatars have separate paths and controls; changing one does not replace the
other.

The Stones page keeps playback controls beside the local stone count. Pause
retains the count; Reset stops playback and clears it without changing clock
calibration. The count follows the synchronized 1.5-second beat grid and
continues past each 100-stone progress block. Sound previews play one clip
without advancing the count. Built-in audio is cached for the current visit;
custom MP3s must be shorter than one stone. Manual calibration and sync
diagnostics remain available below the main controls.

These choices follow [NN/g's progressive disclosure guidance](https://www.nngroup.com/articles/progressive-disclosure/):
keep frequent tasks visible and reveal secondary details on request. Icon
buttons need accessible names and predictable focus behavior
([W3C button pattern](https://www.w3.org/WAI/ARIA/apg/patterns/button/)).
Use 44px touch targets, even for visually small controls
([W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html)).

`src/appearance.rs` owns browser preferences and system-mode listeners;
`src/components/appearance_menu.rs` and its adjacent stylesheet provide the
controls. The app wrapper
sets `data-palette` and Bootstrap's `data-bs-theme` without changing the
document's global theme. Keep canvas node/port geometry in the dedicated bracket
and schedule stylesheets. Scoreboard/OBS and exact-size offline frame routes
remain outside the app theme. Styles and icons are vendored under
`assets/vendor/` with licenses; no CSS/font CDN or Node build is needed.

## Routing

Routes are declared in [`src/main.rs`](src/main.rs) as a single
`Route` enum with `#[route("...")]` attributes. A representative slice:

```rust
#[route("/")]                          Index {}
#[route("/login")]                     Login {}
#[route("/leagues/:league_url")]       LeagueHome { league_url: String }
#[route("/:url")]                      TournamentHome { url: String }
#[route("/:url/schedule")]             Schedule { url: String }
#[route("/:url/manage")]               Manage { url: String }
#[route("/:url/run-match")]            RunMatch { url: String }
#[route("/:url/finalize-match")]       FinalizeMatch { url: String }
```

Note the order - the catch-all `/:url` for tournaments comes after all
the more specific paths. Adding a new top-level page means adding a
new route entry and a matching file in `src/pages/`.

League events use the league registration page. Direct event registration URLs
replace their history entry with that page before rendering a form. Schedule
links use the backend's `can_view_schedule` flag; authorized viewers see
“Preview schedule” before publication, while other visitors see an unavailable
control. Organizer schedule editing remains separate.

## Talking to the backend

[`src/api.rs`](src/api.rs) wraps `reqwest` with credentials and base
URL handling. There's one public `pub async fn` per backend endpoint
(e.g. `login`, `tournaments`, `start_match`, `finalize_match`,
`update_tournament_settings`, ...). Pages call them directly:

```rust
use crate::api;

let user = api::login(&username, &password).await?;
let detail = api::tournament_detail(&tournament_url).await?;
```

Every call goes through the `with_credentials` wrapper, which on
`wasm32` calls `fetch_credentials_include()` so the session cookie is
sent.

Two important details:

- **Base URL.** Uses `window.location.origin` in browser builds. The Dioxus
  development proxy forwards `/_api/`, `/api/`, and `/static/` to Flask on
  `127.0.0.1:5006`. An explicit `ARCTOS_API_BASE` build-time environment
  variable overrides this for separate-host deployments.
- **Credentials.** Without `fetch_credentials_include()`, all `/_api/`
  calls would 401.

## Types

`src/types.rs` holds serde structs that mirror Flask's JSON responses.
When you add or change a backend response shape, update the matching
struct here. Shapes only used in one page can live in that page's file.

## Footage

Tournament organizers attach match footage via the Manage Footage page
(`src/pages/manage_footage.rs`) and the backend footage API
(`app/routes/tournaments/footage.py`): either a YouTube link or a chunked
file upload that the server pushes to YouTube. Jump-to-point uses
`camera_timepoints` on the match page iframe player.

## Conventions

- One file per page in `src/pages/` (matches the route enum).
- Reusable widgets go in `src/components/`.
- API calls go through `src/api.rs`, not direct `reqwest` calls in pages.
- Format with `cargo fmt` before committing.
- The Rust toolchain is pinned in `rust-toolchain.toml` (channel `stable`).

## Testing

Backend: `just test` / `just coverage-check`.

Frontend: Pure Rust unit tests for time sync, counters, filters, display
helpers, image bounds, and crop geometry run on the host target with
`just frontend-test`.

Run the browser matrix against the demo with
`uv run python scripts/check_mobile_ui.py` from the repository root while
`just demo` and `just frontend` are running. It covers phone, tablet, and
desktop layouts plus common input and save failures. See
[`TESTING.md`](../TESTING.md#mobile-browser-verification) for browser setup
and the focused appearance, event, profile/crop, and Stones checks, plus the
remaining real-device checks.

## When the SPA cannot reach the backend

1. Start Flask on `127.0.0.1:5006` (`just demo` or `just dev`).
2. Open the frontend on port 8080. Its `/_api/tournaments` request should use
   that same origin. Restart `dx serve` after changing proxy configuration.
3. With the default proxy, keep `ARCTOS_CORS_DEV` unset/0; secure cross-origin
   cookies are unnecessary and will not work on an ordinary HTTP LAN address.
4. If intentionally setting `ARCTOS_API_BASE`, rebuild after changing it and
   configure backend CORS and secure cookies for that separate origin.
