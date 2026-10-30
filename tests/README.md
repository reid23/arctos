# `tests/` - pytest suite

The Python test suite. Tests run with pytest under uv. The companion
document [`TESTING.md`](../TESTING.md) at the repo root has the deeper
"how to write a good test" guide; this README is the file-by-file map.

## Running

```bash
just test          # everything
just unit          # unit tests only (-m unit)
just integration   # integration tests only (-m integration)

just test -k "registration flow"       # by name (passes extra args through)
just coverage                          # coverage report via just
just coverage -k "registration flow"   # filtered coverage run
just coverage-check                    # CI-style coverage threshold

uv run pytest tests/ -k "registration"                          # by name, without just
uv run pytest tests/ --cov=app --cov-report=term-missing        # thresholded coverage, without just
uv run pytest tests/ --cov=app --cov-report=html                # HTML report -> htmlcov/
```

See [`TESTING.md`](../TESTING.md#coverage) for the coverage threshold
and what's configured under `[tool.coverage.*]` in `pyproject.toml`.

## Layout

- `conftest.py` - shared fixtures (`app`, `test_db`, `client`,
  `tournament`, ...). Documented below.
- `utils.py` - `make_registrable_config`, `login_as`.
- Top-level `test_*.py` - mixed unit / integration tests for
  cross-cutting concerns (the DSL parser, error values, registration
  flows).
- `unit/` - `@pytest.mark.unit` tests; pure logic, no HTTP, fast.
- `integration/` - `@pytest.mark.integration` tests; spin up the test
  client, hit `/_api/...`, assert on JSON.

## Markers

Mark every test:

```python
@pytest.mark.unit
def test_pure_logic(): ...

@pytest.mark.integration
def test_http_endpoint(client): ...

@pytest.mark.slow
def test_full_tournament(): ...
```

Configured in `pyproject.toml` under `[tool.pytest.ini_options]`. Strict
markers are on (`--strict-markers`), so an unknown marker is an error.

## Fixtures and `conftest.py`

If you are new to pytest: a **fixture** is a function whose return value
is injected into any test that names the fixture as a parameter. They
are how pytest provides set-up data and tear-down for tests without
each test rebuilding its own world.

```python
def test_register_team(client, tournament, team):  # 3 fixtures injected
    resp = client.post(f"/_api/{tournament.url}/register-team", ...)
```

`conftest.py` is the file pytest auto-loads to make fixtures available
to every test in that directory (and subdirectories) without an
explicit import. That is why nothing in our test files imports `app` or
`client` - they are pulled in by name.

See the pytest docs for fixtures
([explanation](https://docs.pytest.org/en/stable/explanation/fixtures.html),
[how-to](https://docs.pytest.org/en/stable/how-to/fixtures.html)) and
[`conftest.py`](https://docs.pytest.org/en/stable/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files).

### Key fixtures

| Fixture | Scope | What it gives you |
|---------|-------|-------------------|
| `app` | session | Flask app wired to a tempfile SQLite DB |
| `test_db` | function | Drops/recreates tables before each test, opens an app context for the whole test |
| `client` | function | `FlaskLoginClient` test client |
| `tournament` | function | Published tournament with two fields, registration open |
| `player` | function | A `Player` row with password set |
| `team` | function | A `Team` row with password set |
| `team_registration` | function | Confirmed paid `TeamRegistration` |
| `head_ref_player` | function | Player on the tournament's head-ref allow-list |
| `seeded_teams` | function | Pre-seeds dummy team IDs that some tests reference by string |

`test_db` keeps the app context open, so fixtures that depend on it
should write to `db.session` directly - **don't** push another
`with app.app_context()`.

## Common patterns

### Creating a tournament

`Tournament` requires a `RegistrableConfig`. Use the helper:

```python
from tests.utils import make_registrable_config
from models import Tournament

cfg = make_registrable_config(team_registration_open=True)
t = Tournament(url="my-event", name="My Event", start_date=..., registrable_config_id=cfg.id)
db.session.add(t); db.session.commit()
```

### Logging a user in

```python
from tests.utils import login_as

login_as(client, player)        # injects Flask-Login session keys
client.post("/_api/...")
```

### Reading DB state after an HTTP call

The test client handles each request in its own context, so post-call
queries need a fresh context:

```python
resp = client.post("/_api/...")
assert resp.status_code == 200

with app.app_context():
    row = TeamRegistration.query.filter_by(...).first()
    assert row.status == "CONFIRMED"
```

### API returns JSON, not redirects

Routes under `/_api/` return JSON `{success, ...}`. Don't assert
`resp.status_code == 302` - assert `200` for success and `400` for
validation failure.

### `db.session.begin()` is wrong here

`test_db` already manages the transaction. Use plain
`db.session.add()` + `db.session.commit()`. Nesting `db.session.begin()`
will fail with "transaction already in progress".

## Adding a test

Static API contracts live in `typecheck/` and run through `just typecheck`.
They verify inferred types and deliberately rejected calls. See
[type-check guidance](../TESTING.md#type-checks) for the checked scope and
how to extend it.

1. Pick `unit/` or `integration/` (or a top-level file) based on
   whether it touches HTTP / the DB.
2. Mark it (`@pytest.mark.unit` or `@pytest.mark.integration`).
3. Reuse fixtures from `conftest.py` - only write a new fixture if the
   shared ones don't fit.
4. Run `just test` (or `just unit` / `just integration`) before
   pushing.

For best practices and gotchas, read [`TESTING.md`](../TESTING.md).
