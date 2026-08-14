# ASS Language Extensions Spec

**Status:** Implemented (`feat/ass-usability`)  
**Audience:** Implementers of `app/utils/parser.py`, the ASS entry UI, docs, and tag-auto-update consumers  
**Related:** [arctos-schedule-script.md](./arctos-schedule-script.md), `app/utils/parser.py`, `frontend/src/components/ass_entry.rs`

## 1. Summary

Extend Arctos Schedule Script (ASS) so it can express **standings and seeding lists** used to automatically update tags — not only boolean skip conditions.

The motivating program is: given a list of teams and a list of matches, return those teams ordered by wins in that match set, breaking ties by points won.

Today that program is possible only via an O(n²) rank/index encoding, because ASS cannot build lists, filter, sort, or fold with an explicit initial value.

This spec adds a small, total, functional list/core library plus match-scoped team stats. It deliberately does **not** add macros, mutation, I/O, or special handling for unfinished matches.

## 2. Goals

1. Make “rank these teams over these matches” a short, obvious expression.
2. Keep the grammar unchanged (still S-expressions + existing atoms).
3. Keep evaluation total and sandboxed: no RCE, no host I/O, no unbounded user-defined loops beyond list size.
4. Preserve backward compatibility for all existing skip-condition expressions.
5. Fit the existing typechecker / `Preserved` symbolic evaluation model.
6. Stay consistent with current naming (`max-by`, `is-skipped`, `points-won`).

## 3. Non-goals

| Non-goal | Reason |
|----------|--------|
| `:=` / textual macros | Will be replaced later by a **per-tournament table of global variables** bound into the evaluator env. Not part of this work. |
| Safe defaults for unfinished matches | Tag auto-update only runs when inputs are ready enough to evaluate; unfinished `(winner …)` staying symbolic is fine. |
| Variadic arithmetic beyond what’s specified | Keep signatures simple. |
| Mutable locals, `set!`, objects, strings, floats | Out of scope; fights the language goals. |
| Full Clojure/Haskell prelude | Only the minimum list ops needed for standings. |
| Changing skip-condition evaluation timing | Unrelated. |
| Implementing the tournament globals UI/table | Separate project; this spec only requires that new builtins work when identifiers are already in `Simplifier.env`. |

## 4. Design principles

1. **Hyphenated names** for multi-word builtins (`sort-by`, `map-indexed`), matching `max-by` / `is-skipped`.
2. **Special forms only when required** for non-evaluating binding (`let`, and existing `if` / `lambda` / `quote`). Everything else is a normal builtin in `_SIGNATURES`.
3. **Lists are flat arrays** (Python `List`), not linked cons cells. `cons` means “prepend one element”, not Lisp pair.
4. **Stable, deterministic** ordering where ties remain after user keys.
5. **Empty lists are first-class.** Folds that need a seed take an explicit init; they must not error on `()`.
6. **Symbolic preservation:** if any argument is unresolved / `Preserved` / symbolic team/match such that the result cannot be computed yet, return `Preserved([head, *args])` (same pattern as today).
7. **Docs and UI stay in sync** with `_SIGNATURES` and `DSL_FUNCTIONS`.

## 5. Current baseline (what we extend)

Relevant existing surface:

| Form | Notes |
|------|--------|
| `(map LIST FUNC) -> LIST` | FUNC is unary |
| `(reduce LIST FUNC)` | FUNC binary; **seed = first element**; **empty list errors** |
| `(max-by LIST FUNC)` / `(min-by LIST FUNC)` | FUNC → INT key |
| `(quote …)` / `'…` | Only way to make a data list literal |
| `(wins TEAM)` / `(points-won TEAM)` / `(points-won TEAM MATCH)` | Event-wide or single-match |
| `(winner MATCH)` / `(loser MATCH)` | Symbolic until match complete |
| `Simplifier.env` | Already supports bound identifiers (lambda params); globals will plug in here later |

Grammar (`grammar.lark`) needs **no changes** for this spec except whatever `let` / `cond` require as special forms (same list shape as today).

---

## 6. Feature specifications

Each feature below is **normative**. Pseudocode is Python-shaped for implementers; the ASS API is the signature block.

Conventions used in signatures:

- `LIST`, `INT`, `BOOL`, `TEAM`, `MATCH`, `FUNC`, `ANY`, `NIL` — existing type tags
- `KEYFN` — unary `FUNC` that must return `INT` when applied to a list element (same rule as `max-by` today)
- `PREDFN` — unary `FUNC` that must return `BOOL`
- Variadic args written `*ARGS` with `min_args` / `max_args = None`

---

### 6.1 List construction

#### 6.1.1 `(list *ARGS) -> LIST`

**Signature**

```text
(list)              -> ()
(list A B C ...)    -> (A B C ...)
```

| Field | Value |
|-------|--------|
| `min_args` | 0 |
| `max_args` | None |
| Arg types | each `ANY` |
| Return | `LIST` |

**Semantics**

- Evaluate all arguments left to right.
- Return a new data `List` containing those values in order.
- If any arg is unresolved in the sense of `_has_unresolved`, return `Preserved`.

**Notes**

- This is the primary constructor. Prefer documenting `list` over resurrecting the old broken `cons`-as-variadic-literal story.
- `(list)` is the empty list; equivalent in value to `'()`.

**Errors**

- None beyond generic evaluation errors inside args.

**Tests**

- `(list 1 2 3)` → `[1,2,3]`
- `(list)` → `[]`
- `(list [a] {m})` → team + match elements
- Nested: `(list (list 1) 2)` → `[[1], 2]`

---

#### 6.1.2 `(cons X LIST) -> LIST`

**Signature**

```text
(cons X LIST) -> LIST
```

| Field | Value |
|-------|--------|
| Args | `ANY`, `LIST` |
| Return | `LIST` |

**Semantics**

- Return a new list whose head is `X` and whose tail is the elements of `LIST`.
- **Not** a Lisp pair: `(cons 1 (list 2 3))` → `(1 2 3)`, never `(1 . (2 3))`.
- Does not mutate `LIST`.

**Errors**

- Type error if second arg is not a list (after resolution).

**Tests**

- `(cons 0 (list 1 2))` → `[0,1,2]`
- `(cons 1 (list))` → `[1]`
- `(car (cons x ys))` ≡ `x`, `(cdr (cons x ys))` ≡ `ys`

---

#### 6.1.3 `(append LIST1 LIST2) -> LIST`

**Signature**

```text
(append LIST1 LIST2) -> LIST
```

| Field | Value |
|-------|--------|
| Args | `LIST`, `LIST` |
| Return | `LIST` |

**Semantics**

- Concatenate two lists into a new list.
- `(append '() xs)` ≡ `xs` (new list copy is allowed either way; prefer new list always).
- `(append xs '())` ≡ copy of `xs`.

**Out of scope for v1:** multi-arg `append`. Two lists only.

**Tests**

- `(append (list 1 2) (list 3 4))` → `[1,2,3,4]`
- `(append (list) (list 1))` → `[1]`

---

### 6.2 Filter

#### 6.2.1 `(filter LIST PREDFN) -> LIST`

**Signature**

```text
(filter LIST PREDFN) -> LIST
```

| Field | Value |
|-------|--------|
| Args | `LIST`, `FUNC` |
| Return | `LIST` |

**Semantics**

- Apply `PREDFN` to each element left to right.
- Keep elements for which the predicate returns `true`.
- Drop elements for which it returns `false`.
- Preserve relative order.
- Predicate must return `BOOL` (not truthy INT). **Type error** if it returns a non-bool concrete value.
- If list or any predicate application is symbolic/unresolved, preserve the whole call (same conservatism as `map`).

**Tests**

- `(filter (list 1 2 3 4) (lambda (x) (> x 2)))` → `[3,4]`
- `(filter (list) (lambda (x) true))` → `[]`
- Predicate returning `1` → `DSLValidationError`

---

### 6.3 Reduce with initial value

#### 6.3.1 Extend `(reduce …)`

**New accepted forms**

```text
(reduce LIST FUNC)           ; existing: seed = first element
(reduce LIST INIT FUNC)      ; new: seed = INIT
```

| Form | Args | Notes |
|------|------|--------|
| 2-arg | `LIST`, `FUNC` | Unchanged. Empty list → error `"Cannot reduce empty list"`. |
| 3-arg | `LIST`, `ANY`, `FUNC` | `FUNC` is `(lambda (acc x) …)`. Empty list → `INIT`. |

**Signature table**

Represent as one builtin `reduce` with `min_args=2`, `max_args=3`. Type validation:

- Arg0: `LIST`
- If 2 args: arg1 `FUNC`
- If 3 args: arg1 `ANY`, arg2 `FUNC`

**Semantics (3-arg)**

```text
acc = INIT
for x in LIST:
    acc = FUNC(acc, x)
return acc
```

**Return type inference**

- 2-arg: union of element type and FUNC result (existing behavior / best effort)
- 3-arg: type of `INIT` unioned with FUNC result; if INIT is concrete and list empty, result type is INIT’s type

**Why both forms**

- Keep every existing skip condition working.
- Allow `(reduce matchlist 0 (lambda (acc m) …))` without an `if` on `(len …)`.

**Tests**

- `(reduce (list) 0 (lambda (a b) (+ a b)))` → `0`
- `(reduce (list 1 2 3) 0 (lambda (a b) (+ a b)))` → `6`
- `(reduce (list 1 2 3) (lambda (a b) (+ a b)))` → `6` (legacy)
- `(reduce (list) (lambda (a b) (+ a b)))` → error (legacy)

---

### 6.4 Sorting

#### 6.4.1 `(sort-by LIST *KEYFNS) -> LIST`

**Signature**

```text
(sort-by LIST KEYFN)                 ; single key
(sort-by LIST KEYFN1 KEYFN2 ...)     ; lexicographic keys
```

| Field | Value |
|-------|--------|
| `min_args` | 2 (`LIST` + at least one `KEYFN`) |
| `max_args` | None |
| Arg0 | `LIST` |
| Arg1.. | `FUNC` each |
| Return | `LIST` |

**Semantics**

1. Evaluate `LIST` and all key functions (they are values; typically lambdas).
2. For each element `x`, compute the key tuple `(KEYFN1(x), KEYFN2(x), …)`.
3. Each key function **must return `INT`** (same rule as `max-by`). Bool is not a valid key (note: in Python `bool` is a subclass of `int` — **reject `isinstance(val, bool)`** exactly like `max-by` does today).
4. Sort elements by that tuple **descending** on every key (higher INT ranks earlier / “better”).
5. **Stability:** when two elements compare equal on all keys, preserve their original relative order in `LIST`.
6. Do not mutate the input list; return a new `List`.
7. Empty list → empty list.
8. Single element → single-element list.
9. If the list contains symbolic teams/matches that prevent key evaluation, or any key application is unresolved, return `Preserved`.

**Why descending default**

Standings are almost always “more wins first”. Ascending sort is available as:

```text
(sort-by xs (lambda (x) (- 0 (key x))))
```

or, if we find that painful in practice, a follow-up can add `(sort-by-asc …)`. **Not in v1.**

**Multi-key example (normative target)**

```text
(sort-by teamlist
  (lambda (t) (wins t matchlist))
  (lambda (t) (points-won t matchlist)))
```

**Errors**

- Fewer than one keyfn → arity error
- Keyfn returns non-INT → `DSLValidationError("sort-by key function must return an integer")`

**Tests**

- `(sort-by (list 1 3 2) (lambda (x) x))` → `[3,2,1]`
- Stability: equal keys keep input order
- Multi-key: primary wins, secondary points
- Empty list

**Implementation note**

Use Python `sorted(..., key=..., reverse=True)` only for a **single** key. For multiple keys with per-key descending and stability, either:

- `sorted(enumerate(lst), key=lambda pair: (keytuple(pair[1]), -pair[0]), reverse=True)` carefully, or
- decorate with `(keytuple, -original_index)` and sort ascending on the decorate with negated keys,

and document the chosen approach in a code comment. Multi-key all-descending + stable is the acceptance criterion; the Python spell is left to the implementer.

---

### 6.5 Index helpers

#### 6.5.1 `(range N) -> LIST`

**Signature**

```text
(range N) -> LIST   ; (0 1 ... N-1)
```

| Field | Value |
|-------|--------|
| Args | `INT` |
| Return | `LIST` of `INT` |

**Semantics**

- If `N <= 0`, return `()`.
- If `N > 0`, return `(0 1 ... N-1)`.
- **Cap:** if `N > 10000`, raise `DSLValidationError` (“range too large”) to prevent accidental huge allocations. Tag/standings lists are tiny; this is a safety rail, not a product limit users should hit.

**Tests**

- `(range 0)` → `[]`
- `(range 3)` → `[0,1,2]`
- `(range -1)` → `[]`
- `(range 10001)` → error

---

#### 6.5.2 `(map-indexed LIST FUNC) -> LIST`

**Signature**

```text
(map-indexed LIST FUNC) -> LIST
```

`FUNC` is binary: `(lambda (i x) …)` with `i` the 0-based index.

| Field | Value |
|-------|--------|
| Args | `LIST`, `FUNC` |
| Return | `LIST` |

**Semantics**

```text
result = []
for i, x in enumerate(LIST):
    result.append(FUNC(i, x))
return result
```

**Errors**

- FUNC arity must be 2 (lambda param check at call time, same as any lambda call).

**Tests**

- `(map-indexed (list 10 20) (lambda (i x) (+ i x)))` → `[10, 21]`

---

### 6.6 Membership and emptiness

#### 6.6.1 `(empty? LIST) -> BOOL`

```text
(empty? LIST) -> BOOL
```

**Semantics:** `(== (len LIST) 0)` — provided as a readability builtin.

---

#### 6.6.2 `(member? X LIST) -> BOOL`

```text
(member? X LIST) -> BOOL
```

**Semantics**

- Return `true` if any element `e` in `LIST` satisfies the same equality rules as `(== X e)`.
- If equality for a pair would be `Preserved` (incomparable concrete types / symbolic), treat that pair as “not a definite hit” and continue; if no definite hit is found and at least one comparison was symbolic, the whole `member?` may be `Preserved`. Prefer:

  1. If any comparison definitely `true` → `true`
  2. Else if any comparison symbolic/unresolved → `Preserved`
  3. Else → `false`

This matches short-circuit “found it” while not inventing `false` under uncertainty.

**Equality** reuses `_evaluate_equality` rules (INT/BOOL/NIL value equality, Team by id, Match by uuid).

**Tests**

- `(member? 2 (list 1 2 3))` → `true`
- `(member? 4 (list 1 2 3))` → `false`
- Team membership by team id

---

### 6.7 Logical ergonomics

#### 6.7.1 Variadic `(and …)` and `(or …)`

**Extend** existing binary `and` / `or`:

```text
(and)            -> true
(and A)          -> BOOL(A)        ; after eval, must be BOOL
(and A B C ...)  -> left-to-right

(or)             -> false
(or A)           -> BOOL(A)
(or A B C ...)   -> left-to-right
```

| Field | Value |
|-------|--------|
| `min_args` | 0 |
| `max_args` | None |
| Each arg | `BOOL` |

**Semantics**

- `and`: return `false` on first concrete `false`; if all concrete `true`, return `true`; if a concrete `false` never appears and some arg is unresolved, `Preserved`.
- `or`: return `true` on first concrete `true`; if all concrete `false`, return `false`; symmetric preservation rule.
- **No short-circuit skipping of evaluation for side effects** — ASS has no side effects — but preservation/symbolic behavior should still avoid claiming a boolean if a remaining unresolved arg could matter. Concrete recommendation:
  - Evaluate all args (simple, consistent with most current builtins), **or**
  - Stop early on decisive concrete value and only preserve if decisive value not found and unresolved remains.

  Pick **evaluate-all** for v1 (simpler; no side effects anyway). Document it.

**Compatibility:** existing 2-arg calls unchanged.

---

#### 6.7.2 `(cond …)` special form

**Syntax**

```text
(cond
  (PRED1 EXPR1)
  (PRED2 EXPR2)
  ...
  (true DEFAULTEXPR))    ; conventional default clause
```

**Shape rules**

- Zero or more clauses.
- Each clause is a **data-looking** two-element list `(PRED EXPR)` in source. Because ASS evaluates lists as calls, `cond` **must be a special form** that does not evaluate clause structure as function calls.
- Implementation approach (either is fine; pick one and test):

  **Recommended:** parse like `if` — `cond`’s children are list nodes; for each clause child, require it is a list/tree of length 2; evaluate PRED; if concrete `true`, evaluate and return EXPR; if concrete `false`, next clause; if symbolic, preserve entire `cond`.

- If no clause matches: return `nil` (and document that). Callers who need a default should end with `(true …)`.
- Using `(true EXPR)` as default is idiomatic; `else` is **not** added as a keyword (avoid new atoms).

**Not valid:** Clojure-style implicit `do` in clauses; each clause is exactly one pred + one expr.

**Tests**

```text
(cond
  (false 1)
  ((> 3 2) 9)
  (true 0))
→ 9

(cond (false 1) (false 2))
→ nil
```

---

### 6.8 Lexical bindings: `let`

#### 6.8.1 `(let BINDINGS BODY)` special form

**Syntax**

```text
(let ((name1 expr1)
      (name2 expr2)
      ...)
  BODY)
```

**Semantics**

1. `BINDINGS` is a list of pairs `(NAME EXPR)`.
2. Names are identifiers (same rules as lambda params).
3. **Sequential binding (`let*` semantics under the name `let`):**  
   `expr2` may refer to `name1`, etc.  
   Rationale: one binding form is enough; sequential is what standings helpers need; Scheme’s parallel `let` is rarely wanted here.
4. Evaluate `expr_i` in an environment that already contains `name_1 … name_{i-1}`.
5. Evaluate `BODY` in the environment extended with all bindings.
6. Binding names shadow outer env / lambda params / (future) tournament globals.
7. Duplicate names in the same `let` bindings list → `DSLValidationError`.
8. Zero bindings: `(let () BODY)` ≡ `BODY`.

**Return type:** type of `BODY`.

**Errors**

- Malformed bindings (not a list of 2-element name/expr pairs)
- Non-identifier name

**Tests**

```text
(let ((x 1) (y (+ x 2))) (+ x y))
→ 4

(let ((t (winner {m}))) (== t [ursae]))
; preserves when winner symbolic
```

**Non-goals for v1**

- Multiple body forms
- `letrec`
- Destructuring

---

### 6.9 Match-scoped team stats

Extend existing team stat builtins so the second argument may be a **list of matches** as well as a single match (where applicable).

#### 6.9.1 `(wins TEAM)` / `(wins TEAM MATCHLIST)`

| Call | Meaning |
|------|---------|
| `(wins TEAM)` | Unchanged: event-wide win count |
| `(wins TEAM MATCHLIST)` | Number of matches in `MATCHLIST` whose `(winner m)` equals `TEAM` |

**Semantics for list form**

```text
count = 0
for m in MATCHLIST:
    w = winner(m)   # may be Preserved / symbolic
    if w definitely equals TEAM: count += 1
    elif w unresolved: whole call Preserved
return count
```

- Matches where the team did not play still count as non-wins (winner ≠ team).
- Skipped matches: if `winner` does not resolve, the call stays symbolic (no special skip handling — non-goal).

**Signature**

```text
wins: min_args=1, max_args=2
  arg0: TEAM
  arg1: LIST   # element type MATCH when concrete
```

Typecheck: if second arg present, must be `LIST`. Optionally, when list elements are concrete, each should be `MATCH`; if a concrete non-match element appears at runtime, `DSLValidationError`.

---

#### 6.9.2 `(losses TEAM)` / `(losses TEAM MATCHLIST)`

Symmetric to wins using `(loser m)`.

---

#### 6.9.3 `(points-won TEAM)` / `(points-won TEAM MATCH)` / `(points-won TEAM MATCHLIST)`

| Call | Meaning |
|------|---------|
| `(points-won TEAM)` | Unchanged: event-wide |
| `(points-won TEAM MATCH)` | Unchanged: single match |
| `(points-won TEAM MATCHLIST)` | Sum of `(points-won TEAM m)` over `m` in list |

**Disambiguation:** second arg is `MATCH` vs `LIST` by runtime type (and static type inference union). No new function name.

Empty list → `0`.

If any element is symbolic/unresolved such that points cannot be summed, `Preserved`.

---

#### 6.9.4 `(points-lost TEAM …)`

Same overload pattern as `points-won`.

---

#### 6.9.5 Optional predicate `(won? TEAM MATCH) -> BOOL`

**Include in v1** — tiny and reads well in filters.

```text
(won? TEAM MATCH) -> BOOL
```

**Semantics:** `(== (winner MATCH) TEAM)` with normal equality/preservation.

**Not required:** `(lost? …)` — can be `(== (loser m) t)`; add only if UI discoverability wants symmetry. **Spec decision: add `won?` only.**

---

### 6.10 Documentation fixes (required with the feature work)

While touching docs / cheat sheet / `DSL_FUNCTIONS`:

1. Replace remaining `cons` **examples** in `arctos-schedule-script.md` that still show `(map (cons …) …)` with `(map (list …) …)` or `'(…)`.
2. Standardize on **hyphen** forms everywhere in user docs: `max-by`, `min-by` (not `max_by`).
3. Expand the intro blurb: ASS is used for skip conditions **and** tag-auto-update / standings expressions that may return `LIST` / `TEAM` / etc., not only `BOOL`.
4. Document that **tournament globals** (future) appear as bare identifiers in expressions; this extension work must not assume a closed global namespace beyond builtins.

---

## 7. Normative target expression

After this spec, pool standings for tag update should be expressible as:

```text
(sort-by teamlist
  (lambda (t) (wins t matchlist))
  (lambda (t) (points-won t matchlist)))
```

Where `teamlist` and `matchlist` are identifiers supplied later via the per-tournament globals table (bound into `Simplifier.env` before evaluation). **Implementing that table is out of scope**, but:

- The evaluator must already resolve free identifiers via `env` (it does for lambda params).
- Validation/`validate_dsl` should accept unknown identifiers as symbolic/env-bound rather than hard-failing if that is required for authoring against declared globals — **follow-up ticket** if current validate rejects free identifiers. Do not block builtins on the globals UI.

With `let`, a more readable form:

```text
(let ((ranked
       (sort-by teamlist
         (lambda (t) (wins t matchlist))
         (lambda (t) (points-won t matchlist)))))
  ranked)
```

First place for a tag:

```text
(car (sort-by teamlist
       (lambda (t) (wins t matchlist))
       (lambda (t) (points-won t matchlist))))
```

Top 2:

```text
(let ((r (sort-by teamlist
           (lambda (t) (wins t matchlist))
           (lambda (t) (points-won t matchlist)))))
  (list (get 0 r) (get 1 r)))
```

---

## 8. Full builtin delta checklist

### 8.1 New builtins

| Name | Kind | Args |
|------|------|------|
| `list` | builtin | `*ANY` → `LIST` |
| `cons` | builtin | `ANY, LIST` → `LIST` |
| `append` | builtin | `LIST, LIST` → `LIST` |
| `filter` | builtin | `LIST, FUNC` → `LIST` |
| `sort-by` | builtin | `LIST, *FUNC` → `LIST` |
| `range` | builtin | `INT` → `LIST` |
| `map-indexed` | builtin | `LIST, FUNC` → `LIST` |
| `empty?` | builtin | `LIST` → `BOOL` |
| `member?` | builtin | `ANY, LIST` → `BOOL` |
| `won?` | builtin | `TEAM, MATCH` → `BOOL` |

### 8.2 Extended builtins

| Name | Change |
|------|--------|
| `reduce` | Optional `INIT` between list and func |
| `and` | Variadic, 0+ args |
| `or` | Variadic, 0+ args |
| `wins` | Optional `MATCHLIST` |
| `losses` | Optional `MATCHLIST` |
| `points-won` | Optional `MATCHLIST` (in addition to MATCH) |
| `points-lost` | Optional `MATCHLIST` |

### 8.3 New special forms

| Name | Kind |
|------|------|
| `let` | special form (sequential bindings) |
| `cond` | special form |

Add both to `Simplifier.BUILTINS` / special-form branch alongside `if`, `lambda`, `quote`. They must **not** go through normal arg-eval-then-dispatch only (bindings/clauses need structural handling).

---

## 9. Typechecker / inference updates

File: `app/utils/parser.py` (`_SIGNATURES`, `_infer_types`, `_RETURN_TYPE_FIXED`).

1. Register all new builtins with correct `min_args` / `max_args`.
2. `list` / `cons` / `append` / `filter` / `sort-by` / `range` / `map-indexed` return `LIST`.
3. `empty?` / `member?` / `won?` return `BOOL`.
4. `reduce` 3-arg: return type from INIT/body best-effort (mirror `if` looseness if needed).
5. `let` / `cond`: infer from taken body/clause expressions; if symbolic, `UNKNOWN` / preserve.
6. Ensure `validate_dsl` and any frontend expected-type checks still work for skip conditions (`BOOL`) while allowing other root types for tag expressions (product-dependent; if tag update has its own validate endpoint, accept `TEAM` or `LIST` there).

---

## 10. Dependency analyzer

File: `app/utils/dsl_dependency_analyzer.py`

- New builtins do not introduce new match/team dependency kinds.
- Walking must recurse into:
  - `let` binding exprs + body
  - `cond` preds + exprs
  - All args of `list` / `filter` / `sort-by` / etc.
- `(wins t matchlist)` when `matchlist` is a literal list of `{Match}` should mark those matches as direct dependencies; when `matchlist` is an identifier/global, dependency discovery may be incomplete until globals are inlined or registered — **document this limitation** and track with the globals project.

---

## 11. Frontend surface

File: `frontend/src/components/ass_entry.rs` (`DSL_FUNCTIONS`)

Add autocomplete entries for every new/extended builtin with short signatures matching this spec. Update descriptions for extended `wins` / `points-won` / `reduce` / `and` / `or`.

No editor grammar change required if autocomplete is list-driven.

---

## 12. Evaluation / performance constraints

| Constraint | Value |
|------------|--------|
| `range` max `N` | 10_000 |
| `sort-by` / `filter` / `map` | O(n) / O(n log n) in list length; list lengths for tags are small (≤ ~64 teams typical) |
| No user-defined recursion builtin | Lambdas may still recurse only if we ever add Y — **we do not**. Depth is bounded by list operations implemented in Python. |
| Stat overloads | `wins` over matchlist is O(\|matchlist\|) DB-ish work via existing `winner` / points helpers — acceptable for tag update batch sizes |

---

## 13. Error message guidelines

Reuse `DSLValidationError` patterns:

- Arity: `'{name}' expects …`
- Types: `Argument k of ({name} ...) must be LIST, got INT`
- `sort-by` / `max-by` key: must return integer
- `filter` pred: must return bool
- `range` too large: explicit message with the cap
- `let` / `cond` shape errors: point at “expected (name expr) binding” / “expected (pred expr) clause”

---

## 14. Testing plan

File: `tests/test_dsl_parser.py` (new test classes preferred)

### 14.1 Unit tests per builtin

Minimum cases listed under each feature above.

### 14.2 Integration: standings

Build a small tournament fixture with 3 teams, 3 round-robin matches, known scores:

| Match | Winner | Points (W–L) |
|-------|--------|----------------|
| m1 | A | 3–1 |
| m2 | B | 2–0 |
| m3 | A | 1–0 |

Expected wins in `{m1,m2,m3}`: A=2, B=1, C=0.  
Expected order: `(A B C)`.

Expression:

```text
(sort-by (list [A] [B] [C])
  (lambda (t) (wins t (list {m1} {m2} {m3})))
  (lambda (t) (points-won t (list {m1} {m2} {m3}))))
```

Also test tie on wins broken by points.

### 14.3 Compatibility

- Existing tests must pass unchanged.
- Spot-check real skip-condition strings from fixtures if any.

### 14.4 Symbolic

- `sort-by` over teams with an unfinished match in `wins` matchlist → `Preserved`
- `let` binding a symbolic winner → body preserved appropriately

---

## 15. Implementation plan (suggested PR slices)

### PR 1 — List core

- `list`, `cons`, `append`, `filter`
- `reduce` 3-arg
- `empty?`, `member?`
- Tests + docs cheat sheet bullets
- `DSL_FUNCTIONS` entries

### PR 2 — Sort + index

- `sort-by`, `range`, `map-indexed`
- Standings integration test with literal lists
- Docs example “rank teams”

### PR 3 — Stats overloads + `won?`

- `wins` / `losses` / `points-won` / `points-lost` matchlist overloads
- `won?`
- Rewrite standings test to use overloads

### PR 4 — Special forms

- `let` (sequential)
- `cond`
- Variadic `and` / `or`
- Dependency analyzer walk updates
- Docs narrative sections

### PR 5 — Docs polish

- Full `arctos-schedule-script.md` rewrite of list section
- Remove stale `cons` examples
- Hyphen consistency
- Mention future tournament globals (identifiers in env), without specifying UI

PRs may be squashed; slices are dependency-ordered.

---

## 16. File touch list

| Path | Change |
|------|--------|
| `app/utils/parser.py` | Signatures, eval, special forms, type inference |
| `app/utils/dsl_dependency_analyzer.py` | Walk `let` / `cond` / new calls |
| `tests/test_dsl_parser.py` | Unit + standings tests |
| `tests/test_dsl_dependency_analyzer.py` | Walk coverage if needed |
| `docs/arctos-schedule-script.md` | User-facing docs |
| `docs/ass-language-extensions-spec.md` | This spec (land as accepted / revise status) |
| `frontend/src/components/ass_entry.rs` | Autocomplete catalog |

No grammar file changes expected unless `cond`/`let` require lexer tweaks (they should not — identifiers + lists only).

---

## 17. Backward compatibility matrix

| Change | Compatible? |
|--------|-------------|
| New function names | Yes — previously unknown names errored |
| `reduce` 3-arg | Yes — new arity |
| `and`/`or` 0–1 / 3+ args | Yes — 2-arg identical |
| `wins` 2nd arg LIST | Yes — new arity; old 1-arg unchanged |
| `points-won` 2nd arg LIST vs MATCH | Yes — distinguished by type |
| `let` / `cond` as new heads | Yes — were invalid function names before |
| Sort stability / descending | N/A (new) |

---

## 18. Security considerations

- Still no `eval` of strings, no imports, no filesystem, no network.
- `range` cap prevents silly memory spikes.
- `sort-by` / `filter` bounded by input list length from DB-derived globals/literals.
- Lambdas remain pure closures over env; `let` only extends env.
- Do not add a builtin that turns strings into identifiers or code.

---

## 19. Open questions (resolved defaults)

| Question | Default in this spec |
|----------|----------------------|
| Sort ascending builtin? | No — negate keys |
| Parallel vs sequential `let`? | Sequential only, name `let` |
| `append` variadic? | No — two lists |
| `lost?`? | No — use `(== (loser m) t)` |
| `cons` Lisp-pair? | No — prepend to list |
| `and`/`or` evaluate-all vs short-circuit? | Evaluate-all |
| Free identifier validation for globals? | Deferred to globals project |
| Unfinished match defaults? | Out of scope (user decision) |

---

## 20. Acceptance criteria

This project is done when:

1. All builtins/special forms in §8 are implemented and unit-tested.
2. The normative standings expression in §7 works against a real multi-match fixture.
3. Existing DSL parser tests pass.
4. User docs and ASS autocomplete list the new surface.
5. Dependency analyzer does not crash on `let`/`cond` and still finds `{match}` literals inside new forms.
6. No `:=` macro system is introduced.
7. No unfinished-match defaulting helpers are introduced.

---

## 21. Appendix: rejected alternatives

| Idea | Why rejected |
|------|----------------|
| Textual `:=` macros | Author will use a tournament globals table instead |
| Only document rank/index encoding | Too hostile for TOs / tag rules |
| Full `loop` / `recur` | Easy to make non-total; list ops suffice |
| Returning linked lists via closure pairs | Unusable with `get` / tags |
| `sort` without keyfn on TEAMs | Teams have no intrinsic order |
| New language for tags separate from ASS | Doubles surface; skip + tags share match/team atoms |

---

## 22. Appendix: implementer sketch for `sort-by` stability

```python
def sort_by(lst, keyfns):
    decorated = []
    for i, item in enumerate(lst):
        keys = []
        for kf in keyfns:
            v = call(kf, item)
            if not isinstance(v, int) or isinstance(v, bool):
                raise DSLValidationError("sort-by key function must return an integer")
            keys.append(v)
        # higher keys first; lower index first among ties (stable among equals)
        decorated.append((keys, i, item))
    decorated.sort(key=lambda row: ([-k for k in row[0]], row[1]))
    return List(row[2] for row in decorated)
```

---

## 23. Status / ownership

- **Spec author:** generated from ASS ranking spike + language review  
- **Implementation owner:** TBD  
- **When landing:** set Status at top to `Accepted` or `Implemented` and link PRs here
