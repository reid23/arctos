# Scheduling Algorithm

## Two timelines

Every match has **two** start-time fields. They look similar and are easy to
confuse; they are not interchangeable.

| Field | Meaning | Who writes it | Moves when games run late? |
|-------|---------|---------------|----------------------------|
| **`scheduled_start_time`** | **Plan** — “if everything went as published” | Planned scheduling pass only | **No** |
| **`nominal_start_time`** | **Live estimate** — when we currently expect the match to start | Live PROCEDURE pass | **Yes** |
| `confirmed_start_time` | Wall-clock start once the match is started | Match start | N/A (history) |
| `completed_time` | Wall-clock end | Match finalize | N/A (history) |

### Why both exist

Dynamic types (`SAFE` / `FAST` / `BREAK` / `JOIN`) recompute `nominal_start_time`
from real dependency finish times. If the UI placed blocks on nominal alone,
a delayed morning game would **slide the whole day** on the board. Players then
treat the slipped time as the new plan, arrive a little late again, and lateness
compounds.

`scheduled_start_time` is the stable **contract of the day**:

- STATIC anchors keep the user-chosen plan time.
- Dynamic matches get plan times by walking dependencies with
  `scheduled_start + nominal_length` only (no confirmed times, no status).
- Match start / end runs the **live pass only**, so the plan does not move.

UI rule (schedule display): a match is shown at its planned time **or
earlier** — blocks are placed at `scheduled_start_time`, but pull earlier
(and show real end times) when the day runs ahead of plan. They are never
displayed later than plan; lateness shows via the now line instead. Edit
mode has an option to display times exactly as they happened.

### When each pass runs

| Event | Pass |
|-------|------|
| Match start / end / finalize | Live only → `recompute_all_match_times` (= `run_scheduling(scheduled_pass=False)`) |
| Match create / edit / delete | Both → `recompute_scheduled_and_nominal_times` |
| TO “Recompute times” / TOML import (API) / app boot | Both |
| Force-start convert to STATIC | Both (after writing the new STATIC anchor into **both** start fields) |

`recompute_scheduled_and_nominal_times` always runs **planned first, then live**,
so the live pass’s cross-field resource-conflict edges can anchor on fresh
planned times.

---

## `MatchGraph` class

represents the data in the Match model, but entirely in memory, and
with relations as actual references like a graph. References are:
- if team1 and team2 are not resolved yet and are the winner or loser
  of another match, that other match is a dependency of this one
- if refs are not resolved yet and any are the winner or loser of
  another match, that other match is a dependency of this one
- the previous match on the same field (as indicated by the
  previous_match column) is a dependency of this match
- any matches referenced by the skip condition. Note that the
  Match.get_skip_condition_dependencies returns a dict of direct and
  skip-condition dependencies; both should be used for the topological
  sort, but later, when getting the end time of dependencies, non
  direct dependencies should return an end time that is actually the
  start time of the match referenced in the `(skip-condition MATCH)`
  command.
- cross-field **resource-conflict** edges (live pass only): a
  SAFE/FAST match depends on the latest earlier (by
  `scheduled_start_time`) match on another field that shares a
  participating team. The planned pass **omits** these edges so the
  plan cannot depend on itself through a shifting conflict edge.

JOIN matches with the same name are stored as a single node, not
multiple. They have the union of each one's dependencies.

There are two methods for getting dependencies:
1. `get_schedule_dependencies`
2. `get_direct_dependencies`

the latter is any dependency that is one link away.The former contains
only static/dynamic matches that have not been skipped. it will do a
graph search that terminates when it finds a static/dynamic match (ie,
it will search past any `BREAK`/`JOIN` matches).

The `Dependency` abstract class wraps a pointer to a
`MatchNode`. it hashes to the same thing as the node it points to, so it
can be used in equality checks. It adds:

- `get_time()` — **live** effective time (confirmed/nominal start or end)
- `get_scheduled_time()` — **plan** effective time (`scheduled_start` or
  `scheduled_start + nominal_length`)

there are two subclasses of Dependency:
- `startOfMatchDep`
- `endOfMatchDep`

---

## Live PROCEDURE (writes `nominal_start_time` + status)

Used by `run_scheduling(..., scheduled_pass=False)`.
**Never writes `scheduled_start_time`.**

```
PROCEDURE: WITH MATCH m {
	IF (m is COMPLETED or IN_PROGRESS or SKIPPED) {
		return
	}
	let nominal_start_if_skipped = Null()
	SWITCH schedule type of m {
		CASE STATIC {
			IF m is NOT_STARTED {
			  SET m TIME_FINALIZED
			}
		}
		CASE BREAK/JOIN {
			SET m.nominal_start_time = latest(END_TIMES m.get_direct_dependencies())
		}
		CASE SAFE {
			IF m is NOT_STARTED {
				SET m.nominal_start_time = \
					m.get_direct_dependencies()
					 .map(|x| 
					 	 IF (x is SKIPPED) {
					 	   (END_TIME x) + x.nominal_length
						 } ELSE {
							 END_TIME x
						 })
					 .latest();

				nominal_start_if_skipped = Some(
					m.get_direct_dependencies()
				   .map(|x| END_TIME x)
				   .latest()
				)
			}
		}
		CASE FAST {
			IF m is NOT_STARTED {
				SET m.nominal_start_time = \
				  m.get_direct_dependencies()
				   .map(|x| END_TIME x)
				   .latest()
			}
		}
	}

	IF ALL m.get_schedule_dependencies() ARE COMPLETED/SKIPPED {
		IF skip_cond {
			SET m SKIPPED
			m.nominal_start_time = nominal_start_if_skipped.or_default(m.nominal_start_time);
		} else {
			IF m is STATIC/SAFE/FAST { // ie, if this match is one people play in
				SET m READY_TO_START
			} else {
				SET m COMPLETED
			}
		}
	} ELSE IF (m is SAFE) AND (ALL m.get_schedule_dependencies() ARE IN_PROGRESS/COMPLETED/SKIPPED) {
		SET m TIME_FINALIZED
	}
}
```

`END_TIME(x)` uses confirmed end when present, else confirmed start + length,
else nominal start + length (and for SKIPPED, nominal start alone in the live
graph helpers — see `MatchGraph._node_end_time`).

---

## Planned PROCEDURE (writes `scheduled_start_time` only)

Used by `run_scheduling(..., scheduled_pass=True)`.
**Never reads status or confirmed times. Never writes `nominal_start_time`.**

```
SCHEDULED_PROCEDURE: WITH MATCH m {
	IF m is STATIC {
		// Keep user-set scheduled_start_time anchor; do nothing.
		return
	}
	// SAFE / FAST / BREAK / JOIN
	SET m.scheduled_start_time = latest(
		SCHEDULED_END_TIMES m.get_direct_dependencies()
	)
	// where SCHEDULED_END(x) = x.scheduled_start_time + x.nominal_length
	// and start-of-match deps use x.scheduled_start_time alone
}
```

Notes:

- Skipped matches still contribute full `nominal_length` on the plan (the plan
  assumes the slot existed).
- If a node is in a dependency cycle, fall back to the DLL previous match’s
  scheduled end (same idea as the live cycle fallback).
- After structural edits, always run planned **then** live.

---

## On Match Start/End

0. acquire lock on matches
1. set the match to COMPLETED or IN_PROGRESS as needed, and notate the relevant timestamps.
2. load match graph from db
3. topological sort.
4. in order from root to leaf nodes, perform **live** PROCEDURE.
5. write **nominal_start_time + status** to db from graph
6. release lock

Do **not** run the planned pass here — that would let real delays rewrite the plan.

---

## On Match Create/Edit

Same as on match start/end, but:

1. For STATIC, user-supplied start time is written to **both**
   `scheduled_start_time` and `nominal_start_time` (the plan anchor).
2. For dynamic types, seed `scheduled_start_time` from the first computed
   nominal if it is still null, then run **planned pass then live pass**
   (`recompute_scheduled_and_nominal_times`).
3. Finalize status on matches that should be finalized from the beginning
   (live PROCEDURE).
