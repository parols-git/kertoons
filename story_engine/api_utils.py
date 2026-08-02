"""
Shared HTTP retry/error-handling helpers used by every LLM client
(openai_client.py, gemini_client.py) so retry policy and error formatting
stay identical no matter which provider a given call targets.
"""
import json
import time

import requests

# Retry policy for a single "item" (one story-generation call, one
# translation call, one vision-description call). Covers: the connection
# never establishing, the connection dropping mid-response, a timeout, a
# malformed/truncated JSON body, or a transient server-side error (429/5xx).
# Does NOT retry on a clean 4xx like a bad API key (401) or bad request
# (400) - those won't succeed on retry and should surface immediately.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2  # 2s, 4s, 8s between attempts (exponential)

_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class StoryGenerationError(Exception):
    pass


def post_with_retry(url, headers, payload, timeout, parse_fn, service_name, max_retries=MAX_RETRIES):
    """POST with retries covering connection failures, timeouts, a
    connection dropping mid-response, transient server errors (429/5xx), and
    a response body that comes back malformed/truncated (`parse_fn` raises).
    A single flaky network blip - or the API disconnecting before this item
    finished - no longer fails the whole item; it retries with backoff.
    Does NOT retry a clean 4xx (bad API key, bad request) since that will
    never succeed on retry. `service_name` is only used to label errors
    (e.g. "OpenAI", "Gemini")."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            last_error = f"connection failed: {e}"
        else:
            if resp.status_code != 200:
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    raise StoryGenerationError(
                        f"{service_name} API error {resp.status_code}: {resp.text[:500]}"
                    )
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            else:
                try:
                    return parse_fn(resp)
                except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
                    # A dropped/truncated stream can land here as a 200 with
                    # a malformed or incomplete body.
                    last_error = f"malformed/incomplete response: {e}"

        if attempt < max_retries:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    raise StoryGenerationError(
        f"{service_name} API request failed after {max_retries} attempts: {last_error}"
    )
