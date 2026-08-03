"""
A tiny JSON-file-backed "database" for user accounts, sessions, and story
ownership/publish state - not a real database engine, just a single
kertoons_data.json (see config.DB_PATH), which is simple enough for this
app's scale (a handful of users, dozens of stories) and needs zero new
dependencies.

Every public function here acquires `_LOCK`, loads the file, does its
read/mutation, and (for writes) saves atomically before releasing the lock -
callers never touch the lock or the file path directly, and never see a
half-written file even under concurrent requests (the server is a
ThreadingHTTPServer, so concurrent writes are a real possibility, not just a
theoretical one).

Story records here deliberately do NOT duplicate title/region/etc - those
stay the single source of truth in each job's own generated/<job_id>/
story.json. A record here only tracks ownership + publish state.
"""
import os
import json
import secrets
import tempfile
import threading
from datetime import datetime, timezone

from . import config

_LOCK = threading.Lock()

# Every new account starts with this many image-generation credits (see
# record_image_generation()); pre-existing user records from before this
# feature existed also fall back to this value rather than reading as 0.
DEFAULT_IMAGE_CREDITS = 50

DEFAULT_SITE_NAME = "Kertoons"
DEFAULT_FOOTER_TEXT = "kertoons.com - Another Elisda AI project"

_SKELETON = {
    "next_user_id": 1,
    "users": [],
    "sessions": [],
    "stories": [],
    "image_usage": [],
    "processed_payments": [],
    "coupons": [],
    "coupon_redemptions": [],
    "site_settings": {"site_name": DEFAULT_SITE_NAME, "footer_text": DEFAULT_FOOTER_TEXT},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not os.path.exists(config.DB_PATH):
        return json.loads(json.dumps(_SKELETON))  # deep copy
    with open(config.DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    # Atomic write: write to a temp file in the same directory, then replace
    # the real file in one filesystem operation - a crash or concurrent
    # reader mid-write can never observe a torn/partial file.
    directory = os.path.dirname(config.DB_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".kertoons_data_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, config.DB_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def init_db():
    """Create the data file with an empty skeleton if it doesn't exist yet.
    Safe to call on every server startup."""
    with _LOCK:
        if not os.path.exists(config.DB_PATH):
            _save(json.loads(json.dumps(_SKELETON)))


def _public_user(user: dict) -> dict:
    """Strip password fields before handing a user record back to a caller
    that isn't specifically verifying a login."""
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user["created_at"],
        "image_credits": user.get("image_credits", DEFAULT_IMAGE_CREDITS),
        "role": user.get("role", "user"),
        "status": user.get("status", "active"),
    }


# ------------------------------------------------------------------- users

def create_user(username: str, password_salt: str, password_hash: str, role: str = "user") -> dict:
    """Raises ValueError if the username is already taken (case-insensitive -
    "Mira" and "mira" are the same account). `role` is only ever passed as
    "admin" by create_admin_if_missing() below - the public /api/register
    flow always creates ordinary "user" accounts."""
    with _LOCK:
        data = _load()
        if any(u["username"].lower() == username.lower() for u in data["users"]):
            raise ValueError("username already taken")
        user = {
            "id": data["next_user_id"],
            "username": username,
            "password_salt": password_salt,
            "password_hash": password_hash,
            "created_at": _now(),
            "image_credits": DEFAULT_IMAGE_CREDITS,
            "role": role,
            "status": "active",
        }
        data["users"].append(user)
        data["next_user_id"] += 1
        _save(data)
        return _public_user(user)


def create_admin_if_missing(username: str, password_salt: str, password_hash: str) -> str:
    """Called once at server startup (see server.py's main()) when
    ADMIN_USERNAME/ADMIN_PASSWORD are configured. If a user with that
    username already exists:
      - already role="admin" -> pure no-op (safe on every restart, never
        clobbers a since-changed password).
      - an ordinary account (e.g. someone already registered that username
        before the admin panel existed) -> promoted to role="admin" in
        place, keeping their existing password rather than silently
        overwriting it with ADMIN_PASSWORD. Silently doing nothing here
        would otherwise leave ADMIN_USERNAME configured but no account
        actually able to reach the admin panel, with no indication why.
    Returns "created", "promoted", or "unchanged" so the caller can log
    what happened."""
    with _LOCK:
        data = _load()
        existing = next((u for u in data["users"] if u["username"].lower() == username.lower()), None)
        if existing is not None:
            if existing.get("role") == "admin":
                return "unchanged"
            existing["role"] = "admin"
            _save(data)
            return "promoted"
        data["users"].append({
            "id": data["next_user_id"],
            "username": username,
            "password_salt": password_salt,
            "password_hash": password_hash,
            "created_at": _now(),
            "image_credits": DEFAULT_IMAGE_CREDITS,
            "role": "admin",
            "status": "active",
        })
        data["next_user_id"] += 1
        _save(data)
        return "created"


def get_user_by_username(username: str) -> dict:
    """Returns the FULL record (including password fields) since this is
    used by the login flow to verify a password - callers that just need a
    display identity should prefer get_user_by_id()."""
    with _LOCK:
        data = _load()
    for u in data["users"]:
        if u["username"].lower() == username.lower():
            return u
    return None


def get_user_by_id(user_id: int) -> dict:
    with _LOCK:
        data = _load()
    for u in data["users"]:
        if u["id"] == user_id:
            return _public_user(u)
    return None


def list_all_users() -> list:
    """Every account (public fields only - no password hash/salt), for the
    admin users table and the CSV export."""
    with _LOCK:
        data = _load()
    return [_public_user(u) for u in data["users"]]


def set_user_status(user_id: int, status: str) -> bool:
    """status is "active" or "suspended". Returns True if the user was found
    and updated. Suspension is enforced centrally in server.py's
    _current_user() - this function just persists the flag."""
    with _LOCK:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            return False
        user["status"] = status
        _save(data)
        return True


def delete_user(user_id: int) -> bool:
    """Removes the user account and any of their sessions. Their stories'
    ownership records are left in place (orphaned) rather than deleted - a
    dangling user_id makes them behave exactly like the pre-accounts legacy
    stories that never had an owner at all: excluded from every listing/view,
    but nothing on disk is touched. Returns True if a matching user was found
    and removed."""
    with _LOCK:
        data = _load()
        remaining = [u for u in data["users"] if u["id"] != user_id]
        if len(remaining) == len(data["users"]):
            return False
        data["users"] = remaining
        data["sessions"] = [s for s in data["sessions"] if s["user_id"] != user_id]
        _save(data)
        return True


# ---------------------------------------------------------------- sessions

def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    with _LOCK:
        data = _load()
        data["sessions"].append({"token": token, "user_id": user_id, "created_at": _now()})
        _save(data)
    return token


def get_user_by_session_token(token: str) -> dict:
    if not token:
        return None
    with _LOCK:
        data = _load()
    session = next((s for s in data["sessions"] if s["token"] == token), None)
    if not session:
        return None
    for u in data["users"]:
        if u["id"] == session["user_id"]:
            return _public_user(u)
    return None


def delete_session(token: str):
    with _LOCK:
        data = _load()
        remaining = [s for s in data["sessions"] if s["token"] != token]
        if len(remaining) != len(data["sessions"]):
            data["sessions"] = remaining
            _save(data)


# ----------------------------------------------------------------- stories

def create_story_record(job_id: str, user_id: int):
    with _LOCK:
        data = _load()
        data["stories"].append({
            "job_id": job_id,
            "user_id": user_id,
            "published": False,
            "created_at": _now(),
        })
        _save(data)


def _find_story(data: dict, job_id: str):
    return next((s for s in data["stories"] if s["job_id"] == job_id), None)


def get_story_record(job_id: str) -> dict:
    with _LOCK:
        data = _load()
    return _find_story(data, job_id)


def get_story_owner_id(job_id: str):
    record = get_story_record(job_id)
    return record["user_id"] if record else None


def is_story_published(job_id: str) -> bool:
    record = get_story_record(job_id)
    return bool(record and record["published"])


def set_story_published(job_id: str, published: bool) -> bool:
    """Returns True if a matching story record was found and updated."""
    with _LOCK:
        data = _load()
        record = _find_story(data, job_id)
        if not record:
            return False
        record["published"] = bool(published)
        _save(data)
        return True


def delete_story_record(job_id: str) -> bool:
    """Removes the ownership/publish record for job_id. Returns True if a
    matching record was found and removed. Ownership is checked by the
    caller (server.py) before calling this, same as set_story_published() -
    this function does not re-check it. Does NOT touch the job's files on
    disk (generated/<job_id>/) - that's the caller's responsibility too."""
    with _LOCK:
        data = _load()
        remaining = [s for s in data["stories"] if s["job_id"] != job_id]
        if len(remaining) == len(data["stories"]):
            return False
        data["stories"] = remaining
        _save(data)
        return True


def list_published_stories() -> list:
    with _LOCK:
        data = _load()
    return [s for s in data["stories"] if s["published"]]


def list_stories_for_user(user_id: int) -> list:
    with _LOCK:
        data = _load()
    return [s for s in data["stories"] if s["user_id"] == user_id]


def list_all_stories() -> list:
    """Every story ownership/publish record across every user, each annotated
    with the owner's username (or None if the owner account was since
    deleted) - for the admin stories table. No existing function lists
    across all users; list_stories_for_user() above is scoped to one."""
    with _LOCK:
        data = _load()
    users_by_id = {u["id"]: u["username"] for u in data["users"]}
    result = []
    for s in data["stories"]:
        record = dict(s)
        record["owner_username"] = users_by_id.get(s["user_id"])
        result.append(record)
    return result


# ------------------------------------------------------------- image usage

def get_image_credits(user_id: int) -> int:
    with _LOCK:
        data = _load()
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    return user.get("image_credits", DEFAULT_IMAGE_CREDITS) if user else 0


def record_image_generation(user_id: int, job_id: str, page_number: int,
                             prompt: str, image_url: str) -> int:
    """Called once per image actually generated (both a story's initial 5
    pages and every "Regenerate image" click) - appends one entry to the
    usage log AND decrements the user's credit balance by 1, in a single
    load/mutate/save under the lock so the two can never drift out of sync.

    Credits ARE allowed to go negative here (server.py's pre-checks are the
    enforcement point - see CREDIT_OVERDRAFT_LIMIT - not this function): a
    single story spends 5 credits across 5 separate calls to this function,
    so someone starting at 1 credit legitimately finishes at -4 even though
    they had enough to be allowed to start. Returns the new balance."""
    with _LOCK:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is not None:
            current = user.get("image_credits", DEFAULT_IMAGE_CREDITS)
            user["image_credits"] = current - 1
        data.setdefault("image_usage", []).append({
            "user_id": user_id,
            "job_id": job_id,
            "page_number": page_number,
            "prompt": prompt,
            "image_url": image_url,
            "created_at": _now(),
        })
        _save(data)
        return user["image_credits"] if user is not None else 0


def add_credits(user_id: int, amount: int) -> int:
    """Adds `amount` credits to the user's balance, unconditionally - the
    MOCK-PAYMENTS "Add credits" top-up used when no Stripe key is configured
    (see config.MOCK_PAYMENTS). Real, paid top-ups go through
    grant_credits_for_payment() below instead, which is idempotent per
    Stripe checkout session - this one isn't, so it must never be reachable
    from a real-money code path. Returns the new balance."""
    with _LOCK:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise ValueError(f"unknown user_id {user_id}")
        user["image_credits"] = user.get("image_credits", DEFAULT_IMAGE_CREDITS) + amount
        _save(data)
        return user["image_credits"]


def grant_credits_for_payment(user_id: int, session_id: str, credits: int,
                               amount_usd: float = None) -> int:
    """Idempotently grants `credits` for a specific, already-verified Stripe
    checkout session AND records a permanent payment-history entry (see
    list_payments_for_user) - safe to call more than once for the same
    session_id (e.g. once from /api/credits/confirm when the user's own
    browser returns from Stripe, and again later from the /api/stripe/webhook
    delivery of the same event) without ever double-crediting or duplicating
    the history entry. Returns the new balance (or the current balance,
    unchanged, if this session_id was already processed)."""
    with _LOCK:
        data = _load()
        processed = data.setdefault("processed_payments", [])
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if any(p.get("session_id") == session_id for p in processed):
            return user["image_credits"] if user else 0
        if user is None:
            raise ValueError(f"unknown user_id {user_id}")
        processed.append({
            "session_id": session_id,
            "user_id": user_id,
            "credits": credits,
            "amount_usd": amount_usd,
            "created_at": _now(),
        })
        user["image_credits"] = user.get("image_credits", DEFAULT_IMAGE_CREDITS) + credits
        _save(data)
        return user["image_credits"]


def list_payments_for_user(user_id: int) -> list:
    with _LOCK:
        data = _load()
    return [p for p in data.get("processed_payments", []) if p.get("user_id") == user_id]


def list_image_usage_for_user(user_id: int) -> list:
    with _LOCK:
        data = _load()
    return [u for u in data.get("image_usage", []) if u["user_id"] == user_id]


def list_all_payments() -> list:
    """Every processed payment across every user, annotated with the payer's
    username (or None if since deleted) - for the admin purchase summary
    report. list_payments_for_user() above is scoped to one user."""
    with _LOCK:
        data = _load()
    users_by_id = {u["id"]: u["username"] for u in data["users"]}
    result = []
    for p in data.get("processed_payments", []):
        record = dict(p)
        record["username"] = users_by_id.get(p.get("user_id"))
        result.append(record)
    return result


# ----------------------------------------------------------------- coupons

def create_coupon(code: str, credits: int) -> dict:
    """Raises ValueError if the code is already taken (case-insensitive, same
    convention as usernames). New coupons start active."""
    with _LOCK:
        data = _load()
        coupons = data.setdefault("coupons", [])
        if any(c["code"].lower() == code.lower() for c in coupons):
            raise ValueError("coupon code already exists")
        coupon = {
            "code": code,
            "credits": credits,
            "active": True,
            "created_at": _now(),
        }
        coupons.append(coupon)
        _save(data)
        return coupon


def list_coupons() -> list:
    with _LOCK:
        data = _load()
    return list(data.get("coupons", []))


def _find_coupon(data: dict, code: str):
    return next((c for c in data.get("coupons", []) if c["code"].lower() == code.lower()), None)


def set_coupon_active(code: str, active: bool) -> bool:
    """Returns True if a matching coupon was found and updated."""
    with _LOCK:
        data = _load()
        coupon = _find_coupon(data, code)
        if not coupon:
            return False
        coupon["active"] = bool(active)
        _save(data)
        return True


def redeem_coupon(code: str, user_id: int) -> int:
    """Redeems `code` for user_id: grants its credits and records the
    redemption, in one locked operation so a duplicate redemption can never
    race past the "already redeemed" check. Any single user may redeem a
    given code at most once; the same code may be redeemed by many different
    users. Raises ValueError with a specific message for every rejection
    case (unknown code, inactive code, already redeemed by this user).
    Returns the user's new credit balance."""
    with _LOCK:
        data = _load()
        coupon = _find_coupon(data, code)
        if not coupon:
            raise ValueError("invalid coupon code")
        if not coupon.get("active", True):
            raise ValueError("this coupon is not active")
        redemptions = data.setdefault("coupon_redemptions", [])
        already = any(
            r["code"].lower() == coupon["code"].lower() and r["user_id"] == user_id
            for r in redemptions
        )
        if already:
            raise ValueError("you've already redeemed this coupon")
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise ValueError(f"unknown user_id {user_id}")
        redemptions.append({
            "code": coupon["code"],
            "user_id": user_id,
            "credits": coupon["credits"],
            "redeemed_at": _now(),
        })
        user["image_credits"] = user.get("image_credits", DEFAULT_IMAGE_CREDITS) + coupon["credits"]
        _save(data)
        return user["image_credits"]


def list_coupon_redemptions() -> list:
    """Every redemption across every coupon/user - for the admin report's
    per-coupon redemption counts."""
    with _LOCK:
        data = _load()
    return list(data.get("coupon_redemptions", []))


# ---------------------------------------------------------- site settings

def get_site_settings() -> dict:
    """Admin-configurable branding - site display name and the trailing
    footer message shown on every page. Falls back to the defaults for
    data files saved before this feature existed."""
    with _LOCK:
        data = _load()
    settings = data.get("site_settings") or {}
    return {
        "site_name": settings.get("site_name") or DEFAULT_SITE_NAME,
        "footer_text": settings.get("footer_text") or DEFAULT_FOOTER_TEXT,
    }


def set_site_settings(site_name: str, footer_text: str) -> dict:
    with _LOCK:
        data = _load()
        data["site_settings"] = {"site_name": site_name, "footer_text": footer_text}
        _save(data)
        return dict(data["site_settings"])
