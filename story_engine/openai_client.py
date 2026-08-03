"""
Story generation and (optional) character-photo description, via the
OpenAI Chat Completions API - the story-text engine per the product spec.

Translation is handled separately, via Gemini - see gemini_client.py.

Every function has a MOCK fallback (used automatically when OPENAI_API_KEY
is not set, or when FORCE_MOCK=1) so the full app can be run and tested
locally without any API key or network access.
"""
import json
import base64

from . import config
from .api_utils import StoryGenerationError, post_with_retry
from .prompts import SYSTEM_PROMPT, build_user_prompt, VISION_SYSTEM_PROMPT

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

def generate_story(initial_text: str, region: str) -> dict:
    if config.MOCK_STORY:
        return _mock_story(initial_text, region)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(initial_text, region)},
    ]
    story = _chat(messages)
    _validate_story_shape(story)
    return story


def _validate_story_shape(story: dict):
    required = ("title", "region", "moral", "characters", "pages")
    for key in required:
        if key not in story:
            raise StoryGenerationError(f"Model response missing required field '{key}'")
    if len(story["pages"]) != config.PAGE_COUNT:
        raise StoryGenerationError(
            f"Expected {config.PAGE_COUNT} pages, got {len(story['pages'])}"
        )


def _mock_story(initial_text: str, region: str) -> dict:
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

    pages = []
    for i, (summary, present, text, spot_and_style) in enumerate(beats, start=1):
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
    return {
        "title": f"{by_name['Pip']['name']}'s Way Home",
        "region": region,
        "moral": "A kind, patient friend can help you find your way home.",
        "characters": characters,
        "pages": pages,
        "_mock": True,
    }


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
