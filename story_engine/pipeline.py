"""
Orchestrates one full story-generation job:
  1. (optional) describe an uploaded character photo
  2. generate the story + characters + 5 pages via OpenAI (or mock)
  3. generate one 3D-cartoon image per page via the image API (or mock)
  4. translate all page text into the requested secondary language (or mock)
  5. persist everything to generated/<job_id>/ so it can be served and
     packaged into a ZIP / PDF storybook.

Designed to run inside a background thread; the caller supplies a `job`
dict (shared, mutable) that this module updates in place so an HTTP layer
can poll it for progress.
"""
import os
import re
import json
import traceback

from . import config
from . import db
from . import openai_client
from . import gemini_client
from . import image_client
from .prompts import build_character_prompt_block


def _log_prompt(character_block: str, region: str, scene_text: str) -> str:
    """A concise, human-readable stand-in for "the image prompt" used in the
    per-user image-usage log (see db.record_image_generation) - the scene-
    specific parts an account owner would actually want to review, not
    image_client.py's full API-call prompt (which also carries a lot of
    fixed boilerplate instruction text that's identical on every call)."""
    return f"{character_block} | Setting: {region} | Scene: {scene_text}"


def _parse_languages(raw: str) -> list:
    """'Hindi, Tamil ; spanish, hindi' -> ['Hindi', 'Tamil', 'spanish']
    (comma/semicolon separated, trimmed, de-duplicated case-insensitively,
    order preserved) - lets one field request any number of storybooks."""
    seen = set()
    languages = []
    for part in re.split(r"[,;]", raw or ""):
        lang = part.strip()
        if lang and lang.lower() not in seen:
            seen.add(lang.lower())
            languages.append(lang)
    return languages


def run_job(job: dict, job_dir: str):
    """Mutates `job` in place: job['status'], job['progress'], job['story'], job['error']."""
    try:
        job["status"] = "processing"
        job["progress"] = 5
        job["message"] = "Preparing character reference..."

        character_hint = None
        photo_path = job.get("character_photo_path")
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as f:
                photo_bytes = f.read()
            character_hint = openai_client.describe_character_photo(photo_bytes)
            job["character_hint"] = character_hint

        job["message"] = "Writing the story..."
        job["progress"] = 20
        initial_text = job["initial_text"]
        if character_hint:
            initial_text = (
                f"{initial_text}\n\n(Inspiration for one character's look: {character_hint})"
            )
        story = openai_client.generate_story(initial_text, job.get("region", ""))
        job["progress"] = 40

        secondary_language = job.get("secondary_language", "").strip()
        languages = _parse_languages(secondary_language)
        for lang in languages:
            job["message"] = f"Translating into {lang}..."
            story = gemini_client.translate_story(story, lang)
        job["progress"] = 48

        # Character consistency strategy:
        # - Each page only includes the characters actually present in that
        #   scene (page["characters_present"], written by the story model) -
        #   never the full cast, so a page never grows an extra character it
        #   doesn't need.
        # - The character-description text for those characters is built
        #   once, deterministically, in code (never re-authored by the LLM
        #   per page), so its wording is byte-identical every time a given
        #   character appears.
        # - Each character's FIRST appearance ("debut") image is kept as
        #   that character's visual reference for every later page they
        #   appear on (image-to-image in real mode, identical pasted sprites
        #   in mock mode) - not hardcoded to page 1, so a character
        #   introduced mid-story (like a crow on page 3) still gets a proper
        #   reference for its later appearances.
        characters = story.get("characters", [])
        by_name = {c["name"]: c for c in characters if c.get("name")}
        job["progress"] = 52

        job["message"] = "Illustrating pages..."
        total_pages = len(story["pages"])
        reference_registry = {}  # character name -> its debut page's image bytes
        for i, page in enumerate(story["pages"]):
            job["message"] = f"Illustrating page {page['page_number']} of {total_pages}..."

            present_names = [n for n in (page.get("characters_present") or []) if n in by_name]
            if not present_names:
                # fall back to the full cast if the model omitted/mangled this field
                present_names = list(by_name.keys())
            relevant_characters = [by_name[n] for n in present_names]

            character_block = build_character_prompt_block(relevant_characters)
            page["character_prompt"] = character_block

            # Reuse the debut image of whichever of this scene's characters
            # was established earliest - the best single reference image we
            # can pass to a one-image-in API for a multi-character scene.
            reference_bytes = None
            reference_from_page = None
            for name in present_names:
                if name in reference_registry:
                    reference_bytes, reference_from_page = reference_registry[name]
                    break

            scene_text = page.get("panel_visual") or page.get("text", "")
            image_bytes = image_client.generate_scene_image(
                character_block,
                scene_text,
                region=story.get("region", ""),
                characters=relevant_characters,
                reference_image_bytes=reference_bytes,
            )
            filename = f"page_{page['page_number']}.png"
            with open(os.path.join(job_dir, filename), "wb") as f:
                f.write(image_bytes)
            page["image_file"] = filename
            page["reference_source_page"] = reference_from_page  # None on a character's debut

            db.record_image_generation(
                job["user_id"], job["job_id"], page["page_number"],
                _log_prompt(character_block, story.get("region", ""), scene_text),
                f"api/story/image?job_id={job['job_id']}&page={page['page_number']}",
            )

            for name in present_names:
                if name not in reference_registry:
                    reference_registry[name] = (image_bytes, page["page_number"])

            job["progress"] = 52 + int(38 * (i + 1) / total_pages)

        story["character_debut_page"] = {
            name: page_no for name, (_, page_no) in reference_registry.items()
        }
        story["mock_story"] = bool(config.MOCK_STORY)
        story["mock_translation"] = bool(config.MOCK_TRANSLATION)
        story["mock_images"] = bool(config.MOCK_IMAGES)

        with open(os.path.join(job_dir, "story.json"), "w", encoding="utf-8") as f:
            json.dump(story, f, ensure_ascii=False, indent=2)

        job["story"] = story
        job["status"] = "done"
        job["progress"] = 100
        job["message"] = "Story ready!"

    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        job["status"] = "error"
        job["error"] = str(e)
        job["message"] = f"Failed: {e}"
        job["trace"] = traceback.format_exc()


def regenerate_page_image(job_dir: str, page_number: int, user_id: int, custom_prompt: str = None):
    """Re-generate a single page's illustration in place (the "Regenerate
    image" button in the UI) and overwrite that page's PNG file on disk.
    PDF/ZIP export (book_export.py) always reads each page's image straight
    off disk by its stored filename, so overwriting the file here is
    sufficient for every future export to pick up the new image - no other
    state needs updating.

    Reuses the exact same character-prompt block and reference image the
    original job used for this page (stored on the page at generation time:
    `character_prompt`, `reference_source_page`), so the regenerated image
    is a fresh roll of the same inputs, not a differently-conditioned one -
    UNLESS `custom_prompt` is given (the user edited the prompt shown under
    the image in the UI), in which case that text is sent to the image API
    verbatim instead (see image_client.generate_scene_image) and saved back
    onto the page as its new `image_prompt`, so it's what's shown/edited
    again next time and what a future export's usage log reflects.

    `user_id` is the caller (server.py already verified they own this
    story) - this counts against their image-generation credits and gets
    logged the same as any page generated during the original job.
    """
    story_path = os.path.join(job_dir, "story.json")
    with open(story_path, "r", encoding="utf-8") as f:
        story = json.load(f)

    page = next((p for p in story.get("pages", []) if p.get("page_number") == page_number), None)
    if page is None:
        raise ValueError(f"page {page_number} not found in this story")

    by_name = {c["name"]: c for c in story.get("characters", []) if c.get("name")}
    present_names = [n for n in (page.get("characters_present") or []) if n in by_name]
    relevant_characters = [by_name[n] for n in present_names] or list(by_name.values())
    character_block = page.get("character_prompt") or build_character_prompt_block(relevant_characters)

    reference_bytes = None
    ref_page_no = page.get("reference_source_page")
    if ref_page_no:
        ref_path = os.path.join(job_dir, f"page_{ref_page_no}.png")
        if os.path.exists(ref_path):
            with open(ref_path, "rb") as f:
                reference_bytes = f.read()

    scene_text = page.get("panel_visual") or page.get("text", "")
    image_bytes = image_client.generate_scene_image(
        character_block,
        scene_text,
        region=story.get("region", ""),
        characters=relevant_characters,
        reference_image_bytes=reference_bytes,
        custom_prompt=custom_prompt,
    )
    image_file = page.get("image_file") or f"page_{page_number}.png"
    with open(os.path.join(job_dir, image_file), "wb") as f:
        f.write(image_bytes)

    log_prompt = _log_prompt(character_block, story.get("region", ""), scene_text)
    if custom_prompt and custom_prompt.strip():
        log_prompt = custom_prompt.strip()
        page["image_prompt"] = log_prompt
        with open(story_path, "w", encoding="utf-8") as f:
            json.dump(story, f, ensure_ascii=False, indent=2)

    job_id = os.path.basename(os.path.normpath(job_dir))
    db.record_image_generation(
        user_id, job_id, page_number, log_prompt,
        f"api/story/image?job_id={job_id}&page={page_number}",
    )
