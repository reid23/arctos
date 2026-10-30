"""Profile banners are normalized, owner-only uploads with bounded cleanup."""

from io import BytesIO

from PIL import Image
import pytest

from app.utils import profile_banner_helpers as banners
from models import Player, Team, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


def image_bytes(mode="RGB", size=(600, 200), format="PNG", **options):
    image = Image.new(mode, size, color=(90, 120, 150, 100) if mode == "RGBA" else (90, 120, 150))
    output = BytesIO()
    image.save(output, format=format, **options)
    return output.getvalue()


@pytest.fixture(params=["player", "team"])
def owner(request, player, team):
    kind = request.param
    return kind, player if kind == "player" else team


@pytest.fixture
def storage(app, tmp_path, monkeypatch):
    root = tmp_path / "static"
    root.mkdir()
    monkeypatch.setattr(app, "static_folder", str(root))
    return root


def endpoint(kind, account):
    return f"/_api/{kind}s/{account.id}/banner-photo"


def upload(client, kind, account, data=None):
    return client.post(endpoint(kind, account), data=image_bytes() if data is None else data, content_type="image/png")


def test_owner_can_upload_replace_and_remove_public_banner_without_touching_avatar(client, owner, storage):
    kind, account = owner
    avatar = storage / "uploads" / "profiles" / "avatar.jpg"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"existing avatar")
    account.profile_photo = "uploads/profiles/avatar.jpg"
    db.session.commit()
    login_as(client, account)

    first = upload(client, kind, account)
    assert first.status_code == 200, first.get_json()
    first_path = first.get_json()["path"]
    assert first_path.startswith(f"uploads/banners/{kind}-")
    assert first_path.endswith(".jpg")
    with Image.open(storage / first_path) as saved:
        assert saved.format == "JPEG" and saved.size == (600, 200)
    public_image = client.get("/static/" + first_path)
    assert public_image.status_code == 200 and public_image.mimetype == "image/jpeg"
    profile = client.get(f"/_api/{kind}s/{account.id}").get_json()
    assert profile[kind]["banner_photo"] == first_path

    second = upload(client, kind, account, image_bytes(mode="RGBA"))
    assert second.status_code == 200
    second_path = second.get_json()["path"]
    assert second_path != first_path and second_path.endswith(".png")
    assert not (storage / first_path).exists()
    with Image.open(storage / second_path) as saved:
        assert saved.format == "PNG" and saved.getpixel((0, 0))[3] == 100
    assert account.profile_photo == "uploads/profiles/avatar.jpg"
    assert avatar.read_bytes() == b"existing avatar"

    assert client.delete(endpoint(kind, account)).get_json() == {"success": True}
    assert client.delete(endpoint(kind, account)).status_code == 200
    assert not (storage / second_path).exists()
    assert client.get(f"/_api/{kind}s/{account.id}").get_json()[kind]["banner_photo"] is None
    assert avatar.read_bytes() == b"existing avatar"


def test_banner_field_is_public_and_nullable(client, owner, storage):
    kind, account = owner
    assert client.get(f"/_api/{kind}s/{account.id}").get_json()[kind]["banner_photo"] is None
    account.banner_photo = "uploads/banners/public-example.jpg"
    db.session.commit()
    assert client.get(f"/_api/{kind}s/{account.id}").get_json()[kind]["banner_photo"] == account.banner_photo


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("viewer", ["guest", "unrelated", "wrong_account_type"])
def test_banner_changes_require_typed_owner(app, client, owner, storage, monkeypatch, method, viewer):
    kind, account = owner
    account.banner_photo = "uploads/banners/existing.jpg"
    if viewer != "guest":
        model = (Team if kind == "player" else Player) if viewer == "wrong_account_type" else type(account)
        actor = model(id=account.id if viewer == "wrong_account_type" else "unrelated", name="Other", pw_hash="x")
        db.session.add(actor)
        db.session.flush()
        login_as(client, actor)
        if viewer == "wrong_account_type":
            # Normal signup forbids duplicate usernames across types. Inject
            # the principal to test type authorization, not the legacy loader.
            monkeypatch.setattr(app.login_manager, "_user_callback", lambda _id: actor)
    db.session.commit()

    response = client.open(endpoint(kind, account), method=method, data=image_bytes(), content_type="image/png")
    assert response.status_code == (401 if viewer == "guest" else 403)
    assert account.banner_photo == "uploads/banners/existing.jpg"
    assert not (storage / "uploads").exists()


@pytest.mark.parametrize(
    "data", [b"", b"not an image", b'<svg xmlns="http://www.w3.org/2000/svg"/>', b"\xff\xd8broken"]
)
def test_invalid_upload_keeps_existing_banner(client, owner, storage, data):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    response = upload(client, kind, account, data)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert account.banner_photo == original
    assert (storage / original).is_file()


def test_byte_limit_rejects_before_replacement(client, owner, storage):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    response = upload(client, kind, account, b"x" * (banners.MAX_BANNER_BYTES + 1))
    assert response.status_code == 413
    assert account.banner_photo == original and (storage / original).is_file()


def test_stream_without_content_length_is_still_bounded(app):
    stream = BytesIO(b"x" * (banners.MAX_BANNER_BYTES + 100))
    with app.test_request_context(
        "/_api/players/owner/banner-photo",
        method="POST",
        environ_overrides={"wsgi.input": stream, "wsgi.input_terminated": True, "CONTENT_LENGTH": ""},
    ):
        with pytest.raises(banners.BannerUploadError) as error:
            banners.read_banner_upload()
        assert error.value.status == 413
        assert stream.tell() == banners.MAX_BANNER_BYTES + 1


@pytest.mark.parametrize("size", [(8193, 1), (6000, 4001)])
def test_large_dimensions_or_pixel_count_are_rejected_before_decoding(size):
    with pytest.raises(banners.BannerUploadError, match="8192 pixels"):
        banners.normalize_banner(image_bytes(size=size))


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_common_rasters_are_reencoded_without_metadata(format):
    exif = Image.Exif()
    exif[270] = "Private image description"
    exif[274] = 6  # Rotate the landscape image upright before downsizing.
    normalized, extension = banners.normalize_banner(image_bytes(size=(3000, 1000), format=format, exif=exif))
    assert extension == "jpg"
    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert image.height == 1600 and image.width < image.height
        assert not image.getexif()
        assert "icc_profile" not in image.info


def test_animated_images_are_rejected():
    output = BytesIO()
    frame = Image.new("RGB", (100, 40), "red")
    frame.save(output, format="PNG", save_all=True, append_images=[Image.new("RGB", (100, 40), "blue")])
    with pytest.raises(banners.BannerUploadError, match="still"):
        banners.normalize_banner(output.getvalue())


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("stored_path", ["traversal", "absolute", "avatar", "other_owner", "symlink"])
def test_cleanup_never_deletes_arbitrary_or_other_owned_files(client, owner, storage, method, stored_path):
    kind, account = owner
    target = storage.parent / "keep-this-image.jpg"
    target.write_bytes(b"do not delete")
    if stored_path == "traversal":
        relative = "../keep-this-image.jpg"
    elif stored_path == "absolute":
        relative = str(target)
    elif stored_path == "avatar":
        target = storage / "uploads/profiles/avatar.jpg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"do not delete")
        relative = "uploads/profiles/avatar.jpg"
    else:
        directory = banners._banner_directory(kind, "another-owner" if stored_path == "other_owner" else account.id)
        directory.mkdir(parents=True)
        existing = directory / ("a" * 32 + ".jpg")
        if stored_path == "symlink":
            existing.symlink_to(target)
        else:
            existing.write_bytes(b"do not delete")
            target = existing
        relative = existing.relative_to(storage).as_posix()
    account.banner_photo = relative
    db.session.commit()
    login_as(client, account)

    response = client.open(endpoint(kind, account), method=method, data=image_bytes(), content_type="image/png")
    assert response.status_code == 200
    assert target.read_bytes() == b"do not delete"


def test_failed_database_save_keeps_previous_banner_and_removes_new_file(client, owner, storage, monkeypatch):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]

    def fail_commit():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = upload(client, kind, account, image_bytes(mode="RGBA"))
    assert response.status_code == 500
    assert account.banner_photo == original
    assert [path.relative_to(storage).as_posix() for path in storage.rglob("*") if path.is_file()] == [original]


def test_filename_collision_does_not_delete_previous_image(client, owner, storage, monkeypatch):
    kind, account = owner
    login_as(client, account)
    original = upload(client, kind, account).get_json()["path"]
    from uuid import UUID

    monkeypatch.setattr(banners, "uuid4", lambda: UUID(original.rsplit("/", 1)[1].split(".")[0]))
    response = upload(client, kind, account)
    assert response.status_code == 500
    assert account.banner_photo == original and (storage / original).is_file()


def test_lost_commit_acknowledgement_preserves_saved_banner(client, owner, storage, monkeypatch):
    kind, account = owner
    login_as(client, account)
    commit = db.session.commit

    def commit_without_reply():
        commit()
        raise RuntimeError("connection lost after commit")

    monkeypatch.setattr(db.session, "commit", commit_without_reply)
    response = upload(client, kind, account)
    assert response.status_code == 500 and "could not be confirmed" in response.json["error"]
    assert (storage / account.banner_photo).is_file()
