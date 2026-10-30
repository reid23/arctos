# `app/` - Flask backend

This package is the entire Python backend. It is wired up by the
`create_app()` factory in [`__init__.py`](__init__.py) and exposed to
gunicorn through the top-level `run_app.py`.

Each subpackage (`models/`, `routes/`, `services/`, `utils/`,
`domain/`, `serializers/`) has its own README; the top-level
[`README.md`](../README.md#repository-structure) lists what they
cover. The top-level files in this directory (`__init__.py`,
`error_handlers.py`, `error_values.py`, `exceptions.py`, `filters.py`)
are documented by their own module docstrings.

## How a request gets served

```
gunicorn ──-> run_app.py:app ──-> app.create_app() ──-> blueprint route
                                                    │
                                                    ├─-> validate request shape
                                                    ├─-> call service (in app/services/)
                                                    │       │
                                                    │       └─-> touch ORM models, commit
                                                    │
                                                    └─-> serialize Result -> JSON response
```

Four layers: **route -> service -> model -> db**. Keep routes thin; put
workflow logic in services; touch the DB through models (or the
[`dual_write`](services/README.md) helpers for join tables).

## What `create_app()` does

Walk-through of [`__init__.py`](__init__.py), top to bottom:

1. **Reads env vars** into `app.config`: `SECRET_KEY`, database URI,
   max upload size, OAuth client ID/secret, S3 video config, etc.
2. **Configures session cookies.** When `ARCTOS_CORS_DEV=1` it sets
   `SameSite=None; Secure` so the browser sends the session cookie on
   credentialed cross-origin requests from `dx serve`.
3. **Initialises extensions:** SQLAlchemy, Flask-Login, OAuth (via
   `authlib`), Flask-Executor (single-worker thread pool used to run
   ffmpeg finalisation off the request thread).
4. **Installs SQLite pragmas on every new connection** -
   `journal_mode=WAL`, `busy_timeout=30000`, and the load-bearing
   `foreign_keys=ON`. Without that last one SQLite ignores all
   `FOREIGN KEY` declarations and lets orphan rows accumulate.
5. **Registers blueprints:** `_api`, `auth`, `tournaments`, `matches`,
   `notes`, `registration`. They all use `url_prefix="/_api"`.
6. **Adds CORS middleware** scoped to `/_api/` (or all paths in dev),
   including `OPTIONS` preflight handling.
7. **Registers error handlers** (see `error_handlers.py`). An
   `ArctosError` becomes a JSON `{success: false, error: "..."}`
   response for API requests, or a flash + redirect for HTML paths.
8. **Boot-time work:** recomputes match schedules for every
   not-yet-complete tournament, and resumes interrupted YouTube uploads
   in background threads (best-effort; never blocks startup).

## Error Values

Most service methods return `Result[T, ArctosError]` from
[`error_values.py`](error_values.py). `Ok(value)` carries success and
`Err(error)` carries an expected failure. Routes pattern-match on the result:

```python
from app.error_values import Ok, Err

res = RegistrationService.register_team(tournament_url, team_id, pseudonym)
match res:
    case Ok(_):
        return jsonify({"success": True, "message": "Registered!"}), 200
    case Err(err):
        return jsonify({"success": False, "error": public_error_message(err)}), 400
```

Use `Option[T]` for an optional value: `Some(value)` carries a value and
`Null()` represents absence. At database and framework boundaries,
`option(value)` converts a nullable Python value to an `Option`. It preserves
`0`, `False`, empty strings, and empty collections as present values.

```python
from app.error_values import Some
from app.utils.name_validation import match_name_char_error

match match_name_char_error(name):
    case Some(message):
        return jsonify({"success": False, "error": message}), 400
```

### Transforming and propagating values

| Method | Use |
| --- | --- |
| `map(function)` | Transform a present or successful value. |
| `map_err(function)` | Transform a `Result` error. |
| `and_then(function)` | Continue with a function that returns an `Option` or `Result` of the same family. |
| `ok_or(error)` / `ok_or_else(factory)` | Convert an `Option` into a `Result`. |
| `unwrap_or(default)` | Supply a deliberate fallback for absence or failure. |

Callbacks run only for the variant they handle. A `Result.and_then` callback
uses the same error type as the original result.

Inside a function decorated with `@allow_Q`, `.Q()` extracts the contained
value and returns early on `Err` or `Null`. Every `.Q()` call must propagate
the function's declared return family and error type. Type checkers verify
the decorated signature; the developer is responsible for matching the
exception-carried value to that signature.

Reserve `unwrap()` and `expect()` for established invariants whose violation
is a programming error. Handle expected missing values and validation
failures through pattern matching, combinators, or `.Q()`. Keep a brief
explanation next to an invariant assertion when its guarantee needs context.

### Boolean conditions

Conditions should express the intended question:

- Use `is_some()`, `is_null()`, `is_ok()`, `is_err()`, or pattern matching
  to inspect a wrapper's variant.
- Use `value is None` for nullable values at external boundaries.
- Use `len(items) == 0` for an empty collection and `text == ""` for an
  empty string.
- Compare numeric values against the relevant threshold.
- Use a known boolean directly.

`Option` and `Result` raise `TypeError` when used as boolean conditions or
passed to `bool()`. Their payloads have independent meaning. JSON nulls,
nullable database columns, and framework callback signatures retain their
native representations at those boundaries. `Result[None, E]` represents
successful completion without a payload.

## Adding a new endpoint

1. Pick (or create) a blueprint in [`routes/`](routes/README.md).
2. If the work is non-trivial, add a method to a service in
   [`services/`](services/README.md). Return a `Result`.
3. Keep the route handler thin: parse request, call the service,
   convert `Result` to JSON.
4. Write a test in [`tests/`](../tests/README.md) - both a unit test
   for the service method and an integration test that hits the HTTP
   endpoint.

## Running

```bash
just run                 # gunicorn, 5 workers, on 0.0.0.0:8081
python run_app.py        # Werkzeug dev server on 127.0.0.1:5006
```

For everything else (frontend, migrations, tests) see the
[top-level README](../README.md).
