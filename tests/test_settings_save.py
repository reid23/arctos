"""Settings and a selected waiver share one persistence outcome."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from app.utils import settings_save as saves
from models import League, Player, Team, TO, Tournament, db
from tests.utils import login_as, make_registrable_config

pytestmark = pytest.mark.integration


@pytest.fixture(params=["tournament", "league"])
def scope(request, client, player, tournament):
    kind = request.param
    if kind == "tournament":
        entity = tournament
        to = TO(user_id=player.id, user_type="player", event=entity.url)
        endpoint = f"/_api/{entity.url}/update-settings"
    else:
        entity = League(
            url="settings-league",
            name="Test league",
            published=True,
            registrable_config_id=make_registrable_config().id,
        )
        db.session.add(entity)
        db.session.flush()
        to = TO(user_id=player.id, user_type="player", league_id=entity.url)
        endpoint = f"/_api/leagues/{entity.url}/settings"
    db.session.add(to)
    db.session.commit()
    login_as(client, player)
    return SimpleNamespace(kind=kind, entity=entity, endpoint=endpoint, owner=player)


@pytest.fixture
def storage(app, tmp_path, monkeypatch):
    root = tmp_path / "static"
    root.mkdir()
    monkeypatch.setattr(app, "static_folder", str(root))
    return root


def settings(scope, **changes):
    if scope.kind == "tournament":
        result = {
            "name": "Updated event",
            "about": "Updated details",
            "end_date": "2030-12-31",
            "published": "on",
            "player_registration_open": "on",
            "team_reg_fee": "12.50",
            "require_waiver_signature": "on",
        }
    else:
        result = {
            "about": "Updated details",
            "published": True,
            "player_registration_open": True,
            "team_reg_fee": "12.50",
            "require_waiver_signature": True,
        }
    result.update(changes)
    return result


def post(client, scope, *, data=None, waiver=b"waiver document", filename="waiver.pdf", legacy=False):
    data = data if data is not None else settings(scope)
    if legacy:
        return client.post(scope.endpoint, **({"data": data} if scope.kind == "tournament" else {"json": data}))
    form = dict(data) if scope.kind == "tournament" else {"settings": json.dumps(data)}
    if waiver is not None:
        form["waiver"] = (BytesIO(waiver), filename)
    return client.post(scope.endpoint, data=form, content_type="multipart/form-data")


def snapshot(scope):
    db.session.expire_all()
    entity = db.session.get(Tournament if scope.kind == "tournament" else League, scope.entity.url)
    config = entity.registrable_config
    return (
        entity.about,
        entity.published,
        str(config.team_reg_fee),
        config.player_registration_open,
        config.waiver_filepath,
        config.waiver_sha256,
    )


def disk_files(storage):
    return {path.relative_to(storage).as_posix(): path.read_bytes() for path in storage.rglob("*") if path.is_file()}


def test_combined_save_commits_once_and_keeps_historical_files(client, scope, storage, monkeypatch):
    calls = []
    original_commit = db.session.commit

    def commit():
        calls.append(1)
        original_commit()

    monkeypatch.setattr(db.session, "commit", commit)
    assert post(client, scope).status_code == 200
    assert len(calls) == 1
    first = snapshot(scope)
    assert first[:4] == ("Updated details", True, "12.50", True)
    assert first[5] == hashlib.sha256(b"waiver document").hexdigest()
    assert disk_files(storage)[first[4]] == b"waiver document"
    assert post(client, scope, waiver=b"replacement document").status_code == 200
    second = snapshot(scope)
    assert second[4] != first[4] and second[5] != first[5]
    assert len(disk_files(storage)) == 2 and disk_files(storage)[first[4]] == b"waiver document"
    assert post(client, scope, waiver=b"replacement document").status_code == 200
    third = snapshot(scope)
    assert third[4] != second[4] and third[5] == second[5]
    # A retained waiver needs no reupload. Removing the requirement retains all documents.
    assert post(client, scope, waiver=None).status_code == 200
    assert snapshot(scope)[4:] == third[4:]
    clear = settings(scope)
    if scope.kind == "tournament":
        clear.pop("require_waiver_signature")
    else:
        clear["require_waiver_signature"] = False
    assert post(client, scope, data=clear, waiver=None).status_code == 200
    assert snapshot(scope)[4:] == (None, None)
    assert len(disk_files(storage)) == 3


@pytest.mark.parametrize("case", ["empty", "invalid_fee", "invalid_cap", "missing_file", "contradiction"])
def test_validation_preserves_everything(client, scope, storage, case):
    before = snapshot(scope)
    data = settings(scope)
    waiver = b"new document"
    if case == "empty":
        waiver = b""
    elif case == "invalid_fee":
        data["team_reg_fee"] = "NaN"
    elif case == "invalid_cap":
        data["max_team_size_roster"] = "abc"
    elif case == "missing_file":
        waiver = None
    elif scope.kind == "tournament":
        data.pop("require_waiver_signature")
    else:
        data["require_waiver_signature"] = False
    response = post(client, scope, data=data, waiver=waiver)
    assert response.status_code == 400, response.get_json()
    assert snapshot(scope) == before and not disk_files(storage)


def test_commit_failure_rolls_back_and_removes_only_new_file(client, scope, storage, monkeypatch):
    assert post(client, scope).status_code == 200
    before, files = snapshot(scope), disk_files(storage)

    def fail():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db.session, "commit", fail)
    response = post(client, scope, data=settings(scope, about="Must not save"), waiver=b"new")
    assert response.status_code == 500
    assert snapshot(scope) == before and disk_files(storage) == files


def test_failed_commit_acknowledgement_never_removes_committed_document(client, scope, storage, monkeypatch):
    real_commit = db.session.commit

    def uncertain():
        real_commit()
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(db.session, "commit", uncertain)
    response = post(client, scope)
    assert response.status_code == 500
    path = snapshot(scope)[4]
    assert disk_files(storage)[path] == b"waiver document"


def test_partial_write_cleanup_and_collision_safety(client, scope, storage, monkeypatch):
    assert post(client, scope).status_code == 200
    before, files = snapshot(scope), disk_files(storage)
    real_open = Path.open

    class PartialWriter:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, data):
            self.file.write(data[:2])
            raise OSError("disk full")

    def failing_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return PartialWriter(stream) if mode == "xb" else stream

    monkeypatch.setattr(Path, "open", failing_open)
    assert post(client, scope, waiver=b"new").status_code == 500
    assert snapshot(scope) == before and disk_files(storage) == files
    monkeypatch.setattr(Path, "open", real_open)
    existing_name = Path(before[4]).name.split("_", 1)[0]
    monkeypatch.setattr(saves, "uuid4", lambda: SimpleNamespace(hex=existing_name))
    assert post(client, scope, waiver=b"would overwrite").status_code == 500
    assert snapshot(scope) == before and disk_files(storage) == files


def test_request_limit_and_duplicate_file_do_not_write(client, scope, storage, app, monkeypatch):
    before = snapshot(scope)
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 128)
    assert post(client, scope, waiver=b"x" * 1024).status_code == 413
    assert snapshot(scope) == before and not disk_files(storage)
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 100 * 1024 * 1024)
    data = settings(scope) if scope.kind == "tournament" else {"settings": json.dumps(settings(scope))}
    form = MultiDict(data)
    form.add("waiver", (BytesIO(b"one"), "one.pdf"))
    form.add("waiver", (BytesIO(b"two"), "two.pdf"))
    assert client.post(scope.endpoint, data=form, content_type="multipart/form-data").status_code == 400
    assert snapshot(scope) == before and not disk_files(storage)


def test_legacy_settings_and_upload_endpoints_remain_compatible(client, scope, storage):
    assert post(client, scope, legacy=True).status_code == 200
    assert snapshot(scope)[4:] == (None, None)
    prefix = f"/leagues/{scope.entity.url}" if scope.kind == "league" else f"/{scope.entity.url}"
    response = client.post(
        f"/_api{prefix}/upload-waiver", data={"waiver": (BytesIO(b"plain document"), "../waiver.txt")}
    )
    assert response.status_code == 200
    path = response.get_json()["waiver_filepath"]
    assert disk_files(storage)[path] == b"plain document" and ".." not in path
    assert post(client, scope, legacy=True).status_code == 200
    assert snapshot(scope)[4] == path


@pytest.mark.parametrize("actor", ["guest", "unrelated", "wrong_type"])
def test_permissions_before_any_file_write(app, scope, storage, actor):
    before = snapshot(scope)
    user = None
    if actor != "guest":
        user = (
            Team(id="wrong-type-team", name="Other type", pw_hash="x")
            if actor == "wrong_type"
            else Player(id="unrelated", name="Other", pw_hash="x")
        )
        db.session.add(user)
        if actor == "wrong_type":
            # A typed organizer entry for a player must not grant the team access.
            db.session.add(
                TO(
                    user_id=user.id,
                    user_type="player",
                    **({"event": scope.entity.url} if scope.kind == "tournament" else {"league_id": scope.entity.url}),
                )
            )
        db.session.commit()
    # A fresh request context avoids Flask-Login's context-cached actor.
    from flask import g

    g.pop("_login_user", None)
    with app.test_client() as separate:
        if user is not None:
            login_as(separate, user)
        response = post(separate, scope)
    assert response.status_code in (401, 403)
    assert snapshot(scope) == before and not disk_files(storage)


def test_event_settings_cannot_replace_shared_league_waiver(client, tournament, player, storage):
    cfg = make_registrable_config(waiver_filepath="old.pdf", waiver_sha256="a" * 64)
    league = League(url="parent-league", name="Parent", published=True, registrable_config_id=cfg.id)
    db.session.add(league)
    db.session.flush()
    tournament.registrable_config_id = None
    tournament.league_id = league.url
    db.session.add(TO(user_id=player.id, user_type="player", league_id=league.url))
    db.session.commit()
    login_as(client, player)
    scoped = SimpleNamespace(kind="tournament", entity=tournament, endpoint=f"/_api/{tournament.url}/update-settings")
    assert post(client, scoped).status_code == 400
    assert cfg.waiver_filepath == "old.pdf" and not disk_files(storage)
    assert post(client, scoped, waiver=None).status_code == 200
    assert cfg.waiver_filepath == "old.pdf" and str(cfg.team_reg_fee) == "0.00"


def test_invalid_date_does_not_flush_head_ref_or_publication(client, tournament, player, storage):
    db.session.add(TO(user_id=player.id, user_type="player", event=tournament.url))
    db.session.commit()
    login_as(client, player)
    scoped = SimpleNamespace(kind="tournament", entity=tournament, endpoint=f"/_api/{tournament.url}/update-settings")
    response = post(client, scoped, data=settings(scoped, end_date="not-a-date", head_refs_allowed_list=player.id))
    assert response.status_code == 400 and tournament.name == "Test Tournament" and not disk_files(storage)


def test_same_bytes_keep_signatures_valid_replacement_marks_outdated(client, scope, storage):
    from app.serializers.registration_serializer import player_reg_waiver_api

    assert post(client, scope).status_code == 200
    config = scope.entity.registrable_config
    signature = SimpleNamespace(
        waiver_legal_name_signature_sha256=config.waiver_sha256, waiver_legal_name_signature="Fictional player"
    )
    assert post(client, scope).status_code == 200
    assert player_reg_waiver_api(signature, config)["waiver_status"] == "VALID"
    assert post(client, scope, waiver=b"new terms").status_code == 200
    assert player_reg_waiver_api(signature, config)["waiver_status"] == "OUT_OF_DATE"


def test_correctly_typed_team_organizer_can_save(app, scope, storage, team):
    db.session.add(
        TO(
            user_id=team.id,
            user_type="team",
            **({"event": scope.entity.url} if scope.kind == "tournament" else {"league_id": scope.entity.url}),
        )
    )
    db.session.commit()
    from flask import g

    g.pop("_login_user", None)
    with app.test_client() as separate:
        login_as(separate, team)
        assert post(separate, scope).status_code == 200
    assert snapshot(scope)[0] == "Updated details" and len(disk_files(storage)) == 1


@pytest.mark.parametrize("value", ["", None, 0, "0", "15"])
def test_optional_cap_semantics(client, scope, storage, value):
    data = settings(scope, max_team_size_roster=value)
    # URL-encoded form fields have no JSON null representation.
    if scope.kind == "tournament" and value is None:
        data.pop("max_team_size_roster")
    assert post(client, scope, data=data).status_code == 200
    assert scope.entity.registrable_config.max_team_size_roster == (None if value in (None, "") else int(value))


def test_malformed_multipart_settings_do_not_change_data(client, scope, storage):
    before = snapshot(scope)
    if scope.kind == "league":
        form = {"settings": "[1,2]", "waiver": (BytesIO(b"new"), "waiver.pdf")}
    else:
        form = MultiDict(settings(scope))
        form.add("name", "second name")
    assert client.post(scope.endpoint, data=form, content_type="multipart/form-data").status_code == 400
    assert snapshot(scope) == before and not disk_files(storage)
