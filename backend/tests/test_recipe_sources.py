import hashlib

import pytest
from sqlalchemy import select

from minemydinner.main import app
from minemydinner.models import ExtractionLog, Recipe, RecipeImage
from minemydinner.settings import Settings, get_settings
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import saved

PNG = b"\x89PNG fictional screenshot one"
OTHER_PNG = b"\x89PNG fictional screenshot two"


@pytest.fixture
def image_dir(tmp_path, client):
    """Keep test images out of the real data folder."""
    folder = tmp_path / "images"
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, image_dir=folder)
    return folder


def file_name(data: bytes, extension: str = "png") -> str:
    return f"{hashlib.sha256(data).hexdigest()}.{extension}"


def upload(client, recipe_id, files, log_id=None):
    data = {"log_id": str(log_id)} if log_id is not None else {}
    return client.post(
        f"/api/recipes/{recipe_id}/images",
        files=[("images", (name, content, mime)) for name, content, mime in files],
        data=data,
    )


def png(content=PNG):
    return ("a.png", content, "image/png")


@pytest.fixture
def screenshot_recipe(client, session, seeded, fake_llm, recipe_write, image_dir):
    body = {**recipe_write, "source_type": "screenshot", "source_url": None}
    body["provenance"] = {**recipe_write["provenance"], "input_text": "[screenshot]"}
    response = client.post("/api/recipes", json=body)
    assert response.status_code == 201
    return response.json()


def test_pasted_text_is_kept_as_the_full_method(client, session, seeded, fake_llm, recipe_write):
    recipe = saved(client, session, {**recipe_write, "source_type": "text"})
    assert recipe.source_text == "葱花蛋：少量葱花，秘制酱"
    body = client.get(f"/api/recipes/{recipe.id}").json()
    assert body["source_text"] == "葱花蛋：少量葱花，秘制酱"


def test_batch_part_is_kept(client, session, seeded, fake_llm, recipe_write):
    assert saved(client, session, {**recipe_write, "source_type": "batch"}).source_text


@pytest.mark.parametrize("source_type", ["web", "screenshot", "manual"])
def test_other_imports_keep_no_source_text(
    client, session, seeded, fake_llm, recipe_write, source_type
):
    assert saved(client, session, {**recipe_write, "source_type": source_type}).source_text is None


def test_editing_keeps_the_source_text(client, session, seeded, fake_llm, recipe_write):
    recipe = saved(client, session, {**recipe_write, "source_type": "text"})
    edit = {**recipe_write, "source_type": "text", "title": "新名字", "provenance": None}
    assert client.put(f"/api/recipes/{recipe.id}", json=edit).status_code == 200
    session.expire_all()
    assert session.get(Recipe, recipe.id).source_text == "葱花蛋：少量葱花，秘制酱"


def test_create_returns_the_extraction_log_id(client, session, seeded, fake_llm, recipe_write):
    created = client.post("/api/recipes", json=recipe_write).json()
    assert created["log_id"] == session.scalars(select(ExtractionLog.id)).one()
    manual = client.post("/api/recipes", json={**recipe_write, "provenance": None}).json()
    assert manual["log_id"] is None


def test_upload_saves_files_by_content_and_lists_them_in_order(
    client, session, screenshot_recipe, image_dir
):
    response = upload(client, screenshot_recipe["id"], [png(), png(OTHER_PNG)])
    assert response.status_code == 201
    assert (image_dir / file_name(PNG)).read_bytes() == PNG
    images = client.get(f"/api/recipes/{screenshot_recipe['id']}").json()["images"]
    assert [image["file_name"] for image in images] == [file_name(PNG), file_name(OTHER_PNG)]
    assert images[0]["mime_type"] == "image/png"


def test_uploaded_image_can_be_viewed(client, screenshot_recipe, image_dir):
    upload(client, screenshot_recipe["id"], [png()])
    response = client.get(f"/api/images/{file_name(PNG)}")
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png"


@pytest.mark.parametrize("name", ["missing.png", "..%2F..%2Fsecret.png", f"{'a' * 64}.exe"])
def test_only_stored_image_names_can_be_read(client, image_dir, name):
    assert client.get(f"/api/images/{name}").status_code == 404


def test_upload_limits(client, screenshot_recipe, image_dir):
    recipe_id = screenshot_recipe["id"]
    assert upload(client, recipe_id, [("a.gif", b"x", "image/gif")]).status_code == 415
    too_big = ("a.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")
    assert upload(client, recipe_id, [too_big]).status_code == 413
    assert upload(client, recipe_id, [png(bytes([n])) for n in range(6)]).status_code == 422
    assert upload(client, recipe_id, [png(bytes([n])) for n in range(5)]).status_code == 201
    assert upload(client, recipe_id, [png(b"sixth")]).status_code == 422
    assert upload(client, 999999, [png()]).status_code == 404


def test_upload_records_the_images_on_the_extraction_log(client, session, screenshot_recipe):
    log_id = session.scalars(select(ExtractionLog.id)).one()
    assert upload(client, screenshot_recipe["id"], [png()], log_id=log_id).status_code == 201
    session.expire_all()
    assert session.get(ExtractionLog, log_id).image_files == [file_name(PNG)]


def test_deleting_a_recipe_removes_its_unshared_images(client, session, screenshot_recipe, image_dir):
    upload(client, screenshot_recipe["id"], [png()])
    assert client.delete(f"/api/recipes/{screenshot_recipe['id']}").status_code == 204
    assert not (image_dir / file_name(PNG)).exists()
    assert session.scalars(select(RecipeImage)).all() == []


def test_images_an_extraction_log_uses_are_kept(client, session, screenshot_recipe, image_dir):
    log_id = session.scalars(select(ExtractionLog.id)).one()
    upload(client, screenshot_recipe["id"], [png()], log_id=log_id)
    client.delete(f"/api/recipes/{screenshot_recipe['id']}")
    assert (image_dir / file_name(PNG)).exists()


def test_images_another_recipe_uses_are_kept(
    client, session, recipe_write, screenshot_recipe, image_dir
):
    other = client.post("/api/recipes", json={**recipe_write, "provenance": None}).json()
    upload(client, screenshot_recipe["id"], [png()])
    upload(client, other["id"], [png()])
    client.delete(f"/api/recipes/{screenshot_recipe['id']}")
    assert (image_dir / file_name(PNG)).exists()
