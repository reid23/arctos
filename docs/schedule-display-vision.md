# Schedule Display Vision

Parent issue: [#164 Schedule Display Improvements](https://github.com/reid23/arctos/issues/164)
Branch: `feat/schedule-display`
Sub-issues: #195, #196, #197, #198, #222

## One-sentence purpose

Help **players and reffing teams** (especially on mobile) answer “when and where am I next?” against the **published plan**, while making **lateness visible enough that it does not silently accumulate**.

## Personas and priority

| Priority | Who | Need |
|----------|-----|------|
| P0 | Players | Next play time, field, opponent |
| P0 | Reffing teams | Next ref assignment, field, which match |
| P1 | Anyone browsing | Readable full schedule; optional “true times” |
| P2 | TOs / desk | Same views; edit mode unchanged in spirit |

**Context:** match refs are **teams** (players ref other teams’ matches). Head refs are distinct individuals and are out of scope for “my schedule” identity.

## Core mental model: Plan vs Reality

The schedule UI intentionally splits two timelines (issue #196, option C):

| Concept | Field(s) | Meaning | Default UI |
|---------|----------|---------|------------|
| **Plan** | `scheduled_start_time` + `nominal_length` | “If everything went as planned” — the contract of the day | **Default placement of blocks** |
| **Live estimate** | `nominal_start_time` (+ length) | Solver’s current expected start after delays/deps | Used when “Show true times” is on for not-yet-finished matches |
| **Reality** | `confirmed_start_time` / `completed_time` | What actually happened | True-times mode; small lateness cue on completed matches in plan mode |

### Why this exists (failure mode we are fixing)

Today, when the tournament runs behind, dynamic scheduling **pushes** `nominal_start_time`. The timeline follows that push, so the board looks like “the new official time.” People arrive a little late to *that*, lateness compounds, and nobody feels the urgency of “we are behind the plan.”

**Success:** looking at the default schedule, a player sees blocks at **planned** times, a **now line**, and enough signal that the day is late — so they hustle rather than treat the slipped estimate as the new plan.

### Display rules

**Default mode (“Plan”):**

1. Place every match block at `scheduled_start_time` (fallback: `nominal_start_time` if scheduled is missing).
2. Block duration = `nominal_length` (planned length), **not** actual duration.
3. Draw a horizontal **now line** (Google Calendar–style) across the timeline when viewing today.
4. For **completed** matches that started late relative to plan, show a small indicator (e.g. “+12m late”) without moving the block.
5. Status badges (ready / in progress / done) still communicate lifecycle; they do not move the block in plan mode.

**True-times mode (toggle):**

1. Place unfinished matches at live estimate: `confirmed_start_time` if started, else `nominal_start_time`.
2. Place completed matches at actual start/end (`confirmed_start_time` → `completed_time`).
3. Now line still shown.
4. Toggle label should be plain language, e.g. **“Show actual times”** (off by default).

**Match detail page:**

- Always label **Planned start** (`scheduled_start_time`).
- For STATIC / FAST: planned start is the main time story (FAST may still show live estimate if useful; do not invent extra jargon).
- For SAFE: also show **Start deadline** semantics clearly (current product language around when the match becomes time-finalized / must start — use existing status + planned/nominal, with short help text).
- Show **status** with a brief help affordance explaining each status.

### Solver / data model (do not break this)

`scheduled_start_time` was added on `main` in migration `0009_match_scheduled_start_time`
(commit that introduced the dual-pass solver). **This epic does not add a new column**;
it uses that field as the plan timeline for display and hardens write paths that
were only updating `nominal_start_time`.

Authoritative algorithm docs: [`docs/scheduling.md`](scheduling.md).

| Pass | Writes | Trigger |
|------|--------|---------|
| Planned (`scheduled_pass=True`) | `scheduled_start_time` only | create/edit/delete, import, recompute button, push-back, boot |
| Live (`scheduled_pass=False`) | `nominal_start_time` + status | match start/end **and** after every planned pass |

**Invariants:**

1. Match start/end must **never** rewrite `scheduled_start_time` (live pass only).
2. STATIC user start / push-back / force-start-to-STATIC must write the plan anchor
   (`scheduled_start_time`), not only nominal.
3. TOML export must carry `scheduled_start_time` for STATIC anchors; import seeds
   plan from nominal when only nominal is present (legacy files).
4. Resource-conflict edges exist only on the live pass; planned pass is structural.

Display code places default blocks on `scheduled_start_time` (fallback nominal if null).

## Views

Keep three view modes (names can be lightly renamed for clarity):

| Mode | Role |
|------|------|
| **Schedule** (timeline) | Full multi-field day grid — default for browsing the event |
| **Table** | Dense list / TO-oriented table |
| **My timeline** (#195) | Personal day strip: only matches involving the selected team |

### My timeline (#195)

**Goal:** more space, less noise, answer “where do I need to be?”

- Show only matches where the focus team is **playing** or **reffing**.
- Color-code **playing** vs **reffing**.
- Always show field name, both sides, and refs (see #197).
- Not required in edit mode (hide or disable when editing).
- Uses the same Plan / Actual times toggle and now line as the main schedule.

**Identity / team selection:**

1. If the logged-in user is a **team registered** for this tournament, or a **player registered under a team**, default focus team = that team (if multiple, pick a sensible default and allow switching).
2. Otherwise (anonymous or no registration), require choosing a team; persist choice in `localStorage` for the tournament.
3. Logged-in users with a default still get the team picker to view another team’s day.

## Content on every match block

### Playing teams

Show both sides using shortnames / truncated labels (shortnames already exist). Prefer fitting text over forcing a click-through.

### Reffing teams (#197)

**Always** show ref teams on match blocks and table rows:

- Resolved team IDs → display name / shortname.
- Unresolved tags / ASS references → still render the token (tag/reference icon + label), never hide the row because refs are “unknown.”

Empty refs: show nothing or a muted “—” only if the product already treats empty refs as valid; do not invent fake refs.

### Breaks and joins (#198)

Breaks and joins are schedule structure, not games:

- **No status badge / status coloring** that makes them look “completed.”
- Stable, neutral appearance at all times.
- Joins remain the cross-field line treatment; breaks remain non-clickable (non-edit) blocks without lifecycle chrome.

## Zoom (#222)

Vertical scale on the timeline (and my timeline) must be user-adjustable:

- **Mobile:** pinch-to-zoom vertical scale.
- **Desktop:** ctrl/cmd + scroll wheel.
- Persist scale per device (`localStorage`).
- Reasonable min/max so blocks never become unusable.

Purpose: there is no single slot height that fits both dense multi-field days and “I need to read every name.”

## Non-goals (this epic)

- Schedule **editing** robustness, solver feature work (STATBREAK, CHECKPOINT, dependency arrows) — see #148 / #199 / #246 / #247.
- TOML import/export semantics (#243), except if a bug blocks writing `scheduled_start_time` correctly for display.
- Head-ref individual “my schedule.”
- Offline-first / PWA.

## Success criteria

A player on a phone at a multi-field tournament can:

1. Open **My timeline**, see only their play + ref blocks with field and opponents/refs readable.
2. See the **now line** and blocks at **planned** times by default.
3. Notice the day is late (now line past unfinished planned blocks / late markers) and still know the plan.
4. Optionally toggle **actual times** to see the live estimate.
5. Pinch to enlarge blocks when names overflow.
6. Never confuse a break’s color for “this game is done.”
7. Always see who is supposed to ref, even before tags resolve.

## Implementation order (suggested)

1. Time model in UI: plan placement, actual-times toggle, now line, match page labels, lateness chip (#196).
2. Always show refs (#197); neutralize break/join status chrome (#198).
3. My timeline + team picker identity (#195).
4. Pinch / ctrl-scroll vertical zoom (#222).
5. Regression tests for solver + any create/edit paths that touch `scheduled_start_time`.

## Glossary

| Term | Definition |
|------|------------|
| Plan / scheduled | `scheduled_start_time` — stable “as published / as dependencies planned” |
| Nominal | `nominal_start_time` — live solver estimate (moves when deps slip) |
| Confirmed | `confirmed_start_time` — wall-clock start when match actually begins |
| SAFE / FAST / STATIC | Existing schedule types; semantics unchanged by this epic’s display work |
