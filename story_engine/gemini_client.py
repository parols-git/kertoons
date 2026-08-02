"""
Story translation, via the Gemini API.

Story generation and the character-photo vision description stay on OpenAI
(see openai_client.py) - only the translation step runs through Gemini.

A story can be translated into MULTIPLE languages (e.g. "Hindi, Tamil,
Spanish" typed into one field), and pipeline.py calls translate_story() once
per language rather than once total. Each call ADDS that language's text
without overwriting any language translated earlier, keyed under
story["translations"][<language>] = {page_number: text} and mirrored onto
each page as page["translations"][<language>] = text - so
book_export.build_pdf() can build one separate storybook per language.

Has the same MOCK fallback pattern as openai_client.py (used automatically
when GEMINI_API_KEY is not set, or when FORCE_MOCK=1) so the full app can be
run and tested locally without any API key or network access.
"""
import json

from . import config
from .api_utils import post_with_retry
from .prompts import TRANSLATE_SYSTEM_PROMPT, build_translate_user_prompt

GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _generate(system_prompt: str, user_prompt: str, timeout=60) -> dict:
    url = GEMINI_URL_TEMPLATE.format(model=config.GEMINI_MODEL)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.GEMINI_API_KEY,
    }
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6},
    }

    def parse(resp):
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)

    return post_with_retry(url, headers, payload, timeout, parse, service_name="Gemini")


def translate_story(story: dict, target_language: str) -> dict:
    if not target_language:
        return story
    if config.MOCK_TRANSLATION:
        return _mock_translate(story, target_language)

    result = _generate(TRANSLATE_SYSTEM_PROMPT, build_translate_user_prompt(story, target_language))
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
