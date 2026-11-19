# For AI Agents

This page is for LLM agents (and the people running them) that set up,
analyze, and check tournaments through the Arctos API. It is a
workflow guide; the [API reference](developers.md#api-reference) has
every endpoint, and [Arctos Schedule Script](arctos-schedule-script.md)
has the expression language.

An agent with a `manage` token can do everything an organizer does
before the event: create the tournament, change its settings, register
teams, build and fix the schedule, set up side competitions, penalty
types, and the bracket, and publish it all. It can't run matches
(start them, score points, finalize them); head refs do that on the
website.

## Getting a token

A person has to grant access once, because `manage` acts as their
account:

1. On [Your apps](/developers/apps), register a **public** app with a
   loopback redirect URI such as `http://127.0.0.1/callback`.
2. Run the [authorization code flow with PKCE](developers.md#sign-in-with-arctos)
   asking for `manage` (the person signs in and approves in a browser),
   and give the agent the resulting refresh token.
3. The agent exchanges the refresh token for an access token, which
   lasts an hour, and sends it as `Authorization: Bearer <token>`.

Every refresh returns a new refresh token: save it and drop the old
one. Reusing an old refresh token signs the person out of the app, so
two agents must not share one refresh token. The user can revoke the
app at any time under **Connected apps** on their profile.

The user only manages tournaments and leagues they organize (or create
through the API).

## Rules that prevent most mistakes

- **Importing a schedule replaces it.** `PUT .../schedule` deletes
  variables, tags, fields, and matches the file leaves out. To change an
  existing schedule, export it (`GET .../schedule/script`), edit the
  export, and import it again. Keep the `uuid` / `id` lines, which update
  rows in place; removing them recreates the rows with new ids.
- **An invalid file changes nothing.** A `400` lists every problem; fix
  them all and send the file again.
- **The import's `warnings` aren't the schedule's warnings.** The import
  only warns about names it turned into tags. Always also call
  `GET .../schedule/warnings`, which finds double-booked teams (including
  referees playing elsewhere), cycles, and unknown references.
- **Times are UTC.** `scheduled_start_time = 2026-07-04T16:00:00` in a
  schedule file is 16:00 UTC; convert from the event's local time.
- **Only `STATIC` matches (and static breaks) have times you set.**
  `FAST` and `SAFE` matches start when their `previous_match` on the
  same field ends, or later if they wait on another match's
  `::winner` / `::loser`. The first match on each field must be
  `STATIC`. Read the resulting times from `GET .../matches`
  (`start_time`). See [Scheduling](docs.md#dynamic-matches) for `FAST`
  versus `SAFE`.
- **Names are identifiers.** Match names are unique in a tournament
  (breaks and joins share a name across fields) and can't contain `/`,
  `,`, or `::`. Teams are referred to by team id (the username), not
  display name; a name that isn't a registered team id becomes a tag.
- **Started matches are locked** (`409`). Plan changes before the event.
- **Expressions evaluate immediately.** A tag like
  `(get 0 (sort-by pool (lambda (t) (wins t))))` picks a team before any
  game is played (everyone has 0 wins). Only `(winner {Match})` and
  `(loser {Match})` wait for a match to finish. For seeds that depend on
  pool standings, use tags without expressions and set their teams once
  the pool is done (see [After the pool](#after-the-pool)).
- **Deleting a tournament or league needs `?confirm=<id>`.** Don't delete
  unless the person asked you to.
- **Publish last.** Tournaments and schedules start unpublished, which
  lets you build and check them before anyone sees them.

## Building a tournament

1. **Create it:** `POST /tournaments` with `{"id", "name"}` (and
   `"league"` for a league round).
2. **Configure it:** `PATCH /tournaments/{id}` with dates, location,
   `about`, and `registration` settings. Read them back with
   `GET .../settings`.
3. **Register teams**, or open registration and wait for them:
   `POST .../registrations/teams` with `{"team_id"}`. Check
   `GET .../registrations` for the teams, their ids, and payment.
   Team ids must exist as Arctos team accounts.
4. **Write the schedule file** (below) and `PUT .../schedule` with
   `{"script": "..."}`.
5. **Verify** (below) and fix by editing the file and importing it again.
6. **Extras:** side competitions, penalty types, schedule items (meals,
   ceremonies), and the bracket layout, as described in the
   [API reference](developers.md#managing-tournaments).
7. **Publish:** `PATCH /tournaments/{id}` with
   `{"published": true, "schedule_published": true}`, and
   `PATCH .../bracket` with `{"published": true}`.

### Example schedule file

Six teams in two pools of three, one pool per field. In each pool game
the team sitting out referees, so no one is booked on both fields at
once. After lunch the pool winners meet in a best-of-three final. A
second final is played only if `A1` loses the first one:

```toml
[[tags]]
name = "A1"

[[tags]]
name = "A2"

[[tags]]
name = "B1"

[[fields]]
name = "North"

[[fields]]
name = "South"

[[matches]]
name = "A: Bears v Wolves"
field = "North"
schedule_type = "STATIC"
scheduled_start_time = 2026-07-04T16:00:00
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "bears"
team2_initial = "wolves"
refs_initial = "otters"

[[matches]]
name = "A: Wolves v Otters"
field = "North"
schedule_type = "FAST"
previous_match = "A: Bears v Wolves"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "wolves"
team2_initial = "otters"
refs_initial = "bears"

[[matches]]
name = "A: Otters v Bears"
field = "North"
schedule_type = "FAST"
previous_match = "A: Wolves v Otters"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "otters"
team2_initial = "bears"
refs_initial = "wolves"

[[matches]]
name = "B: Foxes v Hawks"
field = "South"
schedule_type = "STATIC"
scheduled_start_time = 2026-07-04T16:00:00
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "foxes"
team2_initial = "hawks"
refs_initial = "crows"

[[matches]]
name = "B: Hawks v Crows"
field = "South"
schedule_type = "FAST"
previous_match = "B: Foxes v Hawks"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "hawks"
team2_initial = "crows"
refs_initial = "foxes"

[[matches]]
name = "B: Crows v Foxes"
field = "South"
schedule_type = "FAST"
previous_match = "B: Hawks v Crows"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "crows"
team2_initial = "foxes"
refs_initial = "hawks"

[[matches]]
name = "Lunch"
field = "North"
schedule_type = "BREAK"
previous_match = "A: Otters v Bears"
nominal_length = 45

[[matches]]
name = "Lunch"
field = "South"
schedule_type = "BREAK"
previous_match = "B: Crows v Foxes"
nominal_length = 45

[[matches]]
name = "Final"
field = "North"
schedule_type = "SAFE"
previous_match = "Lunch"
set_type = "SETS"
nsets = 3
nominal_length = 40
team1_initial = "tag::A1"
team2_initial = "tag::B1"
refs_initial = "tag::A2"

[[matches]]
name = "Final (if necessary)"
field = "North"
schedule_type = "FAST"
previous_match = "Final"
set_type = "SETS"
nsets = 3
nominal_length = 40
team1_initial = "tag::A1"
team2_initial = "tag::B1"
refs_initial = "tag::A2"
skip_condition = "(won? [tag::A1] {Final})"
```

Importing it creates 2 fields, 3 tags, and 10 rows, with no schedule
warnings. Pool games run 16:00 to 17:30 UTC on both fields, lunch until
18:15, then the final.

Useful patterns:

- **Bracket progressions:** `team1_initial = "Semi 1::winner"`. The match
  waits for `Semi 1` and fills in its team when it finishes.
- **Referees:** `refs_initial` is comma-separated (`"otters, hawks"`) and
  takes the same references as teams.
- **Match formats:** `set_type = "SETS"` with `nsets`; `"STONES"` with
  `stones_per_set`; or `"TIMED"` for fixed-length sets of wall time, such
  as two 12-minute halves (`nsets = 2`, `stones_per_set = 12`, in
  minutes). Make `nominal_length` cover the whole match, including the
  time between sets.
- **Optional games:** `skip_condition` is an expression that skips the
  match when it becomes `true`; check it with
  `POST .../schedule/validate` first.
- **Fields waiting for each other:** put a same-named `JOIN` on each
  field, then breaks or matches after it (see [Joins](docs.md#joins)).
  For several fields' breaks edited as one, use
  [break groups](developers.md#break-groups).
- **Starting from an existing event:** export a past tournament's
  schedule and adapt it; exports show every option in use.

### After the pool

Once the pool games are finished, read the standings and fill the
seeds:

1. `GET /tournaments/{id}/results` returns standings ranked the way the
   website ranks them.
2. `GET .../tags` gives each tag's `id`.
3. `PUT .../tags/{tag_id}/team` with `{"team_id": "wolves"}` sets a seed.
   Matches that haven't started pick it up right away.

## Verifying a schedule

Run these after every import, and fix anything they report before
publishing:

1. `GET .../schedule/warnings` returns `{"warnings": []}`.
2. `GET .../matches` (organizers see it unpublished): every row has the
   field, teams, length, and `start_time` you intended. Slots waiting on
   a tag or another match show the placeholder as the team's `name` with
   `"id": null`. Check that only the slots you meant to leave open show up
   that way.
3. Every expression (tag, variable, and skip condition) passes
   `POST .../schedule/validate` with `{"expression": "..."}`. An unknown
   team or match name gives `"valid": false` and an `error`.
4. Export the schedule and compare it with the file you meant to import.
5. Check fairness: the number of games and refereeing assignments per
   team, rest between commitments, and when each team's day ends. The
   script below prints this.

```python
"""Summarize a tournament's schedule per team: games, refereeing, and rest.

Usage: ARCTOS_TOKEN=... python analyze_schedule.py <tournament_id>
"""

import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

BASE_URL = os.environ.get("ARCTOS_URL", "https://events.californiajugger.org") + "/api/v1"


def get(path):
    request = urllib.request.Request(BASE_URL + path, headers={"Authorization": f"Bearer {os.environ['ARCTOS_TOKEN']}"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def team_timelines(matches, referees_by_match):
    """team name -> sorted [(start, end, role, match name)]; also the slots that aren't filled yet."""
    timelines = defaultdict(list)
    unfilled = []
    for match in matches:
        if match["schedule_type"] in ("BREAK", "JOIN", "STATBREAK") or match["start_time"] is None:
            continue
        start = datetime.fromisoformat(match["start_time"].replace("Z", "+00:00"))
        end = start + timedelta(minutes=match["length_minutes"] or 0)
        slots = [(match["team1"], "plays"), (match["team2"], "plays")]
        slots += [(referee, "refs") for referee in referees_by_match[match["id"]]]
        for team, role in slots:
            if team["id"] is None:
                unfilled.append((match["name"], role, team["name"]))
            else:
                timelines[team["name"]].append((start, end, role, match["name"]))
    return {team: sorted(entries) for team, entries in timelines.items()}, unfilled


def report(timelines, unfilled, minimum_rest=timedelta(minutes=0)):
    for team, entries in sorted(timelines.items()):
        games = sum(role == "plays" for _, _, role, _ in entries)
        refs = sum(role == "refs" for _, _, role, _ in entries)
        print(f"{team}: {games} games, {refs} refs, {entries[0][0]:%H:%M}-{entries[-1][1]:%H:%M} UTC")
        for (_, end, _, name), (start, _, _, next_name) in zip(entries, entries[1:]):
            if start < end:
                print(f"  CONFLICT: {name} overlaps {next_name}")
            elif start - end < minimum_rest:
                print(f"  short rest: {start - end} between {name} and {next_name}")
    for name, role, placeholder in unfilled:
        print(f"unfilled: {name} {role} {placeholder}")


if __name__ == "__main__":
    tournament_id = sys.argv[1]
    matches = get(f"/tournaments/{tournament_id}/matches")["matches"]
    referees_by_match = {
        match["id"]: get(f"/tournaments/{tournament_id}/matches/{match['id']}")["match"]["referees"] for match in matches
    }
    report(*team_timelines(matches, referees_by_match), minimum_rest=timedelta(minutes=30))
```

For the example above it reports two games and one refereeing slot per
team, all between 16:00 and 17:30. It also flags every pool team's
commitments as back to back, because the team sitting out referees. It
lists the finals' slots as unfilled until the seeds are set.

## Analyzing a tournament

Everything public is readable without the `manage` scope. Useful
starting points:

- `GET /tournaments?when=past` and `GET /tournaments/{id}` to find events.
- `GET .../matches` and `GET .../matches/{match_id}` for results, points
  with timestamps, and referees. `started_at` and `completed_at` against
  `start_time` show how far the day ran behind.
- `GET .../results` for standings, and `GET /leagues/{id}/results` across
  a league.
- `GET .../schedule/script` (organizers only) to reuse a past event's
  format.

## Rehearsing safely

To try a format without touching a real event, either build it as an
unpublished tournament and delete it afterward
(`DELETE /tournaments/{id}?confirm={id}`), or run Arctos locally (see
the [README](https://github.com/reid23/arctos#running-the-app-locally))
and point the agent at it with its own app and token. On a local copy
you can also play matches on the website to see brackets, skip
conditions, and dynamic start times move as results come in.
