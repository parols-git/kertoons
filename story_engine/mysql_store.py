"""
MySQL-backed implementation of every function db.py exposes - a drop-in
alternative to the JSON-file store, selected at runtime via
backend_config.get_backend(). Every function here has the exact same name,
signature, and return shape as its story_engine.db counterpart, so db.py's
public functions can dispatch to either one without callers (server.py,
pipeline.py, etc.) ever needing to know which backend is active.

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
"SAVE10"/"save10" as the same value, matching db.py's manual
`.lower() == .lower()` checks without needing to replicate that logic here.
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


# --------------------------------------------------------------- migration

def migrate_from_json(data: dict):
    """One-time copy of every record from a JSON store's already-loaded
    dict (db.py's _load() shape) into MySQL, preserving every id (user id,
    footer_link id) so relationships (sessions/stories/image_usage/
    payments/redemptions -> user_id) stay intact after the switch. Runs
    inside a single transaction - if anything fails partway through,
    nothing is left half-migrated.

    Assumes ensure_schema() has already been called (so the target
    database and empty tables exist) and that the tables are actually
    empty - this is a first-time import, not a merge."""
    conn = _connect()
    try:
        cur = conn.cursor()

        for u in data.get("users", []):
            cur.execute(
                "INSERT INTO users (id, username, password_salt, password_hash, role, status, "
                "image_credits, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (u["id"], u["username"], u["password_salt"], u["password_hash"],
                 u.get("role", "user"), u.get("status", "active"),
                 u.get("image_credits", DEFAULT_IMAGE_CREDITS), u["created_at"]),
            )

        # Skip any session whose user_id has no matching user - the JSON
        # store never enforced referential integrity, so a session can
        # outlive the account it belonged to (e.g. delete_user() doesn't
        # touch OTHER rows' user_id references). get_user_by_session_token()
        # already treats such a session as functionally invalid (its JOIN/
        # loop finds no matching user and returns None either way), so
        # dropping it here changes no observable behavior - it only avoids
        # violating the FK constraint sessions.user_id -> users.id below.
        valid_user_ids = {u["id"] for u in data.get("users", [])}
        for s in data.get("sessions", []):
            if s["user_id"] not in valid_user_ids:
                continue
            cur.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (%s, %s, %s)",
                (s["token"], s["user_id"], s["created_at"]),
            )

        for s in data.get("stories", []):
            cur.execute(
                "INSERT INTO stories (job_id, user_id, published, view_count, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (s["job_id"], s["user_id"], 1 if s.get("published") else 0,
                 s.get("view_count", 0), s["created_at"]),
            )

        for iu in data.get("image_usage", []):
            cur.execute(
                "INSERT INTO image_usage (user_id, job_id, page_number, prompt, image_url, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (iu["user_id"], iu["job_id"], iu.get("page_number"), iu.get("prompt"),
                 iu.get("image_url"), iu["created_at"]),
            )

        for p in data.get("processed_payments", []):
            cur.execute(
                "INSERT INTO processed_payments (session_id, user_id, credits, amount_usd, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (p["session_id"], p["user_id"], p["credits"], p.get("amount_usd"), p["created_at"]),
            )

        for c in data.get("coupons", []):
            cur.execute(
                "INSERT INTO coupons (code, credits, active, created_at) VALUES (%s, %s, %s, %s)",
                (c["code"], c["credits"], 1 if c.get("active", True) else 0, c["created_at"]),
            )

        for r in data.get("coupon_redemptions", []):
            cur.execute(
                "INSERT INTO coupon_redemptions (code, user_id, credits, redeemed_at) "
                "VALUES (%s, %s, %s, %s)",
                (r["code"], r["user_id"], r["credits"], r["redeemed_at"]),
            )

        for l in data.get("footer_links", []):
            cur.execute(
                "INSERT INTO footer_links (id, name, url, new_tab, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (l["id"], l["name"], l["url"], 1 if l.get("new_tab") else 0, l["created_at"]),
            )

        settings = data.get("site_settings") or {}
        cur.execute(
            "UPDATE site_settings SET site_name=%s, footer_text=%s, contact_email=%s, "
            "contact_phone=%s, page_count=%s, signup_credits=%s WHERE id = 1",
            (settings.get("site_name", "Kertoons"), settings.get("footer_text", ""),
             settings.get("contact_email", ""), settings.get("contact_phone", ""),
             settings.get("page_count", 5), settings.get("signup_credits", DEFAULT_IMAGE_CREDITS)),
        )

        cost = data.get("cost_settings") or {}
        cur.execute(
            "UPDATE cost_settings SET cost_per_image=%s, server_fee=%s WHERE id = 1",
            (cost.get("cost_per_image", 0.0), cost.get("server_fee", 0.0)),
        )

        # ALTER TABLE is DDL, and MySQL/InnoDB implicitly commits the
        # current transaction the moment DDL runs - so these two MUST come
        # last, only once every row insert above has already succeeded.
        # Running them any earlier would silently commit whatever had been
        # inserted so far, defeating the "nothing left half-migrated on
        # failure" guarantee this function promises (conn.rollback() below
        # can't undo a commit that already happened).
        if data.get("users"):
            cur.execute(
                "ALTER TABLE users AUTO_INCREMENT = %s",
                (max(u["id"] for u in data["users"]) + 1,),
            )
        if data.get("footer_links"):
            cur.execute(
                "ALTER TABLE footer_links AUTO_INCREMENT = %s",
                (max(l["id"] for l in data["footer_links"]) + 1,),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
