# Kertoons - Kids Cartoon Story Generator (local dev build)

Built from the product spec in `../p.md.txt`. Generates a 5-page, region-based,
bilingual kids cartoon story: story text (generation + character-photo vision)
via **OpenAI**, translation via **Gemini** (a deliberate deviation from the
spec's "OpenAI only" rule for story text - see `story_engine/gemini_client.py`),
illustrations via the image API the spec calls **"Deepak.org"** only (see note
below).

Want to run this somewhere other than your own machine? See
**[DEPLOY.md](DEPLOY.md)** for a complete, step-by-step DigitalOcean Droplet
deployment guide (systemd service + nginx + optional HTTPS included).

## Accounts, ownership, and publishing

Every story belongs to whichever logged-in user created it - only its owner can
create, regenerate images for, or publish/unpublish it. The **home page
(`/`, `static/index.html`)** is a public gallery of every **published** story
(cover image + title + author); clicking one opens `static/story.html`, which
shows the language switcher and downloads to anyone, but only shows the
Regenerate/Publish controls to the owner. Unpublished stories are never visible
to anyone except their owner - enforced server-side (`Handler._can_view()` in
`server.py`), not just hidden in the UI, so there's no way to reach one by
guessing/sharing its URL.

Accounts and story ownership/publish state live in a single JSON file,
`kertoons_data.json` (see `story_engine/db.py`) - not a full database engine,
which isn't needed at this app's scale. Passwords are salted/hashed with
stdlib `hashlib.pbkdf2_hmac` (`story_engine/auth.py`); sessions are opaque
tokens in an `HttpOnly` cookie, stored server-side so they're revocable on
logout. The ~30 stories generated before accounts existed have no owner record
and are intentionally unreachable through the app now (not deleted, just
invisible - see `_can_view()`).

Pages: `static/index.html` (gallery), `static/login.html` /
`static/register.html` (auth), `static/create.html` (the story-creation form,
gated behind login, plus a "My Stories" list), `static/story.html` (viewing any
one story by `?job_id=`). `static/book.js` holds the page-card/language-switch/
regenerate/publish rendering shared by `create.html` and `story.html`;
`static/nav.js` renders the top nav (shared by every page).

**The login form (only - not registration) requires a 4-digit numeric
captcha**, a Pillow-rendered image with light noise-line obfuscation
(`server.py`'s `_generate_captcha_image`/`_issue_captcha`/`_consume_captcha`,
no external captcha service or new dependency). `GET /api/captcha` issues a
`captcha_id` and stores its code in memory (`CAPTCHAS`, never persisted -
meaningless after the page closes) for 5 minutes; `GET /api/captcha/image`
renders it. `POST /api/login` checks and **consumes** the captcha (single-
use - removed on any attempt, right or wrong) *before* even looking up the
username, so a scripted brute-force attempt can't rack up password guesses
without solving a fresh captcha every single time. The 🔄 button next to the
image, and the always-load-a-new-one-after-any-attempt behavior in
`static/auth.js`, mean a failed login never leaves a dead code on screen.

## Editing a page's image prompt before regenerating

Each page's illustration prompt is shown in an editable textarea between the
image and the "Regenerate image" button (`static/book.js`'s
`buildPagesHtml()`/`regenerateImage()`, owner-only, same as the button
itself). What's shown is **always the exact, complete prompt that produced
the image currently on disk** - `image_client.generate_scene_image()`
returns `(image_bytes, prompt_used)`, and `pipeline.py` unconditionally
saves `prompt_used` onto that page's `image_prompt` in `story.json` every
time an image is generated or regenerated, whether or not the box was
edited. This replaced an earlier version of this field that held a
separately LLM-authored *approximation* of the prompt rather than what was
actually sent - the box could show something subtly different from reality.

Editing the box and clicking Regenerate sends that exact text to the image
API verbatim (`custom_prompt` param) instead of the usual character-block +
scene-text + fixed boilerplate composition - the point of exposing it is
that what you type is what gets sent, not something wrapped further. No
automatic "unsafe content" softening retry applies to a custom prompt
either, for the same reason - a rejection is surfaced directly so you can
adjust the wording yourself. Leaving the box unchanged (or clearing it)
regenerates using the original character-consistency-driven composition.

**Every page's default composed prompt also embeds an explicit "this is
page N of TOTAL" clause** (`image_client.build_prompt()`), guaranteeing the
exact prompt text can never be byte-identical across two pages of the same
story even if the story model ever writes near-duplicate scene descriptions
for two of them - a text-to-image API given the same prompt twice tends to
return the same or near-identical image, so this is a concrete guarantee of
per-page uniqueness, not just an instruction asking the model nicely.

## PDF download status bar

Clicking a "Download {Language} PDF" button (`static/book.js`'s
`downloadPdf()`) shows a status bar above the download row instead of the
old plain `window.open(url)` (which gave zero feedback while the file was
being built - increasingly worth noticing now that every embedded
illustration is upscaled to 300 DPI, see "Editing a page's image prompt"
above, making generation take a bit longer than before that existed). The
PDF is fetched via `fetch()` into a blob (rather than a direct navigation)
specifically so the request's lifecycle is trackable: an info banner while
in flight, a success banner once the browser's own download is triggered
(auto-clearing after a few seconds), or an error banner with the server's
actual error message on failure. The download itself still uses the
server's real filename (`Content-Disposition`) rather than a guessed one.

## PDF print quality (high) vs. screen quality (low)

A "High (print, 300 DPI)" / "Low (screen, smaller file)" radio choice sits
above the download row (`static/book.js`) and is sent as
`&quality=high|low` on `GET /api/story/download`. "High" (the default) is
the existing 300 DPI upscale behavior; "Low" resizes every illustration
(up **or** down) to a ~96 DPI screen target instead - a real illustration
tested at ~1.9MB (high) vs. ~310KB (low), roughly a 6x reduction, since low
quality actively downscales large images rather than just capping how much
they're allowed to grow. Both tiers are implemented as one function,
`story_engine/book_export.py`'s `_quality_reader(img_path, quality)`; an
unrecognized `quality` value falls back to `"high"`. The low-quality
filename gets a `_web` suffix so the two downloads for the same story never
collide on disk.

**Generated PDFs are cached on disk, per language and quality tier**, right
alongside that story's other files in `generated/<job_id>/` -
`story_engine/book_export.py`'s `get_pdf()` (used by both the download
endpoint and `build_zip()`) writes `storybook_<lang>.pdf` /
`storybook_<lang>_web.pdf` (see `pdf_filename()`) the first time a given
language+quality combination is requested, and every later request for
that same combination is served straight off disk instead of re-rendering
the whole storybook (including the per-illustration resize pass) again -
confirmed in testing to go from ~2s to ~0.03s on a repeat request. The
cache is invalidated by `pipeline.py`'s `regenerate_page_image()`, which
deletes every cached PDF for a job (all languages, both quality tiers)
the moment any page's image changes, since they'd otherwise keep serving
stale art forever.

Each download button's label reflects that cache directly: "📥 Download
{Language} PDF" if that language+quality is already on disk, or "⚙️
Generate {Language} PDF" if clicking it will build one from scratch.
`GET /api/story/view` computes this per language via `pdf_filename()` +
`os.path.isfile()` and returns it as `pdf_available` (`server.py`);
`static/book.js` uses it to label buttons on load, re-labels them whenever
the quality radio changes (high/low are cached independently, so a
language can be "Download" at one tier and "Generate" at the other), and
flips a button to "Download" the moment its own download finishes -
without waiting for a page reload.

## Image credits & buying more

Every image generated (a story's initial 5 pages, or any "Regenerate image"
click) costs 1 credit; new accounts start with 50 by default, admin-
configurable from `/admin.html`'s Settings section (see "Admin panel"
below). Once a balance goes negative, creating/regenerating is blocked until the
account tops up. The nav bar's "Add credits" button sells one pack - 50
credits for $5 - through **Stripe Checkout**, a payment page hosted entirely
on Stripe's own domain (`story_engine/payments.py`); this app never receives
or stores a card number. Without `STRIPE_SECRET_KEY` configured, the same
button instead grants a free mock top-up so the whole app - including
"buying" credits - stays testable with zero paid services set up. See
[DEPLOY.md](DEPLOY.md)'s "Selling credits with Stripe" section for the full
setup (account, API keys, webhook registration).

Payment confirmation is deliberately double-covered and idempotent
(`db.grant_credits_for_payment`, keyed by Stripe's checkout session id): the
user's own browser confirms immediately on return from Stripe
(`POST /api/credits/confirm`), and Stripe's webhook
(`POST /api/stripe/webhook`, signature-verified) confirms independently and
asynchronously - whichever arrives first grants the credits; the other is a
guaranteed no-op, so a payment is never double-credited and is still
eventually credited even if the user closes the tab before returning.

Two checkout paths are supported, preferring the simpler one whenever it's
configured: a static **Stripe Payment Link** (`STRIPE_PAYMENT_LINK` - no API
key needed just to start checkout, "Add credits" redirects straight to it
with `?client_reference_id=<user id>` appended) or, if that's not set, a
dynamically-created **Checkout Session** per purchase via the Stripe API
(`STRIPE_SECRET_KEY`, `payments.create_checkout_session`). A Payment Link's
"After payment" redirect is configured in the Stripe dashboard, not in code -
this app's `/stories` route (aliased to the gallery) and `gallery.js`'s
`handleCheckoutReturn()` exist specifically to match one currently
configured to `kertoons.com/stories?cid={CHECKOUT_SESSION_ID}`.

Every successful purchase is also kept as a permanent, per-user history
entry (`db.list_payments_for_user`, same `processed_payments` records used
for idempotency) - visible on `usage.html` under "Payment history"
(credits granted, amount paid, date), fetched via `GET /api/payments/mine`.

## Admin panel

Set both `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env` (or the environment)
to auto-provision an admin account on server startup
(`db.create_admin_if_missing`, called from `server.py`'s `main()`) - safe to
leave set across restarts, it's a no-op once that username already exists.
Leave either unset to disable the admin panel entirely. No admin password is
ever entered through the UI or stored in source, same convention as every
other secret in this app.

An admin account (`role: "admin"` on the user record) gets a "🛠 Admin" link
in the nav bar, leading to `static/admin.html` / `admin.js`
(`GET /api/admin/*`, gated server-side by `Handler._require_admin` - never
trust a client-reported role). Users and Stories each have their own
dedicated page (`admin-users.html`/`admin-users.js`,
`admin-stories.html`/`admin-stories.js`, linked from admin.html's "Manage"
section) rather than sharing space with Settings/Coupons/Footer
links/Reports - each with real numbered pagination (page 1, 2, 3... buttons,
not just Prev/Next) since those two tables are the ones most likely to
outgrow a single screen. From there an admin can:
- Create, suspend/activate, or delete any user account (`admin-users.html`).
  Suspension blocks login and every authenticated action immediately
  (enforced centrally in `_current_user()`) but leaves the account's
  already-published stories visible. Deleting a user leaves their stories
  on disk but permanently hidden (no owner record - same fate as the ~30
  pre-accounts legacy stories), rather than deleting anything.
- Regenerate images, publish/unpublish, or delete **any** user's story
  (`admin-stories.html`) - the existing owner-only checks on those three
  endpoints also accept `role == "admin"`.
- Download a CSV of every account (`GET /api/admin/users/export`) - id,
  username, role, status, credits, created date; never password fields.
- Create and toggle **coupon codes** (`db.create_coupon` /
  `set_coupon_active`) - a code can be redeemed by any number of different
  users, but each user may redeem a given code at most once
  (`db.redeem_coupon`, keyed on `(code, user_id)`). Any logged-in user
  redeems a code from `usage.html`'s "Redeem coupon" form
  (`POST /api/coupons/redeem`).
- View a purchases + coupon-redemption summary report
  (`GET /api/admin/reports/summary`): total purchases/revenue/credits sold,
  a per-coupon redemption count, and a total-images-generated stat with a
  per-calendar-month breakdown (grouped from `db.list_all_image_usage()`'s
  `created_at` timestamps - every initial-page AND "Regenerate image"
  generation counts, oldest month first).
- Rebrand the whole app under **Settings**: site name and footer text
  (`db.get_site_settings`/`set_site_settings`, `POST /api/admin/settings`).
  These are served publicly on `GET /api/config` since every page needs
  them. The site name isn't just a template variable in a couple of spots -
  `static/nav.js`'s `_applyBranding()` walks every text node on the page
  (plus `document.title`) replacing the literal word "Kertoons" wherever it
  appears, so FAQ/Help marketing copy, page titles, and the header all stay
  in sync automatically without hardcoding the name in more than one place.
  The footer's trailing message is a separate free-text field
  (`#site-footer-text` on every page). Both are also applied server-side
  where no browser JS runs: the crawler-facing share page
  (`story_engine/share_page.py`) and exported PDFs
  (`story_engine/book_export.py` - cover subtitle, closing line, and the
  branded footer link on every page).
- Optionally set a **contact email/phone** - blank by default, so nothing
  extra appears anywhere until an admin sets at least one. Shown in three
  places: `faq.html`'s `#contact-card`, appended to every page's HTML
  footer (`#footer-contact-email-wrap`/`#footer-contact-phone-wrap`) right
  after the footer text, and in the exported **PDF**'s branded footer
  (`story_engine/book_export.py`'s `_draw_footer()`) - each present field
  becomes its own separately clickable segment (site name → the site link,
  email → a real `mailto:` link, phone → a `tel:` link with everything but
  digits/`+` stripped). Every occurrence independently omits whichever
  field is blank, rather than showing an empty line/separator. A submitted
  email is only checked for a plausible shape (contains `@`) - not fully
  RFC-validated.
- Upload a new **main banner image** (shown below the gallery on
  `index.html`) via `POST /api/admin/settings/banner` - decoded, validated
  as a real image (Pillow), and re-encoded as JPEG to a fixed filename,
  `static/kertoons_bar.jpg`, **always overwritten** regardless of the
  uploaded file's original format, so `index.html`'s `<img>` tag never
  needs to change. Capped at 8 MB (`server.py`'s `MAX_BANNER_UPLOAD_BYTES`).
- Change **scenes per story** (default 5, range 2-10 -
  `db.MIN_PAGE_COUNT`/`MAX_PAGE_COUNT`) - every hardcoded "5 pages" mention
  in `story_engine/prompts.py`'s story-generation instructions is actually a
  `<<PAGE_COUNT>>` placeholder, substituted by `build_system_prompt()` at
  generation time, so the model is always told the number currently saved
  here. Read fresh from `site_settings` at the start of every generation job
  (`pipeline.run_job`), not cached - takes effect on the very next story
  created, no restart needed. Stories already generated keep whatever page
  count they were made with; this only changes new ones.
- Change how many **free image credits a new account starts with** (default
  50, range 0-500 - `db.MIN_SIGNUP_CREDITS`/`MAX_SIGNUP_CREDITS`), read at
  account-creation time by both `db.create_user` (the public `/api/register`
  flow) and `db.create_admin_if_missing` (server startup bootstrap). 0 is a
  valid choice (new accounts start with no free credits and must buy/redeem
  a coupon before generating) - handled explicitly throughout
  (`.get(key, default)` rather than `or`) so it isn't silently coerced back
  to 50. Only affects accounts created after the change; existing balances
  are untouched.
- Add/remove extra **footer links** (e.g. "Terms", "Privacy Policy") shown
  after FAQ/Help on every page, each with its own "open in a new tab" toggle
  (`db.add_footer_link`/`list_footer_links`/`delete_footer_link`,
  `POST /api/admin/footer_links/create`/`delete`). Public via
  `GET /api/config` (every page already fetches this for site_name/etc.) and
  rendered by `static/nav.js`'s `_applyFooterLinks()` into a
  `#footer-custom-links` span present in every static page's footer AND the
  server-rendered `share.html` template (`story_engine/share_page.py`) -
  the only page that also calls `renderNav()` server-side-first. A
  `javascript:` URL is rejected server-side (400) rather than merely
  discouraged, since the URL becomes a real `href` rendered for every site
  visitor, not just the admin who entered it.

## The Storytelling Expert skill (used for every story)

The form only ever collects a 1-2 sentence seed idea. Left alone, a model tends
to just lightly reword that seed sentence across all 5 pages - technically "a
story," but not a good one. `story_engine/prompts.py` defines a standalone,
named prompt component, `STORYTELLER_EXPERT_SKILL`, that's prepended to every
single `SYSTEM_PROMPT` used by `generate_story()` - so it's applied
automatically to every story creation, not opted into per call. It instructs
the model to act as a professional children's-book author and *elaborate* the
seed - inventing a real setup/problem/setback/resolution arc, sensory detail,
character motivation, varied sentence rhythm, and natural dialogue - rather
than transcribing the seed back with minor variations. `build_user_prompt()`
also now explicitly labels the input as a "SEED IDEA (elaborate, don't
restate)" so the instruction is reinforced at both the system and user-message
level. The mock story generator (used with no API key) was rewritten to
demonstrate the same shape - a real setup, a small setback on page 4, dialogue,
and sensory detail - though true elaboration quality for arbitrary seeds
depends on the real OpenAI model in live mode.

## Multiple languages, one storybook PDF each

The "Also translate into" field accepts a comma-separated list (e.g. `Hindi,
Tamil, Spanish`), not just one language. `pipeline.py` translates the story
into every language listed (deduplicated, case-insensitive) and stores each
one under `story["translations"][<language>]` / each page's
`page["translations"][<language>]`, alongside the English original.

Each language then gets its OWN standalone storybook PDF - not English text
and translated text mixed on the same page - downloadable independently:
`/api/story/download?job_id=...&format=pdf&language=en` for English, `&language=Hindi`
for the Hindi book, etc. The UI (`renderBook()` in `static/book.js`) renders
one download button per language automatically. The ZIP download
(`format=zip`) now also bundles every language's PDF alongside the raw
images and `story.json`, so one archive has everything.

### Rendering Indian-language PDFs correctly

reportlab's built-in PDF fonts (Helvetica, Times) only cover Latin-1, so a
Hindi/Tamil/Bengali/etc. translation drawn with them comes out as blank
boxes. `story_engine/book_export.py` fixes this properly rather than just
switching fonts blindly:

1. `detect_script()` scans a language's translated text by Unicode code
   point range and identifies which Indic script it is (Devanagari,
   Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, or
   Malayalam) - or reports none for Latin-script languages like Spanish.
2. `resolve_fonts()` then picks a real Unicode TrueType font that actually
   covers that script, in this order: (a) a font you drop into this app's
   own `fonts/` folder (see `fonts/README.txt` - works on any OS), (b)
   Windows' built-in **Nirmala UI** (`C:\Windows\Fonts\Nirmala.ttf`), which
   covers all nine scripts above plus Latin in one file and ships on every
   Windows 8+ machine with zero setup - the expected common case since this
   app runs locally on Windows, (c) a couple of known Linux/Mac Indic font
   locations if present, (d) DejaVu Sans / Helvetica as a last resort.
3. If no font supporting the detected script could be found anywhere, the
   generated PDF still builds successfully (never crashes) and prints a
   short note on that language's cover page saying which script font is
   missing and where to add one - so the failure mode is visible and
   actionable, not silent blank pages.
4. This was verified directly, specifically for **Hindi, Tamil, and
   Malayalam** (the three most-requested Indian languages):
   - `detect_script()` correctly identified real Devanagari (Hindi), Tamil,
     and Malayalam sample sentences (each their own Unicode block).
   - In this dev sandbox (which has no Indic fonts installed and no
     internet access to fetch one), `resolve_fonts()` correctly fell back
     to DejaVu Sans for all three and reported `supports_script=False`, so
     each language's cover page shows the "no script font found" note
     instead of silently rendering blank boxes - confirmed by actually
     building and rendering all three PDFs.
   - Separately, with the Windows Nirmala UI path simulated as present,
     `resolve_fonts()` correctly picked it (`NirmalaUI`/`NirmalaUI-Bold`)
     and reported `supports_script=True` for Hindi, Tamil, AND Malayalam -
     confirming the *selection logic* is correct for all three scripts
     (Nirmala UI is one font file covering all of them, so there's no
     per-script gap). Nirmala UI shipping by default on Windows 8+ and
     Office 2013+ (covering Devanagari, Bengali, Gujarati, Gurmukhi,
     Kannada, Malayalam, Odia, Tamil, and Telugu) was confirmed via
     [Wikipedia: Nirmala UI](https://en.wikipedia.org/wiki/Nirmala_UI).
   - What could NOT be verified in this sandbox: actual glyph shapes
     rendering correctly, since no real Devanagari/Tamil/Malayalam font
     file is available here and outbound font downloads are blocked. On
     your actual Windows machine, Nirmala UI is expected to already be
     present with zero setup and render all three correctly. If it's ever
     missing (e.g. a stripped-down Windows install), drop a "Noto Sans
     Devanagari/Tamil/Malayalam" `.ttf` into this app's `fonts/` folder
     (see `fonts/README.txt`) as a guaranteed-correct fallback.

## Quick start

```bash
cd kertoons-app
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env        # then fill in your API keys (optional - see Mock Mode)
python3 server.py
```

Open **http://127.0.0.1:8765** in a browser.

## Mock mode (test it with zero API keys)

If `OPENAI_API_KEY`, `GEMINI_API_KEY`, and/or `DEEPAI_API_KEY` are missing
from `.env`, the app automatically runs that part in **mock mode**:

- **Mock story**: a deterministic template story (5 pages, 2 consistent
  characters, safety rules respected) so you can test the full flow, UI,
  translation plumbing, and PDF/ZIP export without calling OpenAI.
- **Mock translation**: each page's text prefixed with `[<language> mock
  translation]` so the multi-language plumbing and per-language PDFs can be
  tested without calling Gemini (or OpenAI, if `TRANSLATION_PROVIDER=openai`
  - see "Translation provider" below).
- **Mock images**: Pillow-rendered placeholder illustrations (no network
  call) standing in for the real 3D cartoon art.

A demo-mode banner appears in the UI whenever either mock is active. Add real
keys to `.env` (or set `FORCE_MOCK=0` with both keys present) to switch to
live generation - no code changes needed.

This was actually run and tested locally in mock mode while building it (see
"What was tested" below).

## Translation provider

Translation defaults to **Gemini** (`GEMINI_API_KEY`), a deliberate deviation
from the product spec's "OpenAI only" rule (see the table above). Set
`TRANSLATION_PROVIDER=openai` in `.env` to run translation through OpenAI
instead - it reuses `OPENAI_API_KEY`/`OPENAI_MODEL` (no separate key needed)
via `story_engine/openai_client.py`'s own `translate_story()`, which mirrors
`gemini_client.py`'s one exactly (same `TRANSLATE_SYSTEM_PROMPT`/
`build_translate_user_prompt`, same per-language-additive story mutation,
same mock fallback). `story_engine/pipeline.py` picks whichever function to
call based on `config.TRANSLATION_PROVIDER`; an invalid value falls back to
`"gemini"`. Whichever provider is active, `MOCK_TRANSLATION` (and the
demo-mode banner) is based on *that* provider's own API key being set, not
the other one's.

## About "Deepak.org"

The spec's image-API section is titled **Deepak.org** and gives a payload
shape (`prompt`, `style`, `resolution`) that doesn't match any real, public
API found under that name. The closest real, well-documented service is
**DeepAI** (`deepai.org`), which offers a `text2img` REST API (`api-key`
header, `text` field, JSON response with `output_url`) - very likely what was
intended. `story_engine/image_client.py` targets DeepAI by default via
`DEEPAI_API_KEY` / `DEEPAI_BASE_URL`. If "Deepak.org" is actually a different,
specific service, only `image_client.py`'s `_deepai_image()` function needs to
change - everything else (pipeline, UI, export) is provider-agnostic.

## What the app does (mapped to the spec)

| Spec requirement | Where it's implemented |
|---|---|
| Web form: 2-3 sentence idea, region, secondary language | `static/create.html` / `create.js` (login required - see "Accounts, ownership, and publishing" above) |
| Story text via OpenAI only | `story_engine/openai_client.py` (`generate_story`) - translation is the one deviation, see below |
| 5 pages, 5 images, 5 text sections (default; admin-configurable) | `story_engine/prompts.py` (`build_system_prompt(page_count)`) - see "Scenes per story" below |
| Images via "Deepak.org"(/DeepAI) only | `story_engine/image_client.py` |
| Region influences visuals/culture/language | Baked into `SYSTEM_PROMPT` + `build_user_prompt` |
| Character consistency (name/look/personality fixed) | See "Character consistency strategy" below |
| Translation to a user-specified language, names preserved | `story_engine/gemini_client.py` (`translate_story`) + `TRANSLATE_SYSTEM_PROMPT` - **via Gemini by default, not OpenAI** (explicit deviation from the spec's OpenAI-only rule), switchable to OpenAI with `TRANSLATION_PROVIDER=openai` (`story_engine/openai_client.py`'s own `translate_story`, same prompts/behavior) - see `story_engine/pipeline.py`'s dispatch |
| Safety rules (no fear/violence/stereotypes, soft moral) | Hard-coded into `SYSTEM_PROMPT` |
| Assemble into downloadable PDF/ZIP storybook | `story_engine/book_export.py`, `/api/story/download` |
| Optional: upload a kid's photo to inspire a character | `describe_character_photo()` (OpenAI vision) + upload field in the form |

## Character consistency strategy

Earlier versions of this app just repeated every character's text description
on every page's prompt - in practice that still drifted, and it also drew
characters into scenes they weren't part of (see the bug it replaced, below).
The current pipeline (`story_engine/pipeline.py`):

1. The story model now returns a `characters_present` list on **every page**
   - the exact names (from the fixed `characters` list) that are actually in
   that scene. A crow introduced on page 3 is absent from pages 1-2; a page
   where one character "waits nearby" doesn't render them. Only those
   characters' descriptions go into that page's prompt and (in mock mode)
   only their sprites get drawn - never the full cast by default.
2. For the characters in a given page, their description text is built
   **once, deterministically, in code**
   (`prompts.build_character_prompt_block`) - never re-authored by the LLM
   per page, so the wording is byte-identical every time that character
   appears.
3. Each character's **first ("debut") appearance page is still tracked**
   (`story["character_debut_page"]`, each page's `reference_source_page` in
   `story.json`) - kept as bookkeeping/metadata even though, per point 4
   below, no reference image is actually sent to the API anymore.
4. **Real mode generates every page from text alone** (plain text2img,
   `image_client._deepai_image`) - an earlier version instead sent each
   later page's debut-character reference image to DeepAI's Image Editor
   endpoint (an image-to-image edit call), hoping for pixel-level
   consistency. In practice, inspecting real generated stories showed that
   endpoint essentially ignores the prompt's instructions to change pose/
   framing/background and just lightly touches up the reference image -
   every page ends up as the same scene redecorated, no matter how strongly
   the prompt says otherwise. That reference-image conditioning was removed
   for this reason: the locked character block's detailed, verbatim-repeated
   text description is relied on for consistency instead, trading a little
   pixel-level sameness for pages that actually depict different moments -
   which matters more for a picture book than perfect character-pixel
   identity. `_deepai_image_editor` is kept in the code, unused by default,
   for a future provider whose reference-image conditioning actually
   respects the prompt.
5. In mock mode this is directly, visibly testable with zero API keys: each
   character is drawn once as a deterministic Pillow "sprite" - fixed shape
   and palette derived only from the character's name+appearance+type, never
   from the scene text - and the literal same sprite bytes are pasted only
   into the pages that character is actually in.

**Honest limitation:** without reference-image conditioning, real-mode
character consistency now rests entirely on the text description (detailed
and byte-identical every page, but still just text) - occasional drift in
exact proportions/shading is possible in a way pixel-conditioning would have
reduced, in exchange for pages that are actually visually distinct scenes
rather than near-duplicates of page 1. If you have credentials for a
provider whose reference-image/character-seed conditioning genuinely
respects prompted changes (unlike DeepAI's editor), only
`generate_scene_image`'s body needs to change back to using it - the rest of
the pipeline (locking the block, per-character debut tracking) stays the
same.

### Fixing real-world drift: unstated accessories and hairstyle changes

A real (non-mock) run surfaced a concrete drift bug: two characters wearing
aviator goggles on one page only, and one character's ponytail height/bow
varying page to page - neither was in either character's fixed appearance
text. Root cause: `appearance` strings were free-form prose, so slots like
"accessories" were simply never mentioned, leaving the image model free to
invent (and un-invent) things like goggles between calls. Fixed by making
`appearance` a mandatory 5-slot schema, enforced in `SYSTEM_PROMPT`'s
`CHARACTER CONSISTENCY RULES` (`prompts.py`):

1. Skin/fur/feather tone (exact)
2. Hair color, length, and style (or fur/feather pattern for animals)
3. Face - eye color + exactly one fixed distinguishing feature
4. Outfit - exact garment(s) and exact color(s)
5. Accessories - every one named explicitly, or an explicit "no glasses,
   hats, jewelry, bows, or other accessories" - this slot is never left
   unstated, since an unstated slot is exactly where the drift crept in.

`IMAGE PROMPT FORMAT` now also closes every prompt with an explicit negative
constraint: *"Exact same face, hairstyle, and outfit colors as previously
established for each character - no new accessories, hats, goggles, or props
added to their body that weren't in their fixed appearance."* The same
negative-constraint language was added to the real-mode prompt built in
`image_client.generate_scene_image()`, and a rule was added forbidding a
one-scene prop (goggles found in a box, a rocket on a table) from silently
becoming a permanent worn accessory in later pages. Verified via a local
mock-mode pipeline run (`pipeline.run_job`) end-to-end after the change -
still produces 5 pages, correct `characters_present` filtering, and correct
debut-reference tracking.

### The bug this replaced

The very first mock-image renderer picked its color palette from a hash of
the *entire* per-page prompt (which changes page to page because the scene
text differs), so even the offline placeholders didn't look consistent -
exactly the "no match for the characters in each scene" problem. The fix
above (identity-only hashing for sprites, locked character block, page-1-as-
reference for real mode) directly addresses that.

## What was tested locally (mock mode, no API keys)

- Full pipeline: form submit -> job created -> background thread runs
  story generation -> 5 mock images rendered -> job reaches `done`
- `/api/story/status` polling reaches 100% / `done`
- `/api/story/image` serves each of the 5 generated PNGs
- `/api/story/download?format=zip` and `format=pdf` both produce valid files
- Safety/structure: exactly 5 pages, characters list present, moral present
- Every generated image carries a centered watermark at 15% opacity, using
  the admin's currently-configured site name (mock and real code paths both
  pass through the same `_add_watermark()` choke point in
  `image_client.py`, called from `generate_scene_image()` with
  `db.get_site_settings()["site_name"]`). Baked into the pixels at
  generation time, so renaming the site only affects newly generated
  images going forward, not ones already on disk.
- LLM/image API calls retry with backoff on connection failure, timeout, or
  a malformed/truncated response, and fail fast (no retry) on a clean 4xx
  like a bad API key - verified with simulated flaky/failing/truncated
  responses
- Multi-language: submitting `secondary_language: "Hindi, Tamil, hindi"`
  produces `story["languages"] == ["Hindi", "Tamil"]` (deduplicated), a
  separate valid PDF for English + each language, and a ZIP containing all
  three PDFs plus the images/story.json - tested both as direct function
  calls and through a live HTTP request against `server.py`
- `detect_script()`/`resolve_fonts()` tested directly against real
  Devanagari (Hindi), Tamil, and Malayalam text, confirming correct script
  detection, a clean (non-crashing) fallback when no Indic font is present,
  and correct font selection (`NirmalaUI`, `supports_script=True`) for all
  three when the Windows font path is simulated as present
- Story-page caption text auto-fits its font size (up to 17pt, stepping
  down only as far as needed) so longer pages of text never overflow past
  the illustration or collide with the page badge/footer - verified with
  both a typical ~50-word page and a deliberately long 59-word page

## Project layout

```
kertoons-app/
  server.py                 # stdlib HTTP server, no framework dependency
  story_engine/
    config.py               # env/.env loading, mock-mode detection
    prompts.py               # every spec rule encoded as prompts
    api_utils.py              # shared HTTP retry/error handling for both LLM clients
    openai_client.py          # story generation, vision (+ mocks)
    gemini_client.py          # translation (+ mocks)
    image_client.py           # Deepak.org/DeepAI image calls (+ mock renderer)
    pipeline.py               # orchestrates one end-to-end job
    book_export.py            # ZIP / PDF storybook assembly (one PDF per language)
    auth.py                   # password hashing (stdlib pbkdf2_hmac)
    db.py                     # JSON-file-backed accounts/sessions/story-ownership
  static/
    index.html, gallery.js    # public gallery (home page) - published stories only
    login.html, register.html, auth.js
    create.html, create.js    # story-creation form (login-gated) + "My Stories"
    story.html, story-view.js # view one story by job_id (owner-or-published)
    book.js                   # shared page-card/language-switch/regenerate/publish rendering
    nav.js                    # shared top nav (logged in/out state)
  generated/                 # per-job output (story.json + page_N.png)
  fonts/                     # drop Unicode fonts here for Indic-script PDFs (see fonts/README.txt)
  kertoons_data.json         # accounts / sessions / story ownership+publish state
```

## Notes on this build environment

This was developed in a sandboxed environment where `pip install` cannot
reach the internet, so it deliberately avoids any framework dependency
(Flask, FastAPI, etc.) and uses only Python's standard library `http.server`
for routing, plus `requests`/`Pillow`/`reportlab`/`python-dotenv`, which are
common and install instantly with normal internet access. On your own
machine, `pip install -r requirements.txt` should work normally.
