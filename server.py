#!/usr/bin/env python3
"""
Kertoons - local dev server.

Run:  python3 server.py
Then open http://127.0.0.1:8765 in a browser.

Implements the pipeline described in p.md.txt:
  - web form takes 2-3 sentences + region + optional secondary language
    (+ optional child photo for character inspiration)
  - story text generated ONLY via OpenAI (story_engine/openai_client.py)
  - 3D cartoon images generated ONLY via the image API the spec calls
    "Deepak.org" (story_engine/image_client.py targets the real, closest
    match: DeepAI - see that file's docstring)
  - falls back to MOCK MODE automatically when API keys are absent, so
    the whole app is runnable and testable locally with zero credentials
  - assembles the finished story into a downloadable ZIP / PDF storybook

Accounts: every story belongs to the logged-in user who created it. Only its
owner can create/regenerate/publish it; everyone else only ever sees it if
its owner has published it (see story_engine/db.py for how ownership and
publish state are tracked, and the visibility rule enforced by _can_view()
below).
"""
import os
import re
import csv
import io
import json
import time
import base64
import random
import secrets
import mimetypes
import shutil
import threading
import traceback
import uuid
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PIL import Image, ImageDraw, ImageFont

from story_engine import config, db, auth, payments, share_page
from story_engine.pipeline import run_job, regenerate_page_image
from story_engine.book_export import build_zip, build_pdf
from story_engine.image_client import ImageGenerationError

JOBS = {}
JOBS_LOCK = threading.Lock()

# In-memory only (never persisted) - a login captcha is short-lived and
# meaningless after the page is closed or the code is used, so there's
# nothing worth surviving a server restart for.
CAPTCHAS = {}
CAPTCHAS_LOCK = threading.Lock()
CAPTCHA_TTL_SECONDS = 300  # 5 minutes - long enough to read and type, short enough that abandoned page loads don't grow this dict forever

_JOB_ID_RE = re.compile(r"[0-9a-f]{6,40}")
_USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,30}")
_SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# Once a user's balance is negative, every further create/regenerate is
# blocked until they top up. A story costs 5 credits in one go, so someone
# starting at, say, 2 credits can still start one (ending at -3) - the
# check is on the balance BEFORE the action, not on whether that action
# alone would push it negative.
MIN_CREDITS_TO_GENERATE = 0
# Free instant top-up amount used by POST /api/credits/checkout when
# config.MOCK_PAYMENTS is on (no Stripe key configured) - the demo/hobby
# fallback so the whole app, including "buying" credits, is testable
# without a real payment processor. With a real key, that same button click
# instead redirects to a real $5-for-50-credits Stripe Checkout page (see
# story_engine/payments.py).
ADD_CREDITS_AMOUNT = 20
# Sent straight to the image API verbatim when regenerating (see
# image_client.generate_scene_image's custom_prompt) - capped generously
# above what a real image prompt needs, just to stop a pathological payload.
MAX_CUSTOM_PROMPT_LEN = 2000


def new_job_id():
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------- captcha
#
# A simple 4-digit numeric captcha on the login form, meant to slow down
# scripted brute-force login attempts, not to defeat a determined attacker
# with OCR - deliberately simple (stdlib/Pillow only, no external captcha
# service or new dependency) to match this app's zero-paid-services ethos.

def _issue_captcha():
    """Generates a new 4-digit login captcha. Returns (captcha_id, code).
    Opportunistically prunes expired entries on every call, so CAPTCHAS
    never grows unbounded just from visitors who load the login page and
    leave without submitting."""
    now = time.time()
    code = f"{random.randint(0, 9999):04d}"
    captcha_id = secrets.token_hex(12)
    with CAPTCHAS_LOCK:
        for expired_id in [cid for cid, (_, exp) in CAPTCHAS.items() if exp < now]:
            CAPTCHAS.pop(expired_id, None)
        CAPTCHAS[captcha_id] = (code, now + CAPTCHA_TTL_SECONDS)
    return captcha_id, code


def _peek_captcha_code(captcha_id: str):
    """The code for captcha_id, WITHOUT consuming it - used only by the
    image endpoint, which may legitimately be requested more than once for
    the same captcha_id (e.g. a slow image load retried by the browser).
    Returns None if captcha_id is unknown or has expired."""
    with CAPTCHAS_LOCK:
        entry = CAPTCHAS.get(captcha_id)
        if not entry:
            return None
        code, expires = entry
        if expires < time.time():
            CAPTCHAS.pop(captcha_id, None)
            return None
        return code


def _consume_captcha(captcha_id: str, answer: str) -> bool:
    """Checks answer against captcha_id's code - single-use: the entry is
    removed on ANY attempt, correct or not, so neither a captured answer
    nor a guessed one can ever be replayed, and a wrong attempt always
    forces a fresh captcha rather than allowing repeated guesses against
    the same one."""
    with CAPTCHAS_LOCK:
        entry = CAPTCHAS.pop(captcha_id, None)
    if not entry:
        return False
    code, expires = entry
    if expires < time.time():
        return False
    return (answer or "").strip() == code


def _generate_captcha_image(code: str) -> bytes:
    """Renders `code` (4 digits) as a small PNG with randomized per-digit
    vertical jitter and background noise lines/dots - basic obfuscation
    against trivial automated OCR, not a rigorous anti-bot measure."""
    W, H = 140, 50
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    rng = random.Random()  # fresh randomness per render, independent of `code`

    for _ in range(6):
        p1 = (rng.randint(0, W), rng.randint(0, H))
        p2 = (rng.randint(0, W), rng.randint(0, H))
        shade = rng.randint(170, 215)
        draw.line([p1, p2], fill=(shade, shade, shade), width=1)

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
    except Exception:
        try:
            font = ImageFont.load_default(size=28)  # Pillow >= 9.2
        except TypeError:
            font = ImageFont.load_default()

    x = 18
    for ch in code:
        y = rng.randint(4, 14)
        draw.text((x, y), ch, font=font, fill=(35, 30, 70))
        x += 27

    for _ in range(90):
        px, py = rng.randint(0, W - 1), rng.randint(0, H - 1)
        shade = rng.randint(160, 210)
        draw.point((px, py), fill=(shade, shade, shade))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "KertoonsDev/1.0"

    # -------------------------------------------------------------- helpers
    def _send_json(self, obj, status=200, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_str: str, status=200):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str, download_name=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status)

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _read_json_body(self):
        raw = self._read_raw_body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _serve_static_file(self, rel_path: str):
        safe_path = os.path.normpath(rel_path).lstrip(os.sep)
        full_path = os.path.join(config.STATIC_DIR, safe_path)
        if not full_path.startswith(config.STATIC_DIR) or not os.path.isfile(full_path):
            self.send_response(404)
            self.end_headers()
            return
        ctype, _ = mimetypes.guess_type(full_path)
        with open(full_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _load_story_summary(self, job_id: str):
        story_path = os.path.join(config.GENERATED_DIR, job_id, "story.json")
        if not os.path.isfile(story_path):
            return None
        try:
            with open(story_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    # --------------------------------------------------------- auth/session
    def _session_token(self):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:
            return None
        morsel = jar.get("session")
        return morsel.value if morsel else None

    def _current_user(self):
        token = self._session_token()
        if not token:
            return None
        user = db.get_user_by_session_token(token)
        # A suspended account is treated as logged-out everywhere, the
        # moment an admin flips the flag - this one check blocks every
        # authenticated action app-wide without needing to touch each
        # endpoint individually. Login itself has a more specific rejection
        # (see /api/login) so a suspended user sees "account suspended"
        # rather than a generic "logged out" state.
        if user and user.get("status") == "suspended":
            return None
        return user

    def _require_admin(self):
        """Returns the current user if they're an admin, otherwise sends a
        403 and returns None. Every /api/admin/* handler must call this
        first - the client-reported role is never trusted, only what's
        actually stored server-side."""
        user = self._current_user()
        if not user or user.get("role") != "admin":
            self._send_error_json("admin access required", 403)
            return None
        return user

    def _session_cookie_header(self, token: str) -> str:
        return f"session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_COOKIE_MAX_AGE}"

    def _clear_session_cookie_header(self) -> str:
        return "session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    def _can_view(self, job_id: str) -> bool:
        """Visibility rule used everywhere a story could be read: its owner
        can always see it; everyone else only once it's published. A job_id
        with no DB record at all (e.g. one of the pre-accounts stories on
        disk) is never visible to anyone through the app - and neither is one
        whose owner account has since been deleted by an admin (the story
        record itself is deliberately left in place, see db.delete_user, but
        a dangling user_id must not keep a previously-published story
        publicly visible forever)."""
        owner_id = db.get_story_owner_id(job_id)
        if owner_id is None or db.get_user_by_id(owner_id) is None:
            return False
        if db.is_story_published(job_id):
            return True
        user = self._current_user()
        return bool(user and user["id"] == owner_id)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/" or path == "/index.html" or path == "/stories":
                # "/stories" is an alias for the gallery - it's the Stripe
                # Payment Link's configured post-payment redirect target
                # (kertoons.com/stories), which isn't one of this app's
                # "real" page names otherwise.
                self._serve_static_file("index.html")
                return

            if path in ("/login.html", "/register.html", "/create.html", "/story.html",
                        "/usage.html", "/faq.html", "/help.html", "/admin.html"):
                self._serve_static_file(path.lstrip("/"))
                return

            if path == "/share.html":
                # Server-rendered (not a static file + client-side fetch,
                # unlike every other page) specifically so social media
                # crawlers - which don't run JavaScript - see real Open
                # Graph/Twitter Card meta tags in the initial response. See
                # story_engine/share_page.py.
                job_id = qs.get("job_id", [None])[0]
                if not job_id or not self._can_view(job_id):
                    self.send_response(404)
                    self.end_headers()
                    return
                story = self._load_story_summary(job_id)
                if not story:
                    self.send_response(404)
                    self.end_headers()
                    return
                owner = db.get_user_by_id(db.get_story_owner_id(job_id))
                author = owner["username"] if owner else "unknown"
                site_settings = db.get_site_settings()
                self._send_html(share_page.render_share_page(
                    job_id, story, author,
                    site_name=site_settings["site_name"], footer_text=site_settings["footer_text"],
                ))
                return

            if path.startswith("/static/"):
                self._serve_static_file(path[len("/static/"):])
                return

            if path == "/api/config":
                site_settings = db.get_site_settings()
                self._send_json({
                    "mock_story": bool(config.MOCK_STORY),
                    "mock_translation": bool(config.MOCK_TRANSLATION),
                    "mock_images": bool(config.MOCK_IMAGES),
                    "mock_payments": bool(config.MOCK_PAYMENTS),
                    "page_count": config.PAGE_COUNT,
                    "credit_pack_credits": payments.CREDIT_PACK_CREDITS,
                    "credit_pack_price_usd": payments.CREDIT_PACK_PRICE_USD_CENTS / 100,
                    "site_name": site_settings["site_name"],
                    "footer_text": site_settings["footer_text"],
                    "contact_email": site_settings["contact_email"],
                    "contact_phone": site_settings["contact_phone"],
                })
                return

            if path == "/api/me":
                self._send_json({"user": self._current_user()})
                return

            if path == "/api/captcha":
                captcha_id, _code = _issue_captcha()
                self._send_json({"captcha_id": captcha_id})
                return

            if path == "/api/captcha/image":
                captcha_id = qs.get("captcha_id", [None])[0]
                code = _peek_captcha_code(captcha_id) if captcha_id else None
                if not code:
                    self.send_response(404)
                    self.end_headers()
                    return
                self._send_bytes(_generate_captcha_image(code), "image/png")
                return

            if path == "/api/stories/gallery":
                records = sorted(db.list_published_stories(), key=lambda r: r["created_at"], reverse=True)
                items = []
                for r in records:
                    owner = db.get_user_by_id(r["user_id"])
                    if not owner:
                        continue  # owner account deleted - orphaned, permanently hidden (see _can_view)
                    story = self._load_story_summary(r["job_id"])
                    if not story:
                        continue  # still generating, or files missing - not gallery-ready
                    items.append({
                        "job_id": r["job_id"],
                        "title": story.get("title", "Untitled"),
                        "region": story.get("region", ""),
                        "moral": story.get("moral", ""),
                        "author": owner["username"],
                    })
                self._send_json({"stories": items})
                return

            if path == "/api/stories/mine":
                user = self._current_user()
                if not user:
                    self._send_error_json("login required", 401)
                    return
                records = sorted(db.list_stories_for_user(user["id"]), key=lambda r: r["created_at"], reverse=True)
                items = []
                for r in records:
                    story = self._load_story_summary(r["job_id"])
                    items.append({
                        "job_id": r["job_id"],
                        "title": story.get("title") if story else None,
                        "ready": story is not None,
                        "published": r["published"],
                    })
                self._send_json({"stories": items})
                return

            if path == "/api/image-usage/mine":
                user = self._current_user()
                if not user:
                    self._send_error_json("login required", 401)
                    return
                usage = sorted(db.list_image_usage_for_user(user["id"]),
                                key=lambda u: u["created_at"], reverse=True)
                self._send_json({"credits": db.get_image_credits(user["id"]), "usage": usage})
                return

            if path == "/api/payments/mine":
                user = self._current_user()
                if not user:
                    self._send_error_json("login required", 401)
                    return
                payments_list = sorted(db.list_payments_for_user(user["id"]),
                                        key=lambda p: p["created_at"], reverse=True)
                self._send_json({"payments": payments_list})
                return

            if path == "/api/admin/users":
                if not self._require_admin():
                    return
                users = sorted(db.list_all_users(), key=lambda u: u["created_at"], reverse=True)
                self._send_json({"users": users})
                return

            if path == "/api/admin/users/export":
                if not self._require_admin():
                    return
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow(["id", "username", "role", "status", "image_credits", "created_at"])
                for u in sorted(db.list_all_users(), key=lambda u: u["id"]):
                    writer.writerow([u["id"], u["username"], u["role"], u["status"],
                                      u["image_credits"], u["created_at"]])
                self._send_bytes(buf.getvalue().encode("utf-8"), "text/csv", "kertoons_users.csv")
                return

            if path == "/api/admin/stories":
                if not self._require_admin():
                    return
                records = sorted(db.list_all_stories(), key=lambda r: r["created_at"], reverse=True)
                items = []
                for r in records:
                    story = self._load_story_summary(r["job_id"])
                    items.append({
                        "job_id": r["job_id"],
                        "title": story.get("title") if story else None,
                        "ready": story is not None,
                        "published": r["published"],
                        "owner_username": r.get("owner_username"),
                        "created_at": r["created_at"],
                    })
                self._send_json({"stories": items})
                return

            if path == "/api/admin/coupons":
                if not self._require_admin():
                    return
                coupons = sorted(db.list_coupons(), key=lambda c: c["created_at"], reverse=True)
                self._send_json({"coupons": coupons})
                return

            if path == "/api/admin/reports/summary":
                if not self._require_admin():
                    return
                all_payments = db.list_all_payments()
                total_revenue = sum(p.get("amount_usd") or 0 for p in all_payments)
                total_credits_sold = sum(p.get("credits") or 0 for p in all_payments)
                redemptions = db.list_coupon_redemptions()
                by_code = {}
                for r in redemptions:
                    entry = by_code.setdefault(r["code"], {"code": r["code"], "count": 0, "credits_granted": 0})
                    entry["count"] += 1
                    entry["credits_granted"] += r.get("credits") or 0
                self._send_json({
                    "purchases": {
                        "count": len(all_payments),
                        "total_revenue_usd": total_revenue,
                        "total_credits_sold": total_credits_sold,
                    },
                    "coupon_usage": sorted(by_code.values(), key=lambda e: e["code"]),
                })
                return

            if path == "/api/story/view":
                job_id = qs.get("job_id", [None])[0]
                if not job_id or not self._can_view(job_id):
                    self._send_error_json("story not found", 404)
                    return
                story = self._load_story_summary(job_id)
                if not story:
                    self._send_error_json("story not ready", 404)
                    return
                owner_id = db.get_story_owner_id(job_id)
                owner = db.get_user_by_id(owner_id)
                user = self._current_user()
                self._send_json({
                    "job_id": job_id,
                    "story": story,
                    "author": owner["username"] if owner else "unknown",
                    "is_owner": bool(user and user["id"] == owner_id),
                    "published": db.is_story_published(job_id),
                })
                return

            if path == "/api/story/status":
                job_id = qs.get("job_id", [None])[0]
                if not job_id or not self._can_view(job_id):
                    self._send_error_json("unknown job_id", 404)
                    return
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if not job:
                    self._send_error_json("unknown job_id", 404)
                    return
                safe_job = {k: v for k, v in job.items() if k != "trace"}
                self._send_json(safe_job)
                return

            if path == "/api/story/image":
                job_id = qs.get("job_id", [None])[0]
                page = qs.get("page", [None])[0]
                if not job_id or not page or not self._can_view(job_id):
                    self._send_error_json("image not found", 404)
                    return
                job_dir = os.path.join(config.GENERATED_DIR, job_id)
                img_path = os.path.join(job_dir, f"page_{page}.png")
                if not os.path.isfile(img_path):
                    self._send_error_json("image not found", 404)
                    return
                with open(img_path, "rb") as f:
                    self._send_bytes(f.read(), "image/png")
                return

            if path == "/api/story/download":
                job_id = qs.get("job_id", [None])[0]
                fmt = qs.get("format", ["zip"])[0]
                language = qs.get("language", [None])[0]
                if not job_id or not self._can_view(job_id):
                    self._send_error_json("story not found", 404)
                    return
                job_dir = os.path.join(config.GENERATED_DIR, job_id)
                story = self._load_story_summary(job_id)
                if not story:
                    self._send_error_json("story not ready", 404)
                    return
                title = re.sub(r"[^A-Za-z0-9_-]+", "_", story.get("title", "kertoons_story"))
                if fmt == "pdf":
                    is_english = not language or language.strip().lower() in ("en", "english", "original")
                    suffix = "_en" if is_english else "_" + re.sub(r"[^A-Za-z0-9]+", "_", language.strip().lower())
                    self._send_bytes(build_pdf(job_dir, language=language), "application/pdf", f"{title}{suffix}.pdf")
                else:
                    self._send_bytes(build_zip(job_dir), "application/zip", f"{title}.zip")
                return

            self.send_response(404)
            self.end_headers()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send_error_json(str(e), 500)

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/register":
                body = self._read_json_body()
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                if not _USERNAME_RE.fullmatch(username):
                    self._send_error_json(
                        "username must be 3-30 characters: letters, numbers, _ . -", 400)
                    return
                if len(password) < 6:
                    self._send_error_json("password must be at least 6 characters", 400)
                    return
                salt, digest = auth.hash_password(password)
                try:
                    user = db.create_user(username, salt, digest)
                except ValueError as e:
                    self._send_error_json(str(e), 409)
                    return
                token = db.create_session(user["id"])
                self._send_json({"user": user}, extra_headers={"Set-Cookie": self._session_cookie_header(token)})
                return

            if path == "/api/login":
                body = self._read_json_body()
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                captcha_id = (body.get("captcha_id") or "").strip()
                captcha_answer = body.get("captcha_answer") or ""

                # Checked (and consumed - see _consume_captcha) BEFORE ever
                # touching the credential store, so a scripted brute-force
                # attempt can't rack up password guesses without also
                # solving a fresh captcha for every single one.
                if not captcha_id or not _consume_captcha(captcha_id, captcha_answer):
                    self._send_error_json("incorrect or expired captcha - please try again", 400)
                    return

                record = db.get_user_by_username(username)
                if not record or not auth.verify_password(password, record["password_salt"], record["password_hash"]):
                    self._send_error_json("invalid username or password", 401)
                    return
                if record.get("status") == "suspended":
                    self._send_error_json("this account has been suspended", 403)
                    return
                token = db.create_session(record["id"])
                user = db.get_user_by_id(record["id"])
                self._send_json({"user": user}, extra_headers={"Set-Cookie": self._session_cookie_header(token)})
                return

            if path == "/api/logout":
                token = self._session_token()
                if token:
                    db.delete_session(token)
                self._send_json({"ok": True}, extra_headers={"Set-Cookie": self._clear_session_cookie_header()})
                return

            if path == "/api/credits/checkout":
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in", 401)
                    return

                if config.MOCK_PAYMENTS:
                    # Neither a Payment Link nor a Stripe key configured -
                    # same free instant top-up this endpoint always did,
                    # just clearly a "mock" result to the frontend now that
                    # a real paid path also exists.
                    new_balance = db.add_credits(user["id"], ADD_CREDITS_AMOUNT)
                    self._send_json({"ok": True, "mock": True, "credits": new_balance})
                    return

                if config.STRIPE_PAYMENT_LINK:
                    # Static Payment Link - no API call needed to start
                    # checkout. client_reference_id is how the webhook
                    # later ties the completed payment back to this user;
                    # Stripe echoes any query params on a Payment Link URL
                    # through onto the resulting Checkout Session the same
                    # way the dynamic API path sets it explicitly below.
                    sep = "&" if "?" in config.STRIPE_PAYMENT_LINK else "?"
                    checkout_url = f"{config.STRIPE_PAYMENT_LINK}{sep}client_reference_id={user['id']}"
                    self._send_json({"ok": True, "mock": False, "checkout_url": checkout_url})
                    return

                success_url = (
                    f"{config.PUBLIC_BASE_URL}/usage.html"
                    f"?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
                )
                cancel_url = f"{config.PUBLIC_BASE_URL}/usage.html?checkout=cancel"
                try:
                    checkout_url = payments.create_checkout_session(
                        success_url, cancel_url, str(user["id"]))
                except payments.PaymentError as e:
                    self._send_error_json(f"Could not start checkout: {e}", 502)
                    return
                self._send_json({"ok": True, "mock": False, "checkout_url": checkout_url})
                return

            if path == "/api/credits/confirm":
                # Called when the user's own browser returns from Stripe's
                # success_url - gives immediate feedback instead of waiting
                # on the webhook, which is still the durable source of
                # truth (see /api/stripe/webhook below); both paths funnel
                # through the same idempotent db.grant_credits_for_payment,
                # so whichever fires first "wins" and the other is a no-op.
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in", 401)
                    return
                if config.MOCK_PAYMENTS:
                    self._send_error_json("no payment to confirm in mock mode", 400)
                    return

                body = self._read_json_body()
                session_id = (body.get("session_id") or "").strip()
                if not session_id:
                    self._send_error_json("session_id is required", 400)
                    return

                if not config.STRIPE_SECRET_KEY:
                    # Payment-Link-only setup: there's no API key to call
                    # Stripe's session-retrieve endpoint with, so immediate
                    # confirmation isn't possible here - calling it anyway
                    # would just fail and show someone who paid successfully
                    # a scary error. The webhook (needs only
                    # STRIPE_WEBHOOK_SECRET, not this key) is still the real
                    # source of truth and will grant credits shortly on its
                    # own regardless.
                    self._send_json({
                        "ok": True, "pending": True,
                        "credits": db.get_image_credits(user["id"]),
                    })
                    return

                try:
                    session = payments.retrieve_checkout_session(session_id)
                except payments.PaymentError as e:
                    self._send_error_json(f"Could not verify payment: {e}", 502)
                    return

                # The session must both be actually paid AND belong to the
                # CURRENT logged-in user - without the second check, user A
                # could confirm-credit themselves using user B's session_id
                # (e.g. a guessed or leaked one) since session ids aren't
                # secret to begin with.
                if session.get("payment_status") != "paid":
                    self._send_error_json("payment not completed", 402)
                    return
                if session.get("client_reference_id") != str(user["id"]):
                    self._send_error_json("session does not belong to this account", 403)
                    return

                new_balance = db.grant_credits_for_payment(
                    user["id"], session_id, payments.CREDIT_PACK_CREDITS,
                    amount_usd=payments.CREDIT_PACK_PRICE_USD_CENTS / 100)
                self._send_json({"ok": True, "credits": new_balance})
                return

            if path == "/api/stripe/webhook":
                # No login check - this is Stripe calling us directly, not a
                # logged-in browser. Authenticity comes entirely from the
                # signature check below, not from a session cookie.
                raw_body = self._read_raw_body()
                sig_header = self.headers.get("Stripe-Signature", "")
                try:
                    event = payments.verify_webhook_signature(raw_body, sig_header)
                except payments.PaymentError as e:
                    self._send_error_json(f"webhook signature check failed: {e}", 400)
                    return

                if event.get("type") == "checkout.session.completed":
                    session = event.get("data", {}).get("object", {})
                    session_id = session.get("id")
                    client_reference_id = session.get("client_reference_id")
                    if session.get("payment_status") == "paid" and client_reference_id and session_id:
                        try:
                            user_id = int(client_reference_id)
                        except ValueError:
                            user_id = None
                        if user_id is not None:
                            db.grant_credits_for_payment(
                                user_id, session_id, payments.CREDIT_PACK_CREDITS,
                                amount_usd=payments.CREDIT_PACK_PRICE_USD_CENTS / 100)

                # Stripe just wants a 200 to know delivery succeeded - it
                # doesn't read or care about the response body.
                self._send_json({"received": True})
                return

            if path == "/api/story":
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in to create a story", 401)
                    return

                credits = db.get_image_credits(user["id"])
                if credits < MIN_CREDITS_TO_GENERATE:
                    self._send_error_json(
                        f"Out of image credits (balance: {credits}). "
                        f"Click \"Add credits\" next to your credit balance to keep creating stories.", 402)
                    return

                body = self._read_json_body()
                initial_text = (body.get("initial_text") or "").strip()
                region = (body.get("region") or "").strip()
                secondary_language = (body.get("secondary_language") or "").strip()
                photo_b64 = body.get("character_photo_base64")

                if not initial_text:
                    self._send_error_json("initial_text is required", 400)
                    return

                job_id = new_job_id()
                job_dir = os.path.join(config.GENERATED_DIR, job_id)
                os.makedirs(job_dir, exist_ok=True)
                db.create_story_record(job_id, user["id"])

                photo_path = None
                if photo_b64:
                    try:
                        header, b64data = photo_b64.split(",", 1) if "," in photo_b64 else ("", photo_b64)
                        photo_bytes = base64.b64decode(b64data)
                        photo_path = os.path.join(job_dir, "character_photo.png")
                        with open(photo_path, "wb") as f:
                            f.write(photo_bytes)
                    except Exception:
                        photo_path = None

                job = {
                    "job_id": job_id,
                    "user_id": user["id"],
                    "status": "queued",
                    "progress": 0,
                    "message": "Queued...",
                    "initial_text": initial_text,
                    "region": region,
                    "secondary_language": secondary_language,
                    "character_photo_path": photo_path,
                }
                with JOBS_LOCK:
                    JOBS[job_id] = job

                thread = threading.Thread(target=run_job, args=(job, job_dir), daemon=True)
                thread.start()

                self._send_json({"job_id": job_id})
                return

            if path == "/api/story/regenerate_image":
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in", 401)
                    return

                body = self._read_json_body()
                job_id = (body.get("job_id") or "").strip()
                page_number = body.get("page_number")
                custom_prompt = (body.get("prompt") or "").strip() or None

                if not _JOB_ID_RE.fullmatch(job_id):
                    self._send_error_json("invalid job_id", 400)
                    return
                try:
                    page_number = int(page_number)
                except (TypeError, ValueError):
                    self._send_error_json("page_number must be an integer", 400)
                    return
                if custom_prompt and len(custom_prompt) > MAX_CUSTOM_PROMPT_LEN:
                    self._send_error_json(f"prompt is too long (max {MAX_CUSTOM_PROMPT_LEN} characters)", 400)
                    return

                owner_id = db.get_story_owner_id(job_id)
                if owner_id is None:
                    self._send_error_json("unknown job_id", 404)
                    return
                if owner_id != user["id"] and user["role"] != "admin":
                    self._send_error_json("you don't own this story", 403)
                    return

                # Admins moderating someone else's story aren't gated on
                # their own credit balance - only the story's own owner is.
                if user["role"] != "admin":
                    credits = db.get_image_credits(user["id"])
                    if credits < MIN_CREDITS_TO_GENERATE:
                        self._send_error_json(
                            f"Out of image credits (balance: {credits}). "
                            f"Click \"Add credits\" next to your credit balance to keep regenerating images.", 402)
                        return

                job_dir = os.path.join(config.GENERATED_DIR, job_id)
                if not os.path.isfile(os.path.join(job_dir, "story.json")):
                    self._send_error_json("unknown job_id", 404)
                    return

                try:
                    regenerate_page_image(job_dir, page_number, owner_id, custom_prompt=custom_prompt)
                except ValueError as e:
                    self._send_error_json(str(e), 404)
                    return
                except ImageGenerationError as e:
                    # Most often an edited prompt the image API itself
                    # rejected (e.g. flagged unsafe) - surfaced directly so
                    # the user can adjust their wording and retry, rather
                    # than the generic 500 the outer handler would send.
                    self._send_error_json(f"Could not generate image: {e}", 502)
                    return

                self._send_json({"ok": True, "job_id": job_id, "page_number": page_number})
                return

            if path == "/api/story/publish":
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in", 401)
                    return

                body = self._read_json_body()
                job_id = (body.get("job_id") or "").strip()
                published = bool(body.get("published"))

                if not _JOB_ID_RE.fullmatch(job_id):
                    self._send_error_json("invalid job_id", 400)
                    return

                owner_id = db.get_story_owner_id(job_id)
                if owner_id is None:
                    self._send_error_json("unknown job_id", 404)
                    return
                if owner_id != user["id"] and user["role"] != "admin":
                    self._send_error_json("you don't own this story", 403)
                    return

                db.set_story_published(job_id, published)
                self._send_json({"ok": True, "job_id": job_id, "published": published})
                return

            if path == "/api/story/delete":
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in", 401)
                    return

                body = self._read_json_body()
                job_id = (body.get("job_id") or "").strip()

                if not _JOB_ID_RE.fullmatch(job_id):
                    self._send_error_json("invalid job_id", 400)
                    return

                owner_id = db.get_story_owner_id(job_id)
                if owner_id is None:
                    self._send_error_json("unknown job_id", 404)
                    return
                if owner_id != user["id"] and user["role"] != "admin":
                    self._send_error_json("you don't own this story", 403)
                    return

                job_dir = os.path.join(config.GENERATED_DIR, job_id)
                if os.path.isdir(job_dir):
                    shutil.rmtree(job_dir)
                db.delete_story_record(job_id)
                with JOBS_LOCK:
                    JOBS.pop(job_id, None)

                self._send_json({"ok": True, "job_id": job_id})
                return

            if path == "/api/coupons/redeem":
                user = self._current_user()
                if not user:
                    self._send_error_json("please log in", 401)
                    return
                body = self._read_json_body()
                code = (body.get("code") or "").strip()
                if not code:
                    self._send_error_json("coupon code is required", 400)
                    return
                try:
                    new_balance = db.redeem_coupon(code, user["id"])
                except ValueError as e:
                    self._send_error_json(str(e), 400)
                    return
                self._send_json({"ok": True, "credits": new_balance})
                return

            if path == "/api/admin/users/create":
                if not self._require_admin():
                    return
                body = self._read_json_body()
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                if not _USERNAME_RE.fullmatch(username):
                    self._send_error_json(
                        "username must be 3-30 characters: letters, numbers, _ . -", 400)
                    return
                if len(password) < 6:
                    self._send_error_json("password must be at least 6 characters", 400)
                    return
                salt, digest = auth.hash_password(password)
                try:
                    new_user = db.create_user(username, salt, digest)
                except ValueError as e:
                    self._send_error_json(str(e), 409)
                    return
                self._send_json({"user": new_user})
                return

            if path == "/api/admin/users/delete":
                admin = self._require_admin()
                if not admin:
                    return
                body = self._read_json_body()
                user_id = body.get("user_id")
                if not isinstance(user_id, int):
                    self._send_error_json("user_id must be an integer", 400)
                    return
                if user_id == admin["id"]:
                    self._send_error_json("you can't delete your own account", 400)
                    return
                if not db.delete_user(user_id):
                    self._send_error_json("unknown user_id", 404)
                    return
                self._send_json({"ok": True})
                return

            if path in ("/api/admin/users/suspend", "/api/admin/users/activate"):
                admin = self._require_admin()
                if not admin:
                    return
                body = self._read_json_body()
                user_id = body.get("user_id")
                if not isinstance(user_id, int):
                    self._send_error_json("user_id must be an integer", 400)
                    return
                if user_id == admin["id"]:
                    self._send_error_json("you can't suspend your own account", 400)
                    return
                status = "suspended" if path.endswith("/suspend") else "active"
                if not db.set_user_status(user_id, status):
                    self._send_error_json("unknown user_id", 404)
                    return
                self._send_json({"ok": True, "user_id": user_id, "status": status})
                return

            if path == "/api/admin/coupons/create":
                if not self._require_admin():
                    return
                body = self._read_json_body()
                code = (body.get("code") or "").strip()
                credits = body.get("credits")
                if not code:
                    self._send_error_json("coupon code is required", 400)
                    return
                try:
                    credits = int(credits)
                except (TypeError, ValueError):
                    self._send_error_json("credits must be an integer", 400)
                    return
                if credits <= 0:
                    self._send_error_json("credits must be greater than 0", 400)
                    return
                try:
                    coupon = db.create_coupon(code, credits)
                except ValueError as e:
                    self._send_error_json(str(e), 409)
                    return
                self._send_json({"coupon": coupon})
                return

            if path == "/api/admin/coupons/toggle":
                if not self._require_admin():
                    return
                body = self._read_json_body()
                code = (body.get("code") or "").strip()
                active = bool(body.get("active"))
                if not code:
                    self._send_error_json("coupon code is required", 400)
                    return
                if not db.set_coupon_active(code, active):
                    self._send_error_json("unknown coupon code", 404)
                    return
                self._send_json({"ok": True, "code": code, "active": active})
                return

            if path == "/api/admin/settings":
                if not self._require_admin():
                    return
                body = self._read_json_body()
                site_name = (body.get("site_name") or "").strip()
                footer_text = (body.get("footer_text") or "").strip()
                contact_email = (body.get("contact_email") or "").strip()
                contact_phone = (body.get("contact_phone") or "").strip()
                if not site_name:
                    self._send_error_json("site name is required", 400)
                    return
                # Contact info is optional (blank hides the "Contact us"
                # section entirely - see faq.html), but if given, at least
                # a plausible email shape - typos here would otherwise
                # silently publish a broken "Contact us" link to visitors.
                if contact_email and "@" not in contact_email:
                    self._send_error_json("contact email doesn't look valid", 400)
                    return
                settings = db.set_site_settings(site_name, footer_text, contact_email, contact_phone)
                self._send_json({"ok": True, "settings": settings})
                return

            self.send_response(404)
            self.end_headers()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send_error_json(str(e), 500)

    def log_message(self, fmt, *args):
        print("[kertoons]", self.address_string(), fmt % args)


def main():
    db.init_db()
    if config.ADMIN_USERNAME and config.ADMIN_PASSWORD:
        salt, digest = auth.hash_password(config.ADMIN_PASSWORD)
        result = db.create_admin_if_missing(config.ADMIN_USERNAME, salt, digest)
        if result == "created":
            print(f"Admin account '{config.ADMIN_USERNAME}' created.")
        elif result == "promoted":
            print(
                f"Existing account '{config.ADMIN_USERNAME}' promoted to admin "
                f"(its existing password was kept, NOT ADMIN_PASSWORD)."
            )
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    mode = []
    if config.MOCK_STORY:
        mode.append("MOCK STORY (no OPENAI_API_KEY)")
    if config.MOCK_IMAGES:
        mode.append("MOCK IMAGES (no DEEPAI_API_KEY)")
    mode_str = " | ".join(mode) if mode else "LIVE (using real APIs)"
    print(f"Kertoons dev server running at http://{config.HOST}:{config.PORT}")
    print(f"Mode: {mode_str}")
    if not config.MOCK_PAYMENTS and not config.STRIPE_WEBHOOK_SECRET:
        print(
            "WARNING: real payments are enabled (STRIPE_PAYMENT_LINK or STRIPE_SECRET_KEY "
            "is set) but STRIPE_WEBHOOK_SECRET is NOT - purchases will succeed on Stripe's "
            "side but credits will never be granted until this is configured. See DEPLOY.md."
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
