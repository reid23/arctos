"""
Arctos Schedule Script (ASS) — a small sandboxed Lisp for skip conditions
and tag-auto-update / standings expressions.

Team/match ops:
  (wins TEAM) / (wins TEAM MATCHLIST) -> INT
  (losses TEAM) / (losses TEAM MATCHLIST) -> INT
  (winner MATCH) -> TEAM
  (loser MATCH) -> TEAM
  (points-won TEAM) / (points-won TEAM MATCH|MATCHLIST) -> INT
  (points-lost TEAM) / (points-lost TEAM MATCH|MATCHLIST) -> INT
  (won? TEAM MATCH) -> BOOL
  (is-skipped MATCH) -> BOOL

Arithmetic / logic:
  (+ - * /) ( > < >= <= ) (==) (and *BOOL) (or *BOOL) (not BOOL)

Special forms:
  (if COND THEN ELSE)
  (let ((name expr) ...) BODY)   ; sequential bindings
  (cond (PRED EXPR) ...)         ; first true pred wins; else nil
  (quote EXPR) / 'EXPR
  (lambda (params) BODY)

Lists:
  (list *ARGS) (cons X LIST) (append LIST LIST)
  (car LIST) (cdr LIST) (get INDEX LIST) (len LIST)
  (empty? LIST) (member? X LIST)
  (map LIST FUNC) (map-indexed LIST FUNC)
  (filter LIST PREDFN)
  (reduce LIST FUNC) / (reduce LIST INIT FUNC)
  (sort-by LIST *KEYFNS)         ; descending keys, stable
  (range N)                      ; (0 .. N-1), N capped
  (max/min LIST) (max-by/min-by LIST FUNC)
  (or-default VAL DEFAULT)

Curly braces = matches `{name}`; square braces = teams `[name]`.

Types: INT, NIL, BOOL, MATCH, TEAM, LIST, FUNC
"""

# Max N accepted by (range N) — standings lists are tiny; this is a safety rail.
RANGE_MAX = 10_000

import difflib
import re

from lark import Lark, Tree
from lark.exceptions import LarkError, UnexpectedInput
from sqlalchemy import or_, and_
from app.models import Match as MatchDB, Point as PointDB, Team as TeamDB, Tag
from app.domain.enums import WinnerSide


class DSLValidationError(Exception):
    """Raised when DSL expression validation fails."""

    pass


class Nil:
    """Singleton representation of the NIL value."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "nil"

    def __str__(self):
        return "nil"

    def __bool__(self):
        return False

    def __eq__(self, other):
        return isinstance(other, Nil)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash("__nil__")


NIL = Nil()


def _format_value(value, *, quote_lists: bool = True) -> str:
    """Render an interpreted DSL value as readable Lisp-like text.

    A data list (`List`) renders with a leading `'` when `quote_lists=True`. We
    only suppress the `'` when descending into *another data list*, since those
    elements are already part of the outer quote and shouldn't accumulate their
    own `'`. `Preserved` (deferred function calls) renders bare, but its children
    are independent values — a child data list there should still be quoted.
    """
    if isinstance(value, Nil):
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Preserved):
        # Children of a preserved expression are independent values — keep quoting.
        return "(" + " ".join(_format_value(x, quote_lists=True) for x in value) + ")"
    if isinstance(value, list):
        # Children of a data list are part of the outer quote — don't re-quote them.
        body = "(" + " ".join(_format_value(x, quote_lists=False) for x in value) + ")"
        return ("'" + body) if quote_lists else body
    if isinstance(value, Team):
        return f"[{value.obj.id}]"
    if isinstance(value, Match):
        return f"{{{value.obj.name}}}"
    if isinstance(value, SymbolicTeam):
        return f"[{value.literal}]"
    if isinstance(value, SymbolicMatch):
        return f"{{{value.literal}}}"
    if isinstance(value, Lambda):
        params_str = " ".join(value.params) if value.params else ""
        return f"(lambda ({params_str}) ...)"
    return str(value)


def _format_dsl_value(value) -> str:
    """Display form for user-facing results (always quotes top-level data lists)."""
    return _format_value(value, quote_lists=True)


class List(list):
    """A quoted/data list. Lisp-style space-separated parenthesized form, prefixed with `'`."""

    def __repr__(self):
        return _format_value(self, quote_lists=True)

    __str__ = __repr__


class Preserved(list):
    """A deferred function-call expression, kept structurally because some argument is symbolic.

    Renders as `(head arg arg ...)` without a quote prefix — distinguishes it from a
    quoted data list, so type inference and pretty-printing don't conflate the two.
    """

    def __repr__(self):
        return _format_value(self, quote_lists=False)

    __str__ = __repr__


# Bracket pairings that ASS supports.
_BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN = {v: k for k, v in _BRACKET_PAIRS.items()}


def _format_position(text: str, pos: int) -> str:
    """Render `(line N, col M)` for a 0-based byte offset into `text`."""
    if pos < 0 or pos > len(text):
        return ""
    line = text.count("\n", 0, pos) + 1
    last_nl = text.rfind("\n", 0, pos)
    col = pos - last_nl  # 1-based when last_nl == -1 because pos - (-1) = pos + 1
    return f"line {line}, col {col}"


def _check_balanced_brackets(text: str) -> None:
    """Lightweight bracket-balance check that points at the offending character.

    ASS uses three nesting kinds — `()`, `[]`, `{}`. Square and curly
    literals don't nest (they enclose names, not sub-expressions), but they
    still need to be paired. We scan once and raise a `DSLValidationError`
    with a position the caller can show in the UI.
    """
    stack: list[tuple[str, int]] = []
    in_team_literal = False
    in_match_literal = False
    for i, c in enumerate(text):
        if in_team_literal:
            if c == "]":
                in_team_literal = False
                stack.pop()
            continue
        if in_match_literal:
            if c == "}":
                in_match_literal = False
                stack.pop()
            continue
        if c == "[":
            if stack and stack[-1][0] == "[":
                raise DSLValidationError(
                    f"Nested '[' at {_format_position(text, i)} — team literals can't contain '['."
                )
            stack.append(("[", i))
            in_team_literal = True
        elif c == "{":
            if stack and stack[-1][0] == "{":
                raise DSLValidationError(
                    f"Nested '{{' at {_format_position(text, i)} — match literals can't contain '{{'."
                )
            stack.append(("{", i))
            in_match_literal = True
        elif c == "(":
            stack.append(("(", i))
        elif c in (")", "]", "}"):
            expected_open = _CLOSE_TO_OPEN[c]
            if not stack:
                raise DSLValidationError(
                    f"Unexpected '{c}' at {_format_position(text, i)} — no matching '{expected_open}' to close."
                )
            open_c, _open_pos = stack[-1]
            if open_c != expected_open:
                raise DSLValidationError(
                    f"Mismatched bracket: '{c}' at {_format_position(text, i)} closes a '{open_c}' opened at {_format_position(text, _open_pos)}."
                )
            stack.pop()
    if stack:
        open_c, open_pos = stack[-1]
        close_c = _BRACKET_PAIRS[open_c]
        raise DSLValidationError(
            f"Unclosed '{open_c}' at {_format_position(text, open_pos)} — expected matching '{close_c}'."
        )


def _suggest_function(name: str, candidates) -> str | None:
    """Return the best fuzzy match for `name` in `candidates`, or None."""
    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return matches[0] if matches else None


# Type-set shorthands used in the signature table below.
_INT = frozenset({"INT"})
_BOOL = frozenset({"BOOL"})
_TEAM = frozenset({"TEAM"})
_MATCH = frozenset({"MATCH"})
_LIST = frozenset({"LIST"})
_FUNC = frozenset({"FUNC"})
_NIL = frozenset({"NIL"})
_ANY = frozenset({"INT", "BOOL", "TEAM", "MATCH", "LIST", "FUNC", "NIL"})


# Centralized signature table for all built-in functions. `args` is the per-position
# expected type set for the *fixed* arguments. `min_args`/`max_args` allow optional or
# variadic functions; `max_args = None` means unbounded. `return` is the declared
# return type when statically known; functions whose return depends on the args
# (`if`, `or-default`, `car`, `get`, `reduce`, `max`/`min`, `max-by`/`min-by`) are
# omitted from the return-type lookup and computed in `_infer_types`.
# MATCH or LIST — used for points-won/points-lost second-arg overload.
_MATCH_OR_LIST = frozenset({"MATCH", "LIST"})

_SIGNATURES: dict[str, dict] = {
    "+": {"args": (_INT, _INT), "return": _INT},
    "-": {"args": (_INT, _INT), "return": _INT},
    "*": {"args": (_INT, _INT), "return": _INT},
    "/": {"args": (_INT, _INT), "return": _INT},
    ">": {"args": (_INT, _INT), "return": _BOOL},
    "<": {"args": (_INT, _INT), "return": _BOOL},
    ">=": {"args": (_INT, _INT), "return": _BOOL},
    "<=": {"args": (_INT, _INT), "return": _BOOL},
    "==": {"args": (_ANY, _ANY), "return": _BOOL},
    "or": {"args": (_BOOL,), "min_args": 0, "max_args": None, "return": _BOOL},
    "and": {"args": (_BOOL,), "min_args": 0, "max_args": None, "return": _BOOL},
    "not": {"args": (_BOOL,), "return": _BOOL},
    "wins": {"args": (_TEAM, _LIST), "min_args": 1, "max_args": 2, "return": _INT},
    "losses": {"args": (_TEAM, _LIST), "min_args": 1, "max_args": 2, "return": _INT},
    "winner": {"args": (_MATCH,), "return": _TEAM},
    "loser": {"args": (_MATCH,), "return": _TEAM},
    "is-skipped": {"args": (_MATCH,), "return": _BOOL},
    "won?": {"args": (_TEAM, _MATCH), "return": _BOOL},
    "points-won": {"args": (_TEAM, _MATCH_OR_LIST), "min_args": 1, "max_args": 2, "return": _INT},
    "points-lost": {"args": (_TEAM, _MATCH_OR_LIST), "min_args": 1, "max_args": 2, "return": _INT},
    "list": {"args": (_ANY,), "min_args": 0, "max_args": None, "return": _LIST},
    "cons": {"args": (_ANY, _LIST), "return": _LIST},
    "append": {"args": (_LIST, _LIST), "return": _LIST},
    "car": {"args": (_LIST,)},
    "cdr": {"args": (_LIST,), "return": _LIST},
    "get": {"args": (_INT, _LIST)},
    "or-default": {"args": (_ANY, _ANY)},
    "len": {"args": (_LIST,), "return": _INT},
    "empty?": {"args": (_LIST,), "return": _BOOL},
    "member?": {"args": (_ANY, _LIST), "return": _BOOL},
    "map": {"args": (_LIST, _FUNC), "return": _LIST},
    "map-indexed": {"args": (_LIST, _FUNC), "return": _LIST},
    "filter": {"args": (_LIST, _FUNC), "return": _LIST},
    # reduce: 2-arg (LIST FUNC) or 3-arg (LIST INIT FUNC) — validated specially.
    "reduce": {"args": (_LIST,), "min_args": 2, "max_args": 3},
    "sort-by": {"args": (_LIST, _FUNC), "min_args": 2, "max_args": None, "return": _LIST},
    "range": {"args": (_INT,), "return": _LIST},
    "max": {"args": (_LIST,)},
    "min": {"args": (_LIST,)},
    "max-by": {"args": (_LIST, _FUNC)},
    "min-by": {"args": (_LIST, _FUNC)},
    # `if`, `lambda`, `quote`, `let`, `cond` are special forms (not table-validated).
}


_RETURN_TYPE_FIXED: dict[str, frozenset[str]] = {
    head: sig["return"] for head, sig in _SIGNATURES.items() if "return" in sig
}
# Special forms aren't in `_SIGNATURES` but have well-defined return types.
_RETURN_TYPE_FIXED["lambda"] = _FUNC


def _human_type_name(types: frozenset[str]) -> str:
    """Pretty-print a type set for error messages."""
    if not types:
        return "?"
    return " | ".join(sorted(types))


def _infer_list_element_types(lst) -> frozenset[str]:
    """Infer the union of element types in a list-valued expression.

    Handles already-evaluated data lists (e.g. `[True, False, True]` from a
    quoted literal `'(true false true)`). Returns `{"UNKNOWN"}` for anything
    else (e.g. a `map` preserved expression), since the per-element type isn't
    visible without evaluating.
    """
    if isinstance(lst, list) and len(lst) > 0:
        types: frozenset[str] = frozenset()
        for elem in lst:
            types |= _infer_types(elem)
        return types
    return frozenset({"UNKNOWN"})


def _infer_types(value) -> frozenset[str]:
    """Infer the set of possible types of an interpreted DSL value.

    Concrete values map directly. Preserved expressions (lists starting with a
    function name) dispatch on the head: most functions have a fixed return type;
    the few whose result type depends on their args (`if`, `or-default`, `get`,
    `car`, `reduce`, `max`/`min`/`max-by`/`min-by`) recurse into their branches
    and union the possibilities.

    Returns `frozenset({"UNKNOWN"})` when nothing better can be said — callers
    should treat that as "don't enforce a type constraint".
    """
    # Order matters: bool is a subclass of int, so check bool first.
    if isinstance(value, bool):
        return frozenset({"BOOL"})
    if isinstance(value, int):
        return frozenset({"INT"})
    if isinstance(value, Nil):
        return frozenset({"NIL"})
    if isinstance(value, (Team, SymbolicTeam)):
        return frozenset({"TEAM"})
    if isinstance(value, (Match, SymbolicMatch)):
        return frozenset({"MATCH"})
    if isinstance(value, Lambda):
        return frozenset({"FUNC"})
    if isinstance(value, Preserved):
        if value and isinstance(value[0], str):
            head = value[0]
            args = value[1:]
            if head in _RETURN_TYPE_FIXED:
                return _RETURN_TYPE_FIXED[head]
            if head == "if":
                # (if cond then else) — type is union of the two branches.
                if len(args) >= 3:
                    return _infer_types(args[1]) | _infer_types(args[2])
                return frozenset({"UNKNOWN"})
            if head == "or-default":
                # (or-default val default) — val if non-nil, else default; union them
                # but exclude NIL from the val side since or-default falls through.
                if len(args) >= 2:
                    val_types = _infer_types(args[0]) - {"NIL"}
                    return val_types | _infer_types(args[1])
                return frozenset({"UNKNOWN"})
            if head == "get":
                # (get index list) — union of element types in the list, plus NIL for out-of-bounds.
                if len(args) >= 2:
                    return _infer_list_element_types(args[1]) | {"NIL"}
                return frozenset({"UNKNOWN", "NIL"})
            if head == "car":
                # First element of a list — element types of the underlying list if we can see them.
                if len(args) >= 1:
                    return _infer_list_element_types(args[0])
                return frozenset({"UNKNOWN"})
            if head == "reduce":
                return frozenset({"UNKNOWN"})
            if head in ("max", "min", "max-by", "min-by", "sort-by"):
                # Element type of the source list when visible.
                if len(args) >= 1:
                    return _infer_list_element_types(args[0]) | frozenset({"UNKNOWN"})
                return frozenset({"UNKNOWN"})
            if head == "let":
                # (let bindings body) — type is body's type when we can see it.
                if len(args) >= 2:
                    return _infer_types(args[1])
                return frozenset({"UNKNOWN"})
            if head == "cond":
                # Union of all clause expression types (odd positions after head packaging).
                # Preserved form is (cond (pred expr) ...) — args are clause lists.
                types: frozenset[str] = frozenset()
                for clause in args:
                    if isinstance(clause, list) and len(clause) >= 2:
                        types |= _infer_types(clause[1])
                return types if types else frozenset({"UNKNOWN", "NIL"})
        return frozenset({"UNKNOWN"})
    if isinstance(value, list):
        # Plain data list (a quoted literal).
        return frozenset({"LIST"})
    return frozenset({"UNKNOWN"})


class Lambda:
    """Represents a lambda function closure."""

    def __init__(self, params, body_tree, env, parse_team_literal, parse_match_literal):
        """
        Args:
            params: List of parameter names (or list with single element for variadic)
            body_tree: The body as a Lark Tree node (unevaluated)
            env: The environment (dict) at the time of lambda creation
            parse_team_literal: Function to parse team literals
            parse_match_literal: Function to parse match literals
        """
        self.params = params
        self.body_tree = body_tree  # Store as Tree node, not evaluated
        self.env = env.copy() if env else {}
        self.parse_team_literal = parse_team_literal
        self.parse_match_literal = parse_match_literal

    def __repr__(self):
        return f"Lambda({self.params}, ...)"


class SymbolicTeam:
    """Symbolic representation of an unresolved team reference."""

    def __init__(self, literal: str, event: str):
        self.literal = literal
        self.url = event

    def __hash__(self):
        return hash((self.url, self.literal))

    def __repr__(self):
        return f"SymbolicTeam([{self.literal}])"


class SymbolicMatch:
    """Symbolic representation of an unresolved match reference."""

    def __init__(self, literal: str, event: str):
        self.literal = literal
        self.url = event

    def __hash__(self):
        return hash((self.url, self.literal))

    def __repr__(self):
        return f"SymbolicMatch({{{self.literal}}})"


def parse_team_literal(literal: str, event: str):
    """parse team literal into Team object or SymbolicTeam if not found. parses tags and match winners/losers just like normal options for team references.

    Args:
        literal (str): literal inside of square brackets
        event (str): tournament url
    Returns:
        Team | SymbolicTeam: team object or symbolic representation
    """
    if "::" not in literal:
        team_obj = TeamDB.query.filter_by(id=literal).first()
        if team_obj:
            return Team(team_obj, event)
        return SymbolicTeam(literal, event)
    else:
        split = literal.split("::")
        assert len(split) == 2, f"Invalid team literal: {literal}"
        match_name, qualifier = split
        if qualifier == "winner":
            match_obj = MatchDB.query.filter_by(name=match_name, event=event).first()
            if not match_obj:
                raise DSLValidationError(f"Match {match_name} not found")
            return Match(match_obj, event).winner()
        elif qualifier == "loser":
            match_obj = MatchDB.query.filter_by(name=match_name, event=event).first()
            if not match_obj:
                raise DSLValidationError(f"Match {match_name} not found")
            return Match(match_obj, event).loser()
        elif match_name == "tag":
            tag = Tag.query.filter_by(name=qualifier, event=event).first()
            if not tag:
                known = [t.name for t in Tag.query.filter_by(event=event).all()]
                suggestion = _suggest_function(qualifier, known)
                hint = f" Did you mean 'tag::{suggestion}'?" if suggestion else ""
                raise DSLValidationError(f"Tag '{qualifier}' does not exist.{hint}")
            # Effective resolution: manual team override, else the tag's ASS
            # expression (cycle-guarded), else unresolved.
            from app.utils.helpers import resolve_tag_to_team

            team_id = resolve_tag_to_team(f"tag::{qualifier}", event)
            if not team_id:
                # Tag exists but doesn't resolve to a team yet — stay symbolic.
                return SymbolicTeam(literal, event)
            team_obj = TeamDB.query.filter_by(id=team_id).first()
            if team_obj:
                return Team(team_obj, event)
            # Tag's team id refers to a deleted team — stay symbolic.
            return SymbolicTeam(literal, event)
        else:
            raise DSLValidationError(f"Invalid team literal: {literal}")


def parse_match_literal(literal: str, event: str):
    """parse match literal into Match object or SymbolicMatch if not found.

    Args:
        literal (str): literal inside of curly braces
        event (str): tournament url
    Returns:
        Match | SymbolicMatch: match object or symbolic representation
    """
    obj = MatchDB.query.filter_by(name=literal, event=event).first()
    if not obj:
        return SymbolicMatch(literal, event)
    return Match(obj, event)


class Team:
    def __init__(self, obj: TeamDB, event: str):
        self.url = event
        self.obj = obj

    def points_won(self, m=None):
        # Filter Point columns first (before join) to reduce join set
        query = PointDB.query.filter(PointDB.rerolled == False)
        if m is not None:
            query = query.filter(PointDB.match == m.obj.uuid)
        # Then join and filter on Match columns
        query = query.join(MatchDB, PointDB.match == MatchDB.uuid).filter(
            MatchDB.event == self.url,
            or_(
                and_(MatchDB.team1 == self.obj.id, PointDB.winner == WinnerSide.TEAM1),
                and_(MatchDB.team2 == self.obj.id, PointDB.winner == WinnerSide.TEAM2),
            ),
        )
        return query.count()

    def points_lost(self, m=None):
        query = PointDB.query.filter(PointDB.rerolled == False)
        if m is not None:
            query = query.filter(PointDB.match == m.obj.uuid)
        query = query.join(MatchDB, PointDB.match == MatchDB.uuid).filter(
            MatchDB.event == self.url,
            or_(
                and_(MatchDB.team1 == self.obj.id, PointDB.winner == WinnerSide.TEAM2),
                and_(MatchDB.team2 == self.obj.id, PointDB.winner == WinnerSide.TEAM1),
            ),
        )
        return query.count()

    def wins(self):
        return (
            MatchDB.query.filter_by(event=self.url, team1=self.obj.id, match_winner=WinnerSide.TEAM1).count()
            + MatchDB.query.filter_by(event=self.url, team2=self.obj.id, match_winner=WinnerSide.TEAM2).count()
        )

    def losses(self):
        return (
            MatchDB.query.filter_by(event=self.url, team1=self.obj.id, match_winner=WinnerSide.TEAM2).count()
            + MatchDB.query.filter_by(event=self.url, team2=self.obj.id, match_winner=WinnerSide.TEAM1).count()
        )

    def __hash__(self):
        return hash((self.url, self.obj.id))


class Match:
    def __init__(self, obj: MatchDB, event: str):
        self.url = event
        self.obj = obj

    def winner(self):
        winner = self.obj.winner_team_id
        if winner is None:
            # Return symbolic representation instead of None
            return SymbolicTeam(f"{self.obj.name}::winner", self.url)
        team_obj = TeamDB.query.filter_by(id=winner).first()
        if team_obj:
            return Team(team_obj, self.url)
        return SymbolicTeam(winner, self.url)

    def loser(self):
        loser = self.obj.loser_team_id
        if loser is None:
            # Return symbolic representation instead of None
            return SymbolicTeam(f"{self.obj.name}::loser", self.url)
        team_obj = TeamDB.query.filter_by(id=loser).first()
        if team_obj:
            return Team(team_obj, self.url)
        return SymbolicTeam(loser, self.url)

    def __hash__(self):
        return hash((self.url, self.obj.uuid))


class Simplifier:
    """Evaluates DSL expressions by executing code and resolving symbols."""

    # Built-in function names that are valid identifiers
    BUILTINS = set(_SIGNATURES.keys()) | {"if", "lambda", "quote", "let", "cond"}

    def __init__(self, parse_team_literal, parse_match_literal, env=None):
        self.parse_team_literal = parse_team_literal
        self.parse_match_literal = parse_match_literal
        self.env = env if env is not None else {}

    def visit(self, tree):
        """Top-down visitor - dispatches to appropriate method based on tree.data."""
        if not isinstance(tree, Tree):
            return tree

        method_name = tree.data
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return method(tree)
        else:
            # Default: visit children
            return self.visit_children(tree)

    def visit_children(self, tree):
        """Visit all children of a tree node."""
        return [self.visit(child) for child in tree.children]

    def _resolve_identifier(self, value):
        """Resolve an identifier from environment if it's a string."""
        if isinstance(value, str):
            if value in self.env:
                return self.env[value]
            elif value in self.BUILTINS:
                return value  # Built-in function name
        return value

    def _visit_params_list(self, tree):
        """Visit a parameter list, ensuring it returns a list of parameter names.

        For lambda parameters like (x) or (x y), we want to return ["x"] or ["x", "y"].
        This handles the case where (x) is a single-element list that should stay as a list.
        """
        if not isinstance(tree, Tree) or tree.data != "list":
            # Not a list - error
            raise DSLValidationError("Lambda parameters must be a list")

        if not tree.children:
            return []  # Empty parameter list

        # Visit all children (they should be identifiers)
        params = []
        for child in tree.children:
            param = self.visit(child)
            # Parameters should be identifiers (strings)
            if isinstance(param, str):
                params.append(param)
            else:
                raise DSLValidationError(f"Lambda parameter names must be identifiers, got {type(param).__name__}")

        return params

    def _is_preserved_expression(self, value):
        """Check if a value is a preserved (deferred function-call) expression."""
        return isinstance(value, Preserved)

    def _is_unresolved_identifier(self, value):
        """Free identifier — a string we couldn't bind to a builtin or a value in env."""
        return isinstance(value, str) and value not in self.BUILTINS and value not in self.env

    def _has_unresolved(self, args) -> bool:
        """True if any argument can't be evaluated yet (symbolic, preserved, free)."""
        for arg in args:
            if isinstance(arg, (SymbolicTeam, SymbolicMatch)):
                return True
            if self._is_preserved_expression(arg):
                return True
            if self._is_unresolved_identifier(arg):
                return True
        return False

    def _validate_arity(self, head: str, args, min_count: int, max_count, optional_count: int = 0):
        """Validate argument count. Raises DSLValidationError if invalid."""
        actual = len(args)
        if max_count is None:
            if actual < min_count:
                raise DSLValidationError(f"({head} ...) expects at least {min_count} argument(s), got {actual}")
            return
        if actual < min_count or actual > max_count:
            if min_count == max_count:
                expected_desc = f"{max_count}"
            else:
                expected_desc = f"{min_count} to {max_count}"
            raise DSLValidationError(f"({head} ...) expects {expected_desc} argument(s), got {actual}")

    def _validate_arg_types(self, head: str, args, expected_types):
        """Exhaustively check each arg's inferred type against the expected per-position set.

        For variadic positions (more args than `expected_types` entries) the last entry's
        expected set is reused. An arg passes when its inferred types intersect the
        expected set, or when inference yielded `UNKNOWN` (no information either way).
        """
        if not expected_types:
            return
        for i, arg in enumerate(args):
            expected = expected_types[i] if i < len(expected_types) else expected_types[-1]
            inferred = _infer_types(arg)
            if "UNKNOWN" in inferred:
                continue
            if inferred & expected:
                continue
            raise DSLValidationError(
                f"Argument {i + 1} of ({head} ...) must be {_human_type_name(expected)}, "
                f"got {_human_type_name(inferred)}"
            )

    def _validate_call(self, head: str, args):
        """Run arity + per-arg type validation against the signature table."""
        sig = _SIGNATURES.get(head)
        if sig is None:
            return  # No signature (e.g. lambda/if special forms — handled separately).
        expected_types = sig.get("args", ())
        fixed_count = len(expected_types)
        max_args = sig.get("max_args", fixed_count)
        min_args = sig.get("min_args", fixed_count)
        self._validate_arity(head, args, min_args, max_args)

        # reduce: (LIST FUNC) or (LIST INIT FUNC) — middle arg is not FUNC in the 3-arg form.
        if head == "reduce":
            self._validate_arg_types(head, args[:1], (_LIST,))
            if len(args) == 2:
                self._validate_arg_types(head, args[1:], (_FUNC,))
            else:
                # arg1 = INIT (ANY), arg2 = FUNC
                self._validate_arg_types(head, args[2:], (_FUNC,))
            return

        self._validate_arg_types(head, args, expected_types)

    # Interpreter methods - top-down traversal with explicit control

    def expression(self, tree):
        """Visit expression node - just visit the child."""
        if tree.children:
            return self.visit(tree.children[0])
        return Nil()

    # Atom transformations
    def int_atom(self, tree):
        token = tree.children[0]
        return int(token.value)

    def bool_atom(self, tree):
        token = tree.children[0]
        return token.value == "true"

    def nil_atom(self, tree):
        return Nil()

    def team_atom(self, tree):
        token = tree.children[0]
        team_str = token.value[1:-1]  # Remove brackets
        return self.parse_team_literal(team_str)

    def match_atom(self, tree):
        token = tree.children[0]
        match_str = token.value[1:-1]  # Remove braces
        return self.parse_match_literal(match_str)

    def identifier_atom(self, tree):
        """Resolve identifier to variable value or return as string."""
        token = tree.children[0]
        name = token.value
        # Handle boolean and nil literals (fallback)
        if name == "true":
            return True
        if name == "false":
            return False
        if name == "nil":
            return Nil()
        # Check if it's in the environment (lambda argument or closure variable)
        if name in self.env:
            return self.env[name]
        # Check if it's a built-in function name
        if name in self.BUILTINS:
            return name  # Return as string for function calls
        # Unknown symbol - return as string (will be resolved/error later)
        return name

    def quoted(self, tree):
        """Visit `'EXPR` syntactic-sugar form — same as `(quote EXPR)`."""
        return self._quote_tree(tree.children[0])

    def _quote_tree(self, tree):
        """Return the literal data form of a parse tree without evaluating function calls.

        Atoms become their concrete value (int_atom -> int, identifier_atom -> name as string,
        team_atom / match_atom -> resolved Team/Match). Lists become `List` of recursively
        quoted children. Nested `'EXPR` (or `(quote EXPR)`) collapses through `_quote_tree`
        on the inner expression as well.
        """
        if not isinstance(tree, Tree):
            return tree
        if tree.data == "list":
            return List([self._quote_tree(child) for child in tree.children])
        if tree.data == "expression":
            if tree.children:
                return self._quote_tree(tree.children[0])
            return Nil()
        if tree.data == "quoted":
            # `''x` desugars to `'(quote x)` → the literal 2-element list (quote x).
            return List(["quote", self._quote_tree(tree.children[0])])
        if tree.data == "identifier_atom":
            # Quoted identifier — preserve the literal symbol as a string. The IDENTIFIER
            # token also matches the keyword literals `true` / `false` / `nil`, which
            # should still resolve to their literal value when quoted.
            name = tree.children[0].value
            if name == "true":
                return True
            if name == "false":
                return False
            if name == "nil":
                return Nil()
            return name
        # Other atoms (int / bool / nil / team / match) carry their literal value either way.
        return self.visit(tree)

    # Main expression handling
    def list(self, tree):
        """Process a list/s-expression with top-down control."""
        if not tree.children:
            return List()  # Empty list

        # Evaluate head first (top-down: check what we're calling before evaluating args)
        head_tree = tree.children[0]
        head = self.visit(head_tree)
        head = self._resolve_identifier(head)

        # Handle quote special form — return the argument literally without evaluation.
        if isinstance(head, str) and head == "quote":
            if len(tree.children) != 2:
                raise DSLValidationError("quote expects exactly 1 argument")
            return self._quote_tree(tree.children[1])

        # Handle lambda special form - DON'T evaluate the body
        if isinstance(head, str) and head == "lambda":
            if len(tree.children) != 3:
                raise DSLValidationError("lambda expects 2 arguments: (params) and body")

            # Evaluate params (second child) - this should be a list like (x) or (x y)
            params_tree = tree.children[1]
            # For params, we need to visit it but ensure it returns a list
            # If it's a single-element list like (x), we want ["x"]
            params_expr = self._visit_params_list(params_tree)
            if not isinstance(params_expr, list):
                raise DSLValidationError("Lambda parameters must be a list")

            # DON'T evaluate body (third child) - store the Tree node
            body_tree = tree.children[2]
            return self._evaluate_lambda(head, params_expr, body_tree)

        # Handle if special form - evaluate condition first, then choose branch
        if isinstance(head, str) and head == "if":
            if len(tree.children) != 4:
                raise DSLValidationError("if expects 3 arguments: condition, if_true, if_false")

            # Evaluate condition
            cond_tree = tree.children[1]
            cond = self.visit(cond_tree)
            cond = self._resolve_identifier(cond)

            # Validate that the condition can be a BOOL.
            cond_types = _infer_types(cond)
            if "UNKNOWN" not in cond_types and not (cond_types & _BOOL):
                raise DSLValidationError(f"Argument 1 of (if ...) must be BOOL, got {_human_type_name(cond_types)}")

            # Evaluate appropriate branch based on condition
            if isinstance(cond, bool):
                branch_tree = tree.children[2] if cond else tree.children[3]
                return self.visit(branch_tree)
            else:
                # Condition is symbolic or not boolean - preserve expression
                if_true = self.visit(tree.children[2])
                if_false = self.visit(tree.children[3])
                return Preserved([head, cond, if_true, if_false])

        # Handle let special form — sequential bindings, then body.
        if isinstance(head, str) and head == "let":
            return self._evaluate_let(tree)

        # Handle cond special form — first matching clause wins.
        if isinstance(head, str) and head == "cond":
            return self._evaluate_cond(tree)

        # Regular function call - evaluate all arguments
        args = [self.visit(child) for child in tree.children[1:]]

        # Resolve all arguments (they might be identifiers)
        args = [self._resolve_identifier(arg) for arg in args]

        # If head is a Lambda, call it
        if isinstance(head, Lambda):
            return self._call_lambda(head, args)

        # If head is a string (identifier), check if it's a function call
        if isinstance(head, str):
            if head in _SIGNATURES:
                # Validate against the centralized signature table before dispatching.
                self._validate_call(head, args)
            # Handle built-in functions
            if head == "car":
                return self._evaluate_car(head, args)
            elif head == "cdr":
                return self._evaluate_cdr(head, args)
            elif head == "get":
                return self._evaluate_get(head, args)
            elif head == "or-default":
                return self._evaluate_or_default(head, args)
            elif head == "len":
                return self._evaluate_len(head, args)
            elif head == "empty?":
                return self._evaluate_empty(head, args)
            elif head == "member?":
                return self._evaluate_member(head, args)
            elif head == "list":
                return self._evaluate_list(head, args)
            elif head == "cons":
                return self._evaluate_cons(head, args)
            elif head == "append":
                return self._evaluate_append(head, args)
            elif head == "map":
                return self._evaluate_map(head, args)
            elif head == "map-indexed":
                return self._evaluate_map_indexed(head, args)
            elif head == "filter":
                return self._evaluate_filter(head, args)
            elif head == "reduce":
                return self._evaluate_reduce(head, args)
            elif head == "sort-by":
                return self._evaluate_sort_by(head, args)
            elif head == "range":
                return self._evaluate_range(head, args)
            elif head == "max":
                return self._evaluate_max(head, args)
            elif head == "min":
                return self._evaluate_min(head, args)
            elif head == "max-by":
                return self._evaluate_max_by(head, args)
            elif head == "min-by":
                return self._evaluate_min_by(head, args)
            # Handle arithmetic and comparison operators
            elif head in {"+", "-", "*", "/", ">", "<", ">=", "<="}:
                return self._evaluate_arith_or_cmp(head, args)
            elif head == "==":
                return self._evaluate_equality(head, args)
            elif head in {"or", "and", "not"}:
                return self._evaluate_logical_op(head, args)
            # Handle team/match operations
            elif head == "wins":
                return self._evaluate_wins(head, args)
            elif head == "losses":
                return self._evaluate_losses(head, args)
            elif head == "winner":
                return self._evaluate_winner(head, args)
            elif head == "loser":
                return self._evaluate_loser(head, args)
            elif head == "points-won":
                return self._evaluate_points_won(head, args)
            elif head == "points-lost":
                return self._evaluate_points_lost(head, args)
            elif head == "won?":
                return self._evaluate_won(head, args)
            elif head == "is-skipped":
                return self._evaluate_is_skipped(head, args)
            else:
                # Unknown function name — try did-you-mean from the builtin set + bound env names.
                known = set(self.BUILTINS) | set(self.env.keys())
                suggestion = _suggest_function(head, known)
                if suggestion:
                    raise DSLValidationError(f"Unknown function '{head}'. Did you mean '{suggestion}'?")
                raise DSLValidationError(f"Unknown function '{head}'.")

        # If head is not a string or lambda, it's an error
        raise DSLValidationError(f"Cannot call {type(head).__name__} as a function")

    # Lambda evaluation
    def _call_lambda(self, lambda_func, args):
        """Call a lambda function with arguments."""
        if not isinstance(lambda_func, Lambda):
            raise DSLValidationError(f"Expected Lambda, got {type(lambda_func).__name__}")

        # Create new environment with closure
        new_env = lambda_func.env.copy()

        # Validate argument count
        if len(args) != len(lambda_func.params):
            raise DSLValidationError(f"Lambda expects {len(lambda_func.params)} argument(s), got {len(args)}")

        # Bind arguments to parameters
        for param, arg in zip(lambda_func.params, args):
            new_env[param] = arg

        # Evaluate body in new environment by visiting the Tree node
        # Create a new simplifier with the new environment
        simplifier = Simplifier(lambda_func.parse_team_literal, lambda_func.parse_match_literal, env=new_env)
        # Visit the Tree node with the new environment
        result = simplifier.visit(lambda_func.body_tree)

        # Special case: if body is a single-element list like (x) containing just an identifier,
        # unwrap it to return the identifier's value
        # This handles cases like ((lambda (x) x) 42) where body is just x
        if isinstance(result, list) and len(result) == 1:
            single_elem = result[0]
            # If it's a string identifier that's in the environment, return its value
            if isinstance(single_elem, str) and single_elem in new_env:
                return new_env[single_elem]
            # If it's a string that's not a builtin and not in env, it's an unresolved identifier
            # Keep it as a list for now (will error later if used as function)
            # But if it's resolved to a value, we can unwrap
            if not isinstance(single_elem, str):
                # It's already a value, unwrap it
                return single_elem

        return result

    def _evaluate_value(self, value):
        """Evaluate a single value, resolving identifiers from environment."""
        if isinstance(value, str):
            # Check if it's an identifier in the environment
            if value in self.env:
                return self.env[value]
            elif value in self.BUILTINS:
                return value  # Built-in function name
            else:
                # Identifier not found - return as string for now
                return value
        elif isinstance(value, list):
            # Could be a function call, preserved expression, or data list
            if not value:
                return List()
            # Check if it's a preserved expression (starts with function name)
            if self._is_preserved_expression(value):
                return value  # Return as-is, it's already preserved
            # Check if it looks like a function call (first element is a string or Lambda)
            # vs a data list (first element is not a string/Lambda)
            if len(value) > 0:
                first = value[0]
                # If first element is a string (function name) or Lambda, it's a function call
                if isinstance(first, (str, Lambda)):
                    return self.list(value)
                # Otherwise, it's a data list - return as-is
                return value
            return value
        else:
            # Literal value (int, bool, Nil, Team, Match, Lambda, etc.)
            return value

    def _evaluate_lambda(self, head, params_expr, body_tree):
        """Evaluate (lambda (params) body) expression.

        Args:
            head: The function name ("lambda")
            params_expr: List of parameter name strings (not evaluated)
            body_tree: The body as a Lark Tree node (unevaluated)
        """
        # Parse parameters - params_expr should be a list of strings (parameter names)
        if not isinstance(params_expr, list):
            raise DSLValidationError("Lambda parameters must be a list")

        # Check for variadic (*args) syntax
        if len(params_expr) == 1 and isinstance(params_expr[0], str) and params_expr[0].startswith("*"):
            # Variadic parameter
            param_name = params_expr[0][1:]  # Remove *
            params = [param_name]
        else:
            # Fixed parameters - extract parameter names
            params = []
            for p in params_expr:
                # Parameter names should be strings (identifiers)
                if isinstance(p, str):
                    params.append(p)
                else:
                    # If it was evaluated to something else, that's an error
                    raise DSLValidationError(f"Lambda parameter names must be identifiers, got {type(p).__name__}")

        # Create lambda closure with current environment, storing the Tree node
        lambda_obj = Lambda(
            params,
            body_tree,
            self.env,
            self.parse_team_literal,
            self.parse_match_literal,
        )
        return lambda_obj

    # Helper methods for evaluation

    def _evaluate_arith_or_cmp(self, op, args):
        """Evaluate arithmetic and ordered-comparison binary operators."""
        if self._has_unresolved(args):
            return Preserved([op, *args])
        a, b = args
        if op == "+":
            return a + b
        elif op == "-":
            return a - b
        elif op == "*":
            return a * b
        elif op == "/":
            if b == 0:
                raise DSLValidationError("Division by zero")
            return a // b
        elif op == ">":
            return a > b
        elif op == "<":
            return a < b
        elif op == ">=":
            return a >= b
        elif op == "<=":
            return a <= b

    def _definite_equal(self, a, b):
        """Equality used by member?/won?/stat filters.

        Returns True, False, or None when the comparison is still symbolic.
        """
        if isinstance(a, (SymbolicTeam, SymbolicMatch)) or isinstance(b, (SymbolicTeam, SymbolicMatch)):
            return None
        if self._is_preserved_expression(a) or self._is_preserved_expression(b):
            return None
        if self._is_unresolved_identifier(a) or self._is_unresolved_identifier(b):
            return None
        # bool before int (bool is a subclass of int)
        if isinstance(a, bool) and isinstance(b, bool):
            return a == b
        if type(a) is int and type(b) is int:
            return a == b
        if isinstance(a, Nil) and isinstance(b, Nil):
            return True
        if isinstance(a, (int, bool, Nil)) and isinstance(b, (int, bool, Nil)):
            # Mixed nil/int/bool that aren't both nil — concrete inequality when both concrete.
            if isinstance(a, Nil) or isinstance(b, Nil):
                return False
            if isinstance(a, bool) or isinstance(b, bool):
                return None  # don't coerce bool vs int
            return a == b
        if isinstance(a, Team) and isinstance(b, Team):
            return a.obj.id == b.obj.id
        if isinstance(a, Match) and isinstance(b, Match):
            return a.obj.uuid == b.obj.uuid
        # Different concrete types — not equal (member? treats as miss, not preserve).
        return False

    def _evaluate_equality(self, op, args):
        """Evaluate (== ANY ANY) — preserves on unresolved, else delegates to value equality."""
        if self._has_unresolved(args):
            return Preserved([op, *args])
        a, b = args
        if isinstance(a, (int, bool, Nil)) and isinstance(b, (int, bool, Nil)):
            return a == b
        if isinstance(a, Team) and isinstance(b, Team):
            return a.obj.id == b.obj.id
        if isinstance(a, Match) and isinstance(b, Match):
            return a.obj.uuid == b.obj.uuid
        # Different concrete types (e.g. INT vs TEAM) — preserve rather than report False.
        return Preserved([op, a, b])

    def _evaluate_logical_op(self, op, args):
        """Evaluate logical operations. `and`/`or` are variadic (evaluate-all)."""
        if op == "not":
            if self._has_unresolved(args):
                return Preserved([op, *args])
            (a,) = args
            if not isinstance(a, bool):
                # Allow only concrete bools; otherwise preserve.
                return Preserved([op, *args])
            return not a

        # Variadic and/or — evaluate-all; preserve if any arg unresolved and no
        # decisive concrete result would still require that arg... Spec says
        # evaluate-all and preserve if unresolved remains material. Simpler rule:
        # if any unresolved → Preserved; else fold.
        if self._has_unresolved(args):
            return Preserved([op, *args])

        if op == "and":
            if not args:
                return True
            for a in args:
                if not isinstance(a, bool):
                    return Preserved([op, *args])
                if a is False:
                    return False
            return True

        if op == "or":
            if not args:
                return False
            for a in args:
                if not isinstance(a, bool):
                    return Preserved([op, *args])
                if a is True:
                    return True
            return False

    def _unwrap_tree(self, tree):
        """Unwrap expression/atom wrappers down to the meaningful node."""
        while isinstance(tree, Tree) and tree.data in {"expression", "atom"} and tree.children:
            tree = tree.children[0]
        return tree

    def _extract_identifier_name(self, tree):
        """Get a raw identifier name from a parse tree without env resolution."""
        tree = self._unwrap_tree(tree)
        if isinstance(tree, Tree) and tree.data == "identifier_atom" and tree.children:
            return tree.children[0].value
        raise DSLValidationError("let binding name must be an identifier")

    def _evaluate_let(self, tree):
        """Evaluate (let ((name expr) ...) BODY) with sequential bindings."""
        if len(tree.children) != 3:
            raise DSLValidationError("let expects 2 arguments: bindings and body")

        bindings_node = self._unwrap_tree(tree.children[1])
        body_tree = tree.children[2]

        if not isinstance(bindings_node, Tree) or bindings_node.data != "list":
            raise DSLValidationError("let bindings must be a list of (name expr) pairs")

        new_env = self.env.copy()
        seen: set[str] = set()
        for binding in bindings_node.children:
            b = self._unwrap_tree(binding)
            if not isinstance(b, Tree) or b.data != "list" or len(b.children) != 2:
                raise DSLValidationError("let binding must be (name expr)")
            name = self._extract_identifier_name(b.children[0])
            if name in seen:
                raise DSLValidationError(f"Duplicate let binding name '{name}'")
            if name in self.BUILTINS:
                raise DSLValidationError(f"Cannot bind builtin name '{name}' in let")
            seen.add(name)
            # Evaluate binding expr in env so far (sequential / let* semantics).
            simplifier = Simplifier(self.parse_team_literal, self.parse_match_literal, env=new_env)
            val = simplifier.visit(b.children[1])
            new_env[name] = val

        body_simplifier = Simplifier(self.parse_team_literal, self.parse_match_literal, env=new_env)
        return body_simplifier.visit(body_tree)

    def _evaluate_cond(self, tree):
        """Evaluate (cond (PRED EXPR) ...) — first true pred wins; else nil."""
        clause_nodes = list(tree.children[1:])
        for idx, clause_tree in enumerate(clause_nodes):
            clause = self._unwrap_tree(clause_tree)
            if not isinstance(clause, Tree) or clause.data != "list" or len(clause.children) != 2:
                raise DSLValidationError("cond clause must be (pred expr)")
            pred_tree, expr_tree = clause.children
            pred = self.visit(pred_tree)
            pred = self._resolve_identifier(pred)
            pred_types = _infer_types(pred)
            if "UNKNOWN" not in pred_types and not (pred_types & _BOOL):
                raise DSLValidationError(f"cond predicate must be BOOL, got {_human_type_name(pred_types)}")
            if isinstance(pred, bool):
                if pred:
                    return self.visit(expr_tree)
                continue
            # Symbolic predicate — preserve entire remaining cond.
            clauses_preserved = []
            expr_val = self.visit(expr_tree)
            clauses_preserved.append(List([pred, expr_val]))
            for rest in clause_nodes[idx + 1 :]:
                r = self._unwrap_tree(rest)
                if not isinstance(r, Tree) or r.data != "list" or len(r.children) != 2:
                    raise DSLValidationError("cond clause must be (pred expr)")
                p = self._resolve_identifier(self.visit(r.children[0]))
                e = self.visit(r.children[1])
                clauses_preserved.append(List([p, e]))
            return Preserved(["cond", *clauses_preserved])
        return NIL

    def _require_match_list(self, head, matchlist):
        """Validate MATCHLIST arg; return list of Match or raise / signal preserve."""
        if self._is_preserved_expression(matchlist):
            return None  # caller should preserve
        if not isinstance(matchlist, list):
            raise DSLValidationError(
                f"Argument 2 of ({head} ...) must be LIST, got {_human_type_name(_infer_types(matchlist))}"
            )
        for m in matchlist:
            if isinstance(m, SymbolicMatch) or self._is_preserved_expression(m):
                return None
            if not isinstance(m, Match):
                raise DSLValidationError(f"({head} ...) match list elements must be matches")
        return matchlist

    def _count_outcomes_over_matches(self, head, team, matchlist, side: str):
        """Count wins or losses for team over matchlist. side is 'winner' or 'loser'."""
        count = 0
        for m in matchlist:
            outcome = m.winner() if side == "winner" else m.loser()
            eq = self._definite_equal(outcome, team)
            if eq is None:
                return Preserved([head, team, matchlist])
            if eq:
                count += 1
        return count

    def _sum_points_over_matches(self, head, team, matchlist, won: bool):
        total = 0
        for m in matchlist:
            if isinstance(m, SymbolicMatch) or self._is_preserved_expression(m):
                return Preserved([head, team, matchlist])
            if not isinstance(m, Match):
                raise DSLValidationError(f"({head} ...) match list elements must be matches")
            total += team.points_won(m) if won else team.points_lost(m)
        return total

    def _evaluate_wins(self, head, args):
        """Evaluate (wins TEAM) or (wins TEAM MATCHLIST)."""
        if self._has_unresolved(args[:1]):
            return Preserved([head, *args])
        team = args[0]
        if len(args) == 1:
            if self._has_unresolved(args):
                return Preserved([head, *args])
            return team.wins()
        matchlist = self._require_match_list(head, args[1])
        if matchlist is None:
            return Preserved([head, *args])
        return self._count_outcomes_over_matches(head, team, matchlist, "winner")

    def _evaluate_losses(self, head, args):
        """Evaluate (losses TEAM) or (losses TEAM MATCHLIST)."""
        if self._has_unresolved(args[:1]):
            return Preserved([head, *args])
        team = args[0]
        if len(args) == 1:
            if self._has_unresolved(args):
                return Preserved([head, *args])
            return team.losses()
        matchlist = self._require_match_list(head, args[1])
        if matchlist is None:
            return Preserved([head, *args])
        return self._count_outcomes_over_matches(head, team, matchlist, "loser")

    def _evaluate_winner(self, head, args):
        """Evaluate (winner MATCH) expression."""
        if self._has_unresolved(args):
            return Preserved([head, *args])
        match = args[0]
        return match.winner()

    def _evaluate_loser(self, head, args):
        """Evaluate (loser MATCH) expression."""
        if self._has_unresolved(args):
            return Preserved([head, *args])
        match = args[0]
        return match.loser()

    def _evaluate_points_won(self, head, args):
        """Evaluate (points-won TEAM) / (points-won TEAM MATCH|MATCHLIST)."""
        if self._has_unresolved(args[:1]):
            return Preserved([head, *args])
        team = args[0]
        if len(args) == 1:
            return team.points_won()
        second = args[1]
        if isinstance(second, list) or self._is_preserved_expression(second):
            if self._is_preserved_expression(second):
                return Preserved([head, *args])
            return self._sum_points_over_matches(head, team, second, won=True)
        if self._has_unresolved(args):
            return Preserved([head, *args])
        if not isinstance(second, Match):
            raise DSLValidationError(
                f"Argument 2 of (points-won ...) must be MATCH or LIST, got {_human_type_name(_infer_types(second))}"
            )
        return team.points_won(second)

    def _evaluate_points_lost(self, head, args):
        """Evaluate (points-lost TEAM) / (points-lost TEAM MATCH|MATCHLIST)."""
        if self._has_unresolved(args[:1]):
            return Preserved([head, *args])
        team = args[0]
        if len(args) == 1:
            return team.points_lost()
        second = args[1]
        if isinstance(second, list) or self._is_preserved_expression(second):
            if self._is_preserved_expression(second):
                return Preserved([head, *args])
            return self._sum_points_over_matches(head, team, second, won=False)
        if self._has_unresolved(args):
            return Preserved([head, *args])
        if not isinstance(second, Match):
            raise DSLValidationError(
                f"Argument 2 of (points-lost ...) must be MATCH or LIST, got {_human_type_name(_infer_types(second))}"
            )
        return team.points_lost(second)

    def _evaluate_won(self, head, args):
        """Evaluate (won? TEAM MATCH) -> BOOL."""
        if self._has_unresolved(args):
            return Preserved([head, *args])
        team, match = args
        outcome = match.winner()
        eq = self._definite_equal(outcome, team)
        if eq is None:
            return Preserved([head, team, match])
        return eq

    def _evaluate_is_skipped(self, head, args):
        """Evaluate (is-skipped MATCH) expression.

        Returns True if match status is SKIPPED, False if IN_PROGRESS or COMPLETED,
        otherwise stays symbolic (NOT_STARTED, TIME_FINALIZED, READY_TO_START).
        """
        from app.domain.enums import MatchStatus

        if self._has_unresolved(args):
            return Preserved([head, *args])
        match = args[0]

        status = getattr(match.obj, "status", None)
        if status is None:
            return Preserved([head, match])  # Stay symbolic

        # Normalize to string for comparison (DB may store as enum or string)
        status_str = str(status) if status else None
        if status_str == MatchStatus.SKIPPED:
            return True
        if status_str in (MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED):
            return False
        # NOT_STARTED, TIME_FINALIZED, READY_TO_START: stay symbolic
        return Preserved([head, match])

    def _evaluate_list(self, head, args):
        """Evaluate (list *ARGS) -> LIST."""
        if self._has_unresolved(args):
            return Preserved([head, *args])
        return List(args)

    def _evaluate_cons(self, head, args):
        """Evaluate (cons X LIST) -> LIST (prepend)."""
        x, lst = args
        if self._is_preserved_expression(lst) or self._has_unresolved([x]):
            return Preserved([head, *args])
        if not isinstance(lst, list):
            raise DSLValidationError("cons expects a list as second argument")
        return List([x, *lst])

    def _evaluate_append(self, head, args):
        """Evaluate (append LIST1 LIST2) -> LIST."""
        a, b = args
        if self._is_preserved_expression(a) or self._is_preserved_expression(b):
            return Preserved([head, *args])
        if not isinstance(a, list) or not isinstance(b, list):
            raise DSLValidationError("append expects two lists")
        return List([*a, *b])

    def _evaluate_car(self, head, args):
        """Evaluate (car LIST) expression."""
        lst = args[0]
        if self._is_preserved_expression(lst):
            return Preserved([head, lst])
        if not lst:
            raise DSLValidationError("Cannot take car of empty list")
        return lst[0]

    def _evaluate_cdr(self, head, args):
        """Evaluate (cdr LIST) expression."""
        lst = args[0]
        if self._is_preserved_expression(lst):
            return Preserved([head, lst])
        if not lst:
            raise DSLValidationError("Cannot take cdr of empty list")
        return List(lst[1:])

    def _evaluate_get(self, head, args):
        """Evaluate (get INDEX LIST) expression."""
        index, lst = args
        if self._is_preserved_expression(index) or self._is_preserved_expression(lst):
            return Preserved([head, index, lst])
        if 0 <= index < len(lst):
            return lst[index]
        # Out of bounds — NIL is a valid result.
        return Nil()

    def _evaluate_or_default(self, head, args):
        """Evaluate (or-default VAL DEFAULT) expression."""
        if self._has_unresolved(args):
            return Preserved([head, *args])
        val, default = args
        if not isinstance(val, Nil):
            return val
        return default

    def _evaluate_len(self, head, args):
        """Evaluate (len LIST) expression."""
        lst = args[0]
        if self._is_preserved_expression(lst):
            return Preserved([head, lst])
        return len(lst)

    def _evaluate_empty(self, head, args):
        """Evaluate (empty? LIST) -> BOOL."""
        lst = args[0]
        if self._is_preserved_expression(lst):
            return Preserved([head, lst])
        return len(lst) == 0

    def _evaluate_member(self, head, args):
        """Evaluate (member? X LIST) -> BOOL.

        True if any element definitely equals X; Preserved if no hit but some
        comparison was symbolic; False if all concrete misses.
        """
        x, lst = args
        if self._is_preserved_expression(lst):
            return Preserved([head, *args])
        if not isinstance(lst, list):
            raise DSLValidationError("member? expects a list as second argument")
        saw_symbolic = False
        if self._has_unresolved([x]):
            saw_symbolic = True
        for e in lst:
            eq = self._definite_equal(x, e)
            if eq is True:
                return True
            if eq is None:
                saw_symbolic = True
        if saw_symbolic:
            return Preserved([head, *args])
        return False

    def _evaluate_max(self, head, args):
        """Evaluate (max LIST) expression."""
        lst = args[0]
        if self._is_preserved_expression(lst):
            return Preserved([head, lst])
        if not lst:
            raise DSLValidationError("Cannot find max of empty list")
        if not all(type(x) is int for x in lst):
            raise DSLValidationError("max requires a list of integers")
        return max(lst)

    def _evaluate_min(self, head, args):
        """Evaluate (min LIST) expression."""
        lst = args[0]
        if self._is_preserved_expression(lst):
            return Preserved([head, lst])
        if not lst:
            raise DSLValidationError("Cannot find min of empty list")
        if not all(type(x) is int for x in lst):
            raise DSLValidationError("min requires a list of integers")
        return min(lst)

    def _evaluate_map(self, head, args):
        """Evaluate (map LIST FUNC) expression."""
        lst, func = args
        if self._is_preserved_expression(lst):
            return Preserved([head, lst, func])
        result = List()
        for item in lst:
            result.append(self._call_lambda(func, [item]))
        return result

    def _evaluate_map_indexed(self, head, args):
        """Evaluate (map-indexed LIST FUNC) where FUNC is (lambda (i x) ...)."""
        lst, func = args
        if self._is_preserved_expression(lst):
            return Preserved([head, lst, func])
        result = List()
        for i, item in enumerate(lst):
            result.append(self._call_lambda(func, [i, item]))
        return result

    def _evaluate_filter(self, head, args):
        """Evaluate (filter LIST PREDFN) -> LIST."""
        lst, func = args
        if self._is_preserved_expression(lst):
            return Preserved([head, lst, func])
        result = List()
        for item in lst:
            pred = self._call_lambda(func, [item])
            if self._is_preserved_expression(pred) or self._has_unresolved([pred]):
                return Preserved([head, lst, func])
            if not isinstance(pred, bool):
                raise DSLValidationError("filter predicate must return a boolean")
            if pred:
                result.append(item)
        return result

    def _evaluate_reduce(self, head, args):
        """Evaluate (reduce LIST FUNC) or (reduce LIST INIT FUNC)."""
        if len(args) == 2:
            lst, func = args
            init = None
            has_init = False
        else:
            lst, init, func = args
            has_init = True

        if self._is_preserved_expression(lst):
            return Preserved([head, *args])
        if has_init and self._has_unresolved([init]):
            return Preserved([head, *args])

        if has_init:
            accumulator = init
            items = lst
        else:
            if not lst:
                raise DSLValidationError("Cannot reduce empty list")
            accumulator = lst[0]
            items = lst[1:]

        for item in items:
            accumulator = self._call_lambda(func, [accumulator, item])
        return accumulator

    def _evaluate_sort_by(self, head, args):
        """Evaluate (sort-by LIST *KEYFNS) — multi-key descending, stable."""
        lst = args[0]
        keyfns = args[1:]
        if self._is_preserved_expression(lst):
            return Preserved([head, *args])
        for item in lst:
            if isinstance(item, (SymbolicTeam, SymbolicMatch)):
                return Preserved([head, *args])

        decorated = []
        for i, item in enumerate(lst):
            keys = []
            for kf in keyfns:
                val = self._call_lambda(kf, [item])
                if self._is_preserved_expression(val) or self._has_unresolved([val]):
                    return Preserved([head, *args])
                if not isinstance(val, int) or isinstance(val, bool):
                    raise DSLValidationError("sort-by key function must return an integer")
                keys.append(val)
            # higher keys first; lower original index among ties (stable)
            decorated.append((keys, i, item))

        decorated.sort(key=lambda row: ([-k for k in row[0]], row[1]))
        return List(row[2] for row in decorated)

    def _evaluate_range(self, head, args):
        """Evaluate (range N) -> (0 1 ... N-1)."""
        if self._has_unresolved(args):
            return Preserved([head, *args])
        (n,) = args
        if not isinstance(n, int) or isinstance(n, bool):
            raise DSLValidationError("range expects an integer")
        if n > RANGE_MAX:
            raise DSLValidationError(f"range too large (max {RANGE_MAX})")
        if n <= 0:
            return List()
        return List(range(n))

    def _evaluate_max_by(self, head, args):
        """Evaluate (max-by LIST FUNC) expression."""
        lst, func = args
        if self._is_preserved_expression(lst):
            return Preserved([head, lst, func])
        # Preserve if list contains symbolic elements that can't be ordered yet.
        for item in lst:
            if isinstance(item, (SymbolicTeam, SymbolicMatch)):
                return Preserved([head, lst, func])
        if not lst:
            raise DSLValidationError("Cannot find max_by of empty list")
        max_val = None
        max_elem = None
        for item in lst:
            val = self._call_lambda(func, [item])
            if not isinstance(val, int) or isinstance(val, bool):
                raise DSLValidationError("max_by function must return an integer")
            if max_val is None or val > max_val:
                max_val = val
                max_elem = item
        return max_elem

    def _evaluate_min_by(self, head, args):
        """Evaluate (min-by LIST FUNC) expression."""
        lst, func = args
        if self._is_preserved_expression(lst):
            return Preserved([head, lst, func])
        for item in lst:
            if isinstance(item, (SymbolicTeam, SymbolicMatch)):
                return Preserved([head, lst, func])
        if not lst:
            raise DSLValidationError("Cannot find min_by of empty list")
        min_val = None
        min_elem = None
        for item in lst:
            val = self._call_lambda(func, [item])
            if not isinstance(val, int) or isinstance(val, bool):
                raise DSLValidationError("min_by function must return an integer")
            if min_val is None or val < min_val:
                min_val = val
                min_elem = item
        return min_elem


# --------------------------------------------------------------------------
# Script variables (tournament-scoped ASS variables)
# --------------------------------------------------------------------------

# Identifier names that can never be used as script-variable names: builtin
# functions, special forms, and the keyword literals.
RESERVED_IDENTIFIERS = frozenset(Simplifier.BUILTINS) | {"true", "false", "nil"}

# Mirrors the IDENTIFIER terminal in grammar.lark.
_IDENTIFIER_RE = re.compile(
    r"-(?![0-9])$"
    r"|-[a-zA-Z_+*/=><!&][a-zA-Z0-9_\-+*/=><!&]*$"
    r"|[a-zA-Z_+*/=><!&][a-zA-Z0-9_\-+*/=><!&]*$"
)

_LARK_PARSER: Lark | None = None


def _get_lark() -> Lark:
    """Shared Lark instance for utility parsing (identifier extraction, env building)."""
    global _LARK_PARSER
    if _LARK_PARSER is None:
        import os

        grammar_path = os.path.join(os.path.dirname(__file__), "grammar.lark")
        with open(grammar_path, "r") as g:
            _LARK_PARSER = Lark(g, parser="lalr")
    return _LARK_PARSER


def is_valid_identifier(name: str) -> bool:
    """True when `name` matches the ASS IDENTIFIER token exactly."""
    return bool(name) and _IDENTIFIER_RE.fullmatch(name) is not None


def _extract_identifier_names(tree) -> set[str]:
    """Collect every identifier_atom name in a parse tree (minus keyword literals)."""
    names: set[str] = set()
    if not isinstance(tree, Tree):
        return names
    if tree.data == "identifier_atom" and tree.children:
        name = tree.children[0].value
        if name not in ("true", "false", "nil"):
            names.add(name)
        return names
    for child in tree.children:
        names |= _extract_identifier_names(child)
    return names


def extract_variable_references(text: str) -> set[str]:
    """Names of identifiers in `text` that could reference script variables.

    Builtins and keyword literals are excluded. Lambda parameter names are NOT
    excluded — a shadowed name is conservatively treated as a reference (this
    only matters for the write-time cycle check, where it errs on rejection).
    Returns an empty set when the expression doesn't parse; the caller
    validates parseability separately.
    """
    try:
        tree = _get_lark().parse((text or "").strip())
    except Exception:
        return set()
    return _extract_identifier_names(tree) - RESERVED_IDENTIFIERS


def build_variable_env(event: str, parse_team, parse_match) -> dict:
    """Evaluate this tournament's script variables into an interpreter env.

    Variables may reference other variables, so they're evaluated eagerly in
    topological order (Kahn). Variables that don't parse, participate in a
    reference cycle, or fail evaluation are simply left unbound — using them
    then behaves like any unknown identifier. Write-time validation is what
    rejects cycles/bad expressions; this function just needs to never blow up.
    """
    from app.models import ScriptVariable

    rows = ScriptVariable.query.filter_by(event=event).all()
    if not rows:
        return {}
    lark = _get_lark()
    trees: dict[str, Tree] = {}
    deps: dict[str, set[str]] = {}
    for row in rows:
        expr = (row.expression or "").strip()
        if not expr:
            continue
        try:
            tree = lark.parse(expr)
        except Exception:
            continue
        trees[row.name] = tree
        deps[row.name] = _extract_identifier_names(tree) - RESERVED_IDENTIFIERS

    # Kahn's algorithm over references to other *defined* variables. Nodes in a
    # cycle (including self-loops) never reach in-degree 0 and stay unbound.
    indegree: dict[str, int] = {}
    dependents: dict[str, set[str]] = {}
    for name in trees:
        refs = {d for d in deps[name] if d in trees}
        indegree[name] = len(refs)
        for d in refs:
            dependents.setdefault(d, set()).add(name)
    queue = [n for n, d in indegree.items() if d == 0]
    env: dict = {}
    while queue:
        name = queue.pop()
        try:
            env[name] = Simplifier(parse_team, parse_match, env=env).visit(trees[name])
        except Exception:
            # Leave unbound; downstream dependents still get evaluated (their
            # reference to this name then stays an unresolved identifier).
            pass
        for m in dependents.get(name, ()):
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)
    return env


def _collect_literals(text: str):
    """Pull out raw team and match literals for a quick reference-existence check.

    Returns (team_literals, match_literals) — lists of (raw_inside, position) tuples,
    skipping things that aren't directly resolvable (MatchName::winner, tag::Foo).
    """
    teams: list[tuple[str, int]] = []
    matches: list[tuple[str, int]] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "[":
            j = text.find("]", i + 1)
            if j == -1:
                break
            inner = text[i + 1 : j].strip()
            teams.append((inner, i))
            i = j + 1
            continue
        if c == "{":
            j = text.find("}", i + 1)
            if j == -1:
                break
            inner = text[i + 1 : j].strip()
            matches.append((inner, i))
            i = j + 1
            continue
        i += 1
    return teams, matches


def _check_references(text: str, event: str) -> list[str]:
    """Return a list of warnings about unresolvable team/match/tag references.

    A `[name]` literal must resolve to:
      - a team id, or
      - a `tag::TagName` for a Tag that exists in this event, or
      - `MatchName::winner` / `MatchName::loser` for a match that exists.
    A `{name}` literal must name a match in this event.
    Anything that doesn't match these rules gets a warning; symbolic references
    that legitimately can't be resolved yet (e.g. an unset tag's team) are not
    warned about here, only typoed names are.
    """
    warnings: list[str] = []
    teams, matches = _collect_literals(text)
    known_match_names: list[str] | None = None
    known_tag_names: list[str] | None = None
    for inner, pos in teams:
        if not inner:
            continue
        if "::" in inner:
            head, _, tail = inner.partition("::")
            if head == "tag":
                if not Tag.query.filter_by(name=tail, event=event).first():
                    if known_tag_names is None:
                        known_tag_names = [t.name for t in Tag.query.filter_by(event=event).all()]
                    suggestion = _suggest_function(tail, known_tag_names)
                    hint = f" Did you mean 'tag::{suggestion}'?" if suggestion else ""
                    warnings.append(f"Unknown tag '[tag::{tail}]' at {_format_position(text, pos)}.{hint}")
                continue
            if tail in ("winner", "loser"):
                if not MatchDB.query.filter_by(name=head, event=event).first():
                    if known_match_names is None:
                        known_match_names = [m.name for m in MatchDB.query.filter_by(event=event).all()]
                    suggestion = _suggest_function(head, known_match_names)
                    hint = f" Did you mean '{suggestion}::{tail}'?" if suggestion else ""
                    warnings.append(f"Unknown match '[{head}::{tail}]' at {_format_position(text, pos)}.{hint}")
                continue
            warnings.append(
                f"Invalid team literal '[{inner}]' at {_format_position(text, pos)} — "
                "expected a team id, tag::TagName, or MatchName::winner/loser."
            )
            continue
        if not TeamDB.query.filter_by(id=inner).first():
            all_names = [t.id for t in TeamDB.query.all()]
            suggestion = _suggest_function(inner, all_names)
            hint = f" Did you mean '{suggestion}'?" if suggestion else ""
            warnings.append(f"Unknown team '[{inner}]' at {_format_position(text, pos)}.{hint}")
    for inner, pos in matches:
        if not inner:
            continue
        if not MatchDB.query.filter_by(name=inner, event=event).first():
            if known_match_names is None:
                known_match_names = [m.name for m in MatchDB.query.filter_by(event=event).all()]
            suggestion = _suggest_function(inner, known_match_names)
            hint = f" Did you mean '{{{suggestion}}}'?" if suggestion else ""
            warnings.append(f"Unknown match '{{{inner}}}' at {_format_position(text, pos)}.{hint}")
    return warnings


def get_parser(event: str, match_resolver=None, env=None, include_variables: bool = True):
    """
    Create a parser for the given event (tournament URL).

    If match_resolver is provided, it is used to resolve match names when
    parsing (e.g. for skip_condition). It should be a callable(name) -> Match
    or SymbolicMatch. This avoids DB reads when matches are already in memory.

    ``env`` is an optional dict of pre-bound identifiers (e.g. future
    per-tournament globals like ``teamlist`` / ``matchlist``); it takes
    precedence over same-named script variables.

    Unless ``include_variables`` is False, the tournament's script variables
    are evaluated (lazily, on the first parse) and bound into the interpreter
    environment so any expression can reference them by name.
    """
    import os

    grammar_path = os.path.join(os.path.dirname(__file__), "grammar.lark")
    with open(grammar_path, "r") as g:
        parser = Lark(g, parser="lalr")

        parse_match = match_resolver if match_resolver is not None else (lambda x: parse_match_literal(x, event))
        base_env = dict(env) if env is not None else {}

        parse_team = lambda x: parse_team_literal(x, event)  # noqa: E731

        # Script-variable env, built once per parser on first use. Building it
        # runs DB queries and evaluates every variable, so skip it for
        # parsers that never parse (static_check-only use).
        env_cache: dict = {}

        def variable_env() -> dict:
            if "env" not in env_cache:
                if include_variables:
                    try:
                        env_cache["env"] = build_variable_env(event, parse_team, parse_match)
                    except Exception:
                        env_cache["env"] = {}
                else:
                    env_cache["env"] = {}
            return env_cache["env"]

        def parse(text):
            # Cheap structural check first — gives clean position-aware errors before Lark tries.
            _check_balanced_brackets(text)
            try:
                tree = parser.parse(text)
            except UnexpectedInput as e:
                # Convert Lark's verbose dump to a one-line message with position.
                pos = getattr(e, "pos_in_stream", None)
                line = getattr(e, "line", None)
                col = getattr(e, "column", None)
                if line is not None and col is not None:
                    where = f"line {line}, col {col}"
                elif pos is not None:
                    where = _format_position(text, pos)
                else:
                    where = "an unknown position"
                got = ""
                tok = getattr(e, "token", None)
                if tok is not None and getattr(tok, "value", None):
                    got = f" near `{tok.value}`"
                raise DSLValidationError(f"Parse error at {where}{got}.") from e
            except LarkError as e:
                raise DSLValidationError(f"Parse error: {e}") from e
            interpreter = Simplifier(
                parse_team,
                parse_match,
                # Script variables first, explicit caller env on top (callers
                # binding globals shouldn't be shadowed by user variables).
                env={**variable_env(), **base_env},
            )
            return interpreter.visit(tree)

        def static_check(text):
            """Quick lint pass: balanced brackets and reference existence.

            Raises DSLValidationError on unbalanced brackets. Returns a list of
            soft warnings (typo-likely team/match names) without raising.
            """
            _check_balanced_brackets(text)
            return _check_references(text, event)

        class Parser:
            def __init__(self, parse_func, static_check_func):
                self.parse = parse_func
                self.static_check = static_check_func

        return Parser(parse, static_check)
