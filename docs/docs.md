# User Documentation

## Start here

- **Find an event or league:** browse [Events](/) or [Leagues](/leagues), then open its schedule, teams, or results.
- **Join an event:** open the event and choose **Register**. A league event uses one registration for the league. If you joined a team, that team must approve your place on its roster.
- **Manage a team:** open its profile and choose **Manage team**. Team accounts and designated player managers can manage its profile, registrations, and rosters. See [Team Managers](#team-managers).
- **Run a match:** open its schedule entry and follow [Running Games](#running-games). Your account needs permission to head referee that match.
- **Set up an event:** use **Organize** on [Events](/), then follow [Tournament Settings](#tournament-settings) and [Match Schedule Setup](#match-schedule-setup).
- **Run a side event:** see [Running a Side Comp](#running-a-side-comp).
- **Play the stone beat:** open [Stones](/stones); see [Stones](#stones) for synchronization and calibration.

Leagues stay **Active** while any event is current or upcoming, including events
the organizer has not published yet. A league moves to **Past** after its final
event's end date has passed in your local calendar. Event dates and counts show
only the events you have access to; league organizers can also see drafts.

## Table of Contents

About Arctos

 - [FAQ](#faq)
 - [Ownership (Arctos not a CAJA project)](#ownership-is-arctos-a-caja-project)
 - [What Arctos *is* and what it is *not*](#what-arctos-is-and-what-it-is-not)
 - [Design Philosophy](#design-philosophy)
 - [Bugs, Feature Requests, and Contributing](#bugs-feature-requests-and-contributing)

Overview

 - [Functionality Overview](#functionality-overview)
    - [Before the Tournament](#before-the-tournament)
    - [Day-Of Operations](#on-the-day-of)
 - [Stones](#stones)
 - [Account Types](#account-types)
 - [Team Managers](#team-managers)
 - [Penalties](#penalties)

For Players

 - [Phone Number](#phone-number)
 - [Logging Injuries](#logging-injuries)

For Head Refs

 - [Running Games](#running-games)
 - [Force Start Games](#force-start-games)


For tournament organizers

 - [Tournament Settings](#tournament-settings)
    - [Basic Information](#basic-information)
    - [Head Ref Options](#head-ref-options)
    - [Visibility and Access Control](#visibility-and-access-control)
 - [Match Schedule Setup](#match-schedule-setup)
    - [Tags and References: Specifying Teams](#tags-and-references-specifying-teams)
    - [Static Scheduling](#static-scheduling)
    - [Dynamic Matches](#dynamic-matches)
    - [Breaks](#breaks)
    - [Joins](#joins)
    - [Ribbon Games](#ribbon-games)
    - [Results and Standings](#results-and-standings)
    - [Skip Conditions](#skip-conditions)
 - [Side Comps](#side-comps)
 - [OBS Scoreboard Integration](#obs-scoreboard-integration)
 - [Match Footage](#match-footage)

For Developers

 - [Sign in with Arctos and the Arctos API](developers.md)


---

## FAQ

**I want to test this out for my tournament, but I don't want to
create a bunch of fake teams on the official site. What can i do?**
  
  - Use the local demo described in the [setup guide](https://github.com/reid23/arctos#running-the-app-locally). It creates sample accounts and events in a separate database. Ask your site administrator about a test instance if you cannot run Arctos locally.

**Can I register two teams for a tournament under one team account?**
  
  - No; make a second team account. Team accounts are meant to
    represent the ephemeral groupings that we Juggers call 'teams',
    not clubs.

**How do I change my username (url)?**

   - You can change your *display name*, but your username is
     permanent. This is to ensure links to your profile anywhere will
     always work (unless you delete your account).

**How is my data used? What can and can't other people see?**

   - The Arctos [privacy policy](privacy-policy.md) describes how we use
     all data in more legally relevant terms. The [data accessibility
     guide](data-accessibility-guide.md) is a more approachable and
     practical guide to when and how data you enter into Arctos gets
     publicized.

**I think something is wrong with Arctos. How do I report this?**

   - You can create an issue on
     [github](https://github.com/reid23/arctos/issues) using the *Bug
     Report* template. This is the fastest way to get eyes on the
     problem.


## Ownership (is Arctos a CAJA project?)

The short answer: No.

The long answer: Arctos is *not* under the jurisdiction of CAJA, the
NJA, or any other Jugger organization[^1]. As an open-source project,
it is controlled entirely by its maintainers. This does mean that you
have to trust me to continue to support this project and be a good
maintainer, and find and train other good maintainers to fill my place
if/when I leave. I appreciate your trust and acknowledge that this is
entirely a leap of faith.

I have decided to do this because I don't want to give these
organizations control over who can use Arctos - it should be available
to all juggers, regardless of whether they comply with rules that
these organizations set out. There is a time and a place for
membership requirements and certifications (and more broadly Jugger
politics), but Arctos is meant to be a tool to make Jugger easier, not
a tool for enforcing jugger politics.

I think there's precedent for this in other sports (see athletic.net,
swimcloud.com, thebluealliance.com, askfred.net,
rttimingsolutions.com, etc); the organization + logistics people are
often different from the regulatory & political bodies.

[^1]: Arctos is hosted on a subdomain of the CAJA site because it was
    free, jugger related, boosts the CAJA site SEO metrics, and lets
    me be a proud Californian.


## What Arctos *is* and what it is *not*

Arctos is a tool for Jugger events that aims to:

  - unify the tracking of results and statistics
  - reduce the workload of organizers while planning events, by:
    - managing registration
    - enforcing team size and team count limits
    - making tournament information easily accessible (giving
      tournaments a basic web presence)
  - reduce the workload of organizers during events, by:
    - communicating the schedule to teams so they know when and where
      to show up for matches
    - collecting and organizing footage
    - collecting and organizing results
    - managing brackets and updating the schedule accordingly
  - reduce the workload of head refs while running games, by:
    - keeping score
    - counting stones
    - tracking penalties/notes

Arctos is ***not***:

  - a bracketing engine
  - a scheduling engine
  - a social media platform

While there is limited functionality for these things, they are not
the main design intent. The presence of profile photos and bios does
not mean this is meant to be a web presence for you or your
team. Likewise, dynamic scheduling, tags, references, breaks, and
joins are powerful tools for expressing a complex, efficient bracket &
schedule, but you should be developing these externally - Arctos is
meant to run tournaments, not do in-depth bracket analysis or solve
massive mixed integer nonlinear programs to schedule your matches.

## Design Philosophy

With the above goals in mind, Arctos has been designed to be:

- **Capable**: it makes it easier to run tournaments by as much as
  possible. I measure this by reduction in admin staffing
  requirements.
- **Accessible**: it gives as much information and tooling as it can
  to as many people as possible (as allowable by privacy
  constraints). This includes small things like ensuring the signup
  process and data api are simple and don't require any restrictive
  infrastructure, but it also includes larger things, like maintaining
  separation from existing regulatory or organizational bodies (e.g.,
  Arctos still aims to help TOs of non-NJA sanctioned tournaments).
- **Unonpinionated**: Jugger tournaments come in many shapes and
  sizes, and in order to best help organize these, it should not
  expect tournaments to fall into preexisting patterns. This is why
  sets are not automatically counted/completed and match winners need
  to be explicitly set by head refs: this way, if TOs want to run a
  strange scoring system, they can use Arctos without any special
  development/modifications needed.
- **Minimal**: Don't do more than advertised. Don't collect more data
  than needed. I don't need to send you email updates on cool
  tournaments you should try to register for; I don't even need your
  email. I don't need to know how old, tall, heavy, or gender you are.

## Bugs, Feature Requests, and Contributing

Arctos is an open-source project, hosted on
[github](https://github.com/reid23/arctos) and licensed with
GPLv3. Anyone can view the source code and propose their own changes
by forking the repository, committing changes, and opening a pull
request. If you are interested in contributing, please see see the
[contributing guide](https://github.com/reid23/arctos/blob/main/CONTRIBUTING.md)
for more information.

If you have feature requests or bug reports, please submit [an Issue
on github](https://github.com/reid23/arctos/issues) using the
appropriate template (it'll prompt you to select a template when you
create an issue). As I have limited bandwidth and no funding for this
project, I can't guarantee that i'll be able to act on these in a
timely manner (especially feature requests), but creating an issue is
the fastest way to get a maintainer to see your bug/request.

---

## Functionality Overview

Here's, briefly, how it works.

#### BEFORE THE TOURNAMENT

  1. TOs create a tournament, with:
     - basic configuration: max team size, max team counts, number of
       fields and their names, location, dates, about, etc.
     - head ref policy: let anyone head ref, or just teams assigned to
       ref, or just a list of specific users (or any combination of
       those).
     - registration configuration: terms that registrants need to
       agree to, the registration price for teams and players,
       etc. (eventually registrants will be able to pay these fees
       through Arctos, but that functionality has not been implemented
       yet).
     - a schedule: This can be as simple as matches at specific times,
       or it can have dynamically schedule matches, breaks,
       synchronization points, and more. Match participats can either
       be specific teams or the winner/loser of another match. If
       desired, stone counting functionality is set up. Each match has
       two teams playing and any number of ref teams.
     - a bracket: optionally, TOs may upload (a) bracket diagram(s),
       which they may then annotate with team names and/or the
       winner/loser of specific matches, which will then update as the
       tournament progresses.
     - youtube livestream links for each field, if set up
	 - penalty types: a list of penalty types defined by the
       tournament. Head refs will be able to choose any of these or
       write in their own custom penalty.
  2. TOs make the tournament public and open registration (and
     eventually make the schedule public too)
  3. Teams register, setting a pseudonym for this tournament
  4. Players register
     - they select either a team to register under or they register
       unattached (as a free merc)
     - they enter their jersey name and number for this tournament
  5. Teams accept player's requests to join their team, finalizing the
     player's registration. If a team declines a request, the player
     stays registered as an unattached free merc.
  6. TOs continuously perform registration management
     - As teams and/or players submit their registration payments, TOs
       mark them paid (optionally notating the amount paid) on the
       registration management page.
     - TOs may deregister players and/or teams as they see fit


#### ON THE DAY OF

  1. TOs optionally set up phones to record the matches (different
     from live stream integration). These phones will automatically
     record and upload footage, which will automatically be clipped to
     just the points and displayed in the same place as the live
     stream footage.
  2. TOs set up stones, playing them from the stones player. Multiple
     devices can be used to play synchronized stones from multiple
     speakers.
  3. Players check the schedule to see when they need to play.
     - guarantees provided by the dynamic scheduling system ensure
       they always know the deadline to arrive at their match at least
       one match in advance
  4. Head Refs click the "start match" button, and are taken to a page
     where they:
     - select who of each team are playing (typically everyone, unless
       the team is larger than the max field size)
       - they can only select players who have been marked paid!
     - search for and add any free mercs (same thing; must have been
       marked paid by TOs)
     - can view other refs' notes on the players and teams selected
       for this match
     - can view players' active logged injuries
     - can notate any important match-level notes (public) like qwik
       contact and any rules variations
  5. Head Refs submit this information at the end of the pre-game
     meeting, officially starting the match (and updating the schedule
     for other matches based on the time). They then run the match:
     - click 'start point' on the 'J' in "3, 2, 1, Jugger"
     - click 'end point' when point is called
     - select the winner of each point, or leave it as None if nobody
       scored
     - select a box to note if the point is being rerun
     - add notes to each point or player-specific penalties if needed.
  6. spectators watch, either in person, or on the match page, which
     updates live with score results and the stone count.
  7. Head refs click "finalize match" when all points are over. They
     select the winner, write any final match-level notes (public),
     and obtain signatures from each team's captain.
  8. Head Refs submit this form, officially marking the end of the
     match. The schedule updates based on the results and timestamp.


## Stones

Open [Stones](/stones) to play the beat. **Start** begins playback and the local
counter; **Pause** keeps the count, and **Reset** stops playback and returns it
to zero. **Mute sound** silences playback while the counter continues. Choose
a sound and use **Preview** to hear one clip without starting the counter.
A custom MP3 must be shorter than one stone (1.5 seconds).

Arctos schedules beats on a shared 1.5-second clock grid. Devices estimate
their offset from the server, so counters and playback use the same timing
reference. Actual audible timing also depends on the device, speaker, Bluetooth
delay, and distance from the listener; synchronized clocks cannot remove these
delays by themselves.

Use **Calibrate manually** to adjust audio and clock timing. **Cancel** restores
the timing settings from before the walkthrough; completing it keeps the new
settings. Compare speakers next to each other when checking synchronization.
Advanced synchronization controls show connection quality and allow a resync.

## Account Types

There are two account types: team and player. Either of these can be
TOs; just create a tournament.

Team accounts are meant to represent a brand more so than a club. Team
members change so frequently that creating a system where players
belong to a team would be to utterly misrepresent the current culture
of (American) Jugger.

Player accounts, on the other hand, are much less ephemeral - your URL
is permanent, and your profile is meant to be the thing that ties you
to you between tournaments. This is why you can change your jersey
name and jersey number for each tournament.

## Team Managers

A team account can give one or more player accounts permission to manage the
team. Open the team's profile, choose **Manage team**, and use **Managers** to
find a player by name or username. Check the username before adding them.
Only the team account can add or remove managers.

Managers sign in with their own player accounts. **Manage teams** in the account
menu lists the teams they can manage. The team name stays visible on management
pages; personal player registration remains separate from team registration.

Managers can edit the team profile and images, register or withdraw the team,
edit its event registration, and approve or decline roster requests. They can
also add registered, unattached players or remove players from the roster.
Removing a player from the team leaves their event registration, payment, and
waiver intact. Team membership is still recorded separately for each event or
league; being a manager does not put a player on the roster.

Managers also have organizer access to events and leagues organized by the
team account. They can see the team information available to that account,
including its private notes. They cannot change the team account's password,
Google connection, or OAuth credentials, and cannot appoint other managers.
Removing a manager ends access granted by that team; any separate organizer
role held by the player still applies.

## Penalties

The penalty system in Jugger (at least in the US) is still changing
rapidly, so there is no Arctos-prescribed way to penalize
players. Instead, TOs set the penalty types they'd like, and head refs
can either choose from those or write their own custom penalty.

Penalties are visible to:
- Players, on their profile
- Head refs of the relevant match (anyone who *could have* head reffed
  that match! If the "allow anyone to head ref" box is ticked, then
  anyone registered for the tournament can view penalties!), on the
  player's profile, start match page, and run match page

---

## Phone Number

A player can store a phone number in Edit profile. It is not shown on the public
profile. Arctos does not currently send match or schedule notifications to it.

## Logging Injuries

Add injuries from your profile's Injuries section. New injuries are private by
default. Public injuries appear on your profile; private injuries remain visible
to you and to authorized referees during match setup at events where you are
registered. Only active injuries appear in that match-setup information.

Mark an injury healed when it no longer applies. See the
[data accessibility guide](data-accessibility-guide.md#injuries) for visibility
rules.

## Running Games

Open the match from the schedule. If **Start match** is unavailable, open its
explanation to check dependencies, field/team conflicts, and referee permission.
A team account cannot act as the head referee; use your player account.

1. Choose the players taking part, within the event's roster limits. Review
   active injuries and relevant notes, and add any match-level instructions.
2. Start the match after the pre-game meeting. This records its start time and
   updates dependent schedule entries.
3. Use **Start point** on the start of play and end the point when it is called.
   Record the winning team, or leave the point without a winner if nobody scored.
4. Use **Notes** for match/point notes and penalties. **Correct count** opens an
   explicit stone-count draft; review it and save or cancel the correction.
5. Open **Review result** when play is complete. Select the match winner and
   obtain the required captain signatures before confirming finalization.

Wait for each write to finish. If a correction has an uncertain outcome, use its
status-check action before retrying. This avoids applying a second change while
the first request may already have reached the server.

[^2]: Static matches do not use skip conditions: their scheduled start does not depend on a predecessor.

## Force Start Games

If Arctos thinks a match cannot be started, it will not let you start
that match. However, there are some cases when you want to override
this. For example, say Match C two ref teams are listed: winner of
Match A and winner of Match B. If Match B has finished but Match A has
not, Arctos will say Match C must wait until Match A is complete
before starting. But if everyone is ready to start playing Match C and
the winner of Match B has supplied enough refs, it's totally fine to
start early.

To provide an escape hatch of sorts for these kinds of situations, a
"force start" option is provided to refs. When a match is force started:

1. it asks you to assign playing and reffing teams
2. if there is another match currently happening on the same field, it
   lets you mark that match as either `SKIPPED` or `COMPLETED`, and if
   the latter, it lets you set the winning team.
3. the match is converted to a STATIC type match, if that is not
   already the case, and is set to start at the current time.
4. the match gets started!

Note that once the ref completes this procedure, they can no longer
change the teams involved. Only TOs may make further edits.

---

## Tournament Settings

### Basic Information

Open **Organizer tools → Settings** to edit event information and registration rules:

  - The start date is not optional. You must enter something.
  - To hide the registration fee callouts on the event page and
    registration forms, just set the value to zero.
  - When entering other TOs, you must enter their exact username for
    it to work.
  - Roster and on-field player limits are enforced during registration and match setup.

### Head Ref Options

As it says on the form:

> Arctos was designed around having dedicated head refs. However, this is not always feasible, so there are a few other options. If you do any of these, please make sure to communicate to players how the system works, in particular that you cannot un-start a match!  
> Explicitly listed player usernames will always be allowed, regardless of their registration status. Anyone else must be registered if they want to head ref.  
> **Please note that only players are allowed to head ref, not
> teams. This is to enforce accountability for ref responsibilities,
> as team accounts are/can be shared.**

TOs can enter an explicit list of allowed usernames, allow players on
ref teams to head ref, or just allow all registered players to head
ref. The union of all selected options is used, and the players
explicitly listed need not be registered to head ref.

### Visibility and Access Control

TOs can set the publication status of the tournament as a whole and
the schedule (along with the bracket) independently. Registration is a
third separate option. Registration should be closed before the
tournament begins so that you can plan better, but that's up to you.

You can add other TOs by entering their exact username. Only TOs can
mark people as paid and deregister others. Be aware that all TOs can
add and remove other TOs!

### Penalty Types

Add penalties here. The description is optional. You can change the
color to be whatever you want; it's mainly meant to make it easier for
head refs to tell different penalties apart when viewing a list.

Note that once a penalty has been made with a specific penalty type,
that type cannot be deleted!

## Match Schedule Setup

Open **Organizer tools → Edit schedule** to create or edit matches, fields,
tags, and script variables. The public Schedule view does not expose editing
controls.

All matches have (among other things) the following information:

- nominal start time
- nominal length
- previous match (if one exists)
- next match (if one exists)

### Tags and References: Specifying Teams

Each match has two teams playing in it as well as any number of teams
assigned to ref. Let's take a brief aside on how to specify teams. You
have three options:

  1. explicit team name: just type their name (autocomplete will help
     you). They must be registered in the tournament.
  2. tag: after adding a tag with **Tags**, you can use it as a team.
  3. reference: you can enter the winner or loser of another match,
     and Arctos will update these when the relevant results become
     available.

Tags are how you can set up a schedule without knowing what teams are
actually playing. A tag is like a generic team; whenever you want, you
can use the tags button to replace all instances of any given
tag with a specific team.

This is most obviously helpful before you know who has registered, but
it's also a very powerful tool in general! This was how we handled the
trials/finals seeding at Fog of War 2025 - teams for day 2 were set to
tags like "seed 1", "seed 2", etc., and after day 1, we compiled the
results, completed the rankings, and updated the tags accordingly.

Tags let you apply the seeding rules for your event. The Results tab
also provides calculated standings, described below; it does not set
tags or advance teams through a bracket.


### Static Scheduling

In the *static scheduling* paradigm (implemented by the `static` match
type), all match start times must be set concretely before the event
starts. This means that teams can be penalized for tardiness because
the schedule is very clear about what time they need to show up (ie,
it's not just 'show up promptly after the previous match ends'). If
head refs then cut off matches after the nominal length, the
tournament can guarantee the safety of the schedule.

Unless you are willing to cut a large portion of matches short,
however, this requires a large nominal length, which leads to lots of
idle time before every match starts (see: Fog of War 2025). To fix
this, we introduce the *dynamic scheduling*, implemented by the
`fast` and `safe` match types.

### Dynamic Matches

Dynamic matches do not have a nominal start time (that you, the TO,
can set, at least). Instead, their nominal start time is computed
based on the nominal length of their dependencies. Match dependencies
are:

- the preceding match: this match has to wait until the previous match
  is over.
- dependent teams: if "match1 winner" is one of the teams playing or
  reffing in this match, this match is dependent on match 1

The system sets this match's start time to the latest possible end
time across all dependencies.

The magic happens when matches begin to be completed. Every time a
match is completed, we update all other matches. If a match ends
early, its end time will be earlier than its nominal length would
predict, and so subsequent matches will be shifted forward! This seems
great, but it creates a problem: it breaks our ability to tell teams
when they need to be at their matches. If we shift their matches
forward, we can't possibly penalize them for being late, which may be
a problem for more formal events.

To solve this, there are two types of dynamic matches: `fast` and
`safe`. `fast` matches do not care about giving adequate warning; they
just move matches as far forward as possible. A fast match will have
its start time listed to be precisely the end time of the previous
match.

`safe` matches, on the other hand, are meant to give at least one
match's worth of warning to teams before the match begins. The start
time of a `safe` match gets locked when the last of its dependency
matches starts. When this happens, matches are marked as "time
finalized" on the schedule. This means that teams always know their
matches' start times at least `nominal_length` ahead of time, so it's
reasonable to expect them to be on time.

!!! note
    Matches without dependencies (like the first match of the day) must be static.

### Breaks

Break matches represent dynamically scheduled breaks in the
tournament. They just reserve time (ie, for a lunch break) on a field
in a way that can be pulled forward if matches finish early.

If you want a non-dynamic break, just use static matches before and
after the break, and leave the break empty.

### Joins

Join matches are like thread joins in multithreading: they are
dynamically-scheduled zero-length entries in the schedule that follow
the rule that all joins with the same name must occur at the same
time. More practically, for example, you could put a join match on
each field (all sharing the same name) after all morning matches on
that field, and then a lunch break on each field, then afternoon
matches afterwards. This would ensure that all teams have at least the
length of the break overlapping in their lunches (unless they choose
to start afternoon games early).

### Ribbon Games

Ribbon games are matches that are not counted in tournament results
(or rather, are by default excluded). They are useful for exhibition
matches, practice games, or matches that don't affect standings. When
creating or editing a match, you can check the "Ribbon Game" checkbox
to mark it as such.

### Results and Standings

Tournament and league results rank teams by match win percentage,
then point win percentage (points won divided by total points played).
Teams tied on both share a rank, with the next place skipped: 1, 1, 3.
Teams without a decided match remain unranked. If a team has no
recorded points, its point win percentage is treated as zero.

Gold, silver, and bronze trophies mark ranks 1, 2, and 3. These are
calculated standings, not organizer-assigned final bracket placements.
Selecting **Include ribbon games** recalculates the standings with
those matches included. Sorting a column changes the display order,
not the ranks. Expand a team to see its matches.

### Skip Conditions

Between tags and references, very nearly any tournament structure can
be expressed. There are, however, some cases when you can't know *how
many* matches will be run before the tournament begins. More
concretely, consider the double-elmination bracket, where a second
finals match must be run if it was the losing team's first loss.

In order to implement this, dynamic (`fast` or `safe`) matches[^2] may
have a *skip condition*, an boolean expression in lisp-like language
called Arctos Schedule Script (ASS).

You can see the full ASS docs [here](arctos-schedule-script.md).

### Exporting and Importing Schedules

The schedule editor offers **Export TOML** and **Import TOML**. An export contains
schedule definitions: fields, tags, script variables, match types, planned times,
and team/referee references. It is not a backup of scores, points, or footage.

An import is authoritative for the destination schedule. Fields, tags, variables,
and matches omitted from the file can be removed. Export the current schedule
and back up important event data before replacing it. Review import warnings and
resolved team/tag references before using the result. Do not treat an exported
schedule as a complete event backup.

## Side Comps

Side competitions track individual entries and results within an event. Players
receive an entry number and choose from the pompfen enabled by the organizer.
A player's profile preferences do not select a weapon for a competition.

Players registered for the event can join its side competitions while
registration is open, including players awaiting team approval. Pending team
members appear as unattached until the team approves them. Organizers can also
manage entries through the side competition's tools.

Organizers control registration, active scoring, enabled weapons, loss tracking,
and results display. Logged-in users can record results while a
competition is active and not finalized. Results record their submitter;
organizers review validity and resolve flags before finalizing.

The **Public result limit** starts at three. It controls the **All weapons**
view; selecting a weapon shows its top player. Blank publishes every matching
player, and zero hides results. Organizers can view complete standings when
results are enabled. Finalizing preserves these settings.

Results appear in one table. Chain Pits (**Chain/breaking**) have **Chains** and
**Breaks** tabs. Bear Pits use **Dueling**. The **Weapon** dropdown includes only
weapons with valid results, including losses. Each row shows the player's rank,
identity, and scores. Ranks apply to the selected weapon or group.

Select **Wins** or **Win %** to change the ranking. Live updates retain the
selection; a weapon without valid results resets to **All weapons**.

Signed-in players see their own rank and scores above the table in **Your result**,
including entries below the public limit. This card uses the default overall or
group ranking and stays visible while filtering. Their rows carry a **You** label.

### Running a Side Comp

Open **Enter results**, enter the player's entry number, and record the result.
The loss control is available only when losses are tracked for that entry.
A loss is a result, not a way to undo an accidental win. Use **Undo** to remove
the latest entry from the current scoring history, or flag an entry for organizer
review.

Recent entries remain visible while scoring. Organizers use **Manage results**
to review entries and finalize the competition. Public standings update as
results change while results are enabled.

## OBS Scoreboard Integration

Arctos provides a public scoreboard endpoint that can be embedded in
OBS or your streaming software of choice as a browser source. This
creates a live scoreboard overlay for your stream.

To set it up:

1. In OBS, add a new **Browser Source**
2. Set the URL to `https://events.californiajugger.org/TOURNAMENT_URL/scoreboard?field=FIELD_NAME`
    - Replace `TOURNAMENT_URL` with your tournament's URL
    - Replace `FIELD_NAME` with the name of the field you're streaming

The scoreboard displays:

  - Team names and profile photos
  - Current score by set
  - For stones matches: the stones remaining countdown with a progress bar
  - When no match is active: the previous and next match's teams (with
    winner listed for the previous match). The previous match is the
    field's most recently completed match, and the next match is the one
    listed after it in the field's schedule (for static matches, the next
    one by scheduled start time). Breaks, joins, and skipped matches are
    never shown, nor are matches on a different day than today in the
    viewer's timezone.

The scoreboard automatically polls for updates and refreshes when
match state changes. For stones matches, the countdown updates in
real-time using the same synchronization system as the match pages.


## Match Footage

When a match has footage available, open the match page to view it. Use the camera selector to choose a recording and the point selector to jump to a point.

Event organizers can open **Organizer tools → Footage** to find recordings by match, field, camera, status, uploader, or timestamp. **Open** lets you review the linked recording. **Delete** asks for confirmation before removing the footage entry; removal cannot be undone.
