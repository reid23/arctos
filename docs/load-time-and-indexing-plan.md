# Load time and search indexing plan

**Status:** design proposal. **Reviewed source:** `44b3777` on `dev`.
**Research date:** October 26, 2026. **Related work:**
[Load Time Improvement, #262](https://github.com/reid23/arctos/issues/262).

## Recommendation

Deliver useful public HTML before the interactive application starts. Keep
Flask responsible for permissions, publication rules, data selection, and HTTP
responses. Keep Dioxus responsible for interactive application workflows.

Implement this in focused changes: measure the loading path; correct asset
delivery and startup feedback; establish a public page data contract; prove a
small server-rendered page; then expand public HTML and indexing metadata.
Compression and loading feedback can ship independently of server rendering.

Dioxus server-side rendering (SSR) is the preferred candidate for sharing public
presentation with the Rust frontend, subject to the proof below. Flask templates
are the lower operational-cost fallback. The architectural requirement is useful
HTML with an explicit client enhancement boundary; full-page hydration is an
optional optimization, not a prerequisite for public content.

This follows Arctos's [design philosophy](docs.md#design-philosophy): accessible
event information, small operational burden, and minimal collection or exposure
of personal data. The plan preserves the route → service → model → database
boundary and the existing application and OAuth API contracts.

## Current system and evidence

The [research record](load-time-and-indexing-research.md) contains source links,
measurement conditions, and limitations. Production and the reviewed checkout
serve different HTML; the deployed commit is unknown.

| Observation | Consequence |
| --- | --- |
| `frontend/index.html` contains an empty `#main`. Layout emits styles and loading UI after Dioxus starts. | A component-level loading indicator cannot cover the WebAssembly download and startup interval. |
| Production delivers a 5,580,099-byte WebAssembly file without content encoding. Its one-year immutable cache already works. | Cold transfer size is a concrete opportunity; adding a cache header alone does not address it. |
| One desktop cold visit first painted content at 3.720 seconds; one throttled mobile cold visit at 29.904 seconds. | Establish repeatable measurements before accepting a performance claim. These samples are diagnostic, not field percentiles. |
| The production root, event URL, `robots.txt`, `sitemap.xml`, and an unknown path return the same small HTML shell with status 200. | Document routing needs explicit status codes and real discovery resources. |
| Event and league descriptions require detail data followed by a Markdown-rendering request. Account and page requests otherwise start concurrently. | Prepare public description HTML with the page data; avoid imposing a new account-loading barrier. |
| Several API responses contain viewer-specific registration, permission, or profile fields. | Public HTML needs an allowlisted projection rather than an existing response serialized wholesale. |
| Dioxus is locked to 0.7.10. The app configures the web renderer and ordinary resources. | Native compilation of tests does not establish SSR or hydration compatibility. |

The current startup path is:

```text
Document → JavaScript loader → WebAssembly download and startup
         → Layout styles and parallel account/page requests
         → page content → dependent description-formatting request
```

The target separates reading from application startup:

```text
Document request → Flask visibility check → public data → rendered HTML
                                                    └→ metadata and links
Browser paints public content → optional client controls become available
Application routes           → visible boot state → Dioxus workflows
```

## Rendering decision and compatibility proof

| Candidate | Benefit | Cost and decision |
| --- | --- | --- |
| Targeted Dioxus SSR | Public Rust components can serve both server and client presentation. | Evaluate first with one event overview. Requires a persistent native renderer, explicit build features, health checks, and coordinated assets. Adopt only after the proof passes. |
| Flask/Jinja public pages | Uses existing services, sessions, sanitizer, and deployment process. | Fallback if the Rust service is not justified. Public presentation exists in two template systems, requiring shared data fixtures and visual parity checks. |
| Build-time static pages | Simple delivery for versioned documentation. | Suitable for fixed documents; live publication and event changes need request-time checks or reliable invalidation. |
| Loading shell with client rendering | Small, independently deployable startup improvement. | Provides feedback while the application loads. Searchable event content still requires public HTML. |

The Dioxus proof uses CLI 0.7.10 and the Cargo lockfile. The repository selects
the moving Rust `stable` channel; record the exact `rustc` version and pin the
tested stable release for a reproducible renderer build.
Extract one pure event-information component that accepts prepared public data.
It must render a heading, dates, location, sanitized description, and ordinary
links without browser storage, account resources, timers, or network calls.

Flask classifies the request, prepares the data, and makes one bounded call to
a persistent local Rust renderer. The renderer receives a page kind, public
data, and a release identifier. It has no database access, session cookies,
credentials, or arbitrary URL-fetching interface. Flask owns document status,
head metadata, and cache policy. Avoid starting a Rust process per request.
Bind the renderer to a private loopback address or Unix socket and exclude it
from public proxy routing. Configure input/output size limits, bounded queued
work, and a concurrency limit that protects Flask's capacity. Enforce deadlines
inside the renderer as well as its caller so abandoned requests do not leave
unbounded work running. Verify the largest permitted public document within
these limits and measure renderer CPU and memory during the proof.

Prove all of the following before selecting that renderer for production:

1. Native rendering works for the extracted component with the pinned versions.
   Two concurrent pages use request-local data and cannot share route or user
   state. Existing browser-lifetime globals are outside the rendering path.
2. The original response contains useful content and links. Blocking JavaScript
   or WebAssembly leaves them readable. Client startup never replaces that
   content with a loading branch.
3. Public, missing, denied, authorized-draft, and unavailable-data outcomes retain
   their correct HTTP status and privacy policy.
4. Account actions, registration links, direct URLs, browser history, fragments,
   and transitions into application tools work. Keyboard focus and scroll
   position remain predictable.
5. The release includes matching HTML, CSS, JavaScript, and WebAssembly assets.
   Startup, renderer failure, deployment, and rollback are exercised together.
6. The renderer meets the latency and resource budgets below under representative
   staging traffic. Its maintenance cost is justified by actual view reuse.

If these gates fail, implement the same public contract with Flask templates
and record the decision. Do not make a framework upgrade or WebAssembly splitting
a dependency of the first public HTML release.

### Client ownership and hydration

Initially keep meaningful public HTML outside client-owned roots. Optional
controls mount in a separate empty container; ordinary links open application
routes through full document navigation. Extract a small enhancement entry point
instead of mounting the complete Router over the public document. The first
release may use native links alone on public pages. Existing client-rendered
routes continue to use their application entry point.

Hydration attaches interactive behavior to matching server-generated markup.
It requires the same initial tree and data, not merely similar HTML. Dioxus
0.7.10's compiled `hydrate` feature forces its hydration launch path, even when
`Config.hydrate(false)` is supplied. Keep ordinary CSR and hydration build
boundaries explicit. Arbitrary Jinja output is not a Dioxus hydration contract.
[Pinned runtime](https://github.com/DioxusLabs/dioxus/blob/v0.7.10/packages/web/src/lib.rs),
[mount configuration](https://github.com/DioxusLabs/dioxus/blob/v0.7.10/packages/web/src/cfg.rs).

If hydration is later adopted, serialize only the public initial data, initialize
the first client render from it, and refresh afterward without clearing content.
Use deterministic initial dates, timezone, theme, and query state; apply browser
preferences after attachment. Test theme changes, local storage, timezone edges,
failure recovery, and duplicate requests. A full-page replacement is a separate
handoff design with its own focus and recovery requirements.

## Public data and route policy

Introduce a small public-page service and explicit page models. Reuse domain
visibility helpers and `app/filters.py::render_markdown`. The public model contains
only the fields needed by its page: canonical path, heading, public dates and
location, approved image URL, sanitized description, and permitted navigation.
Directory models contain bounded lists and pagination information.

Do not embed an ORM object, the account response, or a complete application API
payload. Exclude personal phone numbers, injuries, penalties, payment and waiver
records, private configuration, tokens, and account-specific registration state.
The client obtains its private account and action state through the existing
authenticated API. `/api/v1` remains the separate bearer-token API.

| Route family | Initial HTML and indexing policy |
| --- | --- |
| `/`, `/leagues` | Published entries and useful crawlable links. Index the default directory. Free-text search/filter variants are excluded from the sitemap and receive `noindex`; separately reviewed pagination uses real links and its own canonical URL. |
| `/:url`, `/leagues/:league_url` | Published overview content is the first dynamic SSR scope. Include only publicly visible league rounds and related entities. Default overview URLs are canonical. |
| `/about`, documentation and legal `.md` routes | Render existing sanitized content in the document. Choose one canonical URL for any duplicated documentation surface. |
| Schedules, results, brackets, side competition standings | Keep application behavior during the pilot. Add public HTML only after their independent publication rules, result limits, and freshness tests pass. |
| Player/team directories and profiles | Preserve application access and existing indexing directives. Omit them from the new public HTML allowlist and sitemap until an explicit field and privacy review; any directive change is reviewed separately. Public API availability alone does not approve wider search exposure. |
| Login, registration, settings, roster tools, match operation, audio/video tools, scoreboards | Explicit application routes with startup feedback and `noindex`. Preserve special rendering and authenticated workflow behavior. |
| APIs, callbacks, uploads, waivers, asset files | Retain existing route owners and response contracts. Never send these through an HTML fallback. |

Define exact reserved routes before the generic `/:url` event matcher. Handle
query parameters with an allowlist and bound their lengths. Shared route fixtures
must cover both the Flask dispatcher and Dioxus route definitions. Essential
cross-boundary links are ordinary anchors; a client navigation must not silently
skip the server-rendered document or retain another page's metadata.

### Status and session handling

| Request outcome | Document response |
| --- | --- |
| Published page, signed out | `200`, public projection, matching metadata. |
| Published page, signed in | Same public projection; private account controls load separately. Bypass shared caching, including responses that refresh session cookies. |
| Authorized organizer opens a draft | Private application shell, `200`, `noindex`, `Cache-Control: private, no-store`. Existing draft preview remains available. |
| Unpublished page without permission | Generic denial using the existing authorization status, normally `403`; `noindex`, no private title or description. |
| Missing entity or unknown route | Real `404` with useful navigation and `noindex`. Use `410` only when a deliberate permanent-removal policy exists. |
| Page data or renderer unavailable | Readable `503` error and retry link, `noindex`, `no-store`; a bounded `Retry-After` may be supplied. Keep it distinct from missing data. |

Return status before streaming or sending the body. Use typed outcomes rather
than matching API error strings. A temporary CSR fallback for an operational
rollback is not an indexing success criterion.

### Escaping and cache safety

Escape text and quoted attributes. Only the existing sanitizer's output may
enter a trusted HTML slot. Allowlist public URL schemes and configured origins.
If JSON bootstrap data is embedded, use a serializer safe for a script element,
including `</script>` and Unicode edge cases. Test malicious names, descriptions,
URLs, and query strings. Flask's
[template security guidance](https://flask.palletsprojects.com/en/stable/web-security/)
explains the limits of automatic escaping.

Start public dynamic HTML with `Cache-Control: no-store` and no shared HTML cache.
This keeps publication revocation immediate and makes the first rollout easier
to reason about. Session-bearing responses stay private. Do not add a broad
nginx cache for `/_api/`, or rely on `Vary: Cookie` alone to protect private data.

If measurements justify a render cache, Flask must verify current visibility
before every lookup. Cache only pure public markup, keyed by the complete public
model and renderer/release version. Bound its size and lifetime; discard entries
on publication, content, deletion, or policy changes. Test all mutation paths
and organizer-to-anonymous request sequences. An edge HTML cache must wait for
a proven revocation mechanism. HTTP cache semantics are described in
[MDN's caching guide](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching).

## Asset delivery and loading feedback

1. **Compress public build assets.** Enable gzip for WebAssembly and JavaScript;
   evaluate Brotli only where the deployed server supports it. Serve WebAssembly
   as `application/wasm`, send the correct `Content-Encoding` and
   `Vary: Accept-Encoding`, and verify both compressed and identity responses.
   Prefer build-time compression where supported. Scope this to public assets
   before changing compression of authenticated responses. References:
   [nginx gzip](https://nginx.org/en/docs/http/ngx_http_gzip_module.html),
   [gzip static files](https://nginx.org/en/docs/http/ngx_http_gzip_static_module.html).
2. **Preserve versioned caching.** Keep long-lived immutable caching for
   content-addressed assets. Give matching fingerprinted JavaScript/CSS the
   same policy. Revalidate the mutable application shell. Missing asset paths
   return `404`, never `index.html`. Retain prior release assets for open tabs
   and rollback.
3. **Make critical styling discoverable in HTML.** Reference built CSS through
   a release asset manifest rather than handwritten hashes. Include a small
   boot-state style before WebAssembly. Load only the fonts/icons required for
   that state; do not preload every application asset. Validate generated-loader
   preloads and credential modes to avoid duplicate downloads.
4. **Provide honest startup states.** Application documents contain a visible
   loading message, a useful `noscript` explanation with public links, and a
   retry path for loader/compile failure. A delayed-start message can explain
   that the connection is taking longer; it must not invent a progress
   percentage or stop a load that is still progressing. Hide the boot state
   only after the application has mounted meaningful content or an error.
5. **Reduce required request chains.** Public page preparation includes sanitized
   description HTML. Keep account loading concurrent with public reading.
   Preserve deliberate lazy loading of secondary rosters and tools. Profile
   queries before changing database indexes; SSR must not add one request or
   query per visible list item.

Reserve image dimensions, provide appropriate responsive image sizes, and keep
the main visible image discoverable without making every image high priority.
Measure first readable content and interactive readiness separately: SSR can
improve reading while the full application still needs its WebAssembly bundle.
[LCP optimization guidance](https://web.dev/articles/optimize-lcp).

WebAssembly splitting remains an independent experiment. Record and test the
exact compiler/CLI/profile combination, direct deferred routes, rejected chunk requests,
retries, and mixed release assets. The existing release profile already enables
size optimization, LTO, and a single codegen unit; do not present those as unused
optimizations. Splitting is not the source of indexable HTML.

## Search metadata and discovery

Every eligible document has a descriptive title, concise description, absolute
canonical URL, language, and useful heading before scripts run. Construct URLs
from a validated deployment public origin, not an unchecked Host header. Keep
social preview metadata consistent with visible public content. Client navigation
updates the same metadata contract without leaving duplicate tags.

Canonicalize only equivalent content. Tracking parameters can point to the clean
URL; distinct paginated pages retain distinct canonicals. Do not point every
event or section to the home page. Unknown tabs and query combinations need a
defined redirect or error policy rather than unlimited indexable variants.
[Canonical guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).

Serve a real `robots.txt` and XML sitemap. Build sitemap entries from the same
published-page allowlist as HTML, with canonical absolute URLs. Include an
accurate `lastmod` only when the data supports it; do not use each request's time.
Remove withdrawn/deleted pages promptly. Do not list drafts, account routes,
search queries, private profiles, or API responses. Keep CSS and scripts needed
for public rendering crawlable. `robots.txt` is neither authorization nor a
replacement for a page's `noindex` rule; a crawler must be able to fetch that rule.
[Sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap),
[robots metadata](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag).

Add Event JSON-LD only after public HTML and metadata work. Verify eligibility,
physical location, public bookability, and required fields against real event
data. Do not infer an address, precise time, price, or invitation policy from
free text. Date-only values are appropriate when the time is unknown. The markup
must match visible content and does not guarantee a rich result.
[Google Event guidance](https://developers.google.com/search/docs/appearance/structured-data/event).

Serve equivalent public content to people and crawlers. Avoid user-agent-specific
rendering. Google can process JavaScript, but rendering and indexing are separate
stages; neither a browser smoke test nor a successful response proves indexing.
The reviewed primary documentation does not establish that Google categorically
cannot execute this application's WebAssembly. Verify deployed rendering through
Search Console URL Inspection.
[JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics),
[dynamic rendering guidance](https://developers.google.com/search/docs/crawling-indexing/javascript/dynamic-rendering).

## Delivery sequence

Each row is a focused implementation PR or experiment with independent evidence.
Names for new modules below describe proposed responsibilities.

| Stage | Work and likely locations | Exit evidence | Recovery |
| --- | --- | --- | --- |
| 1. Baseline and delivery | Capture deployment headers/config and release identity; track a reviewed nginx example and asset manifest. Update `update.sh` and `docs/DEPLOY.md` for staged, versioned releases. | Repeatable cold/warm measurements; correct encoding/MIME/404/cache behavior; old and new assets coexist. | Select the preceding release and restore the tested proxy configuration. |
| 2. Startup feedback | `frontend/index.html`, boot CSS/loader boundary, shared recovery controls. Include Figma review of loading/error states and browser validation. | Useful status before Wasm, blocked-loader recovery, keyboard and reduced-motion checks. | Revert shell/loader assets as one release. |
| 3. Public contract and renderer proof | Proposed `app/services/public_page_service.py`, public page serializers, route classification, and an isolated Rust renderer experiment. | Anonymous field allowlist, typed outcomes, one event/doc page proof, and an explicit renderer decision. | Keep public rendering disabled; retain ordinary application routes. |
| 4. Public HTML pilot | Proposed `app/routes/public_pages.py`; selected renderer/templates; public CSS; independent client entry. Start with a published event overview and fixed document. | No-JS reading, draft access, real statuses, privacy tests, navigation, performance, and deployment checks pass. | Disable the selected route group and restore the prior document dispatcher. |
| 5. Discovery coverage | Expand to event/league directories and overviews; metadata builder, robots and sitemap endpoints. | Route/metadata parity, bounded pagination, publication revocation, Search Console inspection. | Disable expansion by route group and remove affected sitemap entries. |
| 6. Measured follow-ups | Independently evaluate render caching, structured data, splitting, and additional public pages. | Each change has its own privacy/freshness and performance evidence. | Independent flags/configuration; no dependency on optional optimizations. |

Keep new modules documented in their area README and API reference. The first
delivery requires no database migration. A future content revision or timestamp
field needs a separate schema design and migration review; the initial sitemap
can omit unavailable modification dates.

## Acceptance and verification

### Performance budgets

Treat these as proposed release gates, to calibrate against a recorded staging
baseline on the same hardware and network profile:

- On the recorded 1.6 Mbps / 150 ms / 4× CPU mobile profile, a pilot public
  overview shows its event heading and primary details (date, location, and
  available description) within 2.5 seconds in at least four of five cold runs.
  Branding or a loading shell does not satisfy this gate. Record the visible
  content milestone, median and worst run, response time, transferred bytes,
  first contentful paint, and observed LCP; do not label five lab samples a field
  percentile.
- Public document preparation, including rendering, has a provisional staging
  p95 budget of 500 ms at the agreed representative concurrency. Bound the
  renderer call to 1 second and total page-data work to 2 seconds; a timeout
  returns a readable failure. Test concurrency on staging, not production.
- Compression reduces the measured cold WebAssembly transfer by at least 50%
  for the same build. Track encoded and decoded sizes separately. No duplicate
  loader, CSS, or Wasm fetch is introduced.
- Existing interactive workflows have no material readiness or interaction
  regression. Record an application-ready mark after actual controls are mounted,
  not after a decorative shell paints. Investigate regressions over 10% against
  repeated baseline runs before release.
- Field goals remain LCP ≤2.5 seconds, INP ≤200 ms, and CLS ≤0.1 at the 75th
  percentile, separated by device group. Lab measurements do not establish INP
  compliance. Use existing Search Console/CrUX data where available; any new
  measurement collection needs a minimal-data policy without account IDs,
  search text, or private URL parameters.
  [Web Vitals definitions](https://web.dev/articles/vitals).

### Required checks

| Area | Required cases |
| --- | --- |
| HTML and metadata | Raw response has the expected heading, description, canonical, links and status; JavaScript disabled; direct URL, refresh, fragment and Back/Forward behavior. |
| Public visibility | Published/unpublished event and league combinations; draft league rounds; independent schedule/bracket/standings policies before those routes are enabled. |
| Privacy | Anonymous, unrelated account, organizer, and authorized draft visits; inspect HTML and data scripts for private fields; organizer→anonymous cache sequence; logout and publication withdrawal. |
| Failure recovery | Slow/offline/failed JS and Wasm, missing asset, page API failure, renderer timeout/restart, malformed data, and retry without duplicate writes or loss of a usable public document. |
| Rendering boundary | Native public view test; concurrent request isolation; separate mount ownership; optional hydration parity for data, timezone and theme; no full-page loading replacement. |
| HTTP delivery | HTML versus asset precedence, content type/encoding, validators/cache policy, cookies, `404` for unknown URLs, `503` for temporary failures, robots text and sitemap XML. |
| Existing workflows | Login/logout and OAuth callbacks, event/league registration, organizer draft editing, match operation, audio/video tools, OBS output, and public API contracts. |
| UI quality | Figma-reviewed actual states; 320/390/1280px, keyboard, screen-reader status, enlarged text, all palettes, reduced motion, and real Safari/mobile checks before broad rollout. |
| Release safety | Native renderer health if selected, matching asset manifest, staged switch, previous open tab, rollback, and missing previous asset recovery. |

Run targeted Python tests, native frontend tests, the Wasm build, the selected
renderer build, and browser smoke tests in CI. Assert exact statuses and data
contracts. Screenshots support visual review; they do not replace semantic,
permission, and network checks.

## Deployment and remaining decisions

Track the actual nginx route and compression configuration before changing it.
The repository's deployment script copies frontend output and restarts Flask;
it does not manage a native renderer or an atomic asset/document release.
Stage assets and services under a release identifier, verify readiness, then
switch the served document/manifest. Retain the preceding release until the
rollback and open-tab window has passed. The tested Dioxus output path, service
supervision, and public origin belong in the deployment runbook.

Start with public rendering disabled behind a configuration flag and enable one
route group after staging checks. Monitor page/renderer errors, response latency,
asset `404` responses, and publication consistency. Roll back on private-data exposure,
incorrect statuses, missing assets, persistent renderer failures, or failed
readiness gates. Restoring client rendering may temporarily reduce indexing
coverage; make that tradeoff explicit and repair the HTML path promptly.

Before selecting a production renderer and enabling public HTML, record: the
renderer proof result; production proxy configuration and release identity;
staging concurrency/resource budget;
the exact indexable route/field allowlist; and the deployment owner's Search
Console validation procedure. Live access logs, Search Console coverage, actual
mobile devices, and production service capacity are not established by this
research. No ranking improvement or indexing deadline is guaranteed.
