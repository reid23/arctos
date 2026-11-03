# Public entry verification

Verification separates visible startup feedback, readable information, and
usable application controls. It also checks permissions, native document
behavior, recovery, navigation, and cache isolation. Performance measurements
run in developer-controlled browsers and remain in local reports.

## Prepare a local target

From the repository root:

```bash
uv sync --group test
cd frontend
dx bundle --web --release
cd ..
uv run python scripts/prepare_public_assets.py --dist frontend/dist/public
uv run playwright install chromium
ARCTOS_PUBLIC_HTML_ENABLED=1 \
ARCTOS_FRONTEND_DIST="$PWD/frontend/dist/public" \
uv run python -m scripts.dev_demo
```

Use the public output path reported by Dioxus for both `--dist` and
`ARCTOS_FRONTEND_DIST`. The demo serves Flask on `http://127.0.0.1:5006`, creates
fictional fixtures in `instance/demo.db`, and preserves edits on restart. Flask
serves the document templates; `dx serve` exercises the application directly.

For a representative local dataset, `scripts.dev_demo --snapshot
/absolute/path/to/snapshot.db` copies and migrates a versioned SQLite snapshot
into `instance/snapshot-demo/demo.db`, then adds fictional demo accounts.
The source is read-only. Restore corresponding uploads into an isolated static
directory and configure the preview app's `static_folder` to that directory.
Use fictional accounts for authenticated checks.

## Automated checks

Run the contracts relevant to the change:

```bash
uv run pytest tests/test_frontend_shell.py \
  tests/integration/test_public_pages.py tests/unit/test_public_page_service.py \
  tests/unit/test_public_league_service.py -n 4
uv run pytest tests/unit/test_public_html_check.py \
  tests/unit/test_compare_startup_profiles.py -n 4
just frontend-test
```

Pytest prepares generated public CSS before collection, including on a fresh
checkout. `just run` and `just demo` prepare it before starting Flask. Use
`just public-assets` after editing shared styles, then restart Flask to refresh
its asset versions and compressed responses.

Server tests cover publication and account boundaries, pagination, query
encoding, calendar dates, error statuses, manifest validation, compression, and
cache headers. Rust tests cover directory state, history, cache expiry and
invalidation. Browser tests cover rendered interactions and failure recovery.
The repository's [testing guide](https://github.com/reid23/arctos/blob/dev/TESTING.md)
describes broader workflow checks.

Check retained directory account state against the local release:

```bash
uv run python scripts/check_directory_account_state.py \
  --base-url http://127.0.0.1:5006 \
  --output /tmp/arctos-directory-account-check.json
```

The four cases verify that logout clears private cards while the anonymous
request is pending, a delayed prior-account response stays hidden, and Leagues
Retry displays its pending state before recovery. A 31-league response also
checks literal query filtering, page 2, workflow navigation, and browser history
while reusing one list response. The runner uses synthetic
account/directory responses, intercepts logout, and blocks unexpected API calls.
It accepts loopback origins and writes assertion results to a fresh report path.
Server records and sessions retain their state.

Check public reading and the first application handoff with deliberate delays
and failures:

```bash
uv run python scripts/check_public_reading_navigation.py \
  --base-url http://127.0.0.1:5006 \
  --event-path /demo-open --league-path /leagues/demo-coast-league \
  --output /tmp/arctos-public-reading-check.json
```

This loopback-only runner checks held/failed Wasm, competing reading requests,
metadata, appearance rebinding, Back/Forward, saved scroll, stylesheet delays
and failures, focus, native recovery, and modified clicks. Write requests are
blocked; the read-only Markdown renderer remains available. Run it separately
from timing measurements.

Run the reading and application suite in another terminal:

```bash
uv run python scripts/check_public_html.py \
  --league-path /leagues/demo-coast-league \
  --runs 5 --profile mobile-4g --output /tmp/arctos-public-mobile
uv run python scripts/check_public_html.py \
  --league-path /leagues/demo-coast-league \
  --width 320 --height 740 --runs 1 --profile unthrottled \
  --screenshots --output /tmp/arctos-public-narrow
```

The commands use Playwright's installed Chromium by default. Use
`--browser-executable /path/to/chrome` for a specific compatible browser. `--public-only` checks reading before a frontend build is available;
application coverage is recorded as untested. `--skip-resilience` similarly
marks recovery coverage as untested. Screenshots require `--screenshots` and
can contain displayed data.
The optional `--league-path` adds the Leagues directory, league Info, and their
JavaScript-disabled search and native navigation checks.

The default application target is `/login?next=/demo-open/register`. To check
another workflow, supply `--app-path` and repeat `--app-ready-selector` for each
required control. For example:

```bash
uv run python scripts/check_public_html.py \
  --app-path '/?app=1' --app-ready-selector '.arctos-event-card' \
  --runs 1 --profile unthrottled --output /tmp/arctos-app-entry
```

Remote staging requires `--base-url` and `--allow-remote`, plus appropriate
fixture paths. A deployment prefix belongs in `--directory-path`,
`--event-path`, and `--app-path`. The harness visits anonymous pages and reads
controls; authenticated registration and organizer actions use the dedicated
workflow suites.

## Browser acceptance criteria

| Area | Required behavior |
| --- | --- |
| Public directories | Events and Leagues headings, search, cards, and metadata are visible; search and pagination work with JavaScript disabled |
| Public Info | Published event/league facts, About content or fallback, and permitted registration actions are readable |
| Interactive entry | Original navigation and theme, clear startup status, single visible header, application styles ready at handoff |
| Application workflow | Required controls are visible and enabled after the startup shell closes |
| Failed or delayed Wasm | Public facts remain readable on public documents; pending navigation has recovery; interactive entry provides slow/failure status and Retry to the selected URL |
| JavaScript disabled | Public documents retain reading and native navigation; interactive entry explains its JavaScript requirement |
| Navigation | One runtime across ordinary routes; encoded queries, fragments, pagination, Back/Forward, scroll, and focus behave correctly |
| Reading during startup | Marked links and GET forms replace the public page while Wasm is pending or failed; stale responses cannot replace a newer destination |
| First handoff | Public page remains visible until the requested application route and its styles are ready; one visible header, coherent focus, and native recovery on failure |
| Leagues cache | Revisit within 60 seconds reuses eligible data; account changes, writes, expiry, and Retry refresh it |
| Mobile and appearance | No horizontal overflow at 320px and 390px; three palettes, light/dark modes, keyboard access, and enlarged text remain usable |

`check_public_html.py` verifies public reading, sign-in controls, native
search/navigation, Wasm failure/delay recovery, retained navigation, Leagues
reuse, and overflow. Its ready criterion requires visible meaningful content,
then two animation frames and observed FCP. Default selectors identify the
public page facts and the username, password, and submit controls. Alternate
selectors should express the page's usable state.

Interactive-entry checks hold Wasm and application CSS, verify keyboard focus
at handoff, and exercise native recovery. Timing runs use normal browser
caching with request interception disabled; failure injection runs separately.

## Comparative measurements

`scripts/compare_startup_profiles.py` measures Events entry and subsequent
Leagues navigation across selected prepared local origins:

| Runner name | Default origin | Server setup |
| --- | --- | --- |
| `original` | `http://127.0.0.1:5017` | Optimized baseline frontend entry and its matching assets |
| `public-html` | `http://127.0.0.1:5016` | Current release with public routing enabled |

Prepare both releases and one shared fictional demo database. From the current
checkout, choose a new directory for the baseline checkout and run:

```bash
export ARCTOS_COMPARE_BASELINE_CHECKOUT=/absolute/path/to/new-baseline-checkout
git clone --no-hardlinks . "$ARCTOS_COMPARE_BASELINE_CHECKOUT"
git -C "$ARCTOS_COMPARE_BASELINE_CHECKOUT" checkout --detach daebd5c6f57b24461f8b899aae74b0aa836543fb
(cd "$ARCTOS_COMPARE_BASELINE_CHECKOUT/frontend" && env -u ARCTOS_API_BASE dx bundle --web --release)
(cd frontend && env -u ARCTOS_API_BASE dx bundle --web --release)
export ARCTOS_COMPARE_BASELINE_DIST="$ARCTOS_COMPARE_BASELINE_CHECKOUT/frontend/dist/public"
export ARCTOS_COMPARE_CURRENT_DIST="$PWD/frontend/dist/public"
export ARCTOS_COMPARE_DATABASE="$PWD/instance/demo.db"
just public-assets
uv run python -m scripts.dev_demo --seed-only
uv run python scripts/prepare_public_assets.py --dist "$ARCTOS_COMPARE_BASELINE_DIST"
uv run python scripts/prepare_public_assets.py --dist "$ARCTOS_COMPARE_CURRENT_DIST"
```

Use the public output directories reported by Dioxus if they differ. Both
builds use same-origin APIs. Run the following from the current checkout in two
terminals with those exported paths: use `public-html` in one and `original`
in the other.

```bash
ARCTOS_COMPARE_VARIANT=public-html uv run python - <<'PY'
import os
from pathlib import Path

from flask import request, send_file
from scripts.dev_demo import create_demo_app

variant = os.environ["ARCTOS_COMPARE_VARIANT"]
port = {"original": 5017, "public-html": 5016}[variant]
dist_key = "ARCTOS_COMPARE_BASELINE_DIST" if variant == "original" else "ARCTOS_COMPARE_CURRENT_DIST"
dist = Path(os.environ[dist_key]).resolve()
os.environ.update(
    ARCTOS_FRONTEND_DIST=str(dist),
    ARCTOS_PUBLIC_HTML_ENABLED="1",
    ARCTOS_PUBLIC_ORIGIN=f"http://127.0.0.1:{port}",
)
app = create_demo_app(Path(os.environ["ARCTOS_COMPARE_DATABASE"]))

@app.before_request
def original_document():
    excluded = ("/_api/", "/api/", "/assets/", "/static/")
    if variant == "original" and request.method == "GET" and not request.path.startswith(excluded):
        response = send_file(dist / "index.html", conditional=False)
        response.headers["Cache-Control"] = "private, no-store"
        return response

app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, load_dotenv=False)
PY
```

This serves each release's own entry/assets through the same current Flask
backend and database. HTML uses identity encoding; prepared application assets
use the same gzip and cache policy. A representative snapshot can replace the
fictional demo using the preparation steps above; keep the database and upload
files identical across variants. The published capture used a local snapshot,
so a fresh fictional demo reproduces the method rather than its exact timings.

The runner expects these servers to remain running. Override origins with
repeated `--variant NAME=ORIGIN` options. Freeze builds and other browser work
during measurement.

```bash
uv run python scripts/compare_startup_profiles.py \
  --variant original=http://127.0.0.1:5017 \
  --variant public-html=http://127.0.0.1:5016 \
  --profile unthrottled --profile mobile-4g --include-direct-leagues \
  --runs 3 --output /tmp/arctos-startup-comparison
```

Omitting `--variant` arguments uses the two default origins. Direct Leagues
checks require server-rendered league cards before any league-list API request.

Each run rotates variant order and measures an independent cold/warm pair.
Cold means a fresh browser context; warm means a new page sharing that
context's HTTP cache after startup and its navigation journey complete.
OS, database, server, and proxy caches retain their normal state. Journeys run
within one page after startup and measure the click through painted content.

| Profile | Latency | Download / upload | CPU slowdown |
| --- | ---: | ---: | ---: |
| Unthrottled | 0ms | Unrestricted | 1× |
| Fast mobile | 40ms | 10 / 5 Mbps | 2× |
| Mobile 4G | 150ms | 1.6 / 0.75 Mbps | 4× |
| Constrained mobile | 300ms | 0.75 / 0.25 Mbps | 6× |

All profiles use a 390×844 touch viewport at scale factor 2. Reports include
raw samples, median/range summaries, request counts, transfer observations,
cache behavior, and errors. Feedback means a visible heading or startup
message. Useful Events readiness requires search, loaded titles, and metadata.
Runtime readiness uses the current navigation bridge or the baseline Layout;
useful-content timings provide the comparable user-facing milestone.

The background-start check allows 4ms for the paint timestamp's
[upward rounding](https://www.w3.org/TR/paint-timing/#mark-paint-timing).
It retains the raw delta and labels accepted negative values
`within-paint-precision`. Blocked-Wasm checks separately establish that reading
does not depend on application startup.

The known anonymous account 401 and canceled read-only Markdown rendering are
counted separately. Other request failures, HTTP errors, writes, JavaScript
errors, and overflow fail a sample. Reports contain numeric observations and
labels; URLs, queries, account identifiers, and page text are omitted.

`check_public_html.py` observes transfers for one second after content readiness
by default. A background download can extend beyond that window. Compare equal
cutoffs, and use the profile runner's completed startup and journey results to
assess the retained runtime's cost.

## Measured results

The public HTML release passed **48/48 startup samples and 24/24
navigation journeys** in the 2026-10-27 comparison. It includes server-rendered
Events, Leagues, and their published Info pages, plus public reading navigation
while the runtime starts.

The comparison used Chrome 154.0.8037.57, optimized releases, the same isolated
October snapshot with fictional fixtures, Flask APIs, and gzip asset policy.
The original frontend was built from
`daebd5c6f57b24461f8b899aae74b0aa836543fb`. Each table entry is the median of
three samples, with its minimum–maximum range in brackets. Times are seconds.

| Useful content | Cache | Original frontend | Public HTML |
| --- | --- | ---: | ---: |
| Events, unthrottled | Cold | 0.234 [0.215–0.282] | 0.198 [0.140–0.205] |
| Events, unthrottled | Warm | 0.285 [0.218–0.289] | 0.208 [0.208–0.210] |
| Leagues, unthrottled | Cold | 0.306 [0.202–0.309] | 0.085 [0.082–0.165] |
| Leagues, unthrottled | Warm | 0.275 [0.246–0.315] | 0.140 [0.096–0.218] |
| Events, mobile 4G | Cold | 12.594 [12.585–12.601] | 0.915 [0.914–0.926] |
| Events, mobile 4G | Warm | 0.796 [0.794–0.798] | 0.452 [0.449–0.464] |
| Leagues, mobile 4G | Cold | 12.246 [12.242–12.249] | 0.896 [0.894–0.907] |
| Leagues, mobile 4G | Warm | 0.756 [0.756–0.773] | 0.448 [0.440–0.460] |

Public directory content becomes readable in about 0.9 seconds on the tested
cold mobile connection. The runtime remains a substantial download: the
prepared Wasm payload is 2,258,344 bytes gzip, before supporting assets and
HTTP overhead. On cold mobile 4G Events entry, the runtime readiness milestone
was 12.747 seconds for public HTML and 12.058 seconds for the original frontend.
An interactive workflow selected before the runtime is ready still waits for
startup. Public reading links can fetch another Flask document during that wait.

The first application activation mounts Layout, loads application styles, and
checks the account. The following Leagues click measurements start after
runtime readiness, from a cold Events entry:

| Leagues navigation | Original frontend | Public HTML |
| --- | ---: | ---: |
| First visit, unthrottled | 0.093 [0.087–0.107] | 0.191 [0.126–0.242] |
| Return visit, unthrottled | 0.093 [0.062–0.094] | 0.043 [0.042–0.047] |
| First visit, mobile 4G | 0.224 [0.223–0.225] | 0.574 [0.574–0.586] |
| Return visit, mobile 4G | 0.210 [0.208–0.210] | 0.058 [0.055–0.059] |

The first activation is slower than a navigation in the already-mounted
original application. Subsequent visits benefit from the retained runtime and
Leagues cache. Every current-release journey kept one document and one Wasm
request, and reused its league-list response. These results support early
public reading and faster repeat navigation; reducing the remaining
interactive startup cost requires further runtime and asset/data work.

## Verification scope and limits

The browser acceptance run on 2026-10-27 passed 10 readiness samples,
15 JavaScript-disabled/delayed/failed-Wasm checks, nine reading/handoff cases,
four account/cache/pagination cases, and four Leagues layouts at 320px with
normal and enlarged text. The commands above produce fresh local reports;
reports and temporary captures are excluded from Git.

The account runner checks private-card removal during logout, rejection of
prior-account responses, Retry, and query/page state across navigation. The
reading runner checks one runtime, competing requests, metadata, appearance,
history, focus, stylesheet failures, and native recovery. Server and Rust tests
cover permissions, data selection, and state transitions.

These small synthetic samples describe the tested release, fixture, browser,
and local serving configuration. Production distributions, TLS/proxy behavior,
load capacity, real-phone Safari, and deployment rollback need validation in
their target environments. Local document responses used identity encoding;
the proxy's compression policy can change deployed results.
