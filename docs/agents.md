# For AI Agents

This page is for LLM agents (and the people running them) that build,
analyze, and check tournament schedules through the Arctos API. It is a
workflow guide; the [API reference](developers.md#api-reference) has
every endpoint, [Arctos Schedule Script](arctos-schedule-script.md) has
the expression language, and the [user docs](docs.md#match-schedule-setup)
describe the schedule editor that organizers use.

## What an agent is for

An organizer sets up the tournament on the website: they create it,
fill in dates, location, and registration settings, and open
registration. Teams register themselves on the website; at the event,
organizers use the quick-register tools for people signing up in person.
The agent's job is the schedule around that:

- build the schedule (fields, tags, matches, breaks) and check it;
- adjust it as plans change, before matches start;
- analyze schedules and results.

The API can also create tournaments, change settings, register teams,
and publish, because organizers' own tools use it. Agents should not do
those unless the organizer explicitly asks. In particular, leave
publishing the tournament and its schedule to the organizer.

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

The token can manage only the tournaments and leagues its user
organizes. Ask the organizer for the tournament's id (the slug in its
URL); `GET /tournaments` with a `manage` token also lists their
unpublished ones.

## How Arctos schedules

A schedule is a set of fields, each with a chain of entries. Every
entry has a field, a nominal length in minutes, and a schedule type:

| Type | Start time | Use |
|------|------------|-----|
| `STATIC` | Fixed (`scheduled_start_time`, UTC) | The first match of the day on each field, or anything that must start at a set time |
| `SAFE` | When everything it depends on has finished; locked once those have all started, so teams get a match's worth of notice | Most matches |
| `FAST` | As soon as everything it depends on has finished | Moving quickly when notice doesn't matter |
| `BREAK` | Like a dynamic match, but no teams. Same-named breaks on different fields start together | Lunch and other pauses |
| `STATBREAK` | Fixed, like `STATIC`. Same-named rows share one start | Pauses at a set clock time |
| `JOIN` | Zero length. Same-named joins wait for every field's predecessor | Lining fields up, for example before lunch |

What a match waits for (its dependencies):

- its `previous_match` on the same field;
- any `Match::winner` / `Match::loser` slot, which waits for that match;
- matches named in its skip condition;
- earlier matches on other fields that share a team (live estimates only).

Arctos keeps two timelines. The **planned** one (`scheduled_start_time`)
assumes every match runs its full length and doesn't move once
published. The **live** one (`start_time` in the API) moves as matches
finish early or late. The [user docs](docs.md#dynamic-matches) explain
`SAFE` versus `FAST`, [breaks](docs.md#breaks), and
[joins](docs.md#joins); the
[scheduling algorithm](https://arctos-docs.cftocp.gay/scheduling.html)
page has the full procedure.

## Tags and Arctos Schedule Script

A **tag** is a named stand-in for a team (`tag::Seed 1`), usable
anywhere a team goes: a match's teams or referees. Each tag gets its
team by hand (organizers do this from **Tags** in the schedule editor)
or from an expression. Until then, matches show the tag's name.

**Arctos Schedule Script** (ASS) is a small Lisp-like language for
expressions over teams and matches: `(winner {Final})`,
`(wins [tag::A1])`, `(== 0 (losses [tag::Seed 1]))`. Its
[documentation](arctos-schedule-script.md) covers the syntax, every
function, and a cheat sheet. Expressions appear in three places: tag
expressions, skip conditions, and script variables.

### Build the schedule around tags

Create a tag for every team you expect, then write every match in terms
of tags. Don't wait for registration to close. Use slots like `A1`,
`A2`, … for pools, or `Seed 1`, `Seed 2`, … for a seeded bracket. As
teams register, or once seeding is decided, the organizer assigns each
tag its team, and every match using the tag updates. Matches that
haven't started pick up changes immediately.

Multi-day events use the same idea for later rounds. Day 2 matches use
tags like `1A`, `2A`, …, and the organizer fills them from day 1's
standings (`GET /tournaments/{id}/results`). Assigning tags is
normally the organizer's call; do it through the API
(`PUT .../tags/{tag_id}/team`) only when they ask.

### Bracket progression: `Match::winner` and `Match::loser`

For a bracket, put `Semi 1::winner` (or `::loser`) directly in the next
match's team or referee slot. The slot fills in when `Semi 1` is
finalized, and the next match also waits for `Semi 1` to finish.

### Tag expressions

A tag can fill itself from an expression that evaluates to a team, such
as `(winner {Grand Final})`. A team assigned by hand overrides the
expression. Two caveats:

- **A tag expression never makes a match wait.** A match using
  `tag::Champion` doesn't depend on `Grand Final`. When one match must
  follow another, use `Match::winner` in the slot.
- **Expressions evaluate as soon as they can.** `(winner {X})` and
  `(loser {X})` wait for `X` to finish, but anything computed from
  current standings picks a team immediately, before a game is played
  (everyone is tied at 0 wins). For seeds based on pool standings, leave
  the tags plain and fill them after the pool.

### Skip conditions

A skip condition is a boolean expression on a `SAFE` or `FAST` match.
Once the matches it depends on are finished, the match is skipped if
the condition is `true`. Most schedules don't need one; the usual case
is a double-elimination grand final reset, which is played only if the
losers'-bracket team wins the first grand final. See
[Double elimination finals](#double-elimination-finals).

### Script variables

The **Scripting** section of the schedule editor (`[[variables]]` in a
schedule file, or `.../script-variables` in the API) defines
tournament-wide variables. A variable is a named expression of any
type: a number, a list of teams or matches, or a function. Tag
expressions, skip conditions, and other variables can all use it.
Variables keep repeated pieces in one place:

```toml
[[variables]]
name = "pool_a"
expression = "(list [tag::A1] [tag::A2] [tag::A3])"

[[variables]]
name = "pool_a_matches"
expression = "(list {A: 1 v 2} {A: 2 v 3} {A: 3 v 1})"

[[variables]]
name = "pool_a_standings"
expression = "(sort-by pool_a (lambda (t) (wins t pool_a_matches)) (lambda (t) (points-won t pool_a_matches)))"
```

`pool_a_standings` is useful for checking results with
`POST .../schedule/validate`. Don't use it to fill a seed tag, because
it picks a team before the pool is played.

### Checking expressions

- `POST /tournaments/{id}/schedule/validate` with
  `{"expression": "..."}` checks one expression against the saved
  schedule. It returns `valid`, the current `value`, and the
  `result_type`, or an `error` naming the problem (unknown team, match,
  or tag; wrong argument type; syntax).
- `PUT /tournaments/{id}/schedule?dry_run=true` with a whole schedule
  file checks everything an import checks, including every tag and
  variable expression against the matches, tags, and variables in the
  file itself, and saves nothing. Use it before importing a new file:
  its expressions can name matches that don't exist yet.

## Rules that prevent most mistakes

- **Importing a schedule replaces it.** `PUT .../schedule` deletes
  variables, tags, fields, and matches the file leaves out. To change an
  existing schedule, export it (`GET .../schedule/script`), edit the
  export, and import it again. Rows are matched to existing ones by
  `uuid` / `id` when present (keep them to rename a match), otherwise by
  name. A file that leaves out a match that has started is rejected.
- **An invalid file changes nothing.** A `400` lists every problem; fix
  them all and send the file again.
- **The import's `warnings` aren't the schedule's warnings.** The import
  only warns about names it turned into tags. Always also call
  `GET .../schedule/warnings`, which finds teams booked in two places at
  once, cycles, and unknown references. It only sees assigned teams, so
  in a schedule built on tags, also run the
  [report script](#verifying-a-schedule).
- **Times are UTC.** `scheduled_start_time = 2026-07-04T16:00:00` in a
  schedule file is 16:00 UTC; convert from the event's local time.
- **Every entry needs a `nominal_length`** in minutes, except joins.
- **Names are identifiers.** Match names are unique in a tournament
  (breaks and joins share a name across fields) and can't contain `/`,
  `,`, or `::`. Teams are referred to by team id (the username), not
  display name. Write tags as `tag::Name`; a bare name that isn't a
  registered team id becomes a tag, with a warning.
- **Started matches are locked** (`409`). Plan changes before the event.

## Building a schedule

1. **Read the tournament:** `GET /tournaments/{id}/settings` for dates
   and limits, `GET .../registrations` for who has registered so far,
   and `GET .../schedule/script` for any schedule that already exists.
2. **Write the schedule file** around tags (below).
3. **Check it:** `PUT .../schedule?dry_run=true` with `{"script": "..."}`.
4. **Import it:** `PUT .../schedule`.
5. **Verify** (below), then fix by editing the file and importing again.
6. **Extras** when asked: side competitions, penalty types, schedule
   items (meals, ceremonies), and the bracket layout, as described in
   the [API reference](developers.md#managing-tournaments).

### Example schedule file

Six teams in two pools of three, one pool per field. The team sitting
out each pool game referees it. After the pools, a join lines both
fields up for lunch, then the pool winners meet in a best-of-three
final. Every team slot is a tag, so the file works before anyone
registers:

```toml
[[tags]]
name = "A1"

[[tags]]
name = "A2"

[[tags]]
name = "A3"

[[tags]]
name = "B1"

[[tags]]
name = "B2"

[[tags]]
name = "B3"

[[tags]]
name = "Pool A winner"

[[tags]]
name = "Pool A runner-up"

[[tags]]
name = "Pool B winner"

[[fields]]
name = "North"

[[fields]]
name = "South"

[[matches]]
name = "A: 1 v 2"
field = "North"
schedule_type = "STATIC"
scheduled_start_time = 2026-07-04T16:00:00
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "tag::A1"
team2_initial = "tag::A2"
refs_initial = "tag::A3"

[[matches]]
name = "A: 2 v 3"
field = "North"
schedule_type = "SAFE"
previous_match = "A: 1 v 2"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "tag::A2"
team2_initial = "tag::A3"
refs_initial = "tag::A1"

[[matches]]
name = "A: 3 v 1"
field = "North"
schedule_type = "SAFE"
previous_match = "A: 2 v 3"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "tag::A3"
team2_initial = "tag::A1"
refs_initial = "tag::A2"

[[matches]]
name = "B: 1 v 2"
field = "South"
schedule_type = "STATIC"
scheduled_start_time = 2026-07-04T16:00:00
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "tag::B1"
team2_initial = "tag::B2"
refs_initial = "tag::B3"

[[matches]]
name = "B: 2 v 3"
field = "South"
schedule_type = "SAFE"
previous_match = "B: 1 v 2"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "tag::B2"
team2_initial = "tag::B3"
refs_initial = "tag::B1"

[[matches]]
name = "B: 3 v 1"
field = "South"
schedule_type = "SAFE"
previous_match = "B: 2 v 3"
set_type = "SETS"
nsets = 2
nominal_length = 30
team1_initial = "tag::B3"
team2_initial = "tag::B1"
refs_initial = "tag::B2"

[[matches]]
name = "Pools done"
field = "North"
schedule_type = "JOIN"
previous_match = "A: 3 v 1"

[[matches]]
name = "Pools done"
field = "South"
schedule_type = "JOIN"
previous_match = "B: 3 v 1"

[[matches]]
name = "Lunch"
field = "North"
schedule_type = "BREAK"
previous_match = "Pools done"
nominal_length = 45

[[matches]]
name = "Lunch"
field = "South"
schedule_type = "BREAK"
previous_match = "Pools done"
nominal_length = 45

[[matches]]
name = "Final"
field = "North"
schedule_type = "SAFE"
previous_match = "Lunch"
set_type = "SETS"
nsets = 3
nominal_length = 40
team1_initial = "tag::Pool A winner"
team2_initial = "tag::Pool B winner"
refs_initial = "tag::Pool A runner-up"
```

Importing it creates 2 fields, 9 tags, and 11 rows, with no warnings.
Pool games run 16:00 to 17:30 UTC on both fields, lunch until 18:15,
then the final. When the pools finish, the organizer assigns the three
pool-result tags from the standings.

Useful patterns:

- **Referees:** `refs_initial` is comma-separated (`"tag::A3, tag::B3"`)
  and takes the same references as teams.
- **Match formats:** `set_type = "SETS"` with `nsets`; `"STONES"` with
  `stones_per_set`; or `"TIMED"` for fixed-length sets, such as two
  12-minute halves (`nsets = 2`, `stones_per_set = 12`, in minutes).
  Make `nominal_length` cover the whole match, including the time
  between sets.
- **Several fields' breaks edited as one:** see
  [break groups](developers.md#break-groups).
- **Starting from an existing event:** export a past tournament's
  schedule and adapt it; exports show every option in use.

### Double elimination finals

The winners' bracket champion has to lose twice, so the grand final
reset is played only if they lose the first grand final. This is the
usual use of a skip condition. These rows follow the bracket's
`WB Final` and `LB Final`, and a tag expression names the champion
whichever way it goes:

```toml
[[tags]]
name = "Champion"
expression = "(if (is-skipped {Grand Final reset}) (winner {Grand Final}) (winner {Grand Final reset}))"

[[matches]]
name = "Grand Final"
field = "North"
schedule_type = "SAFE"
previous_match = "LB Final"
set_type = "SETS"
nsets = 3
nominal_length = 40
team1_initial = "WB Final::winner"
team2_initial = "LB Final::winner"

[[matches]]
name = "Grand Final reset"
field = "North"
schedule_type = "SAFE"
previous_match = "Grand Final"
set_type = "SETS"
nsets = 3
nominal_length = 40
team1_initial = "WB Final::winner"
team2_initial = "LB Final::winner"
skip_condition = "(won? [WB Final::winner] {Grand Final})"
```

## Verifying a schedule

Run these after every import, and fix anything they report before
telling the organizer the schedule is ready:

1. `GET .../schedule/warnings` returns `{"warnings": []}`.
2. `GET .../matches` (organizers see it unpublished): every row has the
   field, teams, length, and `start_time` you intended. A slot not filled
   yet has `"id": null` and shows its placeholder as the `name`
   (`tag::A1`, `Winner of Semi 1`).
3. Every tag expression, variable, and skip condition passes
   `POST .../schedule/validate` (or the whole file passes a dry run).
4. Export the schedule and compare it with the file you meant to import.
5. Check fairness: the number of games and refereeing assignments per
   team, rest between commitments, and when each team's day ends. The
   script below prints this. It follows unfilled slots by their
   placeholder, so it catches a tag booked in two places at once,
   which the warnings in step 1 can't see until teams are assigned.

```python
"""Summarize a tournament's schedule per team or tag: games, refereeing, and rest.

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


def timelines_by_slot(matches, referees_by_match):
    """Team name, or the placeholder of an unfilled slot -> sorted [(start, end, role, match name)]."""
    timelines = defaultdict(list)
    for match in matches:
        if match["schedule_type"] in ("BREAK", "JOIN", "STATBREAK") or match["start_time"] is None:
            continue
        start = datetime.fromisoformat(match["start_time"].replace("Z", "+00:00"))
        end = start + timedelta(minutes=match["length_minutes"] or 0)
        slots = [(match["team1"], "plays"), (match["team2"], "plays")]
        slots += [(referee, "refs") for referee in referees_by_match[match["id"]]]
        for team, role in slots:
            if team["name"]:
                timelines[team["name"]].append((start, end, role, match["name"]))
    return {name: sorted(entries) for name, entries in timelines.items()}


def report(timelines, minimum_rest=timedelta(minutes=0)):
    for name, entries in sorted(timelines.items()):
        games = sum(role == "plays" for _, _, role, _ in entries)
        refs = sum(role == "refs" for _, _, role, _ in entries)
        print(f"{name}: {games} games, {refs} refs, {entries[0][0]:%H:%M}-{entries[-1][1]:%H:%M} UTC")
        for (_, end, _, match_name), (start, _, _, next_name) in zip(entries, entries[1:]):
            if start < end:
                print(f"  CONFLICT: {match_name} overlaps {next_name}")
            elif start - end < minimum_rest:
                print(f"  short rest: {start - end} between {match_name} and {next_name}")


if __name__ == "__main__":
    tournament_id = sys.argv[1]
    matches = get(f"/tournaments/{tournament_id}/matches")["matches"]
    referees_by_match = {
        match["id"]: get(f"/tournaments/{tournament_id}/matches/{match['id']}")["match"]["referees"] for match in matches
    }
    report(timelines_by_slot(matches, referees_by_match), minimum_rest=timedelta(minutes=30))
```

For the example above it reports two games and one refereeing slot for
each of `tag::A1` to `tag::B3`, all between 16:00 and 17:30. It flags
their commitments as back to back, because the team sitting out
referees. The final's three tags appear once each.

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

Check files with `?dry_run=true` before importing them. To try a whole
format without touching a real event, ask the organizer for a scratch
tournament, or run Arctos locally (see the
[README](https://github.com/reid23/arctos#running-the-app-locally))
and point the agent at it with its own app and token. On a local copy
you can play matches on the website to see brackets, skip conditions,
and dynamic start times move as results come in.
