# `app/services/` - application-workflow layer

Services live between the HTTP layer ([`routes/`](../routes/README.md))
and the persistence layer ([`models/`](../models/README.md)). They own
multi-step workflow logic: register a team, start a match, recompute
a schedule. Routes call into a service for anything non-trivial.

Each `*_service.py` file owns one workflow area; read the module
docstring to see what it covers. `dual_write.py` is the canonical
interface for the four normalised join tables - call its helpers
instead of querying those tables directly.

## `RegistrationService` methods

The registration service provides the following key methods:

- **`register_team(tournament_url, team_id, pseudonym)`** — Register a team to a
  tournament. Returns `PENDING_TEAM_APPROVAL` or `CONFIRMED` depending on
  league/tournament config.
- **`register_player(tournament_url, player_id, team_id=None, ...)`** — Register a
  player to a tournament (optionally under a team). Returns `PENDING_TEAM_APPROVAL`
  or `CONFIRMED` depending on config.
- **`register_player_as_to(tournament_url, *, actor_user_id, actor_user_type, player_id, team_id=None, ...)`** — Register a player on behalf of a TO. The actor must be a TO for the tournament. Always returns a `CONFIRMED`, fully-paid `PlayerRegistration` with `paid=True, amount_paid=0`. Existing `CANCELLED`/`REJECTED` registrations for the same (tournament, player) are reused; existing `CONFIRMED` rows return a duplicate-error.
- **`register_team_as_to(tournament_url, *, actor_user_id, actor_user_type, team_id, pseudonym="")`** - Register a team on behalf of a TO. The actor must be a TO for the tournament. Returns a CONFIRMED, fully-paid `TeamRegistration` with `paid=True, amount_paid=0`. Honors `n_max_teams` (error message includes the count and limit). Existing `CANCELLED` rows for the same (tournament, team) are reused; existing `CONFIRMED` rows return a duplicate-error.

## `RegistrationFormService` methods

Registration forms use the effective `RegistrableConfig`: a standalone
tournament's configuration or its parent league's configuration.

- **`validate_settings(..., current_config=config)`** checks the organizer's
  prefilled Form link and private response Sheet. It returns all configuration
  fields for an atomic settings save. A form or code-question change generates
  a new private signing key; Sheet mapping corrections preserve it.
- **`personal_url(config, player_id)`** builds a prefilled link containing a
  stable, opaque player code. Routes authorize the player before returning
  this link. It does not contact Google.
- **`require_response(config, player_id)`** checks for the target player's code
  in the selected response column. Registration and pending-confirmation
  workflows call it before changing records. Missing responses return a
  validation error; unavailable checks return a temporary service error.
- **`public_config(config)`** and **`organizer_config(config)`** provide their
  respective views of configuration. Neither exposes the signing key.

Each configuration has a random key. HMAC-SHA256 derives codes from that key
and the player's account ID, with a fixed purpose prefix. Response data remains
in bounded, short-lived memory caches. The [server and organizer setup
guide](../../docs/env-vars.md#registration-forms) covers access and verification.

## Team roster changes

`team_management_service.change_roster(scope, team_id, registration_id, action, actor=actor)`
updates a player's team assignment within a standalone event or league. Pass
`Scope.event(event_url)` or `Scope.league(league_url)` from `_common.py`. The
calling route passes the signed-in account as `actor`; the service verifies its
permission to manage the team.

| Action | Required player state | Result |
| --- | --- | --- |
| `add` | Confirmed registration, unattached | Joins the team's confirmed roster. |
| `approve` | Pending approval for this team | Joins the team's confirmed roster. |
| `decline` | Pending approval for this team | Becomes a confirmed, unattached player. |
| `remove` | Confirmed on this team | Becomes a confirmed, unattached player. |

The team must have a confirmed registration in the same scope. Adding and removing
players require open player registration; pending requests remain reviewable after
registration closes. Adding and approving players respect the roster limit, with
writes serialized per team to keep concurrent approvals within capacity.
Approving or declining a pending request verifies the player's required form
before acquiring the roster write lock. The transaction rechecks the player,
team, pending status, form settings, and manager permission before confirming the
registration. If the form settings change during verification, the caller must
retry. Confirmed roster changes preserve the player's registration and do not
require another form response.

The service preserves payment, waiver, and jersey details. It commits a successful
change and returns `Ok(None)`, or returns `Err(ArctosError)` for a rejected action.

## Conventions

### Static-method namespace classes

Most services are dataclasses that act as typed namespaces:

```python
@dataclass(frozen=True)
class RegistrationService:
    @staticmethod
    def register_team(tournament_url: str, team_id: str, pseudonym: str) -> Result["TeamRegistration", ArctosError]:
        ...
```

There's no instance state. The class form is purely for grouping and
type-checking - call methods as `RegistrationService.register_team(...)`.

### `Result[T, ArctosError]` returns

Services return `Result` (from
[`app/error_values.py`](../error_values.py)) instead of raising. Routes
pattern-match on `Ok` / `Err` to convert to JSON. Inside a service the
`.Q()` method short-circuits with the contained error - but only when
the function is wrapped in `@allow_Q`:

```python
@staticmethod
@allow_Q
def start_match(...) -> Result["Match", ArctosError]:
    match = MatchActionsService._require_match(tournament_url, match_id).Q()  # propagates Err
    ...
    return Ok(match)
```

### Flask-agnostic where possible

`permission_service.py` is the cleanest example: it never touches
`current_user`, `request`, `flash`, or `redirect`. Pass the `user`
object in. Keeping services Flask-free makes them straightforward to
test and reuse from background tasks.

### `dual_write.py`

Don't query `MatchPlayer`/`MatchReferee`/`HeadRefAllowList`/
`CameraTimepoint` directly from routes or services. Use the helpers
here:

```python
from app.services.dual_write import (
    get_match_player_ids,
    set_match_referees_from_csv,
    get_head_ref_allowlist_ids,
)

players = get_match_player_ids(match, side=WinnerSide.TEAM1)
set_match_referees_from_csv(match, "Alice,Bob,Charlie")
allowed = get_head_ref_allowlist_ids(tournament)
```

The helpers handle invariants (slot ordering, orphan-FK behaviour,
unique-on-`(match_uuid, player_id)`) so callers don't re-implement
them.

## Adding a service

1. Pick a name that describes a workflow (`*Service`), not a noun the
   models already cover.
2. Use `@dataclass(frozen=True)` and `@staticmethod` methods unless you
   genuinely need instance state.
3. Return `Result[T, ArctosError]`. Wrap in `@allow_Q` if you want to
   use `.Q()` internally.
4. Don't import from `flask` (or do so only behind a function-local
   import) - keep the boundary clean.
5. Write a unit test in `tests/unit/`, plus an integration test that
   hits the route that wraps it.

## Example: starting a match

Tracing `MatchService.start_match` shows how the layers cooperate:

1. **Route** (`app/routes/_api.py`) parses request, calls
   `MatchService.start_match(...)`.
2. **`MatchService.start_match`** (this directory):
   - Asks `MatchActionsService._require_match` to fetch and validate
     the match exists and belongs to the tournament. Uses `.Q()` to
     short-circuit on `Err`.
   - Asks `match_start_eligibility.is_eligible_to_start` whether the
     transition is allowed (status, conflicts, ref permissions).
   - Mutates the match: status, started_at, started_by, camera stream
     starts.
   - Calls `dual_write.set_match_players_from_csv` to write the team
     rosters into `match_players`.
   - Triggers `app.utils.scheduling.recompute_all_match_times` so any
     dynamically-scheduled successors update their start times.
3. Returns `Ok(match)` or `Err(error)`.
