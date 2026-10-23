"""Profile text, avatar, and banner edits succeed or fail as one operation."""

from io import BytesIO
import json
from pathlib import Path
from uuid import UUID

from PIL import Image
import pytest
from werkzeug.datastructures import MultiDict

from app.utils import profile_edit_helpers as edits
from app.utils import profile_photo_helpers as photos
from app.utils.profile_banner_helpers import MAX_BANNER_BYTES
from models import Player, Team, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


@pytest.fixture(params=["player", "team"])
def owner(request, player, team):
    return request.param, player if request.param == "player" else team


@pytest.fixture
def storage(app, tmp_path, monkeypatch):
    root = tmp_path / "static"
    root.mkdir()
    monkeypatch.setattr(app, "static_folder", str(root))
    return root


def image_bytes(mode="RGB", size=(1800, 1200)):
    output = BytesIO()
    Image.new(mode, size, (90, 120, 150, 100) if mode == "RGBA" else (90, 120, 150)).save(output, format="PNG")
    return output.getvalue()


def endpoint(owner):
    kind, account = owner
    return f"/_api/{kind}s/{account.id}"


def form(profile=None, photo=None, banner=None, **flags):
    data = {"profile": json.dumps({"name": "Updated name", "location": "Updated city"} if profile is None else profile)}
    if photo is not None:
        data["profile_photo"] = (BytesIO(photo), "photo.png")
    if banner is not None:
        data["banner_photo"] = (BytesIO(banner), "banner.png")
    return data | flags


def save(client, owner, **kwargs):
    return client.put(endpoint(owner), data=form(**kwargs), content_type="multipart/form-data")


def existing_profile(client, owner):
    login_as(client, owner[1])
    assert save(client, owner, photo=image_bytes(), banner=image_bytes()).status_code == 200
    return owner[1].profile_photo, owner[1].banner_photo


def files(storage):
    return {path.relative_to(storage).as_posix(): path.read_bytes() for path in storage.rglob("*") if path.is_file()}


def state(account):
    return account.name, account.location, account.profile_photo, account.banner_photo


def test_both_images_and_text_share_one_commit_then_keep_remove_and_json_remain_compatible(
    client, owner, storage, monkeypatch
):
    old_photo, old_banner = existing_profile(client, owner)
    commits = []
    commit = db.session.commit

    def count_commit():
        commits.append(True)
        commit()

    monkeypatch.setattr(db.session, "commit", count_commit)
    response = save(
        client, owner, profile={"name": "Final name", "location": None}, photo=image_bytes("RGBA"), banner=image_bytes()
    )
    assert response.status_code == 200 and response.get_json() == {"success": True}
    assert len(commits) == 1
    account = owner[1]
    assert account.name == "Final name" and account.location is None
    assert account.profile_photo != old_photo and account.banner_photo != old_banner
    assert not (storage / old_photo).exists() and not (storage / old_banner).exists()
    with Image.open(storage / account.profile_photo) as image:
        assert image.size == (1024, 683) and image.mode == "RGBA"
    with Image.open(storage / account.banner_photo) as image:
        assert image.size == (1800, 1200)
    current_photo, current_banner = account.profile_photo, account.banner_photo
    assert save(client, owner, profile={}, remove_profile_photo="true").status_code == 200
    assert account.profile_photo is None and account.banner_photo == current_banner
    assert not (storage / current_photo).exists() and (storage / current_banner).exists()
    assert client.put(endpoint(owner), json={"location": "JSON city"}).status_code == 200
    assert account.location == "JSON city" and account.name == "Final name" and account.banner_photo == current_banner
    assert save(client, owner, profile={}, remove_banner_photo="true").status_code == 200
    assert account.banner_photo is None and not files(storage)
    assert len(commits) == 4


@pytest.mark.parametrize("payload", ["json", "multipart"])
@pytest.mark.parametrize("viewer", ["guest", "unrelated", "wrong_type"])
def test_save_requires_typed_owner(app, client, owner, storage, monkeypatch, payload, viewer):
    kind, account = owner
    previous = state(account)
    if viewer != "guest":
        model = (Team if kind == "player" else Player) if viewer == "wrong_type" else type(account)
        actor = model(id=account.id if viewer == "wrong_type" else "other", name="Other", pw_hash="x")
        db.session.add(actor)
        db.session.commit()
        login_as(client, actor)
        if viewer == "wrong_type":
            monkeypatch.setattr(app.login_manager, "_user_callback", lambda _id: actor)
    response = (
        client.put(endpoint(owner), json={"name": "Unauthorized"})
        if payload == "json"
        else save(client, owner, photo=image_bytes(), banner=image_bytes())
    )
    assert response.status_code == (401 if viewer == "guest" else 403)
    assert state(account) == previous and not files(storage)


@pytest.mark.parametrize(
    "case",
    [
        "bad_json",
        "non_object",
        "missing_profile",
        "duplicate_profile",
        "duplicate_image",
        "contradictory",
        "bad_flag",
        "bad_name",
        "null_name",
        "long_name",
        "non_text",
        "invalid_photo",
        "invalid_banner",
        "oversize_photo",
        "oversize_banner",
    ],
)
def test_invalid_edit_preserves_text_and_both_files(client, owner, storage, case):
    existing_profile(client, owner)
    before_state, before_files = state(owner[1]), files(storage)
    data = form(profile={"name": "Not saved"}, photo=image_bytes(), banner=image_bytes())
    if case == "bad_json":
        data["profile"] = "{"
    elif case == "non_object":
        data["profile"] = "[]"
    elif case == "missing_profile":
        del data["profile"]
    elif case in ("duplicate_profile", "duplicate_image"):
        data = MultiDict(data)
        field = "profile" if case == "duplicate_profile" else "profile_photo"
        data.add(field, "{}" if field == "profile" else (BytesIO(image_bytes()), "other.png"))
    elif case == "contradictory":
        data["remove_banner_photo"] = "true"
    elif case == "bad_flag":
        data["remove_profile_photo"] = "false"
    elif case in ("bad_name", "null_name", "long_name", "non_text"):
        values = {
            "bad_name": {"name": " "},
            "null_name": {"name": None},
            "long_name": {"name": "x" * 101},
            "non_text": {"location": []},
        }
        data["profile"] = json.dumps(values[case])
    else:
        field = "profile_photo" if case.endswith("photo") else "banner_photo"
        data[field] = (
            BytesIO(b"x" * (MAX_BANNER_BYTES + 1) if case.startswith("oversize") else b"<svg/>"),
            "image.png",
        )
    response = client.put(endpoint(owner), data=data, content_type="multipart/form-data")
    assert response.status_code == (413 if case.startswith("oversize") else 400)
    assert response.get_json()["error"]
    assert state(owner[1]) == before_state and files(storage) == before_files


@pytest.mark.parametrize("action", ["replace", "remove"])
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_commit_failure_rolls_back_every_field_and_cleans_only_new_files(
    client, owner, storage, monkeypatch, action, rollback_fails
):
    existing_profile(client, owner)
    before_state, before_files = state(owner[1]), files(storage)

    def fail_commit():
        raise RuntimeError("simulated database failure")

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("database is unavailable for cleanup queries")

    rollback = db.session.rollback

    def rollback_with_disconnect():
        rollback()
        raise RuntimeError("connection failure during rollback")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    monkeypatch.setattr(db.session, "query", fail_query)
    if rollback_fails:
        monkeypatch.setattr(db.session, "rollback", rollback_with_disconnect)
    changes = (
        {"photo": image_bytes("RGBA"), "banner": image_bytes("RGBA")}
        if action == "replace"
        else {"remove_profile_photo": "true", "remove_banner_photo": "true"}
    )
    response = save(client, owner, profile={"name": "Not saved", "location": None}, **changes)
    assert response.status_code == 500 and response.get_json()["error"]
    assert state(owner[1]) == before_state and files(storage) == before_files


@pytest.mark.parametrize("multipart", [True, False])
def test_all_supported_fields_preserve_values_and_optional_fields_can_be_cleared(client, owner, storage, multipart):
    kind, account = owner
    login_as(client, account)
    data = {"name": "Profile name", "location": "City"}
    data |= (
        {"phone": "12345", "bio": "Player bio"}
        if kind == "player"
        else {"email": "team@example.test", "website": "https://example.test", "about": "Team about"}
    )
    response = save(client, owner, profile=data) if multipart else client.put(endpoint(owner), json=data)
    assert response.status_code == 200
    assert all(getattr(account, key) == value for key, value in data.items())
    cleared = {key: None for key in data if key != "name"}
    response = save(client, owner, profile=cleared) if multipart else client.put(endpoint(owner), json=cleared)
    assert response.status_code == 200 and account.name == "Profile name"
    assert all(getattr(account, key) is None for key in cleared)
    assert not files(storage)


def test_second_file_write_failure_removes_partial_and_first_staged_files(client, owner, storage, monkeypatch):
    existing_profile(client, owner)
    before_state, before_files = state(owner[1]), files(storage)
    original_open = Path.open

    class PartialWrite:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.file.close()

        def write(self, data):
            self.file.write(data[:10])
            raise OSError("simulated disk failure")

    def failing_open(path, mode="r", *args, **kwargs):
        file = original_open(path, mode, *args, **kwargs)
        return PartialWrite(file) if mode == "xb" and "banners" in path.parts else file

    monkeypatch.setattr(Path, "open", failing_open)
    response = save(client, owner, profile={"name": "Not saved"}, photo=image_bytes(), banner=image_bytes())
    assert response.status_code == 500
    assert state(owner[1]) == before_state and files(storage) == before_files


def test_collision_preserves_existing_file_and_profile(client, owner, storage, monkeypatch):
    photo, _ = existing_profile(client, owner)
    before_state, before_files = state(owner[1]), files(storage)
    monkeypatch.setattr(edits, "uuid4", lambda: UUID(Path(photo).stem))
    response = save(client, owner, photo=image_bytes(), banner=image_bytes())
    assert response.status_code == 500
    assert state(owner[1]) == before_state and files(storage) == before_files


def test_legacy_shared_avatar_is_retained_when_owner_removes_it(client, owner, storage):
    kind, account = owner
    shared = "uploads/profiles/" + photos.safe_profile_photo_filename(kind, account.id)
    path = storage / shared
    path.parent.mkdir(parents=True)
    path.write_bytes(image_bytes())
    opposite = Team if kind == "player" else Player
    other = opposite(id="shared-owner", name="Other", pw_hash="x", profile_photo=shared)
    account.profile_photo = shared
    db.session.add(other)
    db.session.commit()
    login_as(client, account)
    assert save(client, owner, remove_profile_photo="true", banner=image_bytes()).status_code == 200
    assert account.profile_photo is None and path.exists() and other.profile_photo == shared


def test_unsafe_stored_paths_are_never_deleted_and_json_cannot_set_image_paths(client, owner, storage):
    protected = storage / "protected.jpg"
    protected.write_bytes(image_bytes())
    account = owner[1]
    account.profile_photo = account.banner_photo = "protected.jpg"
    db.session.commit()
    login_as(client, account)
    assert save(client, owner, remove_profile_photo="true", remove_banner_photo="true").status_code == 200
    assert protected.exists()
    assert (
        client.put(
            endpoint(owner), json={"name": "Safe", "profile_photo": "other.jpg", "banner_photo": "other.jpg"}
        ).status_code
        == 200
    )
    assert account.profile_photo is None and account.banner_photo is None


def test_request_limit_rejects_declared_oversize_before_reading(app):
    stream = BytesIO(b"")
    with app.test_request_context(
        "/_api/players/owner",
        method="PUT",
        content_type="multipart/form-data; boundary=test",
        environ_overrides={"wsgi.input": stream, "CONTENT_LENGTH": str(edits.MAX_PROFILE_EDIT_BYTES + 1)},
    ):
        with pytest.raises(edits.ProfileEditError) as error:
            edits._read_edit()
        assert error.value.status == 413 and stream.tell() == 0


def test_streamed_multipart_request_is_bounded_without_content_length(app):
    header = b'--test\r\nContent-Disposition: form-data; name="profile_photo"; filename="photo.png"\r\n\r\n'
    stream = BytesIO(header + b"x" * edits.MAX_PROFILE_EDIT_BYTES + b"\r\n--test--\r\n")
    with app.test_request_context(
        "/_api/players/owner",
        method="PUT",
        content_type="multipart/form-data; boundary=test",
        environ_overrides={"wsgi.input": stream, "wsgi.input_terminated": True, "CONTENT_LENGTH": ""},
    ):
        with pytest.raises(edits.ProfileEditError) as error:
            edits._read_edit()
        assert error.value.status == 413 and stream.tell() <= edits.MAX_PROFILE_EDIT_BYTES
