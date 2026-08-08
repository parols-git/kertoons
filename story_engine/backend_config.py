"""
Stores the MySQL connection settings (host/port/database/user/password),
editable from the admin panel's Database section. This used to also track
which of two backends ("json" or "mysql") was active - MySQL is now the
only backend, so that switch is gone; this file is purely connection
config now, same "must be readable independent of anything it configures"
reasoning as before (it can't live inside the database it's describing how
to reach).

Same atomic-write-under-lock pattern as the rest of this app's small
JSON-file config stores (api_config.py) - a crash mid-write must never
leave a half-written, unparseable config file behind. This is config, not
data - see story_engine/db.py's module docstring for why the actual
application data no longer has a JSON-file option at all.
"""
import os
import json
import tempfile
import threading

from . import config

_LOCK = threading.Lock()

CONFIG_PATH = os.environ.get(
    "KERTOONS_BACKEND_CONFIG_PATH",
    os.path.join(config.BASE_DIR, "kertoons_backend.json"),
)

_DEFAULT = {
    "mysql": {
        "host": "",
        "port": 3306,
        "database": "",
        "user": "",
        "password": "",
    },
}


def _load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return json.loads(json.dumps(_DEFAULT))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Backfill any keys missing from an older version of this file (e.g. one
    # written before "characters"/"competitions" existed, or from before the
    # "backend"/"migrated" fields were removed).
    merged = json.loads(json.dumps(_DEFAULT))
    merged["mysql"].update(data.get("mysql") or {})
    return merged


def _save(data: dict):
    directory = os.path.dirname(CONFIG_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".kertoons_backend_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_mysql_settings() -> dict:
    """Full connection settings INCLUDING the password - for internal use
    (mysql_store's connection pool) only. Callers building an admin-facing
    response must use get_mysql_settings_public() instead."""
    with _LOCK:
        return dict(_load()["mysql"])


def get_mysql_settings_public() -> dict:
    """Same settings but with the password masked - safe to hand back to
    the admin UI's status display. Never send the real password back down
    to the browser once it's saved; the admin re-enters it only when they
    want to change it."""
    settings = get_mysql_settings()
    settings["password"] = "•" * 8 if settings.get("password") else ""
    return settings


def set_mysql_settings(host: str, port: int, database: str, user: str, password: str) -> dict:
    with _LOCK:
        data = _load()
        data["mysql"] = {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
        }
        _save(data)
        return dict(data["mysql"])
