# Side Comps

Side comps are currently implemented in a very rudimentary state. They
are currently able to run chain pits and bear pits, and just track the
number of wins each player has.

When players register for a side comp, they are given a number as
their ID, and they must choose a pompf to play. The available pompfen
are the normal weapons plus skull or unarmed (for extensibility to
qwik competitions in the future). TOs have the option to
enable/disable any of the pompf choices when configuring the side
comp.

TOs have two other settings: 
1. registration open (can people register)
2. active (can people submit results)
3. number of results to make public

If the side comp is active, anyone who is logged in can submit results
to the side comp. After the side comp is over, TOs may look through
the results manually and invalidate any as they see fit. the user who
submitted each score is recorded.

While the side comp is in progress, the results page will update live
with (unofficial) results. It nominally will show everyone, unless the
TOs have restricted the view to only some top $n$ players.

### Running a Side Comp

During the side comp, refs are presented with a screen where they can
enter player's ID numbers and increment or decrement their score. A
history of recent actions is shown so that refs can see, for example,
how many points in a row the last person has scored. Refs may click on
entries in this history bar to flag them for review later.

The philosophy here is that we should just get all the data and fix it
later. if the ref adds an extra point on accident, they can just
remove a point and it's fine. but anything more complicated than that
is probably going to be more complicated and controversial than I want
to deal with right now, so we can just have the TOs figure it out
according to whatever rules they'd like.
