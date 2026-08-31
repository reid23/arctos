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

In dev:

```bash
cd frontend
dx serve
```

`dx serve` runs the SPA on `http://127.0.0.1:8080` with hot reload. By
default it talks to the Flask backend on `http://127.0.0.1:5006` (see
`api.rs::base_url`); set `ARCTOS_CORS_DEV=1` and start Flask on `5006`
so credentialed cross-origin requests work.

For production:

```bash
cd frontend
dx bundle --release
```

Output ends up in `dist/`. Copy `dist/` to wherever your reverse proxy
serves the SPA from.

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

- **Base URL.** In `cfg!(debug_assertions)` builds the SPA hard-codes
  `http://127.0.0.1:5006`. In release builds it reads
  `window.location.origin` so it talks to the same host that served
  the SPA.
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

Frontend: Pure Rust unit tests (time sync, filters, display helpers,
…) run on the host target with the `just frontend-test` recipe.

Browser/wasm stuff still has to be done manually with `dx serve`.

## When the SPA can't reach the backend

Common gotchas:

1. **CORS.** Set `ARCTOS_CORS_DEV=1` in `.env` *and* restart Flask. The
   browser console will show a CORS error before the request lands.
2. **Cookies blocked.** With `SameSite=None` the browser requires
   `Secure`; some browsers refuse `Secure` cookies over plain HTTP from
   non-localhost origins. Run on `127.0.0.1` / `localhost` in dev.
3. **Wrong base URL.** If the SPA is hitting a stale port, rebuild -
   the URL is baked in at compile time for dev builds.
