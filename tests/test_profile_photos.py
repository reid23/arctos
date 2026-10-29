"""Avatar uploads validate rasters and preserve existing photos on failure."""

from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image
import pytest

from app.utils import profile_photo_helpers as photos
from app.utils.profile_banner_helpers import MAX_BANNER_BYTES
from models import Player, Team, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


def image_bytes(mode="RGB", size=(600, 200), format="PNG", **options):
    output = BytesIO()
    Image.new(mode, size, (90, 120, 150, 100) if mode == "RGBA" else (90, 120, 150)).save(
        output, format=format, **options
    )
    return output.getvalue()


@pytest.fixture(params=["player", "team"])
def owner(request, player, team):
    return request.param, player if request.param == "player" else team


@pytest.fixture
def storage(app, tmp_path, monkeypatch):
    root = tmp_path / "static"
    root.mkdir()
    monkeypatch.setattr(app, "static_folder", str(root))
    return root


def endpoint(kind, account):
    return f"/_api/{kind}s/{account.id}/profile-photo"


def upload(client, kind, account, data=None):
    return client.post(endpoint(kind, account), data=image_bytes() if data is None else data)


def test_replace_uses_new_url_and_bounded_image_then_remove_is_idempotent(client, owner, storage):
    kind, account = owner
    account.banner_photo = "uploads/banners/keep.jpg"
    db.session.commit()
    login_as(client, account)
    response = upload(client, kind, account, image_bytes(size=(3000, 1500)))
    assert response.status_code == 200 and response.get_json()["success"] is True
    original = response.get_json()["path"]
    assert original.startswith(f"uploads/profiles/{kind}-")
    public = client.get("/static/" + original)
    assert public.status_code == 200 and public.mimetype == "image/jpeg"
    assert public.cache_control.max_age == 3600
    with Image.open(BytesIO(public.data)) as image:
        assert image.size == (1024, 512)
    assert client.get(f"/_api/{kind}s/{account.id}").get_json()[kind]["profile_photo"] == original
    assert client.get("/_api/me").get_json()["profile_photo"] == original

    second = upload(client, kind, account, image_bytes(mode="RGBA", size=(80, 40))).get_json()["path"]
    assert second != original and second.endswith(".png")
    assert client.get("/static/" + original).status_code == 404
    with Image.open(storage / second) as image:
        assert image.size == (80, 40) and image.getpixel((0, 0))[3] == 100
    third = upload(client, kind, account).get_json()["path"]
    assert third not in (original, second) and not (storage / second).exists()
    assert client.get("/_api/me").get_json()["profile_photo"] == third
    assert client.delete(endpoint(kind, account)).get_json() == {"success": True}
    assert client.delete(endpoint(kind, account)).status_code == 200
    assert not (storage / third).exists() and account.profile_photo is None
    assert account.banner_photo == "uploads/banners/keep.jpg"
    assert client.get("/_api/me").get_json()["profile_photo"] is None


@pytest.mark.parametrize("viewer", ["guest", "unrelated", "wrong_type"])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_photo_changes_require_typed_owner(app, client, owner, storage, monkeypatch, viewer, method):
    kind, account = owner
    account.profile_photo = "uploads/profiles/existing.jpg"
    if viewer != "guest":
        model = (Team if kind == "player" else Player) if viewer == "wrong_type" else type(account)
        actor = model(id=account.id if viewer == "wrong_type" else "unrelated", name="Other", pw_hash="x")
        db.session.add(actor)
        db.session.flush()
        login_as(client, actor)
        if viewer == "wrong_type":
            monkeypatch.setattr(app.login_manager, "_user_callback", lambda _id: actor)
    db.session.commit()
    response = client.open(endpoint(kind, account), method=method, data=image_bytes())
    assert response.status_code == (401 if viewer == "guest" else 403)
    assert account.profile_photo == "uploads/profiles/existing.jpg"
    assert not (storage / "uploads").exists()


@pytest.mark.parametrize("case", ["empty", "text", "svg", "corrupt", "gif", "animated", "bytes", "dimension", "pixels"])
def test_invalid_photo_keeps_existing_image(client, owner, storage, case):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    if case == "animated":
        output = BytesIO()
        Image.new("RGB", (10, 10), "red").save(
            output, format="PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")]
        )
        data = output.getvalue()
    elif case == "bytes":
        data = b"x" * (MAX_BANNER_BYTES + 1)
    elif case in ("dimension", "pixels"):
        data = image_bytes(size=(8193, 1) if case == "dimension" else (6000, 4001))
    elif case == "gif":
        data = image_bytes(format="GIF")
    else:
        data = {"empty": b"", "text": b"not an image", "svg": b"<svg/>", "corrupt": b"\xff\xd8broken"}[case]
    response = upload(client, kind, account, data)
    assert response.status_code == (413 if case == "bytes" else 400)
    assert response.get_json()["error"]
    assert account.profile_photo == original and (storage / original).exists()


def test_streamed_photo_body_is_bounded_without_content_length(app):
    stream = BytesIO(b"x" * (MAX_BANNER_BYTES + 100))
    with app.test_request_context(
        "/_api/players/owner/profile-photo",
        method="POST",
        environ_overrides={"wsgi.input": stream, "wsgi.input_terminated": True, "CONTENT_LENGTH": ""},
    ):
        with pytest.raises(photos.PhotoUploadError) as error:
            photos.read_profile_photo_upload()
        assert error.value.status == 413 and stream.tell() == MAX_BANNER_BYTES + 1


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_photo_preserves_orientation_and_aspect_while_stripping_metadata(client, owner, storage, format):
    kind, account = owner
    login_as(client, account)
    exif = Image.Exif()
    exif[270] = "Private image description"
    exif[274] = 6
    response = upload(client, kind, account, image_bytes(size=(3000, 1000), format=format, exif=exif))
    assert response.status_code == 200
    with Image.open(storage / response.get_json()["path"]) as image:
        assert image.format == "JPEG" and image.size == (341, 1024)
        assert not image.getexif() and "icc_profile" not in image.info


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_failed_database_commit_keeps_previous_photo(client, owner, storage, monkeypatch, method):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    previous_bytes = (storage / original).read_bytes()

    def fail_commit():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = client.open(endpoint(kind, account), method=method, data=image_bytes(mode="RGBA"))
    assert response.status_code == 500 and response.get_json()["error"]
    assert account.profile_photo == original and (storage / original).read_bytes() == previous_bytes
    assert [path.relative_to(storage).as_posix() for path in storage.rglob("*") if path.is_file()] == [original]


def test_partial_write_failure_keeps_previous_photo(client, owner, storage, monkeypatch):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    open_path = Path.open

    class PartialWrite:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.file.close()

        def write(self, data):
            self.file.write(data[:10])
            raise OSError("simulated full disk")

    def open_with_failure(path, mode="r", *args, **kwargs):
        file = open_path(path, mode, *args, **kwargs)
        return PartialWrite(file) if mode == "xb" else file

    monkeypatch.setattr(Path, "open", open_with_failure)
    response = upload(client, kind, account)
    assert response.status_code == 500
    assert account.profile_photo == original and (storage / original).exists()
    assert [path.relative_to(storage).as_posix() for path in storage.rglob("*") if path.is_file()] == [original]


def test_lost_commit_acknowledgement_preserves_saved_photo(client, owner, storage, monkeypatch):
    kind, account = owner
    login_as(client, account)
    commit = db.session.commit

    def commit_without_reply():
        commit()
        raise RuntimeError("connection lost after commit")

    monkeypatch.setattr(db.session, "commit", commit_without_reply)
    response = upload(client, kind, account)
    assert response.status_code == 500 and "could not be confirmed" in response.json["error"]
    assert (storage / account.profile_photo).is_file()


def test_uuid_collision_does_not_overwrite_or_delete_existing_photo(client, owner, storage, monkeypatch):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    previous_bytes = (storage / original).read_bytes()
    monkeypatch.setattr(photos, "uuid4", lambda: UUID(Path(original).stem))
    response = upload(client, kind, account, image_bytes(size=(200, 400)))
    assert response.status_code == 500
    assert account.profile_photo == original and (storage / original).read_bytes() == previous_bytes


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_exact_legacy_photo_is_readable_and_removed_only_after_success(client, owner, storage, method):
    kind, account = owner
    relative = "uploads/profiles/" + photos.safe_profile_photo_filename(kind, account.id)
    legacy = storage / relative
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(image_bytes())
    account.profile_photo = relative
    db.session.commit()
    assert client.get(f"/_api/{kind}s/{account.id}").get_json()[kind]["profile_photo"] == relative
    assert client.get("/static/" + relative).status_code == 200
    # The preceding anonymous reads retain a cached principal in the fixture;
    # explicitly clear it when switching this client to its owner.
    from flask import g

    login_as(client, account)
    g.pop("_login_user", None)
    response = client.open(endpoint(kind, account), method=method, data=image_bytes())
    assert response.status_code == 200
    assert not legacy.exists()


@pytest.mark.parametrize("kind", ["player", "team"])
def test_colliding_legacy_names_keep_shared_file_until_last_account_replaces(app, client, storage, kind):
    model = Player if kind == "player" else Team
    left = model(id="same-name", name="Left", pw_hash="x")
    right = model(id="same_name", name="Right", pw_hash="x")
    relative = "uploads/profiles/" + photos.safe_profile_photo_filename(kind, left.id)
    assert relative.endswith(photos.safe_profile_photo_filename(kind, right.id))
    legacy = storage / relative
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(image_bytes())
    left.profile_photo = right.profile_photo = relative
    db.session.add_all([left, right])
    db.session.commit()
    login_as(client, left)
    left_path = upload(client, kind, left).get_json()["path"]
    assert legacy.exists() and right.profile_photo == relative
    with app.app_context(), app.test_client() as other_client:
        login_as(other_client, right)
        right_path = upload(other_client, kind, right).get_json()["path"]
    assert Path(left_path).parent != Path(right_path).parent
    assert not legacy.exists() and (storage / left_path).exists() and (storage / right_path).exists()


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_legacy_cleanup_checks_references_from_both_account_types(client, owner, storage, method):
    kind, account = owner
    relative = "uploads/profiles/" + photos.safe_profile_photo_filename(kind, account.id)
    legacy = storage / relative
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"shared image")
    other_model = Team if kind == "player" else Player
    other = other_model(id="other-reference", name="Other", pw_hash="x", profile_photo=relative)
    account.profile_photo = relative
    db.session.add(other)
    db.session.commit()
    login_as(client, account)
    assert client.open(endpoint(kind, account), method=method, data=image_bytes()).status_code == 200
    assert legacy.read_bytes() == b"shared image" and other.profile_photo == relative


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize(
    "stored_path", ["traversal", "absolute", "arbitrary_legacy", "other_owner", "banner", "symlink"]
)
def test_cleanup_never_deletes_unowned_paths(client, owner, storage, method, stored_path):
    kind, account = owner
    target = storage.parent / "keep.jpg"
    target.write_bytes(b"keep image")
    if stored_path == "traversal":
        relative = "../keep.jpg"
    elif stored_path == "absolute":
        relative = str(target)
    elif stored_path in ("arbitrary_legacy", "banner"):
        relative = "uploads/profiles/other.jpg" if stored_path == "arbitrary_legacy" else "uploads/banners/keep.jpg"
        target = storage / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"keep image")
    else:
        directory = photos._profile_directory(kind, "another-owner" if stored_path == "other_owner" else account.id)
        directory.mkdir(parents=True)
        candidate = directory / ("a" * 32 + ".jpg")
        if stored_path == "symlink":
            candidate.symlink_to(target)
        else:
            candidate.write_bytes(b"keep image")
            target = candidate
        relative = candidate.relative_to(storage).as_posix()
    account.profile_photo = relative
    db.session.commit()
    login_as(client, account)
    assert client.open(endpoint(kind, account), method=method, data=image_bytes()).status_code == 200
    assert target.read_bytes() == b"keep image"


def test_symlinked_upload_directory_is_neither_written_nor_cleaned(client, owner, storage):
    kind, account = owner
    outside = storage.parent / "outside"
    outside.mkdir()
    (storage / "uploads").mkdir()
    (storage / "uploads/profiles").symlink_to(outside, target_is_directory=True)
    legacy_name = photos.safe_profile_photo_filename(kind, account.id)
    target = outside / legacy_name
    target.write_bytes(b"keep image")
    account.profile_photo = "uploads/profiles/" + legacy_name
    db.session.commit()
    login_as(client, account)
    assert upload(client, kind, account).status_code == 500
    assert account.profile_photo.endswith(legacy_name)
    assert client.delete(endpoint(kind, account)).status_code == 200
    assert target.read_bytes() == b"keep image"
    assert list(outside.iterdir()) == [target]
