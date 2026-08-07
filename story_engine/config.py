"""
Configuration & environment loading for the Kertoons app.

Reads a .env file (if present) and exposes the API keys / mode flags used
throughout the app. If the required API keys are missing, the app
automatically falls back to MOCK MODE so the whole pipeline can be built,
run, and tested locally without needing real OpenAI / image-API credentials.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # dotenv is optional; env vars can also be set directly

# Admin-edited API keys (see Admin > API Keys / story_engine/api_config.py)
# always win over the env var of the same name - _key() below is just
# os.environ.get() with that override layered on top. Read once here, same
# "takes effect on next restart" contract every other value in this module
# already has (nothing in config.py is re-read after import). A bare
# try/except around the import itself, not just the lookup, since
# api_config.py's very first run (no kertoons_api_keys.json yet) must be
# exactly as harmless as it having never existed.
try:
    from .api_config import get_overrides as _get_api_key_overrides
    _API_KEY_OVERRIDES = _get_api_key_overrides()
except Exception:
    _API_KEY_OVERRIDES = {}


def _key(name: str, default: str = "") -> str:
    return (_API_KEY_OVERRIDES.get(name) or os.environ.get(name, default)).strip()


OPENAI_API_KEY = _key("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Translation defaults to Gemini instead of OpenAI (a deliberate deviation
# from the product spec's "OpenAI only" rule for story text - see
# gemini_client.py) but is switchable back to OpenAI (story_engine/
# openai_client.py's translate_story) by setting TRANSLATION_PROVIDER=openai
# - useful if you'd rather depend on only one LLM provider for everything.
GEMINI_API_KEY = _key("GEMINI_API_KEY")
# "-latest" alias (rather than a pinned version like "gemini-2.0-flash")
# since Google retires pinned model versions over time and a hardcoded one
# 404s once retired - the alias always points at Google's current
# recommended flash model instead.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

TRANSLATION_PROVIDER = os.environ.get("TRANSLATION_PROVIDER", "gemini").strip().lower()
if TRANSLATION_PROVIDER not in ("gemini", "openai"):
    TRANSLATION_PROVIDER = "gemini"

# The product spec refers to an image API at "Deepak.org". No such API
# could be found; the near-identical, real service is DeepAI (deepai.org),
# which offers a text2img / 3D-cartoon-style image API. This app targets
# DeepAI by default under the DEEPAI_API_KEY name. If you actually have a
# different "Deepak.org" endpoint, set DEEPAI_BASE_URL accordingly and the
# image client will call that instead - see story_engine/image_client.py.
DEEPAI_API_KEY = _key("DEEPAI_API_KEY")
DEEPAI_BASE_URL = os.environ.get("DEEPAI_BASE_URL", "https://api.deepai.org/api/text2img")

# Selling image credits - via Stripe Checkout (a Stripe-hosted payment page,
# so this app never sees/touches card details). STRIPE_WEBHOOK_SECRET comes
# from the webhook endpoint you register in the Stripe dashboard pointing at
# this app's /api/stripe/webhook - see DEPLOY.md for the full setup. This is
# the ONLY thing that actually grants credits when using a Payment Link
# (below) - without it, purchases succeed on Stripe's side but nobody ever
# gets credited, so treat it as required, not optional, once real payments
# are enabled.
STRIPE_WEBHOOK_SECRET = _key("STRIPE_WEBHOOK_SECRET")

# Two ways to sell the credit pack - set at most one:
#   1. STRIPE_PAYMENT_LINK - a pre-made, no-code checkout URL from
#      https://dashboard.stripe.com/payment-links ("+ New"). Simplest -
#      "Add credits" just redirects to it (with ?client_reference_id=<user
#      id> appended so the webhook can tell whose payment it was) - no
#      STRIPE_SECRET_KEY needed at all for this path. Preferred over #2
#      whenever both are set.
#   2. STRIPE_SECRET_KEY - dynamically creates a Checkout Session per
#      purchase via Stripe's API (see payments.create_checkout_session).
#      More flexible (e.g. multiple price tiers later) but needs a real API
#      key from https://dashboard.stripe.com/apikeys. Also still used by
#      /api/credits/confirm (the immediate on-return confirmation) even when
#      #1 is active, if you want that - see PUBLIC_BASE_URL below.
# Get either from Stripe test mode first (test-mode Payment Links exist too,
# and "sk_test_..." keys) to try the whole flow with Stripe's documented
# test card 4242 4242 4242 4242 before ever going live.
STRIPE_PAYMENT_LINK = _key("STRIPE_PAYMENT_LINK")
STRIPE_SECRET_KEY = _key("STRIPE_SECRET_KEY")

# The absolute, public URL this app is reachable at - needed because Stripe
# Checkout requires absolute (not relative) success/cancel URLs. If mounted
# under a path on an existing site (see DEPLOY.md's "Mounting under an
# existing site"), the app itself has no way to know that prefix (nginx
# strips it before forwarding - see deploy/nginx_kertoons_subpath.conf), so
# it must be told explicitly here, e.g. PUBLIC_BASE_URL=https://kertoons.com/story
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip()

# Force mock mode regardless of keys (useful for local testing/demo)
FORCE_MOCK = os.environ.get("FORCE_MOCK", "").strip().lower() in ("1", "true", "yes")

# On by default - after generating a page's image, a vision model checks
# whether every character actually kept their own fixed hair/skin/outfit
# traits (no bleeding onto a different character in the scene) and, if not,
# retries once with an amended prompt - see image_client._verify_and_maybe_
# retry(). Costs one extra vision-API call per page (two if a retry fires),
# so it's here as an explicit off switch for cost/latency reasons; it's
# otherwise a no-op without OPENAI_API_KEY regardless of this setting.
IMAGE_CONSISTENCY_CHECK = os.environ.get("IMAGE_CONSISTENCY_CHECK", "1").strip().lower() not in (
    "0", "false", "no",
)

MOCK_STORY = FORCE_MOCK or not OPENAI_API_KEY
MOCK_TRANSLATION = FORCE_MOCK or not (
    GEMINI_API_KEY if TRANSLATION_PROVIDER == "gemini" else OPENAI_API_KEY
)
MOCK_IMAGES = FORCE_MOCK or not DEEPAI_API_KEY
# Mock payments grant the credit pack for free instantly (clearly labeled as
# a demo in the UI) instead of redirecting to a real Stripe Checkout page -
# same fallback pattern as the other three, so the whole app (including
# "buying" credits) is testable with zero paid services configured.
MOCK_PAYMENTS = FORCE_MOCK or not (STRIPE_PAYMENT_LINK or STRIPE_SECRET_KEY)

# Fallback only - the actual page count used for generation is admin-
# configurable at runtime (story_engine/db.py's site_settings, editable from
# /admin.html) and this default is used only until an admin ever changes it.
DEFAULT_PAGE_COUNT = 5

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))

if not PUBLIC_BASE_URL:
    PUBLIC_BASE_URL = f"http://{HOST}:{PORT}"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Overridable so a throwaway test server (see KERTOONS_DB_PATH/
# KERTOONS_GENERATED_DIR below) can point at an isolated copy instead of
# ever writing into the real static/ folder - needed now that the admin
# panel can overwrite a static file in place (the banner image upload, see
# server.py's /api/admin/settings/banner).
STATIC_DIR = os.environ.get("KERTOONS_STATIC_DIR", os.path.join(BASE_DIR, "static"))

# Both overridable via env var so a throwaway server instance (used to
# verify a change in a browser/tests) can point at a fully isolated
# scratch location instead of ever touching real user data or generated
# stories - see KERTOONS_DB_PATH / KERTOONS_GENERATED_DIR.
GENERATED_DIR = os.environ.get("KERTOONS_GENERATED_DIR", os.path.join(BASE_DIR, "generated"))

# User accounts / sessions / story ownership - a single JSON file rather than a
# real database engine (see story_engine/db.py), simple enough at this app's
# scale (a handful of users, dozens of stories) that a real DB isn't needed.
DB_PATH = os.environ.get("KERTOONS_DB_PATH", os.path.join(BASE_DIR, "kertoons_data.json"))

# If both are set, an admin account is auto-created (or left alone if it
# already exists) on every server startup - see db.create_admin_if_missing().
# Leave blank to disable the admin panel entirely, same "blank disables it"
# convention as the mock-mode flags above.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# A separate, more-privileged tier above "admin" - can see/edit the
# per-image and server cost figures (see db.get_cost_settings) that no
# other role, including regular admins, can access. Same bootstrap
# convention as ADMIN_USERNAME/ADMIN_PASSWORD - auto-provisioned on startup,
# blank disables it, never typed anywhere else. Deliberately a DIFFERENT
# account from the regular admin one (see db.create_superadmin_if_missing) -
# logging into /superadmin.html with regular admin credentials must fail.
SUPERADMIN_USERNAME = os.environ.get("SUPERADMIN_USERNAME", "").strip()
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "").strip()

os.makedirs(GENERATED_DIR, exist_ok=True)
