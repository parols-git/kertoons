"""
Admin-editable overrides for the third-party API keys config.py otherwise
only reads from environment variables / .env at startup - OPENAI_API_KEY,
GEMINI_API_KEY, DEEPAI_API_KEY, and the Stripe keys. Before this existed,
changing any of these meant SSH-ing into the server and hand-editing .env;
now they can be viewed (masked) and updated from Admin > API Keys.

Same pattern as backend_config.py: a small, separate local JSON file (kept
apart from the app's actual data, which lives in MySQL - see
story_engine/db.py) that config.py reads once at import time, with a
persisted value here always winning over the environment variable of the
same name. Because config.py
only reads this at import, a save here takes effect on the NEXT server
restart, not instantly - the admin UI says so explicitly rather than
implying a live reload that doesn't actually happen.
"""
import os
import json
import tempfile
import threading

_LOCK = threading.Lock()

# Deliberately NOT `from . import config` - config.py needs to read this
# module's overrides at import time (see below), and that import would
# otherwise be circular. BASE_DIR is recomputed independently instead of
# shared, exactly the same one-line expression config.py itself uses.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get(
    "KERTOONS_API_CONFIG_PATH",
    os.path.join(_BASE_DIR, "kertoons_api_keys.json"),
)

# Every key an admin is allowed to view/edit here - deliberately a fixed
# allowlist (not "whatever's in the POST body") so this can never become a
# way to set arbitrary env-like config through the admin panel.
EDITABLE_KEYS = [
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "DEEPAI_API_KEY",
    "STRIPE_PAYMENT_LINK",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
]


def _load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    directory = os.path.dirname(CONFIG_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".kertoons_api_keys_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_overrides() -> dict:
    """Raw {key: value} of every override actually saved so far (only keys
    that have been explicitly set - never the full EDITABLE_KEYS list padded
    with empties). Used by config.py at import time; not for the admin UI
    (see get_status() below, which never returns real values)."""
    with _LOCK:
        return dict(_load())


def get_status() -> dict:
    """One entry per EDITABLE_KEYS entry, for the admin UI: whether a value
    is currently set (via override here OR the underlying env var) and,
    if so, a masked preview - never the real value. This is deliberately
    never a way to read a key back out once saved, same principle as
    backend_config's MySQL password masking."""
    overrides = get_overrides()
    result = {}
    for key in EDITABLE_KEYS:
        value = overrides.get(key) or os.environ.get(key, "")
        source = "admin" if overrides.get(key) else ("env" if os.environ.get(key) else None)
        result[key] = {
            "set": bool(value),
            "source": source,
            "masked": (value[:4] + "…" + value[-4:]) if len(value) > 10 else ("•" * len(value) if value else ""),
        }
    return result


def set_overrides(updates: dict):
    """`updates` is {key: value}; only keys in EDITABLE_KEYS are accepted,
    everything else is silently ignored (defense in depth alongside the
    server.py endpoint's own filtering). An empty string clears that key's
    override (falls back to the env var, if any) rather than persisting an
    empty value that would otherwise shadow a real env-configured key."""
    with _LOCK:
        data = _load()
        for key, value in updates.items():
            if key not in EDITABLE_KEYS:
                continue
            value = (value or "").strip()
            if value:
                data[key] = value
            else:
                data.pop(key, None)
        _save(data)
