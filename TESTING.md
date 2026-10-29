# Testing

Choose checks for the behavior being changed. Backend tests verify domain rules,
permissions, data integrity, and API contracts. Rust tests verify pure frontend
logic. Browser checks verify interactions that need a rendered application.
A screenshot or passing compile check is not a substitute for those tests.

## Setup and commands

```bash
uv sync --group test
just test                     # backend pytest suite
just test -k registration     # focused backend tests
just frontend-test            # Rust tests on the host target
just lint
just typecheck
uv run ruff format --check .
```

`just unit` and `just integration` select the corresponding pytest markers.
You can pass pytest flags to `just test`, such as `-n 4` to limit parallel workers
or `--tb=long` for full tracebacks. The commands are defined in `justfile`;
pytest defaults and markers are in `pyproject.toml`.

Check the browser build target separately:

```bash
cd frontend
cargo check --locked --target wasm32-unknown-unknown
```

CI runs backend tests with coverage, Python lint/format and type checks, frontend unit
tests, and the WebAssembly compile check. Browser suites require running servers
and are separate from ordinary pytest.

## Type checks

Run `just typecheck` (or `uv run mypy`) to check the modules listed under
`[tool.mypy].files` in `pyproject.toml`. The same command runs in pre-commit
and CI. The checked scope covers `app/error_values.py`,
`app/utils/name_validation.py`, and their static API contracts in
`tests/typecheck/error_values.py`.

The contract file uses `assert_type` for inferred types and precise
`type: ignore` comments for deliberately invalid calls. Mypy's unused-ignore
check reports when an invalid call becomes accepted. These checks cover
callback types, error types, and decorated function signatures. Runtime
tests verify that `Option` and `Result` reject boolean conversion. Ordinary
string, collection, and numeric conditions need explicit predicates during
code review.

Add each fully typed module to the file list together with its contracts.
Imports outside that list are skipped; their implementations require their
own tests and review.

## Writing useful tests

Test outcomes and failure boundaries, rather than reproducing the implementation
or asserting incidental markup. Useful cases include:

- An unauthorized account cannot read or change another account's data.
- Invalid input leaves existing data unchanged and gives a usable error.
- A failed save retains its draft; an uncertain write is reconciled before retry.
- A transaction cannot leave half an update committed.
- A delayed response cannot replace a newer query or draft.
- A keyboard/touch interaction reaches the same action as a pointer click.

Use a focused regression test for a bug when practical. Cosmetic and wording
changes normally need review, not a new unit test. Run the relevant tests first;
repeat broad suites when the scope or a failure warrants it.

### Backend fixtures

Shared fixtures live in `tests/conftest.py`; helpers are in `tests/utils.py`.
See [tests/README.md](tests/README.md) for test organization.

| Fixture/helper | Purpose |
| --- | --- |
| `app` | Session-scoped Flask app with a temporary SQLite database |
| `test_db` | Recreates tables for each test and keeps its app context open |
| `client` | Flask test client using that database |
| `tournament`, `player`, `team` | Basic domain records |
| `team_registration`, `head_ref_player` | Registration and referee setup |
| `make_registrable_config()` | Registration settings with closed defaults |
| `login_as(client, user)` | Authenticated test session without a login request |

Fixtures that depend on `test_db` can use the session directly. Avoid opening
and closing an extra app context inside a fixture, which can detach the objects
it returns. Tests with multiple sessions or explicit transactions should manage
their lifetimes deliberately.

Standalone tournaments need a `RegistrableConfig`; league events inherit their
league's config. Use the shared helper instead of duplicating model defaults.
Assert the actual endpoint contract: API errors may be 400, 401, 403, 404, or 409,
and OAuth callbacks use redirects. Not every `/_api/` response is JSON.

Image and profile tests should cover ownership, accepted media, size/pixel limits,
metadata removal, atomic text/image updates, and cleanup on failure. Migration
and demo tests should use temporary databases and verify that source snapshots
and existing destination data are preserved. They must not depend on a personal
snapshot or production database.

## Coverage

```bash
just coverage                         # report without the project-wide floor
just coverage -k registration         # useful for focused investigation
just coverage --cov-report=html       # writes htmlcov/index.html
just coverage-check                   # CI-style floor
```

Coverage measures `app/`, including branches, with a configured minimum of 30%.
A filtered run does not represent project coverage, so `just coverage` disables
the floor for that run. Use the report to locate untested behavior; do not add
low-value assertions solely to increase the percentage.

## Mobile browser verification

Start an isolated demo and the frontend in separate terminals:

```bash
just demo
just frontend
```

Install Chromium and run a browser check from the repository root:

```bash
uv run playwright install chromium
uv run python scripts/check_mobile_ui.py --screenshots /tmp/arctos-ui
```

Playwright is installed with the application dependencies because scoreboard
rendering also uses it. Pass
`--browser-executable /path/to/chromium` to use an installed browser, or
`--base-url http://127.0.0.1:8080` to select the frontend URL. The runners require
a local origin and the demo-only marker endpoint. They block real data writes,
except demo login and read-only Markdown rendering, and intercept responses for
write/error scenarios. Do not remove those guards to test against a real site.

Run the smallest relevant suite. Each accepts the same base-URL, browser, and
screenshot options; use `--help` for its supported filters.

| Script in `scripts/` | Main purpose |
| --- | --- |
| `check_mobile_ui.py` | Common routes, discovery, registration, live controls, and recovery |
| `check_appearance_ui.py` | Theme/mode preferences, persistence, menus, and contrast |
| `check_event_ui.py` | Event navigation, rosters, registration destinations, and schedule access |
| `check_registration_ui.py` | Contextual registration actions for guest/player/team accounts |
| `check_results_ui.py` | Populated and empty results, statistics, keyboard sorting, ribbon inclusion, and match expansion |
| `check_sidecomp_results_ui.py` | Weapon filters, group selection, personal results, access changes, and delayed responses |
| `check_profile_ui.py` | Profile sections, draft persistence, Markdown, and staged image edits |
| `check_event_tools_ui.py` | Organizer tools, settings, shared headers, and retained input |
| `check_stones_ui.py` | Playback state, mute/preview, audio caching, and calibration controls |

For example:

```bash
uv run python scripts/check_mobile_ui.py --workflow registration-and-schedule
uv run python scripts/check_profile_ui.py --screenshots /tmp/arctos-profiles
```

Browser response fixtures exercise UI behavior; backend tests separately verify
that real services enforce permissions and persist or roll back changes.
Save screenshot output outside the checkout and attach relevant images directly
to the pull request. Include the viewport size and state shown. Screenshots are
visual references, not an exhaustive test record.

### Manual checks and device limits

For changed forms, check failed reads, invalid fields, delayed saves, and rejected
writes. No editable blank record should appear after a failed load. Drafts must
survive failures; feedback must identify the relevant field or service problem.
Pending writes must prevent duplicate actions.

For dialogs, check initial focus, Tab/Shift+Tab, Escape, background taps, nested
dialogs, and focus return. Confirm the intended Cancel behavior: a provisional
bracket item is removed, a saved item is kept, and calibration restores its
snapshot. Pending writes must not be dismissed. Refreshing schedule helpers
must retain other edited rows and block writes after a failed refresh.

Review phones at 320px and 390px, a short landscape viewport, and desktop.
Tables should scroll within their own container. Long dialogs must reach every
field and action. A virtual keyboard must not cover the only save/close control.
Check themes, enlarged text, and keyboard focus as relevant to the change.

Chromium viewport emulation does not reproduce an iPhone. For an Apple-device
issue, record the route, device model, iOS/browser version, orientation, and
keyboard state. Check keyboard open/close and rotation, Safari toolbar changes,
notches/safe areas, native dates/selects, Photos/Files selection, and crop gestures.
Do not disable pinch zoom. CSS inset fixtures can verify arithmetic, but not
physical-device behavior.

Google/YouTube flows require configured integrations. Audio hardware, Bluetooth
latency, actual recording, screen readers, and native file pickers also require
appropriate manual checks. A muted browser audio test cannot verify audible
synchronization.
