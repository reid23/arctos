# `app/utils/` - Helpers & Utility Functions

Helpers shared across routes, services, and models. The modules group
loosely into five topic areas:

- **Scheduling and the ASS DSL** - the Lisp-based dependency/skip
  language and the per-match scheduling algorithm.
- **Dates and times** - naive-UTC conversion helpers.
- **Auth, users, permissions** - the small functions every route reaches
  for (`get_registrable_config`, `can_head_ref_match`, ...).
- **Cameras, footage, video** - YouTube upload helpers and the simplified
  user-upload ingest path used by the footage API.
- **Validation, responses, misc** - reserved-character validation, JSON
  response helpers, TOML parse/write.

Each module's docstring covers its own surface area; the rest of this
README is for the topics that need more than a docstring's worth of
explanation.

## The ASS DSL

Arctos has a small Lisp-based DSL - the **A**rctos **S**chedule
**S**cript, or ASS - for expressing match dependencies and skip
conditions. The grammar (`grammar.lark`) is small enough to fit on one
screen, but the evaluation context is large; this is the part of the
codebase most likely to surprise newcomers.

Start with `parser.py`'s top docstring and walk outward.
`dsl_dependency_analyzer.py` walks an expression and reports which
matches it depends on; `scheduling.py` consumes that to run the
per-match scheduling algorithm (SAFE finalises start time when the
last dependency *starts*; FAST when all dependencies *complete*) on
top of the in-memory DAG built by `MatchGraph.py`. The user-facing
reference lives in
[`docs/arctos-schedule-script.md`](../../docs/arctos-schedule-script.md).

## When to put something here vs. elsewhere

- **Used by one route only** -> keep it in the route file as a private helper.
- **Pure logic, used in one service** -> private helper inside the service.
- **Shared across routes / services / serializers** -> here.
- **Domain workflow with a clear name** -> make it a service in
  [`services/`](../services/README.md) instead of a util.

## Examples

**Resolving the registration config for either a standalone or league
tournament:**

```python
from app.utils.helpers import get_registrable_config

cfg = get_registrable_config(tournament)
if cfg and cfg.team_registration_open:
    ...
```

**Generating a permission key (used for invite-only tournaments):**

```bash
uv run generate_permission_key.py my-tournament
```

That CLI calls `app.utils.helpers.generate_permission_key`.

**Checking head-ref eligibility:**

```python
from app.utils.helpers import can_head_ref_match

if not can_head_ref_match(tournament_url, current_user.id, match=match):
    return jsonify({"error": "Not allowed"}), 403
```

**Recomputing the schedule after a match changes:**

```python
from app.utils.scheduling import recompute_all_match_times

recompute_all_match_times(tournament_url)
```
