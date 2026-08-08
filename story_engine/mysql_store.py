"""
The app's real, only data store - db.py re-exports every function here
under the same name, so every caller (server.py, pipeline.py, etc.) keeps
calling story_engine.db.* without needing to know MySQL is what's actually
answering. This used to be one of two interchangeable backends (a JSON
file was the other, switchable at runtime); that design was removed after
it caused a production incident (a table added here could exist without
ever being created in the live database, or the reverse), so this is now
the single source of truth, unconditionally.

Pooled connections, not connect-per-call: a single page view like GET
/api/story/view touches the database half a dozen times (ownership check,
publish check, current-user lookup, view-count increment, ...), each as
its own db.py call. Opening a brand-new TCP connection (plus MySQL's auth
handshake) for every one of those adds up fast - measured at ~2 seconds
each over a real network link, which turns one page load into a
double-digit-second wait. A small pool (see _get_pool below) keeps a
handful of already-authenticated connections warm and hands one out per
call instead, which is what makes this fast enough to actually use.

Uniqueness/case-insensitivity: the database is created with
utf8mb4_unicode_ci collation (see ensure_schema below), which is
case-insensitive for comparisons AND uniqueness constraints - so a UNIQUE
KEY on `username` or a PRIMARY KEY on `code` already treats "Mira"/"mira" or
"SAVE10"/"save10" as the same value without any extra `.lower()` handling
needed in the query logic below.
"""
import secrets
import threading
from datetime import datetime, timezone

import mysql.connector
import mysql.connector.pooling
from mysql.connector import errorcode

from . import backend_config

DEFAULT_IMAGE_CREDITS = 50

_pool = None
_pool_key = None
_pool_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_pool(settings: dict):
    """Lazily creates (or recreates, if the settings changed since - e.g.
    an admin just re-pointed the app at a different MySQL server) a small
    connection pool. Guarded by its own lock rather than reusing some
    other lock in this module, since pool (re)creation is the one thing
    here that must never run twice concurrently."""
    global _pool, _pool_key
    key = (settings["host"], settings["port"], settings["user"],
           settings["database"], settings["password"])
    with _pool_lock:
        if _pool is None or _pool_key != key:
            _pool = mysql.connector.pooling.MySQLConnectionPool(
                pool_name="kertoons_pool",
                pool_size=5,
                pool_reset_session=True,
                host=settings["host"], port=int(settings["port"]),
                user=settings["user"], password=settings["password"],
                database=settings["database"],
                connection_timeout=10,
                autocommit=False,
            )
            _pool_key = key
        return _pool


def _connect(settings: dict = None):
    s = settings or backend_config.get_mysql_settings()
    return _get_pool(s).get_connection()


def test_connection(host: str, port: int, database: str, user: str, password: str) -> tuple:
    """Tries to connect with the given settings without touching
    backend_config. Returns (ok: bool, message: str) - used by the admin
    "Test connection" button so a typo'd password or unreachable host is
    caught before anything is saved or switched over."""
    try:
        conn = mysql.connector.connect(
            host=host, port=int(port), user=user, password=password,
            database=database or None, connection_timeout=8,
        )
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        conn.close()
        return True, f"Connected - MySQL {version}"
    except mysql.connector.Error as e:
        if e.errno == errorcode.ER_BAD_DB_ERROR:
            # Database doesn't exist yet - not a failure for our purposes,
            # ensure_schema() below creates it. Confirm the SERVER is
            # reachable with a database-less connection instead.
            try:
                conn = mysql.connector.connect(
                    host=host, port=int(port), user=user, password=password,
                    connection_timeout=8,
                )
                conn.close()
                return True, f"Connected - database {database!r} doesn't exist yet and will be created"
            except mysql.connector.Error as e2:
                return False, str(e2)
        return False, str(e)
    except Exception as e:
        return False, str(e)


# --------------------------------------------------------------- schema

_SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(190) NOT NULL,
        password_salt VARCHAR(255) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'user',
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        image_credits INT NOT NULL DEFAULT 50,
        created_at VARCHAR(64) NOT NULL,
        UNIQUE KEY uniq_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS sessions (
        token VARCHAR(64) PRIMARY KEY,
        user_id INT NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        INDEX idx_sessions_user_id (user_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # No FK on user_id - a story's owner account can be deleted while the
    # story record itself is deliberately left in place (orphaned), exactly
    # matching db.py's delete_user()/delete_story_record() behavior.
    """CREATE TABLE IF NOT EXISTS stories (
        job_id VARCHAR(64) PRIMARY KEY,
        user_id INT NOT NULL,
        published TINYINT(1) NOT NULL DEFAULT 0,
        view_count INT NOT NULL DEFAULT 0,
        created_at VARCHAR(64) NOT NULL,
        INDEX idx_stories_user_id (user_id),
        INDEX idx_stories_published (published)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS image_usage (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        job_id VARCHAR(64) NOT NULL,
        page_number INT,
        prompt MEDIUMTEXT,
        image_url VARCHAR(1024),
        created_at VARCHAR(64) NOT NULL,
        INDEX idx_image_usage_user_id (user_id),
        INDEX idx_image_usage_job_id (job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS processed_payments (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(255) NOT NULL,
        user_id INT NOT NULL,
        credits INT NOT NULL,
        amount_usd DECIMAL(10,2),
        created_at VARCHAR(64) NOT NULL,
        UNIQUE KEY uniq_payment_session (session_id),
        INDEX idx_payments_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS coupons (
        code VARCHAR(100) PRIMARY KEY,
        credits INT NOT NULL,
        active TINYINT(1) NOT NULL DEFAULT 1,
        created_at VARCHAR(64) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS coupon_redemptions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        code VARCHAR(100) NOT NULL,
        user_id INT NOT NULL,
        credits INT NOT NULL,
        redeemed_at VARCHAR(64) NOT NULL,
        UNIQUE KEY uniq_redemption_code_user (code, user_id),
        INDEX idx_redemptions_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS footer_links (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        url VARCHAR(1024) NOT NULL,
        new_tab TINYINT(1) NOT NULL DEFAULT 0,
        created_at VARCHAR(64) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # Single-row tables (id is always 1) - simplest way to carry over
    # site_settings/cost_settings' "one dict for the whole site" shape into
    # a relational table without a separate key/value schema.
    """CREATE TABLE IF NOT EXISTS site_settings (
        id TINYINT PRIMARY KEY DEFAULT 1,
        site_name VARCHAR(255) NOT NULL,
        footer_text VARCHAR(1024),
        contact_email VARCHAR(255),
        contact_phone VARCHAR(64),
        page_count INT NOT NULL,
        signup_credits INT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS cost_settings (
        id TINYINT PRIMARY KEY DEFAULT 1,
        cost_per_image DECIMAL(10,4) NOT NULL DEFAULT 0,
        server_fee DECIMAL(10,4) NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # No FK on owner_user_id - same orphan-on-delete convention as stories
    # above (a published character should survive its creator's account
    # being deleted, same as a published story does).
    """CREATE TABLE IF NOT EXISTS characters (
        id INT AUTO_INCREMENT PRIMARY KEY,
        owner_user_id INT NOT NULL,
        name VARCHAR(190) NOT NULL,
        type VARCHAR(20) NOT NULL DEFAULT 'kid',
        description TEXT,
        appearance TEXT NOT NULL,
        personality TEXT,
        prompt_used TEXT,
        reference_image_path VARCHAR(512),
        category VARCHAR(100),
        age_group VARCHAR(50),
        school VARCHAR(190),
        status VARCHAR(20) NOT NULL DEFAULT 'draft',
        moderation_note TEXT,
        source_story_job_id VARCHAR(64),
        source_page_number INT,
        view_count INT NOT NULL DEFAULT 0,
        use_count INT NOT NULL DEFAULT 0,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL,
        INDEX idx_characters_owner (owner_user_id),
        INDEX idx_characters_status (status),
        INDEX idx_characters_category (category),
        INDEX idx_characters_age_group (age_group),
        INDEX idx_characters_school (school)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS competitions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        theme VARCHAR(255),
        start_date VARCHAR(32) NOT NULL,
        end_date VARCHAR(32) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        created_by INT,
        created_at VARCHAR(64) NOT NULL,
        finalized_at VARCHAR(64),
        INDEX idx_competitions_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # No FK on job_id (a story is a JSON file on disk, not a DB-joinable
    # row - same "deliberately no FK across that boundary" convention as
    # image_usage.job_id above) or on user_id (same orphan-on-delete
    # convention as every other user_id column in this schema).
    """CREATE TABLE IF NOT EXISTS competition_entries (
        id INT AUTO_INCREMENT PRIMARY KEY,
        competition_id INT NOT NULL,
        job_id VARCHAR(64) NOT NULL,
        user_id INT NOT NULL,
        entry_type VARCHAR(20) NOT NULL,
        submitted_at VARCHAR(64) NOT NULL,
        score_creativity DECIMAL(5,2),
        score_originality DECIMAL(5,2),
        score_structure DECIMAL(5,2),
        score_educational DECIMAL(5,2),
        score_language DECIMAL(5,2),
        score_total DECIMAL(6,2),
        score_feedback TEXT,
        scored_at VARCHAR(64),
        `rank` INT,
        is_winner TINYINT(1) NOT NULL DEFAULT 0,
        certificate_participation_path VARCHAR(512),
        certificate_winner_path VARCHAR(512),
        created_at VARCHAR(64) NOT NULL,
        UNIQUE KEY uniq_competition_job (competition_id, job_id),
        INDEX idx_entries_competition (competition_id),
        INDEX idx_entries_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]


def ensure_schema(settings: dict = None):
    """Creates the target database (if missing) and every table (if
    missing) - idempotent, safe to call every time MySQL is enabled/
    re-tested. Connects WITHOUT selecting a database first, since the
    database itself might not exist yet."""
    s = settings or backend_config.get_mysql_settings()
    conn = mysql.connector.connect(
        host=s["host"], port=int(s["port"]), user=s["user"], password=s["password"],
        connection_timeout=15,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{s['database']}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cur.execute(f"USE `{s['database']}`")
        for stmt in _SCHEMA_STATEMENTS:
            cur.execute(stmt)
        # Seed the two single-row settings tables so later UPSERTs always
        # have a row to update, and so a fresh MySQL backend behaves like a
        # freshly-created JSON file (db.py's _SKELETON defaults).
        cur.execute("SELECT COUNT(*) FROM site_settings WHERE id = 1")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO site_settings (id, site_name, footer_text, contact_email, "
                "contact_phone, page_count, signup_credits) VALUES (1, 'Kertoons', "
                "'kertoons.com - Another Elisda AI project', '', '', 5, %s)",
                (DEFAULT_IMAGE_CREDITS,),
            )
        cur.execute("SELECT COUNT(*) FROM cost_settings WHERE id = 1")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO cost_settings (id, cost_per_image, server_fee) VALUES (1, 0, 0)")
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------- users

def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
        "image_credits": row["image_credits"],
        "role": row["role"],
        "status": row["status"],
    }


def create_user(username: str, password_salt: str, password_hash: str, role: str = "user") -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT signup_credits FROM site_settings WHERE id = 1")
        row = cur.fetchone()
        signup_credits = row["signup_credits"] if row else DEFAULT_IMAGE_CREDITS
        created_at = _now()
        try:
            cur.execute(
                "INSERT INTO users (username, password_salt, password_hash, role, status, "
                "image_credits, created_at) VALUES (%s, %s, %s, %s, 'active', %s, %s)",
                (username, password_salt, password_hash, role, signup_credits, created_at),
            )
        except mysql.connector.IntegrityError:
            raise ValueError("username already taken")
        user_id = cur.lastrowid
        conn.commit()
        return {
            "id": user_id, "username": username, "created_at": created_at,
            "image_credits": signup_credits, "role": role, "status": "active",
        }
    finally:
        conn.close()


def _create_privileged_if_missing(username: str, password_salt: str, password_hash: str, role: str) -> str:
    """Shared body for create_admin_if_missing/create_superadmin_if_missing -
    same promote-in-place-without-touching-password semantics as db.py."""
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        existing = cur.fetchone()
        if existing is not None:
            already_privileged = (
                existing["role"] in ("admin", "superadmin") if role == "admin"
                else existing["role"] == "superadmin"
            )
            if already_privileged:
                return "unchanged"
            cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, existing["id"]))
            conn.commit()
            return "promoted"
        cur.execute("SELECT signup_credits FROM site_settings WHERE id = 1")
        row = cur.fetchone()
        signup_credits = row["signup_credits"] if row else DEFAULT_IMAGE_CREDITS
        cur.execute(
            "INSERT INTO users (username, password_salt, password_hash, role, status, "
            "image_credits, created_at) VALUES (%s, %s, %s, %s, 'active', %s, %s)",
            (username, password_salt, password_hash, role, signup_credits, _now()),
        )
        conn.commit()
        return "created"
    finally:
        conn.close()


def create_admin_if_missing(username: str, password_salt: str, password_hash: str) -> str:
    return _create_privileged_if_missing(username, password_salt, password_hash, "admin")


def create_superadmin_if_missing(username: str, password_salt: str, password_hash: str) -> str:
    return _create_privileged_if_missing(username, password_salt, password_hash, "superadmin")


def get_user_by_username(username: str) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        return cur.fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return _public_user(row) if row else None
    finally:
        conn.close()


def list_all_users() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users")
        return [_public_user(r) for r in cur.fetchall()]
    finally:
        conn.close()


def set_user_status(user_id: int, status: str) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET status = %s WHERE id = %s", (status, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_user(user_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        # Sessions cascade via the FK; stories/image_usage/payments/
        # redemptions are deliberately left orphaned, same as db.py.
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------- sessions

def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (%s, %s, %s)",
            (token, user_id, _now()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_user_by_session_token(token: str) -> dict:
    if not token:
        return None
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = %s",
            (token,),
        )
        row = cur.fetchone()
        return _public_user(row) if row else None
    finally:
        conn.close()


def delete_session(token: str):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- stories

def create_story_record(job_id: str, user_id: int):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO stories (job_id, user_id, published, view_count, created_at) "
            "VALUES (%s, %s, 0, 0, %s)",
            (job_id, user_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_story_record(job_id: str) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM stories WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if row:
            row["published"] = bool(row["published"])
        return row
    finally:
        conn.close()


def get_story_owner_id(job_id: str):
    record = get_story_record(job_id)
    return record["user_id"] if record else None


def is_story_published(job_id: str) -> bool:
    record = get_story_record(job_id)
    return bool(record and record["published"])


def set_story_published(job_id: str, published: bool) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE stories SET published = %s WHERE job_id = %s",
            (1 if published else 0, job_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def increment_story_view_count(job_id: str) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE stories SET view_count = view_count + 1 WHERE job_id = %s", (job_id,))
        if cur.rowcount == 0:
            conn.commit()
            return 0
        conn.commit()
        cur.execute("SELECT view_count FROM stories WHERE job_id = %s", (job_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def delete_story_record(job_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM stories WHERE job_id = %s", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_published_stories() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM stories WHERE published = 1")
        rows = cur.fetchall()
        for r in rows:
            r["published"] = bool(r["published"])
        return rows
    finally:
        conn.close()


def list_stories_for_user(user_id: int) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM stories WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        for r in rows:
            r["published"] = bool(r["published"])
        return rows
    finally:
        conn.close()


def list_all_stories() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT s.*, u.username AS owner_username FROM stories s "
            "LEFT JOIN users u ON u.id = s.user_id"
        )
        rows = cur.fetchall()
        for r in rows:
            r["published"] = bool(r["published"])
        return rows
    finally:
        conn.close()


# ------------------------------------------------------------- image usage

def get_image_credits(user_id: int) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT image_credits FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def record_image_generation(user_id: int, job_id: str, page_number: int,
                             prompt: str, image_url: str) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO image_usage (user_id, job_id, page_number, prompt, image_url, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, job_id, page_number, prompt, image_url, _now()),
        )
        cur.execute("UPDATE users SET image_credits = image_credits - 1 WHERE id = %s", (user_id,))
        conn.commit()
        cur.execute("SELECT image_credits FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def add_credits(user_id: int, amount: int) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT image_credits FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown user_id {user_id}")
        cur.execute("UPDATE users SET image_credits = image_credits + %s WHERE id = %s", (amount, user_id))
        conn.commit()
        return row[0] + amount
    finally:
        conn.close()


def grant_credits_for_payment(user_id: int, session_id: str, credits: int,
                               amount_usd: float = None) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM processed_payments WHERE session_id = %s", (session_id,))
        if cur.fetchone():
            cur.execute("SELECT image_credits FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else 0
        cur.execute("SELECT image_credits FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown user_id {user_id}")
        cur.execute(
            "INSERT INTO processed_payments (session_id, user_id, credits, amount_usd, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (session_id, user_id, credits, amount_usd, _now()),
        )
        cur.execute("UPDATE users SET image_credits = image_credits + %s WHERE id = %s", (credits, user_id))
        conn.commit()
        return row[0] + credits
    finally:
        conn.close()


def list_payments_for_user(user_id: int) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM processed_payments WHERE user_id = %s", (user_id,))
        return cur.fetchall()
    finally:
        conn.close()


def list_image_usage_for_user(user_id: int) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM image_usage WHERE user_id = %s", (user_id,))
        return cur.fetchall()
    finally:
        conn.close()


def list_all_image_usage() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM image_usage")
        return cur.fetchall()
    finally:
        conn.close()


def list_all_payments() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT p.*, u.username FROM processed_payments p LEFT JOIN users u ON u.id = p.user_id"
        )
        return cur.fetchall()
    finally:
        conn.close()


# ----------------------------------------------------------------- coupons

def create_coupon(code: str, credits: int) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor()
        created_at = _now()
        try:
            cur.execute(
                "INSERT INTO coupons (code, credits, active, created_at) VALUES (%s, %s, 1, %s)",
                (code, credits, created_at),
            )
        except mysql.connector.IntegrityError:
            raise ValueError("coupon code already exists")
        conn.commit()
        return {"code": code, "credits": credits, "active": True, "created_at": created_at}
    finally:
        conn.close()


def list_coupons() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM coupons")
        rows = cur.fetchall()
        for r in rows:
            r["active"] = bool(r["active"])
        return rows
    finally:
        conn.close()


def set_coupon_active(code: str, active: bool) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE coupons SET active = %s WHERE code = %s", (1 if active else 0, code))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def redeem_coupon(code: str, user_id: int) -> int:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM coupons WHERE code = %s", (code,))
        coupon = cur.fetchone()
        if not coupon:
            raise ValueError("invalid coupon code")
        if not coupon["active"]:
            raise ValueError("this coupon is not active")
        cur.execute(
            "SELECT id FROM coupon_redemptions WHERE code = %s AND user_id = %s",
            (coupon["code"], user_id),
        )
        if cur.fetchone():
            raise ValueError("you've already redeemed this coupon")
        cur.execute("SELECT image_credits FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown user_id {user_id}")
        try:
            cur.execute(
                "INSERT INTO coupon_redemptions (code, user_id, credits, redeemed_at) "
                "VALUES (%s, %s, %s, %s)",
                (coupon["code"], user_id, coupon["credits"], _now()),
            )
        except mysql.connector.IntegrityError:
            raise ValueError("you've already redeemed this coupon")
        cur.execute(
            "UPDATE users SET image_credits = image_credits + %s WHERE id = %s",
            (coupon["credits"], user_id),
        )
        conn.commit()
        return row["image_credits"] + coupon["credits"]
    finally:
        conn.close()


def list_coupon_redemptions() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM coupon_redemptions")
        return cur.fetchall()
    finally:
        conn.close()


# ----------------------------------------------------------- footer links

def list_footer_links() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM footer_links ORDER BY id ASC")
        rows = cur.fetchall()
        for r in rows:
            r["new_tab"] = bool(r["new_tab"])
        return rows
    finally:
        conn.close()


def add_footer_link(name: str, url: str, new_tab: bool) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor()
        created_at = _now()
        cur.execute(
            "INSERT INTO footer_links (name, url, new_tab, created_at) VALUES (%s, %s, %s, %s)",
            (name, url, 1 if new_tab else 0, created_at),
        )
        link_id = cur.lastrowid
        conn.commit()
        return {"id": link_id, "name": name, "url": url, "new_tab": bool(new_tab), "created_at": created_at}
    finally:
        conn.close()


def delete_footer_link(link_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM footer_links WHERE id = %s", (link_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------- site settings

def get_site_settings() -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM site_settings WHERE id = 1")
        row = cur.fetchone()
        row.pop("id", None)
        return row
    finally:
        conn.close()


def set_site_settings(site_name: str, footer_text: str, contact_email: str = "",
                       contact_phone: str = "", page_count: int = None,
                       signup_credits: int = None) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM site_settings WHERE id = 1")
        existing = cur.fetchone() or {}
        final_page_count = page_count if page_count is not None else existing.get("page_count", 5)
        final_signup_credits = (
            signup_credits if signup_credits is not None
            else existing.get("signup_credits", DEFAULT_IMAGE_CREDITS)
        )
        cur.execute(
            "UPDATE site_settings SET site_name=%s, footer_text=%s, contact_email=%s, "
            "contact_phone=%s, page_count=%s, signup_credits=%s WHERE id = 1",
            (site_name, footer_text, contact_email, contact_phone, final_page_count, final_signup_credits),
        )
        conn.commit()
        return {
            "site_name": site_name, "footer_text": footer_text, "contact_email": contact_email,
            "contact_phone": contact_phone, "page_count": final_page_count,
            "signup_credits": final_signup_credits,
        }
    finally:
        conn.close()


# ------------------------------------------------------------ cost settings

def get_cost_settings() -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM cost_settings WHERE id = 1")
        row = cur.fetchone()
        row.pop("id", None)
        row["cost_per_image"] = float(row["cost_per_image"])
        row["server_fee"] = float(row["server_fee"])
        return row
    finally:
        conn.close()


def set_cost_settings(cost_per_image: float, server_fee: float) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE cost_settings SET cost_per_image = %s, server_fee = %s WHERE id = 1",
            (cost_per_image, server_fee),
        )
        conn.commit()
        return {"cost_per_image": cost_per_image, "server_fee": server_fee}
    finally:
        conn.close()


# -------------------------------------------------------------- characters

def create_character(owner_user_id: int, name: str, type: str, description: str,
                      appearance: str, personality: str = "", prompt_used: str = None,
                      category: str = None, age_group: str = None, school: str = None,
                      source_story_job_id: str = None, source_page_number: int = None) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor()
        now = _now()
        cur.execute(
            "INSERT INTO characters (owner_user_id, name, type, description, appearance, "
            "personality, prompt_used, category, age_group, school, status, "
            "source_story_job_id, source_page_number, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s)",
            (owner_user_id, name, type, description, appearance, personality, prompt_used,
             category, age_group, school, source_story_job_id, source_page_number, now, now),
        )
        character_id = cur.lastrowid
        conn.commit()
        return {
            "id": character_id, "owner_user_id": owner_user_id, "name": name, "type": type,
            "description": description, "appearance": appearance, "personality": personality,
            "prompt_used": prompt_used, "reference_image_path": None, "category": category,
            "age_group": age_group, "school": school, "status": "draft", "moderation_note": None,
            "source_story_job_id": source_story_job_id, "source_page_number": source_page_number,
            "view_count": 0, "use_count": 0, "created_at": now, "updated_at": now,
        }
    finally:
        conn.close()


def update_character(character_id: int, name: str = None, description: str = None,
                      appearance: str = None, personality: str = None, type: str = None,
                      category: str = None, age_group: str = None, school: str = None) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        fields, values = [], []
        for column, value in (
            ("name", name), ("description", description), ("appearance", appearance),
            ("personality", personality), ("type", type), ("category", category),
            ("age_group", age_group), ("school", school),
        ):
            if value is not None:
                fields.append(f"{column} = %s")
                values.append(value)
        if not fields:
            cur.execute("SELECT * FROM characters WHERE id = %s", (character_id,))
            return cur.fetchone()
        fields.append("updated_at = %s")
        values.append(_now())
        values.append(character_id)
        cur.execute(f"UPDATE characters SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
        cur.execute("SELECT * FROM characters WHERE id = %s", (character_id,))
        return cur.fetchone()
    finally:
        conn.close()


def set_character_reference_image(character_id: int, path: str, prompt_used: str = None) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        if prompt_used is not None:
            cur.execute(
                "UPDATE characters SET reference_image_path = %s, prompt_used = %s, "
                "updated_at = %s WHERE id = %s",
                (path, prompt_used, _now(), character_id),
            )
        else:
            cur.execute(
                "UPDATE characters SET reference_image_path = %s, updated_at = %s WHERE id = %s",
                (path, _now(), character_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_character(character_id: int) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM characters WHERE id = %s", (character_id,))
        return cur.fetchone()
    finally:
        conn.close()


def delete_character(character_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM characters WHERE id = %s", (character_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def submit_character_for_publication(character_id: int, target_status: str) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "UPDATE characters SET status = %s, moderation_note = NULL, updated_at = %s WHERE id = %s",
            (target_status, _now(), character_id),
        )
        conn.commit()
        cur.execute("SELECT * FROM characters WHERE id = %s", (character_id,))
        return cur.fetchone()
    finally:
        conn.close()


def set_character_status(character_id: int, status: str, moderation_note: str = None) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "UPDATE characters SET status = %s, moderation_note = %s, updated_at = %s WHERE id = %s",
            (status, moderation_note, _now(), character_id),
        )
        conn.commit()
        cur.execute("SELECT * FROM characters WHERE id = %s", (character_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_characters_for_user(owner_user_id: int) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM characters WHERE owner_user_id = %s ORDER BY created_at DESC",
            (owner_user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def list_pending_characters() -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT c.*, u.username AS owner_username FROM characters c "
            "LEFT JOIN users u ON u.id = c.owner_user_id WHERE c.status = 'pending' "
            "ORDER BY c.created_at ASC"
        )
        return cur.fetchall()
    finally:
        conn.close()


def list_published_characters(category: str = None, age_group: str = None,
                               school: str = None, search: str = None,
                               sort: str = "recent") -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        clauses = ["status = 'published'"]
        params = []
        if category:
            clauses.append("category = %s")
            params.append(category)
        if age_group:
            clauses.append("age_group = %s")
            params.append(age_group)
        if school:
            clauses.append("school = %s")
            params.append(school)
        if search:
            clauses.append("(name LIKE %s OR description LIKE %s)")
            needle = f"%{search}%"
            params.extend([needle, needle])
        order = "use_count DESC, created_at DESC" if sort == "popular" else "created_at DESC"
        cur.execute(
            f"SELECT * FROM characters WHERE {' AND '.join(clauses)} ORDER BY {order}",
            params,
        )
        return cur.fetchall()
    finally:
        conn.close()


def increment_character_use_count(character_id: int) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE characters SET use_count = use_count + 1 WHERE id = %s", (character_id,))
        if cur.rowcount == 0:
            conn.commit()
            return 0
        conn.commit()
        cur.execute("SELECT use_count FROM characters WHERE id = %s", (character_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def increment_character_view_count(character_id: int) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE characters SET view_count = view_count + 1 WHERE id = %s", (character_id,))
        if cur.rowcount == 0:
            conn.commit()
            return 0
        conn.commit()
        cur.execute("SELECT view_count FROM characters WHERE id = %s", (character_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------ competitions

def create_competition(title: str, description: str, theme: str,
                        start_date: str, end_date: str, created_by: int) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor()
        now = _now()
        cur.execute(
            "INSERT INTO competitions (title, description, theme, start_date, end_date, "
            "status, created_by, created_at) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)",
            (title, description, theme, start_date, end_date, created_by, now),
        )
        competition_id = cur.lastrowid
        conn.commit()
        return {
            "id": competition_id, "title": title, "description": description, "theme": theme,
            "start_date": start_date, "end_date": end_date, "status": "active",
            "created_by": created_by, "created_at": now, "finalized_at": None,
        }
    finally:
        conn.close()


def update_competition(competition_id: int, title: str = None, description: str = None,
                        theme: str = None, start_date: str = None, end_date: str = None) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        fields, values = [], []
        for column, value in (
            ("title", title), ("description", description), ("theme", theme),
            ("start_date", start_date), ("end_date", end_date),
        ):
            if value is not None:
                fields.append(f"{column} = %s")
                values.append(value)
        if fields:
            values.append(competition_id)
            cur.execute(f"UPDATE competitions SET {', '.join(fields)} WHERE id = %s", values)
            conn.commit()
        cur.execute("SELECT * FROM competitions WHERE id = %s", (competition_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_competitions(status: str = None) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        if status:
            cur.execute("SELECT * FROM competitions WHERE status = %s ORDER BY created_at DESC", (status,))
        else:
            cur.execute("SELECT * FROM competitions ORDER BY created_at DESC")
        return cur.fetchall()
    finally:
        conn.close()


def get_competition(competition_id: int) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM competitions WHERE id = %s", (competition_id,))
        return cur.fetchone()
    finally:
        conn.close()


def create_or_update_entry(competition_id: int, job_id: str, user_id: int, entry_type: str) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM competition_entries WHERE competition_id = %s AND job_id = %s",
            (competition_id, job_id),
        )
        existing = cur.fetchone()
        now = _now()
        if existing:
            cur.execute(
                "UPDATE competition_entries SET entry_type = %s, submitted_at = %s, "
                "score_creativity = NULL, score_originality = NULL, score_structure = NULL, "
                "score_educational = NULL, score_language = NULL, score_total = NULL, "
                "score_feedback = NULL, scored_at = NULL, `rank` = NULL, is_winner = 0 "
                "WHERE id = %s",
                (entry_type, now, existing["id"]),
            )
            conn.commit()
            cur.execute("SELECT * FROM competition_entries WHERE id = %s", (existing["id"],))
            return cur.fetchone()
        cur.execute(
            "INSERT INTO competition_entries (competition_id, job_id, user_id, entry_type, "
            "submitted_at, is_winner, created_at) VALUES (%s, %s, %s, %s, %s, 0, %s)",
            (competition_id, job_id, user_id, entry_type, now, now),
        )
        entry_id = cur.lastrowid
        conn.commit()
        cur.execute("SELECT * FROM competition_entries WHERE id = %s", (entry_id,))
        return cur.fetchone()
    finally:
        conn.close()


def set_entry_scores(entry_id: int, scores: dict, feedback: str) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "UPDATE competition_entries SET score_creativity = %s, score_originality = %s, "
            "score_structure = %s, score_educational = %s, score_language = %s, "
            "score_total = %s, score_feedback = %s, scored_at = %s WHERE id = %s",
            (scores.get("creativity"), scores.get("originality"), scores.get("story_structure"),
             scores.get("educational_value"), scores.get("language_quality"), scores.get("total"),
             feedback, _now(), entry_id),
        )
        conn.commit()
        cur.execute("SELECT * FROM competition_entries WHERE id = %s", (entry_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_entries_for_competition(competition_id: int) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT e.*, u.username FROM competition_entries e "
            "LEFT JOIN users u ON u.id = e.user_id WHERE e.competition_id = %s",
            (competition_id,),
        )
        rows = cur.fetchall()
        for r in rows:
            r["is_winner"] = bool(r["is_winner"])
        return rows
    finally:
        conn.close()


def get_entry(competition_id: int, job_id: str) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM competition_entries WHERE competition_id = %s AND job_id = %s",
            (competition_id, job_id),
        )
        row = cur.fetchone()
        if row:
            row["is_winner"] = bool(row["is_winner"])
        return row
    finally:
        conn.close()


def get_entry_by_id(entry_id: int) -> dict:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM competition_entries WHERE id = %s", (entry_id,))
        row = cur.fetchone()
        if row:
            row["is_winner"] = bool(row["is_winner"])
        return row
    finally:
        conn.close()


def list_entries_for_user(user_id: int) -> list:
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM competition_entries WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        for r in rows:
            r["is_winner"] = bool(r["is_winner"])
        return rows
    finally:
        conn.close()


def finalize_competition(competition_id: int, ranked_entries: list) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM competitions WHERE id = %s", (competition_id,))
        if not cur.fetchone():
            return False
        for entry_id, rank, is_winner in ranked_entries:
            cur.execute(
                "UPDATE competition_entries SET `rank` = %s, is_winner = %s WHERE id = %s",
                (rank, 1 if is_winner else 0, entry_id),
            )
        cur.execute(
            "UPDATE competitions SET status = 'closed', finalized_at = %s WHERE id = %s",
            (_now(), competition_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_entry_certificates(entry_id: int, participation_path: str, winner_path: str = None) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        if winner_path is not None:
            cur.execute(
                "UPDATE competition_entries SET certificate_participation_path = %s, "
                "certificate_winner_path = %s WHERE id = %s",
                (participation_path, winner_path, entry_id),
            )
        else:
            cur.execute(
                "UPDATE competition_entries SET certificate_participation_path = %s WHERE id = %s",
                (participation_path, entry_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

