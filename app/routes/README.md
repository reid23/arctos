# `app/routes/` - Flask blueprints

HTTP handlers are organised into Flask blueprints by topic. Most browser
application APIs use **`/_api/`**; public document, OAuth, third-party API,
and waiver routes intentionally use their existing prefixes.

Each file in this directory is a blueprint; its module-level docstring
states the topic it covers. New endpoints should go into the most
specific blueprint that fits; if none does, add a new one.

## What is a blueprint?

A [Flask blueprint](https://flask.palletsprojects.com/en/stable/blueprints/)
is a way to group related routes into a module that can be registered
on the app as a unit. Instead of every endpoint hanging off the global
`app` object, each topic gets its own `Blueprint(...)` object that
collects its own routes; `create_app()` then calls
`app.register_blueprint(bp, url_prefix="/_api")` once per blueprint.
The result is the same URL routing, but the code is split into
manageable files instead of one mega-module.

## Conventions

### URL prefix

Most application blueprints register with `url_prefix="/_api"`. The waivers
blueprint registers without that prefix because the frontend links to
`/<event>/waiver` directly. `public_pages` also registers without a prefix to
handle public documents and application entry at existing URLs.

`/api/` (no underscore) is the stable, public surface for third-party
apps, documented in [`docs/developers.md`](../../docs/developers.md).
Only add routes there deliberately: they're a contract. They
authenticate with OAuth bearer tokens via
`app.utils.oauth_server.require_bearer`, never with the session
cookie, and send `Access-Control-Allow-Origin: *`. Bearer tokens are
accepted nowhere else; `/_api/` stays session-only. The `oauth`
blueprint hosts both its public `/api/oauth/...` endpoints and the
SPA's `/_api/oauth/...` helpers, so it registers without a prefix.

The public API itself is the [`api_v1/`](api_v1/__init__.py) package:
one blueprint at `/api/v1`, one module per resource area. Its routes
differ from `/_api/` ones in a few ways:

- Pass `g.oauth_user` (and `g.oauth_token.user_type`) to services
  explicitly; never read or set `current_user`.
- Build responses with
  [`app/serializers/api_v1_serializer.py`](../serializers/api_v1_serializer.py),
  never the SPA serializers, and change shapes only by adding fields.
- Errors are `{"error": "..."}` with no `success` key.
- Show only what a signed-out website visitor sees, via the visibility
  helpers in `api_v1/__init__.py` (`with_tournament`,
  `can_view_schedule`, ...), unless a scope grants more.
- Every request is logged on the `arctos.api` logger.

### Return JSON

API routes return JSON `{success, ...}` payloads with HTTP status
codes:

- `200` on success
- `400` on validation failure
- `401` when unauthenticated (handled centrally by `login_manager.unauthorized_handler`)
- `403` when forbidden
- `404` when a resource isn't found

### Public HTML and application documents

[`public_pages.py`](public_pages.py) classifies document GET requests. With
`ARCTOS_PUBLIC_HTML_ENABLED=1`, Flask renders `/`, `/leagues`, and published
`/<event>` and `/leagues/<league>` Info pages through Jinja. `?tab=info` selects
the same reading page. Other declared application URLs use the startup shell.
The public routing flag defaults to `0`.

Both directories preserve `q`, `view=past`, and `page`. The Events directory's
`/_api/public/events` endpoint shares the same bounded projection with Dioxus.
Event Teams/Side events tabs, `?tab=app`, and authorized drafts use Dioxus.
`?app=1` explicitly selects application rendering. Existing URLs remain valid.

The blueprint owns response status, metadata, templates, and cache headers.
Services own data selection and permissions. With public routing enabled,
missing or inaccessible events/leagues return 404; document-selection failures return
503. Indexing follows route publication and purpose, as documented in the
[indexing policy](../../docs/public-html-indexing.md). API and asset namespaces
retain their own response types and missing-resource handling.

Documents and the shared Events endpoint use `private, no-store` and vary on
cookies. The current account's registration/payment/waiver summary is scoped to
its request. Mutations remain on authenticated APIs. Calendar-day filtering
uses the visitor's validated timezone preference or configured fallback;
printed event dates retain their entered day. Canonical URLs use the configured
trusted origin.

When adding an application route, update this classifier, the Rust `Route`
enum, and their contract tests together. See
[architecture](../../docs/public-html-architecture.md) for the ownership map.

### Thin routes

The route handler should:

1. Authenticate (via `@login_required` or a custom decorator).
2. Parse the request - query params, form data, JSON body.
3. Call the service.
4. Convert the `Result` to JSON.

Workflow logic lives in [`app/services/`](../services/README.md). If
you're writing more than ~30 lines of route handler, consider whether
some of it belongs in a service.

```python
@bp.route("/<tournament_url>/register-team", methods=["POST"])
@login_required
def register_team_for_tournament(tournament_url: str):
    if not is_team(current_user):
        return jsonify({"success": False, "error": "Only teams can register"}), 403

    res = RegistrationService.register_team(
        tournament_url, current_user.id, request.form.get("pseudonym", "")
    )
    match res:
        case Ok(_):
            return jsonify({"success": True, "message": "Registered!"}), 200
        case Err(err):
            return jsonify({"success": False, "error": public_error_message(err)}), 400
```

### `@login_required` and the unauthorised handler

`login_manager.unauthorized_handler` (in `app/__init__.py`) returns a
JSON 401 for `/_api/...` paths instead of redirecting to a login URL -
this avoids CORS errors when the browser tries to follow the redirect.
Frontend code treats a 401 as "log in and retry".

For TO-only routes use the `@require_tournament_organizer` decorator
from `app.utils.decorators`.

## CORS in dev

When `ARCTOS_CORS_DEV=1`, `app/__init__.py` adds CORS headers and
handles `OPTIONS` preflight for every path. The browser sends the
session cookie because cookies are configured `SameSite=None; Secure`.
In production, nginx serves assets and proxies Flask documents/APIs on the
same origin. Application API requests use the existing session credentials.

## Adding a new endpoint

1. Pick the right blueprint or create a new one (rarely needed).
2. If you create a new blueprint, register it in
   `app/__init__.py::create_app`.
3. Use `@login_required` for anything that touches user data.
4. Validate inputs early; return `400` with a clear error message.
5. Delegate to a service for any non-trivial logic.
6. Write integration tests in [`tests/`](../../tests/README.md). The
   `client` fixture + `login_as(client, user)` from `tests/utils.py`
   covers most cases.

## When to promote a blueprint to a package

Default to a single file. Promote to a package only when one of these
triggers fires:

- The file crosses **~1,000 lines**.
- The file develops **3+ genuinely distinct sub-topics** that don't share
  state or helpers.
- Recurring **merge conflicts** because two contributors are routinely
  editing different parts of the same file.

A single trigger is sufficient. Conversely, a 1,200-line file whose
contents are tightly interrelated (one CRUD surface, shared helpers
throughout) can stay a single file.

### How to promote (mechanical)

1. `mkdir app/routes/<topic>/`.
2. `git mv app/routes/<topic>.py app/routes/<topic>/__init__.py`.
3. In `__init__.py`, keep the `bp = Blueprint(...)` definition and any
   helpers shared across submodules. Add
   `from . import <submodule1>, <submodule2>, ...` at the bottom so
   importing the package registers all handlers.
4. Create submodule files. Each starts with `from . import bp` (no new
   blueprint - they extend the existing one) and contains the routes
   for one sub-topic.
5. Run the test suite and confirm `app.url_map` is unchanged - the move
   should be a no-op for the URL surface.
6. No external caller updates needed - `from app.routes.<topic> import bp`
   keeps working because `__init__.py` exposes it.

### What NOT to do

- Don't promote preemptively. A 200-line route file that *might* grow
  is not a candidate.
- Don't create new blueprints inside the package. Submodules share one
  Blueprint object so URL endpoint names stay stable across moves.

The :mod:`app.routes.tournaments` package is the canonical example.
