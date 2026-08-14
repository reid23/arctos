# Arctos Schedule Script Documentation

## Introduction

Arctos Schedule Script, or *ASS*, is a lisp-style language meant for
expressing skip conditions and (increasingly) standings / tag-update
expressions over teams and matches.

It was designed with three goals in mind:

- express any arbitrary skip condition (and small pure list programs)
- keep the grammar as simple as possible
- don't give people remote code execution

### Skip conditions

Enter as the skip condition an expression that reduces to a boolean
(`true` or `false`). The moment all of a match's dependencies are
completed, this expression will be evaluated. If it evaluates to
`true`, the match will be skipped! If it evaluates to *anything else*
nothing will happen (asterisk; see [When are things
evaluated?](#when-are-things-evaluated)).

### Standings / tags

Expressions may also return teams or lists of teams (for example, a
pool ranked by wins). Identifiers bound by the evaluator environment
(e.g. future per-tournament globals like `teamlist` and `matchlist`)
can be referenced as bare names.

## Syntax Introduction

An ASS expression is either an *atom* (a literal value or a function)
or a *list* (of expressions).

Some examples of atoms are:

- numbers: `1`, `2`, etc.
- booleans: `true`, `false`
- nil: `nil` (the "nothing value")
- functions: `+`, `and`, `if`, etc.

A list is a space-separated list of items. The parser tries to reduce
expressions into atoms. It deals with lists by calling the first
element of the list with the remainder of the list as arguments. if
the first argument is not a list, it can't do anything, so it just
gives up and lets the expression be a list.

Take the following list for example:

```
(+ 1 2)
```

the first element is `+`, which is a function that takes two
arguments. The parser knows how to call `+` and so this list can be
reduced to the atom `3`.

```
(- (* 2 3) (+ 2 3))
```

This simplifies to `(- 6 5)` which is of course `1`.

## Team and Match Literals

Teams and matches are both types of atoms. A team literal can be
written with square brackets. The options for what you put inside the
square brackets are the same as the options for setting teams/ref
teams for a match; all of the following are valid:

```
[ursae]
[MatchNameHere::winner]
[MatchNameHere::loser]
[tag::TagNameHere]
```

Matches can be written similarly: simply enclose the match name in
curly braces like `{MatchNameHere}`.

the following functions can be used to get info about a team:

- `(wins [TeamName])` - Number of wins for a team this event
- `(wins [TeamName] MATCHLIST)` - Wins in the given list of matches
- `(losses [TeamName])` - Number of losses for a team this event
- `(losses [TeamName] MATCHLIST)` - Losses in the given list of matches
- `(points-won [TeamName])` - Total points won by a team this event
- `(points-lost [TeamName])` - Total points lost by a team this event
- `(points-won [TeamName] {MatchName})` - Points won in a specific match
- `(points-lost [TeamName] {MatchName})` - Points lost in a specific match
- `(points-won [TeamName] MATCHLIST)` - Sum of points won over matches
- `(points-lost [TeamName] MATCHLIST)` - Sum of points lost over matches
- `(won? [TeamName] {MatchName})` - Whether the team won that match

And the following functions can get info about matches:

- `(winner {MatchName})` - Winning team of a match (fails to evaluate
  until match is done)
- `(loser {MatchName})` - Losing team of a match (fails to evaluate
  until match is done)
- `(is-skipped {MatchName})` - Whether a match will be skipped (fails
  to evaluate until the match has either been skipped or started)

The `::winner` and `::loser` options for teams are largely there for
consistency; it is recommended to instead use `(winner
{MatchNameHere})` and `(loser {MatchNameHere})`.

## Lists

All code is already a list, but it gets evaluated by default. To write
a data list, you can use the `quote` function, the standard shorthand
prefix `'`, or the `list` constructor:

```
(quote (1 2 3)) -> the data list 1 2 3
'(1 2 3) -> also the data list 1 2 3
(list 1 2 3) -> also the data list 1 2 3
(1 2 3) -> cannot call 1 as a function
```

Lists can have any (potentially mixed) data inside them.

Now here are some fun things you can do with lists:

- `(list *ARGS)` - build a list from the arguments
- `(cons X LIST)` - prepend `X` onto `LIST` (flat list, not a pair)
- `(append LIST1 LIST2)` - concatenate two lists
- `(car LIST)`  - get first element of the list
- `(cdr LIST)` - get all but the first element of the list
- `(get INDEX LIST)` get the value of the list at index `INDEX` (so
  `(car LIST)` is equivalent to `(get 0 LIST)`)
- `(len LIST)` - get the length of the list
- `(empty? LIST)` - true if the list has length 0
- `(member? X LIST)` - true if `X` is in the list
- `(range N)` - the list `(0 1 ... N-1)` (empty if `N <= 0`)
- `(filter LIST PREDFN)` - keep elements where `PREDFN` returns true
- `(sort-by LIST KEYFN ...)` - sort descending by one or more integer key functions (stable)

## Maps, Reductions and Lambdas

Now, lists are only really useful if you can loop through them, but we
haven't introduced any form of looping yet. Since this is a functional
language, we don't have the familiar concepts like for loops and while
loops, but we do have `map`, `reduce`, and `lambda`. These may be
familiar to you if you've used Google Sheets or Excel.

First, `lambda` creates a function. The following expression is a
function that takes two arguments, `a`, `b`, and `c`, and returns
`a*b + c`.

```
(lambda (a b c) (+ c (* a b)))
```

Now, we can use `map` and `reduce` to apply functions to lists.
`map` just applies a function to every element of the list.

```
(map (list -2 -1 0 1 2) (lambda (x) (* (- x 1) (- x 1))))
```
The above expression reduces to the list `9 4 1 0 1`.

`map-indexed` is like `map` but the function receives `(index element)`:

```
(map-indexed (list 10 20) (lambda (i x) (+ i x)))
; -> (10 21)
```

`reduce` combines all elements of a list. With two arguments, the seed
is the first element (empty list errors). With three arguments, the
middle value is the initial seed (empty list returns the seed):

```
(reduce (list 1 2 5 3 4) (lambda (a b) (+ a b)))
; -> 15

(reduce (list 1 2 3) 0 (lambda (a b) (+ a b)))
; -> 6

(reduce (list) 0 (lambda (a b) (+ a b)))
; -> 0
```

Some of these can be tedious to implement, so i've included some builtins:

- `(max LIST)` - get the max value
- `(min LIST)` - get the min value
- `(max-by LIST FUNC)` - get the max value of a list using `FUNC` as a key
- `(min-by LIST FUNC)` - get the min value of a list using `FUNC` as a key
- `(sort-by LIST KEYFN ...)` - sort a list descending by key function(s)

### Locals and multi-branch conditionals

```
(let ((x 1)
      (y (+ x 2)))
  (+ x y))
; -> 3

(cond
  (false 1)
  ((> 3 2) 9)
  (true 0))
; -> 9
```

`let` bindings are sequential (later bindings see earlier ones). `cond`
returns `nil` if no clause matches; end with `(true …)` for a default.

### Ranking teams (standings)

Given environment bindings `teamlist` and `matchlist` (or literal lists):

```
(sort-by teamlist
  (lambda (t) (wins t matchlist))
  (lambda (t) (points-won t matchlist)))
```

Higher wins first; ties broken by points won; remaining ties keep
original order (stable sort).

## When are things evaluated?

Everything is evaluated when a match's last dependency becomes
finished or skipped. If it is not skipped, the skip condition will be
re-evaluated every time a match starts or finishes until it is started
or the skip condition evaluates to `true` and it gets skipped.

## Cheat Sheet

### Basic Values

- `true` - True
- `false` - False
- `nil` - Nil
- `[TeamName]` - Team name (username, `tag::TagName`, or `MatchName::winner` / `MatchName::loser`)
- `{MatchName}` - Match name

### Basic Operations

- `(== A B)` - Equality comparison
- `(> A B)`, `(< A B)`, `(>= A B)`, `(<= A B)` - Numeric comparisons
- `(and *BOOL)`, `(or *BOOL)`, `(not A)` - Logical operations (and/or are variadic)

### Team Operations

- `(wins [TeamName])` / `(wins [TeamName] MATCHLIST)` - Wins
- `(losses [TeamName])` / `(losses [TeamName] MATCHLIST)` - Losses
- `(points-won [TeamName])` / `(points-won [TeamName] {Match}|MATCHLIST)` - Points won
- `(points-lost [TeamName])` / `(points-lost [TeamName] {Match}|MATCHLIST)` - Points lost
- `(won? [TeamName] {MatchName})` - Whether team won match
- `(is-skipped {MatchName})` - True if match status is SKIPPED, false if IN_PROGRESS or COMPLETED

### Match Operations

- `(winner {MatchName})` - Winner team of a match (returns team or stays symbolic)
- `(loser {MatchName})` - Loser team of a match (returns team or stays symbolic)

### Other Operations

- `(if CONDITION IF_TRUE IF_FALSE)` - If condition is true, return IF_TRUE, otherwise return IF_FALSE
- `(let ((name expr) ...) BODY)` - Sequential local bindings
- `(cond (PRED EXPR) ...)` - First true pred wins; else nil
- `(lambda (args) body)` - Define a lambda function
- `(quote VALUE)` - interpret VALUE as data, not code. Typically used for list literals
- `(list *ARGS)` - Build a list
- `(cons X LIST)` - Prepend
- `(append LIST1 LIST2)` - Concatenate
- `(car LIST)` - Get the first element of a list
- `(cdr LIST)` - Get the rest of a list
- `(get INDEX LIST)` - Get the element at index
- `(or-default VAL DEFAULT)` - Returns VAL if VAL is not NIL else DEFAULT
- `(len LIST)` - Length of a list
- `(empty? LIST)` - Whether list is empty
- `(member? X LIST)` - Membership test
- `(map LIST FUNC)` - Apply a function to each element of a list
- `(map-indexed LIST FUNC)` - Apply `(lambda (i x) …)` to each element
- `(filter LIST PREDFN)` - Keep elements matching predicate
- `(reduce LIST FUNC)` / `(reduce LIST INIT FUNC)` - Reduce a list
- `(sort-by LIST KEYFN ...)` - Sort descending by key(s), stable
- `(range N)` - Integers `0 .. N-1`
- `(max LIST)`, `(min LIST)` - Max/min value in a list
- `(max-by LIST FUNC)`, `(min-by LIST FUNC)` - Max/min by a function

### Examples

- `(== 0 (losses [TeamName]))` - Skip if team has no losses
- `(> (wins [TeamA]) (wins [TeamB]))` - Skip if TeamA has more wins than TeamB
- `(== (winner {Match1}) [TeamName])` - Skip if TeamName won Match1
- `(car (sort-by teamlist (lambda (t) (wins t matchlist))))` - Top team in a pool
