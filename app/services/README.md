# `app/services/` - application-workflow layer

Services live between the HTTP layer ([`routes/`](../routes/README.md))
and the persistence layer ([`models/`](../models/README.md)). They own
multi-step workflow logic: register a team, start a match, recompute
a schedule. Routes call into a service for anything non-trivial.

Each `*_service.py` file owns one workflow area; read the module
docstring to see what it covers. `dual_write.py` is the canonical
interface for the four normalised join tables - call its helpers
instead of querying those tables directly.

## Public documents and application entry

[`public_page_service.py`](public_page_service.py) builds immutable Events and
event Info view models. Routes pass the visitor, current calendar day, and
URL prefix. The service applies permissions, selects one directory page, and
batches registration configuration, confirmed team counts, and personal status.
League events use league registration scope. Descriptions pass through the
existing Markdown sanitizer.

[`public_league_service.py`](public_league_service.py) builds the Leagues directory
and league Info models. It paginates in SQL, filters Active/Past counts by the
search query, and batches visible round summaries and registration status.
Season lifecycle uses all rounds; displayed dates and counts use only visible
rounds. League Info includes sanitized description, live fees and capacity,
waiver requirements, and the requesting account's status.

`DirectoryPage` is shared by the Jinja directory and `GET /_api/public/events`.
It contains bounded cards, counts, search state, and pagination links. Event
models include public facts and permitted actions. Account-specific status
belongs to the requesting visitor; the HTTP layer serves these responses with
`private, no-store`. Unknown fees remain distinct from free registration.

[`frontend_shell.py`](frontend_shell.py) validates the build manifest and URL
prefix, renders application entry, and serves prepared assets for local/staging
use. Missing application assets produce a recoverable 503. Public HTML warms
a parked runtime; marked reading links fetch another document while startup
is pending or unavailable. Navigation activates the application once its bridge
is ready. Interactive entries display feedback until their route and styles
are ready. Data comes from the configured database and permission services.

Jinja handles document rendering; Dioxus handles application workflows. Shared
styles and appearance preferences preserve the existing design. See
[architecture](../../docs/public-html-architecture.md) for the data and startup
contracts, [deployment](../../docs/DEPLOY.md#public-html-rollout) for asset
serving, and [verification](../../docs/public-html-verification.md) for external
performance measurements.

## `RegistrationService` methods

The registration service provides the following key methods:

- **`register_team(tournament_url, team_id, pseudonym)`** — Register a team to a
  tournament. Returns `PENDING_TEAM_APPROVAL` or `CONFIRMED` depending on
  league/tournament config.
- **`register_player(tournament_url, player_id, team_id=None, ...)`** — Register a
  player to a tournament (optionally under a team). Returns `PENDING_TEAM_APPROVAL`
  or `CONFIRMED` depending on config.
- **`register_player_as_to(tournament_url, *, actor_user_id, actor_user_type, player_id, team_id=None, ...)`** — Register a player on behalf of a TO. The actor must be a TO for the tournament. Always returns a `CONFIRMED`, paid `PlayerRegistration`, covered by the player's own paid entry or a new `ORGANIZER` one. Existing `CANCELLED`/`REJECTED` registrations for the same (tournament, player) are reused; existing `CONFIRMED` rows return a duplicate-error. Rejects historical tournaments, whose rosters are managed by `HistoricalClaimsService`.
- **`register_team_as_to(tournament_url, *, actor_user_id, actor_user_type, team_id, pseudonym="")`** - Register a team on behalf of a TO. The actor must be a TO for the tournament. Returns a CONFIRMED, paid `TeamRegistration`, covered like `register_player_as_to`. Honors `n_max_teams` (error message includes the count and limit). Existing `CANCELLED` rows for the same (tournament, team) are reused; existing `CONFIRMED` rows return a duplicate-error. Rejects historical tournaments, whose teams are added by `HistoricalRecordsService`.
- **`deregister_team(scope, team_id, *, transfer_to=None, actor=None)`** / **`deregister_player(scope, player_id, *, transfer_to=None, actor=None)`** — Cancel the caller's own registration. The paid entry stays with the holder for re-registration, or moves to `transfer_to`. A team's roster stays registered as unattached players.
- **`deregister_team_as_organizer(scope, team_id, *, void_entry, actor)`** / **`deregister_player_as_organizer(...)`** — Cancel any registration; the route checks organizer rights. The entry stays with the registrant unless `void_entry` is set.

## Paid entries

`paid_entry_service` is the only writer of `PaidEntry` and `PaidEntryEvent`. A
registration is paid exactly when an `APPLIED` entry covers it; `paid`,
`amount_paid`, and `paid_at` on registrations are read-only properties.

- `cover_registration(registration, *, source_if_new, actor=None)` applies the
  holder's entry for the scope, or creates one from `source_if_new`. Every
  registration path calls it.
- `release_registration` returns the entry to its holder as `AVAILABLE` when
  they deregister.
- `transfer_entry`, `revoke_transfer`, and `decline_transfer` move an entry that
  isn't covering a registration. A recipient who is already registered and unpaid
  is covered at once. A team entry transferred at deregistration keeps its place
  under `n_max_teams` until claimed or returned.
- `record_manual_payment` applies an organizer's mark-paid edit.
- `delete_for_scope` runs before an event's or league's registrations are deleted.

A holder has at most one non-voided entry per scope. Functions flush but never
commit.

## Team roster changes

`team_management_service.change_roster(scope, team_id, registration_id, action)`
updates a player's team assignment within a standalone event or league. Pass
`Scope.event(event_url)` or `Scope.league(league_url)` from `_common.py`. The
calling route authorizes the team account or its manager before calling the service.

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

The service preserves paid entries, waiver, and jersey details. It commits a successful
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
