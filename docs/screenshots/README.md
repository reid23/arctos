# Mobile redesign previews

Captured from the optimized Rust/Dioxus bundle against a migrated, isolated copy
of a database snapshot. Public views show existing events, leagues, and profiles;
organizer controls and registration examples use added fictional demo accounts.
California Jugger/Light is used unless noted otherwise.

Only the top-level Events, Leagues, Players, Teams, and Stones introductions
can be collapsed with a chevron and restored in Appearance settings. Profile, tournament, and league detail headers stay visible.

- `home-mobile.png`: 360px Events page with date filters and contextual Organize control.
- `home-dismissed-mobile.png`: 360px viewport after dismissing the Events introduction.
- `home-desktop.png`: 1280px full page.
- `organize-mobile.png`: 360px Events page with its tournament creation action open.
- `leagues-mobile.png`: 360px league directory with Active/Past filters and season dates.
- `event-desktop.png`: 1280px event banner, navigation, and information.
- `event-mobile.png`: 360px full page with event banner and navigation tabs.
- `event-schedule-mobile.png`: 360px schedule within the shared event layout.
- `league-mobile.png`: 360px league banner, navigation, and information.
- `league-event-mobile.png`: 360px league event with compact registered-team rows.
- `player-profile-mobile.png`: 360px Reid Dye profile with cover, avatar, and information/history tabs.
- `team-profile-mobile.png`: 360px Ursae Majoris profile with cover, avatar, tabs, and a compact past-event count.
- `players-mobile.png`: 360px player directory with past-event counts.
- `home-registration-mobile.png`: pending approval and unpaid badges for a fictional player registration.
- `organizer-tools-mobile.png`: compact organizer menu in the event banner.
- `organizer-settings-mobile.png`: flat settings with expanded referee options and consistent checkboxes.
- `penalty-types-mobile.png`: Add beside the penalty heading, with labeled fields stacked for phones.
- `profile-crop-mobile.png`: image positioning within the profile preview, with one Zoom control.
- `profile-edit-mobile.png`: cover and avatar controls above unframed editing fields.
- `profile-preview-mobile.png`: the unsaved profile preview with a Markdown bio on a phone.
- `profile-edit-desktop.png`: live Markdown preview beside the editing fields.
- `profile-history-mobile.png`: a plain history table with contained horizontal scrolling.
- `stones-mobile.png`: 360px playback and sound selection controls.
- `match-mobile.png`: 360px full page, including point controls and fixed match actions.
- `appearance-mobile.png`: 360px viewport with the Appearance panel open in California Jugger/Light.
- `home-dark-mobile.png`: 360px viewport showing discovery in California Jugger/Dark.
- `home-field-mobile.png`: 360px viewport showing discovery in Field/Light.
- `home-neutral-mobile.png`: 360px viewport showing discovery in Neutral/Light.

To run the application against a snapshot, use
`uv run python -m scripts.dev_demo --snapshot /absolute/path/to/snapshot.db`.
The source database is opened read-only; migrations and fixtures affect only the
local copy. See [`TESTING.md`](../../TESTING.md#mobile-browser-verification) for
browser checks. Available records, dates, and live timers depend on the database
and capture time. The editor previews use a fictional account and do not save
profile changes; the image is selected locally from the snapshot's existing media.
