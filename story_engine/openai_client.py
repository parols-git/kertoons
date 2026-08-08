"""
Story generation and (optional) character-photo description, via the
OpenAI Chat Completions API - the story-text engine per the product spec.

Translation defaults to Gemini (gemini_client.py) but can be switched to
run through this same OpenAI client instead by setting
TRANSLATION_PROVIDER=openai (see config.py and this module's
translate_story()) - see pipeline.py for the dispatch between the two.

Every function has a MOCK fallback (used automatically when OPENAI_API_KEY
is not set, or when FORCE_MOCK=1) so the full app can be run and tested
locally without any API key or network access.
"""
import json
import base64

from . import config
from .api_utils import StoryGenerationError, post_with_retry
from .prompts import (
    build_system_prompt, build_user_prompt, VISION_SYSTEM_PROMPT,
    TRANSLATE_SYSTEM_PROMPT, build_translate_user_prompt,
    IMAGE_CONSISTENCY_CHECK_PROMPT, CHARACTER_CREATION_SYSTEM_PROMPT,
    build_character_creation_user_prompt, COMPETITION_SCORING_SYSTEM_PROMPT,
    build_competition_scoring_user_prompt,
)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _chat(messages, model=None, timeout=60):
    model = model or config.OPENAI_MODEL
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }

    def parse(resp):
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)

    return post_with_retry(OPENAI_CHAT_URL, headers, payload, timeout, parse, service_name="OpenAI")


# --------------------------------------------------------------------- story

def generate_story(initial_text: str, region: str, page_count: int = None,
                    locked_characters: list = None) -> dict:
    page_count = page_count or config.DEFAULT_PAGE_COUNT
    if config.MOCK_STORY:
        return _mock_story(initial_text, region, page_count, locked_characters)

    messages = [
        {"role": "system", "content": build_system_prompt(page_count)},
        {"role": "user", "content": build_user_prompt(
            initial_text, region, page_count, locked_characters=locked_characters)},
    ]
    story = _chat(messages)
    _validate_story_shape(story, page_count)
    return story


def _validate_story_shape(story: dict, page_count: int):
    required = ("title", "region", "moral", "characters", "pages")
    for key in required:
        if key not in story:
            raise StoryGenerationError(f"Model response missing required field '{key}'")
    if len(story["pages"]) != page_count:
        raise StoryGenerationError(
            f"Expected {page_count} pages, got {len(story['pages'])}"
        )


def _mock_story(initial_text: str, region: str, page_count: int = None,
                 locked_characters: list = None) -> dict:
    page_count = page_count or config.DEFAULT_PAGE_COUNT
    region = region or "a cozy village by a hill"
    seed = (initial_text or "a small adventure").strip().rstrip(".")
    characters = [
        {
            "name": "Mira",
            "type": "kid",
            "appearance": "warm brown skin, curly black hair in two short puffs, "
                          "brown eyes with round glasses, a bright yellow raincoat and white "
                          "boots, no hats/bows/other accessories besides her round glasses",
            "personality": "curious, kind, and always ready to help a friend",
        },
        {
            "name": "Bumble",
            "type": "animal",
            "appearance": "orange fur with a fluffy white-tipped tail, short rounded ears, "
                          "big blue eyes, no collar or accessories",
            "personality": "playful, a little shy, loves snacks",
        },
        {
            "name": "Pip",
            "type": "animal",
            "appearance": "sky-blue feathers with a round cream-colored belly, one ruffled "
                          "head feather that always sticks up, small black eyes, no accessories",
            "personality": "nervous but brave once encouraged",
        },
    ]
    by_name = {c["name"]: c for c in characters}

    def block_for(names):
        return ", ".join(f'{n} ({by_name[n]["appearance"]})' for n in names)

    # Deliberately varied cast per page - proves "only relevant characters are
    # included per scene" (page 1: Mira alone; page 3 introduces Pip mid-story;
    # page 4 deliberately drops Mira) rather than always showing the full cast.
    #
    # The mock story can't call a real language model, so it can't truly
    # elaborate an arbitrary seed the way generate_story()'s Storytelling
    # Expert skill does - but it demonstrates the SHAPE that skill produces:
    # setup -> small problem -> attempt with a setback -> turning point ->
    # resolution + dialogue + sensory detail, using the seed only as loose
    # inspiration for the "summary" line, never transcribed into the prose.
    # Each beat also gets its own specific spot + camera framing + lighting
    # (never a bare repeat of `region`) - demonstrates the same visual-variety
    # shape prompts.SYSTEM_PROMPT now requires of the real model, so two
    # pages never read as visually interchangeable even though a mock story
    # can't invent this creatively itself.
    beats = [
        (
            f"Mira is exploring near {region}, inspired by: {seed}.",
            ["Mira"],
            f'The morning air smelled of rain and warm earth near {region}. Mira crouched by '
            f'the water\'s edge, watching the light ripple across the surface. "There has to '
            f'be something interesting out here," she whispered to herself, tucking a loose '
            f'curl behind her ear.',
            "at the water's edge, wide establishing shot, hazy morning mist",
        ),
        (
            "Bumble the fox trots over and something isn't quite right.",
            ["Mira", "Bumble"],
            'A rustle in the reeds made Mira jump - it was only Bumble, his fluffy tail low and '
            'twitchy. "You look worried," Mira said gently. Bumble gave a small, nervous yip '
            "and glanced upward, as if something was missing from the sky.",
            "among tall reeds beside the pond, close shot on Bumble's worried face, bright midday sun",
        ),
        (
            "They meet Pip, a tiny nervous bird, who is lost and afraid.",
            ["Mira", "Bumble", "Pip"],
            'That\'s when they spotted Pip, trembling on a low branch, feathers ruffled and '
            'out of place. "I can\'t find my way home," Pip chirped, voice barely above a '
            "whisper. Mira knelt down slowly so she wouldn't seem so big and scary.",
            "beneath a low branch in a small grove, over-the-shoulder shot toward Pip, dappled afternoon light",
        ),
        (
            "Bumble and Pip try a first plan, but it doesn't quite work.",
            ["Bumble", "Pip"],
            "Bumble bounded ahead to sniff out a path, tail bristling with determination, but "
            "the trail he found only circled back to where they'd started. Pip's wings drooped. "
            '"Maybe we\'re not meant to find it," Pip murmured - until Bumble noticed something '
            "he'd missed the first time.",
            "along a winding forest trail, action shot mid-stride, golden late-afternoon light",
        ),
        (
            "Working together, they find the way home and celebrate.",
            ["Mira", "Bumble", "Pip"],
            'Following the smell of Pip\'s own nest high in an old tree, the three of them '
            'called out together until a flutter of wings answered back. "You found me!" Pip '
            "sang, looping happily through the air. Mira and Bumble cheered, muddy and tired "
            "and completely delighted.",
            "beside a tall old tree with a nest overhead, wide shot looking up from below, soft golden dusk",
        ),
    ]

    # The mock's hand-written narrative arc has exactly 5 fixed beats, but
    # page_count is admin-configurable - always keep the setup (first) and
    # resolution (last) beats, and stretch or shrink the middle to fit
    # whatever count was asked for: sample evenly if fewer are needed, cycle
    # through if more are needed. Real generation doesn't have this
    # constraint (the model writes a fresh arc for any page_count), so this
    # only matters for MOCK_STORY mode.
    if page_count == len(beats):
        selected_beats = beats
    elif page_count <= 1:
        selected_beats = beats[:1]
    else:
        first, last, middle = beats[0], beats[-1], beats[1:-1]
        need = page_count - 2
        if need <= 0:
            selected_beats = [first, last][:page_count]
        elif page_count < len(beats):
            step = len(middle) / need
            selected_beats = [first] + [middle[int(i * step)] for i in range(need)] + [last]
        else:
            selected_beats = [first] + [middle[i % len(middle)] for i in range(need)] + [last]

    pages = []
    for i, (summary, present, text, spot_and_style) in enumerate(selected_beats, start=1):
        char_block = block_for(present)
        pages.append({
            "page_number": i,
            "summary": summary,
            "characters_present": present,
            "panel_visual": f"{char_block} {spot_and_style}, page {i}: {summary}",
            "image_prompt": (
                f"PIXAR 3D cartoon style, {char_block}, {spot_and_style}, kid-safe soft pastel colors"
            ),
            "text": text,
        })

    # Test-mode exercise of the Character Library integration path (see
    # pipeline.run_job's locked-character handling): a real model would be
    # TOLD about a locked character via build_locked_characters_clause and
    # would naturally weave it into the plot - the mock generator can't do
    # that, so this just appends it as a character and puts it on the final
    # page, which is enough to exercise generation + reference-based
    # verification for it end to end without a real OPENAI_API_KEY.
    if locked_characters:
        locked = locked_characters[0]
        characters.append({
            "name": locked["name"], "type": locked.get("type", "kid"),
            "appearance": locked["appearance"], "personality": locked.get("personality", ""),
        })
        if locked["name"] not in pages[-1]["characters_present"]:
            pages[-1]["characters_present"].append(locked["name"])

    return {
        "title": f"{by_name['Pip']['name']}'s Way Home",
        "region": region,
        "moral": "A kind, patient friend can help you find your way home.",
        "characters": characters,
        "pages": pages,
        "_mock": True,
    }


# ----------------------------------------------------- character library

def generate_character(prompt_text: str, char_type: str = "kid") -> dict:
    """Invents ONE standalone character for the Character Library (see
    character_studio.create_character_from_prompt) - same _chat()/
    response_format/mock-fallback pattern as generate_story() above, but a
    much smaller JSON shape (no pages)."""
    if config.MOCK_STORY:
        return _mock_character(prompt_text, char_type)

    messages = [
        {"role": "system", "content": CHARACTER_CREATION_SYSTEM_PROMPT},
        {"role": "user", "content": build_character_creation_user_prompt(prompt_text, char_type)},
    ]
    character = _chat(messages)
    _validate_character_shape(character)
    return character


def _validate_character_shape(character: dict):
    required = ("name", "type", "appearance", "personality", "description")
    for key in required:
        if key not in character:
            raise StoryGenerationError(f"Model response missing required field '{key}'")


def _mock_character(prompt_text: str, char_type: str = "kid") -> dict:
    seed = (prompt_text or "a friendly character").strip().rstrip(".")
    is_animal = (char_type or "kid").lower() == "animal"
    if is_animal:
        return {
            "name": "Sunny",
            "type": "animal",
            "appearance": "golden-yellow fur with a cream-colored belly, short fluffy fur, "
                          "worn natural, no styling, sun-gold color, round brown eyes with one "
                          "floppy ear that folds forward, a small blue bandana around the neck, "
                          "no other accessories",
            "personality": "cheerful and endlessly curious",
            "description": f"Inspired by \"{seed}\", Sunny is a warm, golden little companion "
                            "who is always ready for the next adventure.",
        }
    return {
        "name": "Nova",
        "type": "kid",
        "appearance": "warm tan skin, shoulder-length, loosely wavy, worn in a high ponytail, "
                      "deep chestnut-brown hair, hazel eyes with a small constellation of "
                      "freckles across the nose, a bright teal jacket and white sneakers, "
                      "round silver star-shaped earrings, no other accessories",
        "personality": "imaginative and quietly brave",
        "description": f"Inspired by \"{seed}\", Nova is a thoughtful young explorer who "
                        "notices the small wonders everyone else walks past.",
    }


# ------------------------------------------------------ competition scoring

def score_competition_entry(story: dict, competition: dict) -> dict:
    """Judges one finished, already-illustrated story against a
    competition's theme - see prompts.COMPETITION_SCORING_SYSTEM_PROMPT for
    the rubric. Lower temperature than generate_story's default (0.2 vs
    0.3): consistent, repeatable judging matters more here than creative
    variance - the same story re-scored should land close to the same
    total, not swing widely run to run."""
    if config.MOCK_STORY:
        return _mock_score(story, competition)

    messages = [
        {"role": "system", "content": COMPETITION_SCORING_SYSTEM_PROMPT},
        {"role": "user", "content": build_competition_scoring_user_prompt(story, competition)},
    ]
    return _score_with_low_temperature(messages)


def _score_with_low_temperature(messages) -> dict:
    """_chat() always sends temperature=0.3 (shared by every other caller in
    this file) - scoring wants a lower, steadier 0.2, so this makes its own
    request rather than adding a temperature param to the shared _chat()
    helper that every other call site would need to remember to pass
    correctly."""
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.OPENAI_MODEL, "messages": messages,
        "response_format": {"type": "json_object"}, "temperature": 0.2,
    }

    def parse(resp):
        return json.loads(resp.json()["choices"][0]["message"]["content"])

    result = post_with_retry(OPENAI_CHAT_URL, headers, payload, 60, parse, service_name="OpenAI")
    _validate_score_shape(result)
    return result


def _validate_score_shape(result: dict):
    required = ("creativity", "originality", "story_structure", "educational_value",
                "language_quality", "total", "feedback")
    for key in required:
        if key not in result:
            raise StoryGenerationError(f"Model response missing required field '{key}'")


def _mock_score(story: dict, competition: dict) -> dict:
    """Deterministic pseudo-score derived from the story's own length/page
    count (never random) - matches this app's "everything works fully
    offline, and the same input always produces the same mock output"
    convention (see _mock_story/_mock_character above)."""
    pages = story.get("pages", [])
    word_count = sum(len((p.get("text") or "").split()) for p in pages)
    # Longer, more developed stories score a bit higher on structure/language
    # in this mock heuristic - capped well short of a perfect score, since a
    # mock judge should never look indistinguishable from a real one.
    base = min(7, 4 + len(pages) // 2)
    lang = min(8, 4 + word_count // 60)
    scores = {
        "creativity": base, "originality": max(3, base - 1),
        "story_structure": base, "educational_value": min(7, base + 1),
        "language_quality": lang,
    }
    scores["total"] = sum(scores.values())
    scores["feedback"] = (
        f"[MOCK SCORE] This {len(pages)}-page story about \"{story.get('title', 'your story')}\" "
        f"was judged offline (no OPENAI_API_KEY set) using a simple length-based estimate, not a "
        f"real reading of the story."
    )
    return scores


# -------------------------------------------------------------- translation
#
# Only used when config.TRANSLATION_PROVIDER == "openai" (default is
# "gemini" - see gemini_client.translate_story, which this mirrors exactly:
# same prompts.py templates, same story-mutation shape, same per-language-
# additive behavior, same MOCK_TRANSLATION fallback pattern) - kept as a
# separate, self-contained function here rather than sharing code across
# the two provider modules, consistent with how every other provider in
# this app (OpenAI/Gemini/DeepAI/Stripe) is its own independent module.

def translate_story(story: dict, target_language: str) -> dict:
    if not target_language:
        return story
    if config.MOCK_TRANSLATION:
        return _mock_translate(story, target_language)

    messages = [
        {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
        {"role": "user", "content": build_translate_user_prompt(story, target_language)},
    ]
    result = _chat(messages)
    translated_by_page = {p["page_number"]: p["text"] for p in result.get("pages", [])}
    _store_translation(story, target_language, translated_by_page)
    return story


def _mock_translate(story: dict, target_language: str) -> dict:
    translated_by_page = {
        page["page_number"]: f"[{target_language} mock translation] {page['text']}"
        for page in story["pages"]
    }
    _store_translation(story, target_language, translated_by_page)
    story["_mock_translation"] = True
    return story


def _store_translation(story: dict, target_language: str, translated_by_page: dict):
    story.setdefault("translations", {})[target_language] = translated_by_page
    for page in story["pages"]:
        page.setdefault("translations", {})[target_language] = (
            translated_by_page.get(page["page_number"], "")
        )
    languages = story.setdefault("languages", [])
    if target_language not in languages:
        languages.append(target_language)


# --------------------------------------------------------- optional: vision

def describe_character_photo(image_bytes: bytes, mime_type: str = "image/png") -> str:
    if config.MOCK_STORY:
        return (
            "A cheerful child with a warm smile, short neat hair, and a favorite bright "
            "blue jacket - full of curiosity and energy."
        )
    b64 = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this child's photo as a cartoon character base."},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ],
        },
    ]
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": config.OPENAI_MODEL, "messages": messages, "temperature": 0.3}

    def parse(resp):
        return resp.json()["choices"][0]["message"]["content"].strip()

    return post_with_retry(OPENAI_CHAT_URL, headers, payload, 60, parse, service_name="OpenAI")


def verify_scene_image(image_bytes: bytes, character_block: str, reference_images: list = None,
                        mime_type: str = "image/png") -> dict:
    """Asks a vision-capable model whether the generated image actually
    shows each character with their own fixed traits (see
    prompts.IMAGE_CONSISTENCY_CHECK_PROMPT) - the automated half of
    image_client.py's generate-then-verify-then-retry loop, which exists
    because a plain text2img call has no reliable way to GUARANTEE the
    prompt's character-fidelity instructions were actually followed, only
    to ask nicely for it. Returns {"consistent": bool, "issues": [str]}.

    `reference_images`: optional list of `(character_name, image_bytes)` -
    for characters pulled from the Character Library (see
    character_studio.py), their stored reference image is included alongside
    the text description, so the model can catch a drift the text alone
    might miss (e.g. a subtly wrong shade that reads as compliant against
    a written color name but is visibly off next to the actual reference
    picture). Reference images are NEVER fed into image generation itself
    (see image_client.py's CHARACTER CONSISTENCY STRATEGY docstring for why
    that was tried and reverted) - verification-only, purely additional
    input to this check.

    Best-effort: mock mode returns a trivially-passing result (nothing to
    verify against without a real character_block-following image engine
    in the first place), and any real API failure here also returns a
    passing result rather than raising - a broken verifier should never be
    able to block story generation outright, only skip the extra safety
    net it adds."""
    if config.MOCK_STORY or not config.OPENAI_API_KEY:
        return {"consistent": True, "issues": []}
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        content = [
            {"type": "text", "text": f"Fixed character descriptions:\n{character_block}"},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
        ]
        for name, ref_bytes in (reference_images or []):
            ref_b64 = base64.b64encode(ref_bytes).decode("ascii")
            content.append({"type": "text", "text": f"Reference image for {name}:"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{ref_b64}"}})
        messages = [
            {"role": "system", "content": IMAGE_CONSISTENCY_CHECK_PROMPT},
            {"role": "user", "content": content},
        ]
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.OPENAI_MODEL, "messages": messages,
            "temperature": 0, "response_format": {"type": "json_object"},
        }

        def parse(resp):
            return json.loads(resp.json()["choices"][0]["message"]["content"])

        result = post_with_retry(OPENAI_CHAT_URL, headers, payload, 30, parse, service_name="OpenAI")
        return {
            "consistent": bool(result.get("consistent", True)),
            "issues": list(result.get("issues") or []),
        }
    except Exception as e:
        print(f"[kertoons] image consistency check failed (treating as passed): {e}")
        return {"consistent": True, "issues": []}
