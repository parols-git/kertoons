"""
3D cartoon image generation - the ONLY allowed image engine per the product
spec is "Deepak.org". No such public API could be located; the closest,
real, well-documented match is DeepAI (deepai.org). Swap DEEPAI_BASE_URL /
the functions below if you have a different concrete endpoint for the real
"Deepak.org" service.

CHARACTER CONSISTENCY STRATEGY
-------------------------------
Plain text-to-image calls have no memory between requests, so five
independent calls - even with the same text description repeated in each
prompt - drift in face shape, proportions, palette, and outfit. Fixed as
follows:

1. A single "character prompt block" is assembled once, in code, from the
   story's fixed character list (see `prompts.build_character_prompt_block`)
   - never re-authored by the LLM per page, so its wording cannot drift.

2. Every page - including pages 2-5 - is generated from that character
   block + its own scene text ONLY, via plain text2img
   (`_deepai_image`/`_deepai_image_editor` with no reference image).
   Earlier versions conditioned pages 2-5 on page 1's own generated image
   via DeepAI's "Image Editor" endpoint (image + text -> edited image),
   hoping for pixel-level consistency - in practice, real generated stories
   showed this endpoint essentially ignores the text prompt's instructions
   to change pose/framing/background and just lightly touches up the
   reference image, so every page ends up looking like the same scene
   redecorated (confirmed by inspecting real output: same pose, same
   background, same camera angle on every page, no matter how strongly the
   prompt says otherwise). Reference-image conditioning was removed for
   this reason - the character block's already-detailed, verbatim-repeated
   text description is relied on for consistency instead, which trades a
   little pixel-level sameness for pages that actually depict different
   moments, which matters more for a picture book. `_deepai_image_editor`
   is kept in this file, unused by default, in case a real reference-image-
   capable provider (unlike DeepAI's editor) is swapped in later - see
   `generate_scene_image`.

3. Mock mode: draws each character as a small deterministic "sprite" (fixed
   shape + palette derived from the character's name+appearance+type only -
   NEVER from the per-page scene text), and pastes the literal same sprite
   into every page's placeholder background, so the fix is directly visible
   offline: open any two generated mock pages and the same character is
   pixel-for-pixel the same drawing.
"""
import io
import time
import math
import textwrap
import hashlib

import requests
from PIL import Image, ImageDraw, ImageFont

from . import config, db, openai_client


class ImageGenerationError(Exception):
    pass


class UnsafeContentError(ImageGenerationError):
    """The image API rejected the prompt itself as unsafe (its own content
    filter - the story text already passed the stricter kid-safety rules in
    prompts.SYSTEM_PROMPT, so this is effectively a false positive). Kept
    distinct from ImageGenerationError so callers can retry with an adjusted
    prompt instead of giving up on the whole page."""
    pass


DEEPAI_IMAGE_EDITOR_URL = "https://api.deepai.org/api/image-editor"

# Same retry policy as openai_client.py's LLM calls: a dropped connection,
# timeout, or transient server error while generating/downloading a single
# page's image shouldn't fail that whole page - retry with backoff before
# giving up. A clean 4xx (bad key, bad request) is not retried at this
# layer - an "unsafe content" 400 specifically is handled one level up, in
# generate_scene_image(), by retrying with a different prompt instead.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2
_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_UNSAFE_CONTENT_MARKER = "unsafe content"


def _request_with_retry(method, url, parse_fn, max_retries=MAX_RETRIES, **kwargs):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = method(url, **kwargs)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            last_error = f"connection failed: {e}"
        else:
            if resp.status_code != 200:
                if resp.status_code == 400 and _UNSAFE_CONTENT_MARKER in resp.text.lower():
                    raise UnsafeContentError(f"Image API rejected prompt as unsafe: {resp.text[:300]}")
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    raise ImageGenerationError(f"Image API error {resp.status_code}: {resp.text[:300]}")
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            else:
                try:
                    return parse_fn(resp)
                except (KeyError, ValueError) as e:
                    # e.g. a dropped/truncated stream landing as a 200 with
                    # a missing output_url or corrupt body
                    last_error = f"malformed/incomplete response: {e}"

        if attempt < max_retries:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    raise ImageGenerationError(f"Image API request failed after {max_retries} attempts: {last_error}")

def _watermark_font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 9.2
    except TypeError:
        return ImageFont.load_default()


def _add_watermark(image_bytes: bytes, text: str) -> bytes:
    """Stamp every generated image (mock or real, any provider) with a
    centered, semi-transparent watermark before it's ever written to disk -
    applied once, here, at the single choke point every image passes
    through, so no code path can accidentally skip it. `text` is the
    admin's configured site name (see generate_scene_image) - baked into
    the pixels at generation time, so renaming the site later only affects
    newly generated images, not ones already on disk."""
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(20, base.width // 16)
    font = _watermark_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (base.width - text_w) / 2 - bbox[0]
    y = (base.height - text_h) / 2 - bbox[1]

    draw.text((x, y), text, font=font, fill=(255, 255, 255, 38))  # 15% opacity, no outline

    stamped = Image.alpha_composite(base, overlay).convert("RGB")
    buf = io.BytesIO()
    stamped.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------- public API

# original prompt, plus up to this many increasingly generic rewrites, if
# the image API keeps flagging the prompt as unsafe.
MAX_PROMPT_ADJUST_ATTEMPTS = 3

# Same cap server.py enforces on hand-edited custom prompts (see
# MAX_CUSTOM_PROMPT_LEN) - the image API rejects overlong prompts outright,
# and a story with a large cast (each character's full 5-slot appearance
# repeated in character_block) plus a detailed panel_visual can otherwise
# exceed it. Enforced in build_prompt() below.
MAX_IMAGE_PROMPT_LEN = 2000


def _truncate(text: str, limit: int) -> str:
    """Cut `text` down to at most `limit` characters without severing the
    last word, so a length-capped prompt never ends mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut if cut else text[:limit]


def _soften_prompt(character_block: str, scene_text: str, region: str, level: int) -> str:
    """Progressively more conservative rewrite of the image prompt, used
    after the image API rejects one as unsafe. There's no way to know which
    specific phrase tripped the filter, so each level strips away more
    potentially-flaggable specificity rather than guessing at a fix.

    Capped at MAX_IMAGE_PROMPT_LEN like build_prompt() - these rewrites are
    normally short, but a large character_block or an unusually long
    scene_text could still push one over the limit."""
    if level == 1:
        result = (
            f"Wholesome, G-rated, family-friendly children's picture-book illustration, "
            f"PIXAR 3D cartoon style. Characters: {character_block}. Setting: {region}. "
            f"Scene: {scene_text}. Bright, cheerful, gentle mood, nothing scary or violent, "
            f"kid-safe soft pastel colors."
        )
    else:
        # Final fallback: no character-appearance specifics left (the more
        # likely trigger for a safety filter), but `scene_text` is
        # deliberately KEPT - dropping it here would mean every page that
        # reaches this level gets the exact same generic prompt (differing
        # only by region), which would produce near-identical images for
        # every one of them.
        result = (
            f"A warm, gentle, G-rated PIXAR-style 3D cartoon illustration for a children's picture book, "
            f"set in {region}. Scene: {scene_text}. Friendly, cheerful characters. Bright, "
            f"soft pastel colors, nothing scary, violent, or inappropriate."
        )
    return _truncate(result, MAX_IMAGE_PROMPT_LEN)


def build_prompt(character_block: str, scene_text: str, region: str,
                  page_number: int = None, total_pages: int = None) -> str:
    """The full, default image-generation prompt - character-consistency
    boilerplate + this page's own scene text. Pulled out as its own
    function (rather than inlined in generate_scene_image) so callers can
    know in advance exactly what will be sent, e.g. to store as
    page["image_prompt"] even before/without a real API call.

    When `page_number`/`total_pages` are given, a short "this is page N of
    TOTAL" clause is appended - not just more instruction-following filler,
    but a concrete guarantee that two pages of the same story can never
    produce the byte-identical prompt string even if the story model ever
    writes near-duplicate scene text for two pages, since the page markers
    always differ. A text-to-image API given the exact same prompt twice
    tends to return the exact same (or near-identical) image, so this is
    the cheapest reliable lever this app has over "every page's image must
    actually look different" beyond just asking nicely in the wording.

    The result is always at most MAX_IMAGE_PROMPT_LEN characters. The
    character block and this page's own scene text are what actually
    determine what gets drawn, so they're always included in full; the
    fixed variety/consistency boilerplate below is appended only as space
    allows, and trimmed first (from the end) if a large cast's
    character_block would otherwise push the prompt over the limit."""
    uniqueness_clause = ""
    if page_number and total_pages:
        uniqueness_clause = (
            f" This is page {page_number} of {total_pages} - it must be immediately, "
            f"visually distinguishable from every other page in this book, not merely similar."
        )

    core = f"PIXAR 3D cartoon style, {character_block}. Setting: {region}. Scene: {scene_text}."

    # Character-fidelity instructions come FIRST in the boilerplate (before
    # the pose/framing variety ones) - if a large cast forces truncation
    # below, it trims from the END, and getting a character's face/colors
    # wrong is a worse failure than a slightly less varied camera angle.
    # Hairstyle gets its OWN sentence, ahead of the general fidelity one -
    # in practice it's the single most common thing that drifts page to
    # page (length, curl/texture, and styling silently changing even when
    # the color is right), so it needs standalone emphasis rather than
    # being just one word in a longer list the model can skim past.
    boilerplate = (
        f"HAIRSTYLE IS CRITICAL: each character's hair length, texture (straight/wavy/curly/"
        f"spiky), how it's styled/worn, AND color must all stay EXACTLY as fixed above on every "
        f"single page - this is the detail that drifts most, so double-check it specifically "
        f"before finalizing: does the hair length, curl pattern, and styling in this image match "
        f"their fixed description word for word, not just the color? "
        f"Each character's appearance must exactly match ONLY their own fixed description "
        f"above (the CHARACTER N slot with their name) - identical face, eye color, hairstyle, "
        f"hair color, skin/fur tone, and outfit colors every time. NEVER swap, blend, or copy "
        f"any trait (hair color/style, skin/fur tone, outfit color, ears, accessories) from one "
        f"character onto a different character in this scene, even when they're standing close "
        f"together - each one keeps only their own listed traits. Do NOT add any accessories, "
        f"glasses, hats, goggles, bows, or props onto a character's body that aren't part of "
        f"their own fixed appearance above, even if similar objects appear elsewhere in the "
        f"scene. Kid-safe soft pastel colors."
        f"{uniqueness_clause} "
        f"This illustration must look STRIKINGLY, OBVIOUSLY different from every other page in "
        f"this book at a glance - a different pose and action for each character, a different "
        f"camera framing/shot type, a different specific spot, different background scenery, "
        f"and different lighting, all matching the scene description above. Never a generic or "
        f"repeated composition."
    )

    # +1 for the space joining core and boilerplate.
    remaining = MAX_IMAGE_PROMPT_LEN - len(core) - 1
    if remaining <= 0:
        # An unusually large cast alone exceeds the limit - trim the core
        # itself rather than send an over-limit prompt with no boilerplate.
        return _truncate(core, MAX_IMAGE_PROMPT_LEN)

    return f"{core} {_truncate(boilerplate, remaining)}"


def generate_scene_image(character_block: str, scene_text: str, region: str = "",
                          characters: list = None, reference_image_bytes: bytes = None,
                          size=(1024, 1024), custom_prompt: str = None,
                          page_number: int = None, total_pages: int = None):
    """Generate one page's illustration, from text alone - see the
    CHARACTER CONSISTENCY STRATEGY note at the top of this file for why
    reference-image conditioning (DeepAI's Image Editor endpoint) was tried
    and then removed: it produced near-identical pages regardless of the
    prompt.

    Returns `(image_bytes, prompt_used)` - `prompt_used` is the EXACT text
    that produced this image (the caller's own `custom_prompt` verbatim if
    one was given, otherwise the composed prompt from `build_prompt()`, or
    whichever automatically-softened rewrite actually succeeded after an
    unsafe-content rejection) - callers persist this as the page's
    `image_prompt` so what's shown/edited in the UI is always accurate to
    what's actually on disk, never a separately-guessed approximation.

    - `character_block`: the ONE locked, code-built character description
      string, identical on every call for this story (see prompts.py).
    - `scene_text`: this page's own action/setting text only.
    - `reference_image_bytes`: accepted for backward compatibility with
      pipeline.py's character-debut-tracking bookkeeping, but intentionally
      UNUSED here - see above.
    - `custom_prompt`: when a user has hand-edited the page's image prompt
      in the "Regenerate image" box (see pipeline.regenerate_page_image),
      sent to the image API VERBATIM instead of being wrapped in the usual
      character-consistency boilerplate below - the whole point of exposing
      it for editing is that what they typed is what gets sent. Only takes
      effect for the real DeepAI path; the mock generator has no free-text
      prompt input to honor (it draws deterministic sprites from
      character_block/scene_text instead), so it's ignored there. No
      automatic unsafe-content softening retry applies either - if the API
      rejects a custom prompt, that's surfaced directly so the user can
      edit and retry themselves, rather than silently substituting
      different wording they didn't write.
    - `page_number`/`total_pages`: forwarded to `build_prompt()` for its
      per-page uniqueness clause; ignored when `custom_prompt` is given
      (the user's exact text is never modified).
    """
    watermark_text = db.get_site_settings()["site_name"]

    if config.MOCK_IMAGES:
        prompt_used = (custom_prompt or "").strip() or build_prompt(
            character_block, scene_text, region, page_number, total_pages)
        image_bytes = _add_watermark(
            _mock_scene_image(character_block, scene_text, characters or [], size), watermark_text)
        return image_bytes, prompt_used

    if custom_prompt and custom_prompt.strip():
        prompt_used = custom_prompt.strip()
        return _add_watermark(_deepai_image(prompt_used), watermark_text), prompt_used

    prompt = build_prompt(character_block, scene_text, region, page_number, total_pages)

    last_error = None
    for attempt in range(MAX_PROMPT_ADJUST_ATTEMPTS):
        try:
            image_bytes = _deepai_image(prompt)
            image_bytes, prompt = _verify_and_maybe_retry(image_bytes, prompt, character_block)
            return _add_watermark(image_bytes, watermark_text), prompt
        except UnsafeContentError as e:
            print(f"[kertoons] image prompt flagged unsafe (attempt {attempt + 1}/"
                  f"{MAX_PROMPT_ADJUST_ATTEMPTS}), retrying with an adjusted prompt: {e}")
            last_error = e
            prompt = _soften_prompt(character_block, scene_text, region, attempt + 1)
    raise last_error


def _verify_and_maybe_retry(image_bytes: bytes, prompt: str, character_block: str) -> tuple:
    """Generate-then-verify-then-retry: a plain text2img call can only be
    ASKED to keep every character's fixed traits straight (see
    build_prompt's boilerplate); this is the check that something actually
    listened, using the same vision model already available for character-
    photo description (see openai_client.verify_scene_image). One retry
    only - not a loop until it passes, since each retry costs a real image
    generation call and there's no guarantee a second attempt fixes what
    the first got wrong; if the retry doesn't verify clean either, its
    result is used anyway (the retry's amended prompt is at least no worse
    than the original) rather than looping indefinitely on the API's dime.

    Controlled by config.IMAGE_CONSISTENCY_CHECK (on by default whenever
    OPENAI_API_KEY is set - verify_scene_image() itself already no-ops to
    an always-passing result without one, so this flag exists purely so an
    admin can turn the extra vision-API cost/latency off explicitly)."""
    if not config.IMAGE_CONSISTENCY_CHECK:
        return image_bytes, prompt
    verdict = openai_client.verify_scene_image(image_bytes, character_block)
    if verdict["consistent"]:
        return image_bytes, prompt

    issues = "; ".join(verdict["issues"]) or "a character's fixed traits did not match"
    print(f"[kertoons] image consistency check flagged an issue, retrying once: {issues}")
    corrected_prompt = _truncate(
        f"{prompt} IMPORTANT CORRECTION - a previous attempt got this wrong: {issues}. "
        f"Do not repeat that mistake: give each character exactly their own listed hair "
        f"color, skin/fur tone, and outfit colors, with nothing swapped between characters.",
        MAX_IMAGE_PROMPT_LEN,
    )
    try:
        retry_bytes = _deepai_image(corrected_prompt)
        return retry_bytes, corrected_prompt
    except ImageGenerationError as e:
        # The original image at least generated successfully - keep it
        # rather than failing the whole page over a best-effort retry.
        print(f"[kertoons] consistency-check retry generation failed, keeping original image: {e}")
        return image_bytes, prompt


# ------------------------------------------------------------- real DeepAI

def _deepai_image(prompt: str) -> bytes:
    headers = {"api-key": config.DEEPAI_API_KEY}

    def parse_submit(resp):
        data = resp.json()
        output_url = data.get("output_url")
        if not output_url:
            raise ValueError(f"response missing output_url: {data}")
        return output_url

    output_url = _request_with_retry(
        requests.post, config.DEEPAI_BASE_URL, parse_submit,
        data={"text": prompt}, headers=headers, timeout=90,
    )
    return _request_with_retry(requests.get, output_url, lambda r: r.content, timeout=60)


def _deepai_image_editor(prompt: str, reference_image_bytes: bytes) -> bytes:
    """Best-effort image-to-image call so pages are conditioned on the same
    reference pixels rather than text alone. DeepAI's Image Editor endpoint
    is an edit/inpaint-style call, not a purpose-built consistent-character
    generator - treat this as a meaningful improvement over pure text2img,
    not a perfect fix. If you have credentials for a provider built
    specifically for character consistency (e.g. a service with reference-
    image or character-seed support), replace this function's body with a
    call to that provider instead."""
    headers = {"api-key": config.DEEPAI_API_KEY}

    def parse_submit(resp):
        result = resp.json()
        output_url = result.get("output_url")
        if not output_url:
            raise ValueError(f"response missing output_url: {result}")
        return output_url

    output_url = _request_with_retry(
        requests.post, DEEPAI_IMAGE_EDITOR_URL, parse_submit,
        data={"text": prompt},
        files={"image": ("reference.png", reference_image_bytes, "image/png")},
        headers=headers, timeout=90,
    )
    return _request_with_retry(requests.get, output_url, lambda r: r.content, timeout=60)


# ---------------------------------------------------------- mock: sprites

_BODY_PALETTES = [
    ((255, 179, 102), (140, 90, 40)),   # warm tan / brown hair
    ((255, 224, 189), (60, 40, 30)),    # light skin / dark hair
    ((198, 142, 101), (30, 20, 15)),    # brown skin / black hair
    ((255, 205, 148), (110, 70, 30)),   # tan / auburn
]
_ANIMAL_PALETTES = [
    ((255, 140, 66), (255, 255, 255)),   # orange fox / white tip
    ((160, 120, 90), (245, 235, 220)),   # brown pup / cream
    ((120, 170, 220), (255, 255, 255)),  # blue bird / white
    ((190, 190, 190), (255, 255, 255)),  # grey bunny / white
]


def _character_seed(character: dict) -> int:
    key = f"{character.get('name','')}|{character.get('appearance','')}|{character.get('type','')}"
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)


def _character_sprite(character: dict, height=280) -> Image.Image:
    """Deterministic sprite for one character - identical every time this
    exact character dict is drawn, regardless of which scene it's placed in."""
    seed = _character_seed(character)
    is_animal = character.get("type", "").lower() == "animal"
    palette = _ANIMAL_PALETTES if is_animal else _BODY_PALETTES
    body_color, accent_color = palette[seed % len(palette)]
    width = int(height * 0.62)

    sprite = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(sprite)

    if is_animal:
        # round-bodied animal: body + head + two ears + tail
        d.ellipse([width * 0.15, height * 0.45, width * 0.85, height * 0.95], fill=body_color)
        d.ellipse([width * 0.20, height * 0.05, width * 0.80, height * 0.55], fill=body_color)
        ear_w = width * 0.18
        d.ellipse([width * 0.22, -height * 0.02, width * 0.22 + ear_w, height * 0.18], fill=body_color)
        d.ellipse([width * 0.78 - ear_w, -height * 0.02, width * 0.78, height * 0.18], fill=body_color)
        d.ellipse([width * 0.30, height * 0.28, width * 0.44, height * 0.40], fill=(20, 20, 20))
        d.ellipse([width * 0.56, height * 0.28, width * 0.70, height * 0.40], fill=(20, 20, 20))
        d.polygon([(width * 0.42, height * 0.42), (width * 0.58, height * 0.42), (width * 0.5, height * 0.5)],
                   fill=accent_color)
        d.ellipse([width * 0.65, height * 0.55, width * 0.95, height * 0.7], fill=accent_color)
    else:
        # simple kid figure: head, hair, body, two legs
        d.rectangle([width * 0.32, height * 0.55, width * 0.68, height * 0.95], fill=body_color)
        d.rectangle([width * 0.34, height * 0.80, width * 0.46, height * 1.0], fill=accent_color)
        d.rectangle([width * 0.54, height * 0.80, width * 0.66, height * 1.0], fill=accent_color)
        d.ellipse([width * 0.22, height * 0.10, width * 0.78, height * 0.62], fill=(255, 219, 172))
        d.pieslice([width * 0.18, height * 0.02, width * 0.82, height * 0.58], 180, 360, fill=accent_color)
        d.ellipse([width * 0.36, height * 0.32, width * 0.46, height * 0.40], fill=(30, 20, 20))
        d.ellipse([width * 0.54, height * 0.32, width * 0.64, height * 0.40], fill=(30, 20, 20))
        d.arc([width * 0.40, height * 0.40, width * 0.60, height * 0.50], 20, 160, fill=(150, 60, 50), width=3)

    return sprite


_sprite_cache = {}


def _get_sprite(character: dict) -> Image.Image:
    # Keyed by full identity (name+appearance+type), NOT name alone - two
    # different stories whose characters happen to share a name (e.g. two
    # different "Mira"s) must not reuse each other's sprite in a long-running
    # server process.
    key = _character_seed(character)
    if key not in _sprite_cache:
        _sprite_cache[key] = _character_sprite(character)
    return _sprite_cache[key]


# ------------------------------------------------------------ mock: scenes

_SCENE_PALETTES = [
    ((255, 246, 217), (222, 209, 160)),
    ((214, 240, 255), (170, 216, 245)),
    ((222, 255, 222), (170, 220, 170)),
    ((255, 230, 240), (240, 190, 210)),
    ((250, 240, 255), (210, 190, 230)),
]


_PROP_SHAPES = ("sun", "cloud", "tree", "star", "hill")
# Each shape's own max vertical extent (its tallest dimension below `y`),
# used to keep it from ever dropping low enough to overlap the caption text
# drawn at CAPTION_TOP below - a shared y-range for every shape would force
# it as small as the tallest one (hill) allows, wasting positional variety
# for the shorter ones.
_PROP_EXTENTS = {"sun": 90, "cloud": 88, "tree": 115, "star": 62, "hill": 160}
_PROP_TOP_MARGIN = 45  # stay clear of the black label bar at the very top
_CAPTION_TOP = 240      # scene caption text starts at y=260 - leave clearance


def _draw_scene_prop(draw, scene_text: str, size, sky_rgb, ground_rgb):
    """A small decorative shape (sun/cloud/tree/star/hill), whose kind AND
    position are both derived from the scene text's own hash - distinct
    from the background palette's hash slice, so two pages that happen to
    land on the same background color still don't look identical. Without
    this, the previous version drew one fixed circle in the exact same
    top-right spot on every single page regardless of scene, which made
    consecutive mock pages look far more alike than they should."""
    digest = hashlib.md5((scene_text or "").encode("utf-8")).hexdigest()
    shape = _PROP_SHAPES[int(digest[8:12], 16) % len(_PROP_SHAPES)]
    x = 50 + (int(digest[12:16], 16) % (size[0] - 220))
    y_range = max(1, _CAPTION_TOP - _PROP_TOP_MARGIN - _PROP_EXTENTS[shape])
    y = _PROP_TOP_MARGIN + (int(digest[16:20], 16) % y_range)
    accent = _darken(ground_rgb, 1.15)

    if shape == "sun":
        draw.ellipse([x, y, x + 90, y + 90], fill=(255, 221, 120))
    elif shape == "cloud":
        for dx, dy, d in ((0, 15, 55), (35, 0, 70), (75, 18, 50)):
            draw.ellipse([x + dx, y + dy, x + dx + d, y + dy + d], fill=(255, 255, 255))
    elif shape == "tree":
        draw.rectangle([x + 28, y + 55, x + 40, y + 115], fill=(120, 80, 40))
        draw.ellipse([x, y, x + 68, y + 68], fill=_darken((110, 175, 110), 1.0))
    elif shape == "star":
        cx, cy, r = x + 30, y + 30, 32
        pts = []
        for i in range(10):
            angle = -90 + i * 36
            rad = r if i % 2 == 0 else r * 0.45
            pts.append((cx + rad * math.cos(math.radians(angle)), cy + rad * math.sin(math.radians(angle))))
        draw.polygon(pts, fill=(255, 240, 150))
    else:  # hill
        draw.ellipse([x - 40, y + 40, x + 120, y + 160], fill=accent)


def _mock_scene_image(character_block: str, scene_text: str, characters: list,
                       size=(1024, 1024)) -> bytes:
    # Background varies scene-to-scene (keyed by the scene text ONLY) so
    # every page still looks like a different moment in the story. The
    # character sprites below are keyed purely by character identity
    # (name+appearance+type), never by scene text, so they are pixel-for-
    # pixel identical on every page - that's the actual consistency fix.
    idx = int(hashlib.md5((scene_text or "").encode("utf-8")).hexdigest(), 16) % len(_SCENE_PALETTES)
    sky, ground = _SCENE_PALETTES[idx]

    img = Image.new("RGB", size, sky)
    draw = ImageDraw.Draw(img)
    _draw_scene_prop(draw, scene_text, size, sky, ground)
    draw.rectangle([0, int(size[1] * 0.78), size[0], size[1]], fill=_darken(ground, 0.95))

    # Always show the full main cast, in the same left-to-right order, so
    # two pages can be compared side by side and the sprites match exactly.
    present = characters or []
    n = max(1, len(present))
    total_w = int(size[0] * 0.6)
    slot_w = total_w // n
    start_x = (size[0] - total_w) // 2
    base_y = int(size[1] * 0.78)

    for i, c in enumerate(present):
        sprite = _get_sprite(c)
        x = start_x + i * slot_w + (slot_w - sprite.width) // 2
        y = base_y - sprite.height
        img.paste(sprite, (x, y), sprite)

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    label = "MOCK IMAGE - connect DEEPAI_API_KEY for real art (character sprites ARE identical across pages)"
    wrapped = textwrap.fill(scene_text or "", width=46)
    draw.rectangle([0, 0, size[0], 34], fill=(0, 0, 0))
    draw.text((12, 8), label, fill=(255, 255, 255), font=font)
    draw.multiline_text((24, 260), wrapped, fill=_darken(sky, 0.35), font=font, spacing=6)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _darken(rgb, factor):
    return tuple(max(0, int(c * factor)) for c in rgb)
