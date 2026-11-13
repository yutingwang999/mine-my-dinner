"""Screenshots kept as a recipe's full method, stored as files named by their content."""

import hashlib
import re
from pathlib import Path

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from minemydinner.extraction.extractor import ImageInput
from minemydinner.models import ExtractionLog, Recipe, RecipeImage

EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MIME_TYPES = {extension: mime for mime, extension in EXTENSIONS.items()}
MAX_IMAGES_PER_RECIPE = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Only names this module writes can be read back, so a path can never leave the folder.
FILE_NAME = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")


class TooManyImages(Exception):
    pass


def file_name_for(image: ImageInput) -> str:
    return f"{hashlib.sha256(image.data).hexdigest()}.{EXTENSIONS[image.mime_type]}"


def store_images(
    session: Session,
    recipe: Recipe,
    images: list[ImageInput],
    image_dir: Path,
    log: ExtractionLog | None = None,
) -> list[RecipeImage]:
    """Write the files and add them after the recipe's existing images."""
    if len(recipe.images) + len(images) > MAX_IMAGES_PER_RECIPE:
        raise TooManyImages()
    image_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for image in images:
        name = file_name_for(image)
        path = image_dir / name
        if not path.exists():
            path.write_bytes(image.data)
        row = RecipeImage(
            file_name=name,
            mime_type=image.mime_type,
            size_bytes=len(image.data),
            position=len(recipe.images),
        )
        recipe.images.append(row)
        added.append(row)
    if log is not None:
        log.image_files = [*log.image_files, *(row.file_name for row in added)]
    session.commit()
    return added


def image_path(image_dir: Path, name: str) -> Path | None:
    if not FILE_NAME.match(name):
        return None
    path = image_dir / name
    return path if path.is_file() else None


def mime_type_of(name: str) -> str:
    return MIME_TYPES[name.rsplit(".", 1)[1]]


def remove_unused_files(session: Session, image_dir: Path, names: list[str]) -> None:
    """Delete files no recipe and no extraction log refers to any more."""
    for name in set(names):
        used_by_recipe = session.scalar(select(exists().where(RecipeImage.file_name == name)))
        in_log = func.array_position(ExtractionLog.image_files, name).is_not(None)
        used_by_log = session.scalar(select(exists().where(in_log)))
        path = image_path(image_dir, name)
        if not used_by_recipe and not used_by_log and path is not None:
            path.unlink()
