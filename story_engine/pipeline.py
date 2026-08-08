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
import glob
import json
import traceback

from . import config
from . import db
from . import openai_client
from . import gemini_client
from . import image_client
from . import character_studio
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
        # Read fresh at generation time (not cached at import/job-creation
        # time) so an admin's change to "scenes per story" in /admin.html
        # takes effect on the very next story, without a server restart.
        page_count = db.get_site_settings().get("page_count") or config.DEFAULT_PAGE_COUNT
        # Character Library entries the user picked on create.html (already
        # loaded and visibility-checked by server.py - see POST /api/story's
        # character_ids handling) - told to the story model as characters it
        # must reuse exactly, not invent.
        referenced_characters = job.get("referenced_characters") or []
        story = openai_client.generate_story(
            initial_text, job.get("region", ""), page_count=page_count,
            locked_characters=referenced_characters,
        )
        job["progress"] = 40

        # Belt-and-braces: don't just trust the LLM followed the locked-
        # characters instruction above - forcibly overwrite (or append, if it
        # dropped the character entirely) each referenced character's entry
        # in the story's own characters[] with the canonical, stored values,
        # so the actual generated story can never end up with a name that
        # exists in the Character Library but a different appearance than
        # what's published there. Only type/appearance/personality are
        # overwritten - the character's "name" field is left as whatever the
        # model wrote (it should already match, since the model was told the
        # exact name to use) so later "characters_present" name-matching
        # below still lines up with this entry.
        for ref in referenced_characters:
            match = next(
                (c for c in story.get("characters", [])
                 if (c.get("name") or "").lower() == ref["name"].lower()),
                None,
            )
            if match:
                match["type"] = ref.get("type", match.get("type", "kid"))
                match["appearance"] = ref["appearance"]
                match["personality"] = ref.get("personality", "")
            else:
                story.setdefault("characters", []).append({
                    "name": ref["name"],
                    "type": ref.get("type", "kid"),
                    "appearance": ref["appearance"],
                    "personality": ref.get("personality", ""),
                })

        secondary_language = job.get("secondary_language", "").strip()
        languages = _parse_languages(secondary_language)
        translate_story = (
            openai_client.translate_story if config.TRANSLATION_PROVIDER == "openai"
            else gemini_client.translate_story
        )
        for lang in languages:
            job["message"] = f"Translating into {lang}..."
            story = translate_story(story, lang)
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
        # character name -> (image bytes, debut page number or None). Seeded
        # up front with each referenced Character Library entry's own stored
        # reference image (page number left None - it isn't from a page of
        # THIS story) so the very first page a locked character appears on
        # already has a real reference to verify against, not just the text
        # description; every OTHER character still gets its entry added the
        # normal way, the moment its own debut page finishes generating
        # below - see "reference_registry[name] = (image_bytes, ...)".
        reference_registry = {}
        for ref in referenced_characters:
            ref_bytes = character_studio.get_reference_image_bytes(ref)
            if ref_bytes:
                reference_registry[ref["name"]] = (ref_bytes, None)
        # Tracked separately from reference_registry above: this one records
        # each character's real FIRST page number in THIS story, regardless
        # of whether reference_registry already had a (library-sourced)
        # entry for it seeded before the loop even started - used for
        # story["character_debut_page"] below (which character_studio.
        # create_character_from_story reads to let a user later save a
        # story's own character into the Library). If debut pages were read
        # back out of reference_registry instead, every Library-referenced
        # character would wrongly show no debut page at all, since its
        # pre-seeded entry means the "if name not in reference_registry"
        # debut-tracking check below never fires for it.
        debut_pages = {}
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

            # Every one of this page's present characters that already has a
            # known-good reference image (a Character Library portrait, or
            # its own debut page from earlier in this same story) - given to
            # the post-generation vision consistency check as extra ground
            # truth alongside the text description (see
            # openai_client.verify_scene_image). Verification-only, per
            # image_client.py's CHARACTER CONSISTENCY STRATEGY note - never
            # fed into the actual DeepAI generation call below.
            verification_reference_images = [
                (name, reference_registry[name][0])
                for name in present_names if name in reference_registry
            ]

            scene_text = page.get("panel_visual") or page.get("text", "")
            image_bytes, prompt_used = image_client.generate_scene_image(
                character_block,
                scene_text,
                region=story.get("region", ""),
                characters=relevant_characters,
                reference_image_bytes=reference_bytes,
                page_number=page["page_number"],
                total_pages=total_pages,
                verification_reference_images=verification_reference_images,
            )
            filename = f"page_{page['page_number']}.png"
            with open(os.path.join(job_dir, filename), "wb") as f:
                f.write(image_bytes)
            page["image_file"] = filename
            page["reference_source_page"] = reference_from_page  # None on a character's debut
            # Always the EXACT prompt that produced this image (see
            # generate_scene_image's return contract) - overwrites whatever
            # the story model itself guessed for this field, so the
            # "Regenerate image" box always shows/edits reality, not an
            # LLM-authored approximation of it.
            page["image_prompt"] = prompt_used

            db.record_image_generation(
                job["user_id"], job["job_id"], page["page_number"],
                _log_prompt(character_block, story.get("region", ""), scene_text),
                f"api/story/image?job_id={job['job_id']}&page={page['page_number']}",
            )

            for name in present_names:
                if name not in reference_registry:
                    reference_registry[name] = (image_bytes, page["page_number"])
                if name not in debut_pages:
                    debut_pages[name] = page["page_number"]

            job["progress"] = 52 + int(38 * (i + 1) / total_pages)

        story["character_debut_page"] = debut_pages
        for ref in referenced_characters:
            if ref["name"] in debut_pages:
                db.increment_character_use_count(ref["id"])
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
    verbatim instead (see image_client.generate_scene_image). Either way,
    the page's `image_prompt` is always overwritten with whatever prompt
    actually produced the new image, so it's exactly what's shown/edited
    again next time - never a stale or approximated value.

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

    # Same reference-based verification as run_job's per-page loop, using
    # story.json's own "character_debut_page" (already recorded for every
    # story, old or new) - for each present character other than on its own
    # debut page, its debut image is included as extra ground truth for the
    # consistency check.
    debut_pages = story.get("character_debut_page") or {}
    verification_reference_images = []
    for name in present_names:
        debut_page_no = debut_pages.get(name)
        if not debut_page_no or debut_page_no == page_number:
            continue
        debut_path = os.path.join(job_dir, f"page_{debut_page_no}.png")
        if os.path.exists(debut_path):
            with open(debut_path, "rb") as f:
                verification_reference_images.append((name, f.read()))

    scene_text = page.get("panel_visual") or page.get("text", "")
    total_pages = len(story.get("pages", []))
    image_bytes, prompt_used = image_client.generate_scene_image(
        character_block,
        scene_text,
        region=story.get("region", ""),
        characters=relevant_characters,
        reference_image_bytes=reference_bytes,
        custom_prompt=custom_prompt,
        page_number=page_number,
        total_pages=total_pages,
        verification_reference_images=verification_reference_images,
    )
    image_file = page.get("image_file") or f"page_{page_number}.png"
    with open(os.path.join(job_dir, image_file), "wb") as f:
        f.write(image_bytes)

    # Any PDF cached on disk (see book_export.get_pdf) was built from the
    # OLD version of this page's image, in every language and quality tier -
    # all now stale, so every one of them is removed here rather than
    # served again by a later download.
    for cached_pdf in glob.glob(os.path.join(job_dir, "storybook_*.pdf")):
        try:
            os.remove(cached_pdf)
        except OSError:
            pass

    # Always the EXACT prompt that produced this image - whether that's the
    # user's own edited text or the default composed prompt (see
    # generate_scene_image's return contract) - so the box always shows/
    # edits reality, never a stale value from before this regeneration.
    page["image_prompt"] = prompt_used
    with open(story_path, "w", encoding="utf-8") as f:
        json.dump(story, f, ensure_ascii=False, indent=2)

    # The usage-history table (usage.html) stays on the shorter, boilerplate-
    # free summary for the default path - prompt_used there is the FULL
    # composed prompt including all the fixed instruction text, which would
    # make every log row unreadable. A custom prompt has no such boilerplate
    # to trim, so it's used as-is.
    log_prompt = prompt_used if (custom_prompt and custom_prompt.strip()) \
        else _log_prompt(character_block, story.get("region", ""), scene_text)

    job_id = os.path.basename(os.path.normpath(job_dir))
    db.record_image_generation(
        user_id, job_id, page_number, log_prompt,
        f"api/story/image?job_id={job_id}&page={page_number}",
    )
