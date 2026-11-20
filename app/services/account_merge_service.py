"""Merging one player or team account into another.

A merge moves everything that refers to the source account onto the target
account, then deletes the source and records it as an
:class:`~app.models.user.AccountMerge`, which keeps its id reserved. Both
accounts must be the same kind and real (historical placeholder teams are
merged by claiming instead, see
:mod:`~app.services.historical_claims_service`).

Rows that can't simply move are conflicts: both accounts actively registered
for the same event, league, or side competition, both playing in one match,
and similar. A merge with conflicts is refused; :meth:`AccountMergeService.preview`
lists them so the user can resolve them first. Exact duplicates (two organizer
roles for the same event, for example) are dropped instead.

Every column that stores a player or team id must be handled here. Foreign keys
are listed in :data:`PLAYER_REFERENCES` / :data:`TEAM_REFERENCES`, and a test
fails when a new foreign key to ``players.id`` or ``teams.id`` is missing.
Columns without a foreign key (``TO.user_id``, ``Match.started_by``, OAuth and
footage owners) and team ids written into schedule text are handled in
:meth:`AccountMergeService._move`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from models import (
    TO,
    AccountMerge,
    BracketLabeledTeam,
    Camera,
    HeadRef,
    HeadRefAllowList,
    Injury,
    League,
    Match,
    MatchNote,
    MatchPlayer,
    MatchReferee,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthToken,
    Player,
    PlayerRegistration,
    ScriptVariable,
    SideComp,
    SideCompEntryNumber,
    SideCompRegistration,
    Tag,
    Team,
    TeamClaimInvitation,
    TeamClaimRequest,
    TeamManager,
    TeamRegistration,
    Tournament,
    db,
)

#: Foreign keys to ``players.id``, with how the preview describes them.
PLAYER_REFERENCES = (
    (PlayerRegistration.player, "event and league registrations"),
    (SideCompRegistration.player, "side competition entries"),
    (SideCompEntryNumber.player, "side competition entry numbers"),
    (MatchPlayer.player_id, "match rosters"),
    (MatchNote.created_by, "match notes written"),
    (MatchNote.player_id, "match notes and penalties about this account"),
    (Injury.player, "injury records"),
    (HeadRef.player, "head referee assignments"),
    (HeadRefAllowList.player_id, "head referee permissions"),
    (TeamManager.player_id, "teams managed"),
)

#: Foreign keys to ``teams.id``, with how the preview describes them.
TEAM_REFERENCES = (
    (TeamRegistration.team, "event and league registrations"),
    (PlayerRegistration.team, "player registrations on the roster"),
    (Match.team1, "matches"),
    (Match.team2, "matches"),
    (MatchReferee.team_id, "refereeing assignments"),
    (Tag.team, "schedule tags"),
    (TeamManager.team_id, "managers"),
    (TeamClaimInvitation.placeholder_team, "historical team claims"),
    (TeamClaimInvitation.invited_team, "historical team claims"),
    (TeamClaimRequest.placeholder_team, "historical team claims"),
    (TeamClaimRequest.requesting_team, "historical team claims"),
)

_INACTIVE_PLAYER_REGISTRATION = (RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED)


@dataclass
class MergePreview:
    """What merging *source* into *target* would do.

    Attributes:
        source: The account that would be deleted.
        target: The account that would receive everything.
        moves: ``(description, count)`` for each kind of record that moves.
        conflicts: Human-readable problems that block the merge.
    """

    source: Player | Team
    target: Player | Team
    moves: list[tuple[str, int]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def _user_type(account: Player | Team) -> str:
    return "team" if isinstance(account, Team) else "player"


def _scope_name(event: str | None, league_id: str | None) -> str:
    if event:
        tournament = db.session.get(Tournament, event)
        return tournament.name if tournament else event
    league = db.session.get(League, league_id)
    return league.name if league else str(league_id)


def _registration_conflicts(model, column, source_id: str, target_id: str, inactive_statuses) -> list[str]:
    def active_scopes(account_id: str) -> set[tuple[str | None, str | None]]:
        rows = model.query.filter(column == account_id, model.status.notin_(inactive_statuses)).all()
        return {(row.event, row.league_id) for row in rows}

    shared = active_scopes(source_id) & active_scopes(target_id)
    return [
        f"Both accounts are registered for {_scope_name(event, league)}." for event, league in sorted(shared, key=str)
    ]


class AccountMergeService:
    @staticmethod
    def load_account(account_id: str, user_type: str) -> Result[Player | Team, ArctosError]:
        """The real (non-placeholder) player or team with *account_id*."""
        model = Team if user_type == "team" else Player
        account = db.session.get(model, account_id)
        if account is None or getattr(account, "placeholder_event", None):
            return Err(NotFoundError("Account not found"))
        return Ok(account)

    @staticmethod
    def preview(source: Player | Team, target: Player | Team) -> Result[MergePreview, ArctosError]:
        """Describe merging *source* into *target*, including any conflicts."""
        if type(source) is not type(target):
            return Err(
                ValidationError("You can only merge a player account with a player account, or a team with a team.")
            )
        if source.id == target.id:
            return Err(ValidationError("That's the account you're signed in to."))
        result = MergePreview(source=source, target=target)

        references = TEAM_REFERENCES if isinstance(source, Team) else PLAYER_REFERENCES
        counts: dict[str, int] = {}
        for column, description in references:
            count = column.class_.query.filter(column == source.id).count()
            if count:
                counts[description] = counts.get(description, 0) + count
        user_type = _user_type(source)
        organizer_roles = TO.query.filter_by(user_id=source.id, user_type=user_type).count()
        if organizer_roles:
            counts["organizer roles"] = organizer_roles
        apps = OAuthClient.query.filter_by(owner_id=source.id, owner_type=user_type).count()
        if apps:
            counts["registered developer apps"] = apps
        result.moves = list(counts.items())

        if isinstance(source, Team):
            result.conflicts += _registration_conflicts(
                TeamRegistration, TeamRegistration.team, source.id, target.id, (TeamRegistrationStatus.CANCELLED,)
            )
            result.conflicts += AccountMergeService._team_match_conflicts(source.id, target.id)
        else:
            result.conflicts += _registration_conflicts(
                PlayerRegistration, PlayerRegistration.player, source.id, target.id, _INACTIVE_PLAYER_REGISTRATION
            )
            result.conflicts += AccountMergeService._player_conflicts(source.id, target.id)
        return Ok(result)

    @staticmethod
    def _player_conflicts(source_id: str, target_id: str) -> list[str]:
        conflicts = []
        source_comps = {row.comp for row in SideCompRegistration.query.filter_by(player=source_id)}
        target_comps = {row.comp for row in SideCompRegistration.query.filter_by(player=target_id)}
        for comp_id in sorted(source_comps & target_comps):
            comp = db.session.get(SideComp, comp_id)
            conflicts.append(f"Both accounts are entered in the side competition {comp.name if comp else comp_id}.")
        source_scopes = {row.scope for row in SideCompEntryNumber.query.filter_by(player=source_id)}
        target_scopes = {row.scope for row in SideCompEntryNumber.query.filter_by(player=target_id)}
        for scope in sorted(source_scopes & target_scopes):
            conflicts.append(f"Both accounts have a side competition entry number for {scope}.")
        source_matches = {row.match_uuid for row in MatchPlayer.query.filter_by(player_id=source_id)}
        target_matches = {row.match_uuid for row in MatchPlayer.query.filter_by(player_id=target_id)}
        for match in Match.query.filter(Match.uuid.in_(source_matches & target_matches)).order_by(Match.name):
            conflicts.append(f"Both accounts played in {match.name} ({match.event}).")
        return conflicts

    @staticmethod
    def _team_match_conflicts(source_id: str, target_id: str) -> list[str]:
        """Matches where the merged team would fill two slots (play itself, or referee its own game)."""
        ids = (source_id, target_id)
        referee_slots: dict[str, list[str]] = {}
        for row in MatchReferee.query.filter(MatchReferee.team_id.in_(ids)):
            referee_slots.setdefault(row.match_uuid, []).append(row.team_id)
        candidate_uuids = set(referee_slots) | {
            match.uuid for match in Match.query.filter(Match.team1.in_(ids) | Match.team2.in_(ids))
        }
        conflicts = []
        for match in Match.query.filter(Match.uuid.in_(candidate_uuids)).order_by(Match.event, Match.name):
            slots = [match.team1, match.team2, *referee_slots.get(match.uuid, [])]
            if source_id in slots and target_id in slots:
                conflicts.append(f"Both accounts are in {match.name} ({match.event}).")
        return conflicts

    @staticmethod
    def merge(source: Player | Team, target: Player | Team) -> Result[None, ArctosError]:
        """Move everything from *source* to *target*, then delete *source*.

        Refused (nothing changes) when :meth:`preview` reports conflicts.
        """
        preview = AccountMergeService.preview(source, target).Q()
        if preview.conflicts:
            return Err(ValidationError("These accounts can't be merged yet: " + " ".join(preview.conflicts)))
        source_id, target_id, user_type = source.id, target.id, _user_type(source)
        AccountMergeService._fill_blank_profile_fields(source, target)
        db.session.flush()
        db.session.expire_all()
        AccountMergeService._move(source_id, target_id, user_type)
        db.session.add(AccountMerge(source_id=source_id, source_type=user_type, target_id=target_id))
        model = Team if user_type == "team" else Player
        model.query.filter_by(id=source_id).delete(synchronize_session=False)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def _fill_blank_profile_fields(source: Player | Team, target: Player | Team) -> None:
        """Copy profile fields the target leaves empty; move Google sign-in if the target has none."""
        for column in type(target).__table__.columns:
            if column.name in ("id", "google_id", "placeholder_event"):
                continue
            if getattr(target, column.name) in (None, "", []) and getattr(source, column.name) not in (None, "", []):
                setattr(target, column.name, getattr(source, column.name))
        if target.google_id is None and source.google_id is not None:
            google_id = source.google_id
            source.google_id = None
            db.session.flush()
            target.google_id = google_id

    @staticmethod
    def _move(source_id: str, target_id: str, user_type: str) -> None:
        def repoint(column) -> None:
            column.class_.query.filter(column == source_id).update({column.key: target_id}, synchronize_session=False)

        def drop_source_duplicates(model, owner_column, *scope_columns) -> None:
            for row in model.query.filter(owner_column == source_id).all():
                duplicate = model.query.filter(
                    owner_column == target_id, *(column == getattr(row, column.key) for column in scope_columns)
                ).first()
                if duplicate is not None:
                    db.session.delete(row)
            db.session.flush()

        if user_type == "player":
            AccountMergeService._drop_stale_registrations(
                PlayerRegistration, PlayerRegistration.player, source_id, target_id, _INACTIVE_PLAYER_REGISTRATION
            )
            drop_source_duplicates(HeadRefAllowList, HeadRefAllowList.player_id, HeadRefAllowList.event)
            drop_source_duplicates(HeadRef, HeadRef.player, HeadRef.event)
            drop_source_duplicates(TeamManager, TeamManager.player_id, TeamManager.team_id)
            for column, _description in PLAYER_REFERENCES:
                repoint(column)
        else:
            AccountMergeService._drop_stale_registrations(
                TeamRegistration, TeamRegistration.team, source_id, target_id, (TeamRegistrationStatus.CANCELLED,)
            )
            drop_source_duplicates(TeamManager, TeamManager.team_id, TeamManager.player_id)
            drop_source_duplicates(
                TeamClaimRequest, TeamClaimRequest.requesting_team, TeamClaimRequest.placeholder_team
            )
            for column, _description in TEAM_REFERENCES:
                repoint(column)
            AccountMergeService._rewrite_team_id_in_schedule_text(source_id, target_id)

        source_organizer_roles = TO.query.filter_by(user_id=source_id, user_type=user_type)
        for role in source_organizer_roles.all():
            if TO.query.filter_by(
                user_id=target_id, user_type=user_type, event=role.event, league_id=role.league_id
            ).first():
                db.session.delete(role)
        db.session.flush()
        source_organizer_roles.update({"user_id": target_id}, synchronize_session=False)
        for column in (Match.started_by, Match.finalized_by):
            repoint(column)
        Camera.query.filter_by(uploaded_by_user_id=source_id, uploaded_by_user_type=user_type).update(
            {"uploaded_by_user_id": target_id}, synchronize_session=False
        )
        OAuthClient.query.filter_by(owner_id=source_id, owner_type=user_type).update(
            {"owner_id": target_id}, synchronize_session=False
        )
        OAuthToken.query.filter_by(user_id=source_id, user_type=user_type).update(
            {"access_token_revoked": True, "refresh_token_revoked": True}, synchronize_session=False
        )
        OAuthAuthorizationCode.query.filter_by(user_id=source_id, user_type=user_type).delete(synchronize_session=False)

    @staticmethod
    def _drop_stale_registrations(model, column, source_id: str, target_id: str, inactive_statuses) -> None:
        """Where both accounts have rows for one event or league, keep only the active one.

        Both being active is a conflict, caught before this runs; if neither
        is active, the target's row stays.
        """
        source_rows = model.query.filter(column == source_id).all()
        target_rows = model.query.filter(column == target_id).all()
        for scope in {(row.event, row.league_id) for row in source_rows} & {
            (row.event, row.league_id) for row in target_rows
        }:
            in_scope = [row for row in source_rows + target_rows if (row.event, row.league_id) == scope]
            source_active = any(row.status not in inactive_statuses for row in in_scope if row in source_rows)
            stale_side = target_rows if source_active else source_rows
            for row in in_scope:
                if row in stale_side and row.status in inactive_statuses:
                    db.session.delete(row)
        db.session.flush()

    @staticmethod
    def _rewrite_team_id_in_schedule_text(source_id: str, target_id: str) -> None:
        """Team ids are also typed into match slots, bracket labels, and script expressions."""
        for column in (Match.team1_initial, Match.team2_initial):
            Match.query.filter(column == source_id).update({column.key: target_id}, synchronize_session=False)
        MatchReferee.query.filter_by(initial=source_id).update({"initial": target_id}, synchronize_session=False)
        BracketLabeledTeam.query.filter_by(team=source_id).update({"team": target_id}, synchronize_session=False)
        literal = re.compile(r"\[" + re.escape(source_id) + r"\]")
        for model, attribute in ((Tag, "expression"), (ScriptVariable, "expression"), (Match, "skip_condition")):
            column = getattr(model, attribute)
            for row in model.query.filter(column.contains(f"[{source_id}]")).all():
                setattr(row, attribute, literal.sub(f"[{target_id}]", getattr(row, attribute)))
        db.session.flush()
