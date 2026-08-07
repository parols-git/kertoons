"""
Tracks which storage backend the app is using - the original JSON file
(story_engine/db.py's default) or a MySQL database (story_engine/
mysql_store.py) - plus the MySQL connection settings, once configured.

This is intentionally a SEPARATE tiny file from kertoons_data.json: it has
to be readable before we know which backend even holds the real data, so it
can never itself live inside the JSON "database" it might be switching away
from. Same atomic-write-under-lock pattern as db.py's _save(), for the same
reason (a crash mid-write must never leave a half-written, unparseable
config file behind).
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
    "backend": "json",
    # True once migrate_from_json() has ever run successfully - guards
    # against re-running it (and hitting duplicate-key errors, or silently
    # re-cloning stale data) if MySQL is switched off and back on again
    # later. Switching back to "json" never touches this - the JSON file
    # itself is left alone by every step here, so it's always there to
    # switch back to.
    "migrated": False,
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
    # Backfill any keys missing from an older version of this file, same
    # forward-compatibility convention as db.py's get_site_settings().
    merged = json.loads(json.dumps(_DEFAULT))
    merged.update({k: v for k, v in data.items() if k != "mysql"})
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


def get_backend() -> str:
    """"json" or "mysql" - which store db.py's public functions should
    actually read/write. Defaults to "json" so an app that's never touched
    this feature behaves exactly as it always has."""
    with _LOCK:
        return _load()["backend"]


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


def set_backend(backend: str):
    if backend not in ("json", "mysql"):
        raise ValueError(f"unknown backend {backend!r}")
    with _LOCK:
        data = _load()
        data["backend"] = backend
        _save(data)


def is_migrated() -> bool:
    with _LOCK:
        return bool(_load().get("migrated"))


def set_migrated():
    with _LOCK:
        data = _load()
        data["migrated"] = True
        _save(data)
