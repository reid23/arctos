"""``/api/v1/tournaments/<id>/schedule`` and match edits: building a schedule (``manage`` scope).

The whole schedule (script variables, tags, fields, matches, and schedule
items) round-trips as a schedule file; single matches and break groups can
also be edited one at a time. Every route needs a token with the ``manage``
scope whose user organizes the tournament. Shares
:class:`~app.services.schedule_import_export_service.ScheduleImportExportService`
and :class:`~app.services.schedule_edit_service.ScheduleEditService` with the
SPA's session routes.
"""

from __future__ import annotations

from flask import jsonify

from app.serializers.api_v1_serializer import TeamNames, match_detail_json, scope_of
from app.services.schedule_edit_service import ScheduleEditService
from app.services.schedule_import_export_service import ScheduleImportExportService
from app.utils.oauth_server import require_bearer
from app.utils.parser import check_expression
from app.utils.scheduling import recompute_scheduled_and_nominal_times, validate_match_warnings

from . import bp, error, json_object_body, result_response, with_organized_tournament


@bp.get("/tournaments/<tournament_id>/schedule/script")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def export_schedule(tournament):
    """-> ``{"script"}``: the schedule file (TOML), ready to edit and ``PUT`` back."""
    return result_response(
        ScheduleImportExportService.export_schedule(tournament.url), lambda script: {"script": script}
    )


@bp.put("/tournaments/<tournament_id>/schedule")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def import_schedule(tournament):
    """JSON ``{script}`` replaces the schedule with the file's contents -> counts and warnings.

    Everything is validated before anything changes; an invalid file changes nothing.
    """
    body = json_object_body()
    if body is None or not isinstance(body.get("script"), str):
        return error("Expected a JSON object body with a script", 400)

    def import_counts(import_result) -> dict:
        recompute_scheduled_and_nominal_times(tournament.url)
        return {
            "variables_created": import_result.variables_created,
            "variables_updated": import_result.variables_updated,
            "tags_created": import_result.tags_created,
            "tags_updated": import_result.tags_updated,
            "fields_created": import_result.fields_created,
            "fields_updated": import_result.fields_updated,
            "matches_created": import_result.matches_created,
            "matches_updated": import_result.matches_updated,
            "warnings": import_result.warnings,
        }

    return result_response(ScheduleImportExportService.import_schedule(tournament.url, body["script"]), import_counts)


@bp.post("/tournaments/<tournament_id>/schedule/validate")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def validate_expression(tournament):
    """JSON ``{expression}`` (a skip condition, tag, or variable expression) -> its value or error."""
    body = json_object_body()
    if body is None or not isinstance(body.get("expression"), str):
        return error("Expected a JSON object body with an expression", 400)
    return jsonify(check_expression(tournament.url, body["expression"]))


@bp.get("/tournaments/<tournament_id>/schedule/warnings")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def schedule_warnings(tournament):
    """Problems in the whole schedule: unknown teams, cycles, missing references, double bookings."""
    return jsonify({"warnings": validate_match_warnings(tournament.url)})


@bp.post("/tournaments/<tournament_id>/schedule/push-back")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def push_back_matches(tournament):
    """JSON ``{minutes, day, tz_offset_minutes}`` -> ``{"moved"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(ScheduleEditService.push_back_matches(tournament.url, body), lambda moved: {"moved": moved})


@bp.post("/tournaments/<tournament_id>/schedule/update-references")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_references(tournament):
    """Re-apply every finished match's result to the matches that reference it."""
    ScheduleEditService.update_all_references(tournament.url)
    return "", 204


def _match_payload(tournament, match) -> dict:
    return {"match": match_detail_json(match, TeamNames(scope_of(tournament)), [])}


@bp.post("/tournaments/<tournament_id>/matches")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_match(tournament):
    """JSON match (see ``docs/developers.md``) -> 201 ``{"match"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        ScheduleEditService.create_match(tournament.url, body), lambda match: _match_payload(tournament, match), 201
    )


@bp.patch("/tournaments/<tournament_id>/matches/<match_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_match(tournament, match_id: str):
    """JSON of the match properties to change -> ``{"match"}``. Started matches can't change."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        ScheduleEditService.update_match(tournament.url, match_id, body),
        lambda match: _match_payload(tournament, match),
    )


@bp.delete("/tournaments/<tournament_id>/matches/<match_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_match(tournament, match_id: str):
    return result_response(ScheduleEditService.delete_match(tournament.url, match_id), lambda _: {}, 204)


@bp.post("/tournaments/<tournament_id>/matches/bulk-length")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def set_match_lengths(tournament):
    """JSON ``{match_ids, length}`` -> ``{"updated", "results"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(ScheduleEditService.set_match_lengths(tournament.url, body), lambda outcome: outcome)


def _break_group_payload(rows) -> dict:
    return {
        "break_group": {
            "name": rows[0].name,
            "schedule_type": str(rows[0].schedule_type),
            "match_ids": [row.uuid for row in rows],
        }
    }


@bp.post("/tournaments/<tournament_id>/break-groups")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_break_group(tournament):
    """JSON ``{name, schedule_type, fields, length, start_time, previous_match}`` -> 201 ``{"break_group"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(ScheduleEditService.create_break_group(tournament.url, body), _break_group_payload, 201)


@bp.patch("/tournaments/<tournament_id>/break-groups/<name>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_break_group(tournament, name: str):
    """JSON ``{schedule_type, fields, length, start_time}``, each optional -> 204."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(ScheduleEditService.update_break_group(tournament.url, name, body), lambda _: {}, 204)


@bp.delete("/tournaments/<tournament_id>/break-groups/<name>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_break_group(tournament, name: str):
    return result_response(ScheduleEditService.delete_break_group(tournament.url, name), lambda _: {}, 204)
