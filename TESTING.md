# Testing

## Setup

Install dependencies including the test group:

```bash
uv sync --group test
```

## Running Tests

```bash
just test          # full suite
just unit          # unit tests only (fast, no DB)
just integration   # integration tests only
```

Without `just`:

```bash
uv run pytest tests/
uv run pytest tests/ -m unit
uv run pytest tests/ -m integration
```

Useful flags:

```bash
just test -k "registration"   # filter by name
just test --tb=long           # full tracebacks
```

## Coverage

Run the suite with coverage:

```bash
just coverage
just coverage -k "registration"   # filtered coverage run
```

`just coverage` disables the project-wide coverage floor so filtered
runs can report useful local numbers without failing because they only
exercise part of the app.

Run the CI-style coverage gate:

```bash
just coverage-check
```

Without `just`:

```bash
uv run pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=0
uv run pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=0 -k "registration"
```

Generate an HTML report (lands in `htmlcov/`, gitignored):

```bash
just coverage --cov-report=html
open htmlcov/index.html                  # or xdg-open on Linux
```

Coverage settings live under `[tool.coverage.*]` in `pyproject.toml`:

- **Source** is `app/` (tests, scripts, and migrations are not measured).
- **Branch coverage** is on, so untested `else` branches count as misses.
- **`fail_under = 30`** is a soft floor that catches significant
  regressions without blocking small fluctuations. CI enforces it with
  `just coverage-check`. Use the generated report for current branch coverage;
  raise the floor when overall coverage grows.

## Structure

```
tests/
├── conftest.py          # shared fixtures (app, test_db, client, tournament, player, team, …)
├── utils.py             # helpers: make_registrable_config(), login_as()
├── test_basic.py
├── test_dependencies.py
├── test_match_start.py
├── test_matches_api.py
├── test_permissions.py
├── test_registration_flow.py
├── test_registration_reregister.py
├── test_tournament_service.py
├── test_update_tags_recompute.py
└── unit/
    ├── test_services_unit.py
    └── test_tag_resolution.py
```

## Markers

| Marker        | Meaning                          |
|---------------|----------------------------------|
| `unit`        | Pure logic, no database or HTTP  |
| `integration` | Hits the SQLite test DB via HTTP |
| `slow`        | Long-running tests               |

Mark a test:

```python
@pytest.mark.unit
def test_something():
    ...
```

## Key Fixtures

All fixtures are defined in `tests/conftest.py`.
- See [scope](https://docs.pytest.org/en/6.2.x/fixture.html#scope-sharing-fixtures-across-classes-modules-packages-or-session)


| Fixture             | Scope    | Description                                      |
|---------------------|----------|--------------------------------------------------|
| `app`               | session  | Flask app wired to a temporary SQLite file       |
| `test_db`           | function | Drops and recreates all tables before each test  |
| `client`            | function | `FlaskLoginClient` test client                   |
| `tournament`        | function | Published tournament with two fields             |
| `player`            | function | A `Player` row with password set                 |
| `team`              | function | A `Team` row with password set                   |
| `team_registration` | function | Confirmed, paid `TeamRegistration`               |
| `head_ref_player`   | function | Player whose ID is in `head_refs_allowed_list`   |

`test_db` keeps the app context open for the entire test, so ORM objects created by other fixtures stay attached to the session without needing extra `with app.app_context()` blocks.

## Utilities

**`make_registrable_config(**kwargs)`** — creates a `RegistrableConfig` with safe defaults (all registration closed). Pass keyword arguments to override specific fields.

**`login_as(client, user)`** — injects Flask-Login session keys so a user is considered authenticated without going through the login endpoint.

## Configuration

pytest options live in `pyproject.toml` under `[tool.pytest.ini_options]`.

---

## Best Practices

For general pytest guidance see the [official how-to guides](https://docs.pytest.org/en/stable/how-to/index.html). Project-specific rules are below.

**Mark every test** with `@pytest.mark.unit` or `@pytest.mark.integration` so it can be run in isolation.

**Use shared fixtures** from `tests/conftest.py` (`test_db`, `client`, `tournament`, `player`, `team`, …). Only write a new fixture when something genuinely test-specific is needed.

**One app context per test, owned by `test_db`** — `test_db` opens an app context that lives for the whole test. Fixtures that depend on `test_db` should write directly to the session; don't push a second `with app.app_context()` inside them.

```python
# good
@pytest.fixture
def my_thing(test_db):
    obj = MyModel(...)
    db.session.add(obj)
    db.session.commit()
    db.session.refresh(obj)
    return obj

# bad — second context closes before the test body runs
@pytest.fixture
def my_thing(app, test_db):
    with app.app_context():
        ...
```

**Reading DB state after HTTP calls** — the test client handles requests in its own context. Wrap post-request queries in `with app.app_context()`:

```python
resp = client.post("/_api/...")
assert resp.status_code == 200

with app.app_context():
    row = MyModel.query.filter_by(...).first()
    assert row.status == "CONFIRMED"
```

**API endpoints return JSON, not redirects** — all `/_api/` routes return `200` on success and `400` on validation failure. Don't assert for `302`.

**Creating tournaments** — `Tournament` requires a `registrable_config_id`. Use `make_registrable_config()`:

```python
from tests.utils import make_registrable_config

cfg = make_registrable_config(registration_open=True, team_registration_open=True)
t = Tournament(url="my-event", ..., registrable_config_id=cfg.id)
```

**Don't use `db.session.begin()`** — `test_db` already manages the transaction. Call `db.session.add()` / `db.session.commit()` directly.

## Images, migrations, and demo snapshots

Image tests exercise raw-byte avatar and banner uploads independently of the
browser crop editor. They cover file and decoded-pixel limits, still-image
validation, metadata removal, typed account ownership and organizer access,
failed writes, unique image URLs, and conservative cleanup of existing files.
`tests/unit/test_markdown.py` checks supported formatting, existing documentation,
and removal of unsafe HTML, attributes, and URL schemes through the shared renderer.
`tests/test_profile_edits.py` covers atomic multipart text/avatar/cover updates,
existing JSON clients, malformed and conflicting fields, model limits, partial
image failures, and cleanup after database or rollback failures.
Migration tests verify the nullable banner columns introduced by revisions
`0023_profile_banners` and `0024_event_banners` without replacing existing rows.

The snapshot launcher has a focused test suite:

```bash
uv run pytest tests/unit/test_dev_demo.py -n 4
```

It constructs temporary databases; no personal snapshot is needed. Checks
include migration from a known older revision, byte-for-byte preservation of
the source and existing demo, additive fixtures, password/edit preservation,
reserved IDs across both account types, committed WAL data, unknown-revision
rejection, failed preparation cleanup, and atomic publication without
overwriting a concurrently created destination. `--seed-only` is checked
without starting Flask. See [the local demo instructions](README.md#try-the-interface-with-sample-events)
for the optional snapshot launcher.

Pure Rust tests run separately with `just frontend-test`; this includes image
header validation and crop geometry as well as counters, time sync, and UI
state helpers.


## Mobile browser verification

The redesign has a repeatable Chromium smoke check against the isolated demo.
It does not run as part of ordinary backend pytest, because it needs a built
WASM frontend and a running server.

```bash
just demo
# In another terminal:
just frontend
# In a third terminal:
uv run playwright install chromium
uv run python scripts/check_mobile_ui.py --screenshots /tmp/arctos-ui
```

Alternatively, point `--browser-executable` at an existing Chrome/Chromium
binary. Use `--base-url http://127.0.0.1:8443` if the frontend uses a different
port. The script requires the demo-only marker endpoint, rejects non-local
origins, and blocks mutations except demo login and read-only Markdown
rendering. It checks phones (360px and 390px), tablet (768px), and desktop
(1280px), including discovery, account forms, tournament/league pages,
profiles, schedules, operator/registration tools, and side competitions.
It also checks search/date filters, top-level introduction dismissal/restoration and persistence,
profile tabs and settings disclosures, unsaved injury-edit retention, keyboard roster
expansion, validation input retention, phone-sized
point controls, modern browser input patterns, and a simulated failed match
finalization without changing a real match. Phone runs exercise bracket
dragging, pointer capture, cancellation, and panning against a browser-only
fixture; no layout is saved to the backend.

Run `uv run python scripts/check_mobile_ui.py --workflow registration-and-schedule`
for registration destinations, league-event redirects, and schedule availability.
These checks use browser-only responses to cover unpublished schedules and
unregistered accounts without changing demo records.

Run `uv run python scripts/check_mobile_ui.py --workflow discovery-controls`
for Events/Leagues navigation, narrow-screen discovery toolbars, contextual
Organize menus, and keyboard dismissal. League fixtures cover Active/Past date
boundaries, counts, and search. Creation checks verify guest login destinations,
retained input after errors, and successful navigation to draft Settings.
Creation responses are intercepted in the browser; no draft is saved.

Check appearance preferences separately with the same running demo:

```bash
uv run python scripts/check_appearance_ui.py --screenshots /tmp/arctos-appearance
```

This covers all three themes in light and dark modes, distinct page/card
backgrounds, text contrast, mobile menu sizing, guest/player/team menu access,
keyboard controls, unsaved form preservation, saved-theme migration, browser
persistence, live system-mode changes, cross-tab updates, and unavailable
storage. Registration summaries are checked at narrow phone widths in each
theme, including approval, payment, and waiver states. It accepts the same
`--base-url` and
`--browser-executable` options. OBS and exact-size render routes retain their
independent presentation.

Check event sections and profile counts with:

```bash
uv run python scripts/check_event_ui.py --screenshots /tmp/arctos-events
```

This checks tournament, league, and league-event tabs, keyboard navigation,
registration and schedule destinations, roster details, and theme contrast at
320px and 360px. Direct Schedule, Results, and Bracket views keep the shared
banner visible; keyboard focus reveals offscreen navigation links without
scrolling the page. Browser history preserves schedule filters. It compares
player/team profile and directory counts with the
API, including zero and nonzero values supplied through GET-only browser
fixtures. Desktop views are checked at 1280px.

Check contextual registration actions with:

```bash
uv run python scripts/check_registration_ui.py --screenshots /tmp/arctos-registration
```

GET-only browser fixtures cover guest, player, and team actions; open and closed
registration; league registration destinations; and side-competition eligibility.
The checks verify one relevant primary action, preserved edit/roster access,
login return paths, and responsive placement and contrast across all themes.

Check calculated tournament and league standings with:

```bash
uv run python scripts/check_results_ui.py --screenshots /tmp/arctos-results
```

GET-only fixtures cover tied ranks, unplayed teams, podium trophies, keyboard
sorting without changing ranks, ribbon inclusion, expandable match details,
empty results, and resizing after collapse. Layout and trophy contrast checks
cover all three themes in light/dark and widths from 320px to 1280px.

Check page banners, profile navigation, uploads, and organizer access with:

```bash
uv run python scripts/check_profile_ui.py --screenshots /tmp/arctos-profiles
```

This covers independent dismissal/restoration of top-level introductions,
legacy preferences, profile tabs and permission-sensitive panels, keyboard
focus, and all themes at phone widths. Profile and event detail headers remain
visible even when older preferences marked them hidden, and offer no dismiss
or restore control. Profile geometry checks cover
the 3:1 cover, overlapping avatar, left-aligned identity, 900px desktop column,
and panels without card frames at 320px, 360px, and 1280px. Back/Forward checks
preserve unsaved injury inputs and expanded team rosters. Player and team edits
check player pronouns/pompfen, live Markdown previews, the phone Preview/Edit switch, private phone exclusion,
validation while fields are hidden, and staged removal/cropping without writes.
Intercepted multipart saves verify that text and both images travel together,
duplicate submissions are blocked, failures retain drafts, and retry sends the
same content. Markdown checks cover formatting, sanitization, delayed responses,
empty drafts, and retry after a rendering failure. Crop checks cover keyboard, mouse, touch, tap-to-center, position reset,
one shared Zoom control, exported PNG dimensions/pixels, and object URL cleanup.
Tournament and league banner uploads retain separate checks for failure,
success, removal, retained form input, and form blocking during a pending crop.
Organizer menus are checked for guest/owner visibility, links, Escape, outside
clicks, and narrow-screen layout. Upload tests do not write images or records to
the demo backend. Backend tests separately exercise image validation, typed
ownership, storage cleanup, and both database migrations. Pending image editors
are cancelled on navigation and release their temporary object URLs.

Check the shared headers and flat editing sections on event and league tools with:

```bash
uv run python scripts/check_event_tools_ui.py --screenshots /tmp/arctos-event-tools
```

This visits settings, registration, management, footage, rosters, schedule editing,
and side competitions at 320px, 360px, and 1280px. It checks header composition,
selected tabs, distinct event/side-competition menus, preserved drafts during
header updates, and schedule editor dialogs without saving data.

Check the Stones controls against the same demo:

```bash
uv run python scripts/check_stones_ui.py --screenshots /tmp/arctos-stones
```

This exercises Start, Pause, Reset, mute, sound previews, cached sounds while
offline, custom-file validation, and calibration controls. It observes real
Web Audio scheduling with Chromium output muted, including cancellation of a
pending start. It also checks all themes at 320px and 360px. The script accepts
the same browser and base-URL options as the other checks.

Camera/audio device behavior and external Google/YouTube integrations still
need real devices and configured credentials. Browser rendering alone cannot
verify Bluetooth delay, actual recording, or third-party account flows.
