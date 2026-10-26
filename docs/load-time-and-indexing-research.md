# Load time and indexing research

**Date:** October 26, 2026. **Source baseline:** `44b3777996cffa497ee32ae41c90b73ffe18ce9b`.
This record supports the [implementation plan](load-time-and-indexing-plan.md).

## Repository findings

| Source | Verified behavior |
| --- | --- |
| [Initial document](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/index.html), [startup](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/main.rs) | Empty application container; the browser starts the Dioxus router after loading WebAssembly. Generic description exists in this checkout. |
| [Layout](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/pages/layout.rs) | CSS links, theme styles, route title, account request, and Suspense loading content are emitted by the application. Account and public page resources can run concurrently. |
| [Event overview](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/pages/tournament_home.rs), [league overview](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/pages/league_home.rs) | Description formatting follows the detail response through an effect and another HTTP request. |
| [API wrapper](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/api.rs) | Native default is localhost port 5006; browser credentials are included only on Wasm. General response errors are flattened to strings. These are not an SSR transport/status contract. |
| [Dependency configuration](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/Cargo.toml), [lockfile](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/Cargo.lock) | Dioxus 0.7.10; web/router/document; optional splitting disabled by default. Size optimization, LTO and one codegen unit are already configured. |
| [Permissions](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/app/services/permission_service.py), [event reads](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/app/routes/tournaments/read.py), [league reads](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/app/routes/leagues.py) | Publication and privileged preview are separate. Schedules have their own visibility gate. Existing detail data includes viewer-specific fields. |
| [Markdown rendering](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/app/filters.py), [content routes](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/app/routes/content.py) | One backend Markdown and Bleach policy supplies formatted descriptions and fixed documents. |
| [Profile privacy](data-accessibility-guide.md) | Player contact data, injuries and penalties have distinct visibility rules. A public render projection requires explicit field selection. |
| [Deployment](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/update.sh), [runbook](DEPLOY.md) | Frontend output is copied and Flask restarted. No tracked nginx server configuration or managed Rust renderer establishes live routing/compression behavior. |

SSR compatibility also requires auditing browser preference initialization,
timezone formatting, initial-location globals, and asynchronous resources.
Relevant sources are [appearance](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/appearance.rs),
[time formatting](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/time_format.rs),
[directory state](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/pages/directory_state.rs), and
[Markdown navigation](https://github.com/reid23/arctos/blob/44b3777996cffa497ee32ae41c90b73ffe18ce9b/frontend/src/components/markdown.rs).

## Production observations

The repository homepage identifies `https://events.californiajugger.org` as the
public site. Inspection used anonymous, bounded GET/HEAD requests and six browser
navigations. It performed no login, application write, load test, or deployment.
The deployed commit could not be identified. Its document differs from the
reviewed checkout, so these results describe that deployed artifact only.

The observed document is 739 decoded bytes, with an empty `#main`. The root,
a published event, robots, sitemap, and an unknown route return the same shell
with status 200 and HTML content type. HTML compression is present. The initial
response does not contain event content or the checkout's generic description.

| Asset | Decoded bytes | Observed delivery |
| --- | ---: | --- |
| WebAssembly | 5,580,099 | No content encoding; `application/wasm`; one-year immutable caching. |
| JavaScript loader | 95,486 | No content encoding; no explicit cache-control in the observed response. |

The production bootstrap already uses streaming instantiation and the correct Wasm MIME type. JavaScript is preloaded; Wasm is discovered by the loader.

Local gzip level 6 compression of the downloaded public Wasm produced 1,922,884
bytes, a 65.5% reduction. This is an offline size estimate, not a deployed
response or a measured speedup.

### Browser measurements

Each row below is one lab navigation, not a median or field percentile. Cold
and warm refer to the measured browser cache state. The mobile profile uses
a 390 × 900 viewport, 1.6 Mbps download, 750 Kbps upload, 150 ms latency and 4× CPU
slowdown. The [measurement record](research/load-time-2026-10-26.json) includes browser version, UTC timestamps, cache definitions, asset URLs and headers, and sanitized numeric results.

| Visit | First contentful paint | Observed largest contentful paint |
| --- | ---: | ---: |
| Root, desktop cold | 3.720s | 4.196s |
| Root, desktop warm | 0.156s | 0.652s |
| Event, desktop cold | 5.108s | 5.604s |
| Event, desktop warm | 0.192s | 0.580s |
| Root, simulated mobile cold | 29.904s | 30.460s |
| Root, simulated mobile warm | 0.240s | 0.728s |

In the desktop cold sample, the loader starts at 0.572s and completes at 1.310s;
Wasm starts at 1.320s and completes at 3.600s. Styles and application data requests
start around 3.69s. First application text is observed at 3.699s. These timings
support improving transfer and resource discovery. They do not isolate Wasm
compilation CPU cost or prove which optimization controls field LCP.

No field INP, Search Console index coverage, production query timings, server
capacity, or real mobile-device results were obtained. Observed LCP is limited
to the measurement window. Warm loading does not remove the cold-start problem.

## Framework and search sources

| Primary source | Finding used in the plan |
| --- | --- |
| [Dioxus 0.7 SSR guide](https://dioxuslabs.com/learn/0.7/essentials/fullstack/ssr/) | SSR/hydration require matching initial output and deliberate async data handling. |
| [dioxus-ssr 0.7.10](https://docs.rs/dioxus-ssr/0.7.10/dioxus_ssr/) | Rust components can render to HTML independently of replacing Flask business logic. |
| [Pinned web runtime](https://github.com/DioxusLabs/dioxus/blob/v0.7.10/packages/web/src/lib.rs), [configuration](https://github.com/DioxusLabs/dioxus/blob/v0.7.10/packages/web/src/cfg.rs) | A compiled hydration feature forces the hydration launch path. Explicit client mounts exist; automatic partial hydration is not established. |
| [Dioxus 0.7 backend tutorial](https://dioxuslabs.com/learn/0.7/tutorial/backend/), [SSG guide](https://dioxuslabs.com/learn/0.7/essentials/fullstack/static_site_generation/) | Server and client build/deployment paths need explicit handling; static generation does not itself solve live publication freshness. |
| [Pinned router macro](https://github.com/DioxusLabs/dioxus/blob/v0.7.10/packages/router-macro/src/lib.rs), [route generation](https://github.com/DioxusLabs/dioxus/blob/v0.7.10/packages/router-macro/src/route.rs) | Current splitting behavior should be verified from the pinned source, not copied from release-blog route syntax. |
| [Upstream #5769](https://github.com/DioxusLabs/dioxus/issues/5769), [#5993](https://github.com/DioxusLabs/dioxus/issues/5993) | Reports describe splitting/data-symbol and lazy-loader failure risks. These are upstream reports, not failures reproduced in Arctos here; #5993 is based on source inspection. |
| [Google JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics), [troubleshooting](https://developers.google.com/search/docs/crawling-indexing/javascript/fix-search-javascript) | Inspect original and rendered HTML, meaningful statuses, crawlable links and metadata. Browser success does not establish indexing. |
| [Google status guidance](https://developers.google.com/crawling/docs/troubleshooting/http-status-codes) | Missing resources and temporary failures need different HTTP outcomes. |
| [Web Vitals](https://web.dev/articles/vitals), [LCP optimization](https://web.dev/articles/optimize-lcp) | Separate repeatable lab diagnosis from field performance and interaction measurements. |

The research did not run a native Arctos SSR/hydration prototype, enable
WebAssembly splitting, or change production configuration. The plan makes those
compatibility and deployment checks explicit implementation gates.
