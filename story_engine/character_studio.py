"""
Orchestrates Character Library operations - creating a standalone character
from a text prompt, or capturing one from an existing story's debut page -
parallel to pipeline.py's story-job orchestration, but much smaller: these
calls are fast enough to run synchronously within a single request, no
background job/progress polling needed.

Ownership/permission checks (does this user own the source story, are they
allowed to create a character at all) are the caller's responsibility (see
server.py) - same convention as pipeline.regenerate_page_image, which
assumes its caller already verified access before calling in.
"""
import json
import os

from . import config, db, image_client, openai_client


def _character_dir(character_id: int) -> str:
    path = os.path.join(config.GENERATED_DIR, "characters", str(character_id))
    os.makedirs(path, exist_ok=True)
    return path


def _reference_relpath(character_id: int) -> str:
    """Relative to config.GENERATED_DIR - same "stored relative filename,
    resolved against a known base directory" convention story.json's own
    page["image_file"] already uses (never an absolute path or a URL).
    Built with a literal "/" rather than os.path.join: this string is
    PERSISTED (in the database), and dev happens on Windows while
    production runs on a Linux droplet - os.path.join would bake in
    backslashes here that silently fail to resolve as a path separator on
    Linux. os.path.join is still used everywhere this gets RESOLVED back to
    a real filesystem path (see get_reference_image_bytes below), since
    that step is OS-appropriate on whichever OS is running at the time."""
    return f"characters/{character_id}/reference.png"


def create_character_from_prompt(owner_user_id: int, prompt_text: str, char_type: str = "kid",
                                  category: str = None, age_group: str = None,
                                  school: str = None) -> dict:
    """Invent a brand new character from a short text idea, then generate
    its reference portrait. Order matters: the DB row is created (and its
    id known) BEFORE the image call, so the reference image can be written
    to a path named after that id - not the other way around."""
    generated = openai_client.generate_character(prompt_text, char_type)
    character = db.create_character(
        owner_user_id=owner_user_id,
        name=generated["name"],
        type=generated.get("type", char_type),
        description=generated.get("description", ""),
        appearance=generated["appearance"],
        personality=generated.get("personality", ""),
        category=category,
        age_group=age_group,
        school=school,
    )
    image_bytes, prompt_used = image_client.generate_character_reference_image(character)
    image_path = os.path.join(_character_dir(character["id"]), "reference.png")
    with open(image_path, "wb") as f:
        f.write(image_bytes)
    db.set_character_reference_image(
        character["id"], _reference_relpath(character["id"]), prompt_used
    )
    return db.get_character(character["id"])


def create_character_from_story(owner_user_id: int, job_id: str, character_name: str) -> dict:
    """Save one of an existing story's own characters into the Character
    Library, reusing that character's debut-page illustration as its
    reference image (see story.json's "character_debut_page", written by
    pipeline.run_job) instead of a second, separate image-generation call -
    the character has already been drawn once, consistently, for that story;
    no need to draw it again just to have a "reference" picture."""
    job_dir = os.path.join(config.GENERATED_DIR, job_id)
    story_path = os.path.join(job_dir, "story.json")
    if not os.path.exists(story_path):
        raise ValueError(f"story {job_id} not found")
    with open(story_path, "r", encoding="utf-8") as f:
        story = json.load(f)

    source = next(
        (c for c in story.get("characters", []) if c.get("name") == character_name), None
    )
    if source is None:
        raise ValueError(f'no character named "{character_name}" in this story')

    debut_page = (story.get("character_debut_page") or {}).get(character_name)
    if not debut_page:
        raise ValueError(f'"{character_name}" has no debut page image in this story yet')
    source_image_path = os.path.join(job_dir, f"page_{debut_page}.png")
    if not os.path.exists(source_image_path):
        raise ValueError(f'debut page image for "{character_name}" is missing on disk')

    character = db.create_character(
        owner_user_id=owner_user_id,
        name=source["name"],
        type=source.get("type", "kid"),
        description=f'Saved from the story "{story.get("title", job_id)}".',
        appearance=source.get("appearance", ""),
        personality=source.get("personality", ""),
        source_story_job_id=job_id,
        source_page_number=debut_page,
    )
    with open(source_image_path, "rb") as f:
        image_bytes = f.read()
    image_path = os.path.join(_character_dir(character["id"]), "reference.png")
    with open(image_path, "wb") as f:
        f.write(image_bytes)
    db.set_character_reference_image(character["id"], _reference_relpath(character["id"]))
    return db.get_character(character["id"])


def get_reference_image_bytes(character: dict):
    """Reads a character's stored reference image straight off disk (its
    path resolved from the DB's stored `reference_image_path`, relative to
    config.GENERATED_DIR - same convention as story.json's page["image_file"])
    - for server.py's GET /api/characters/image and for pipeline.py's
    reference_registry seeding (see run_job). Returns None if the character
    has no reference image yet (e.g. a draft still being edited)."""
    if not character or not character.get("reference_image_path"):
        return None
    path = os.path.join(config.GENERATED_DIR, character["reference_image_path"])
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()
