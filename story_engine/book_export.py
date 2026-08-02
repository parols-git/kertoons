"""
Packages a finished story (story.json + page_N.png files in a job
directory) into a downloadable ZIP, or into ONE PDF storybook PER LANGUAGE
(the original English book, plus one more for every language the story was
translated into) - per spec item 9: "assemble the pages into a downloadable
PDF/ZIP ... real cartoon storybook with images and text in specified
language."
"""
import os
import json
import zipfile
import io
import random

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --------------------------------------------------------- Unicode fonts
#
# reportlab's built-in fonts (Helvetica etc.) only cover Latin-1, so any
# Indian-language translation (Hindi, Tamil, Bengali, ...) would render as
# empty boxes. Fixed by registering a real Unicode TrueType font and
# picking one, per PDF, that actually covers the script the book's text
# uses. Priority order:
#
#   1. A font file dropped into this app's own `fonts/` folder - works
#      identically on every OS, so it's the most reliable option if your
#      OS doesn't already ship a font for the script you need. See
#      `fonts/README.txt` for exactly what to put there.
#   2. Windows' built-in "Nirmala UI" (C:\\Windows\\Fonts\\Nirmala.ttf) -
#      ONE font file that covers Devanagari, Bengali, Gujarati, Gurmukhi,
#      Kannada, Malayalam, Odia, Tamil, Telugu, AND Latin, and ships on
#      every Windows 8+ machine with zero setup - the expected common case
#      since this app is meant to be run locally on Windows.
#   3. A few known Linux/Mac Indic-capable font locations, if present.
#   4. DejaVu Sans / Helvetica as a last resort (Latin/Cyrillic/Greek only
#      - Indic scripts will NOT render correctly with this, but the PDF
#      still gets built instead of crashing, and a note is printed on the
#      cover page explaining why).

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

# (script key, unicode block start, unicode block end)
_SCRIPT_RANGES = {
    "devanagari": (0x0900, 0x097F),  # Hindi, Marathi, Nepali, Sanskrit, Konkani
    "bengali":    (0x0980, 0x09FF),  # Bengali, Assamese
    "gurmukhi":   (0x0A00, 0x0A7F),  # Punjabi
    "gujarati":   (0x0A80, 0x0AFF),
    "oriya":      (0x0B00, 0x0B7F),  # Odia
    "tamil":      (0x0B80, 0x0BFF),
    "telugu":     (0x0C00, 0x0C7F),
    "kannada":    (0x0C80, 0x0CFF),
    "malayalam":  (0x0D00, 0x0D7F),
}

_WINDOWS_INDIC_FONT = (r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\NirmalaB.ttf")
_OTHER_OS_INDIC_FONTS = [
    # (regular, bold-or-None) - only used if actually present on disk
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"),
    ("/usr/share/fonts/lohit-devanagari/Lohit-Devanagari.ttf", None),
    ("/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf", None),
    ("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", None),
]
_DEJAVU_FALLBACK = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_registered = set()  # font names already registered with reportlab this process


def detect_script(text: str) -> str:
    """Return the Indic script name most present in `text`, or "" if the
    text is plain Latin script (English, Spanish, French, ...)."""
    counts = {}
    for ch in text or "":
        cp = ord(ch)
        for script, (lo, hi) in _SCRIPT_RANGES.items():
            if lo <= cp <= hi:
                counts[script] = counts.get(script, 0) + 1
                break
    return max(counts, key=counts.get) if counts else ""


def _try_register(name: str, path: str) -> bool:
    if name in _registered:
        return True
    if not path:
        return False
    if not os.path.exists(path):
        print(f"[kertoons] font not found, skipping: {path}")
        return False
    try:
        pdfmetrics.registerFont(TTFont(name, path))
        _registered.add(name)
        print(f"[kertoons] font loaded OK: {name} <- {path}")
        return True
    except Exception as e:
        # Previously this failure was swallowed silently, so a font that
        # EXISTS on disk but that reportlab's TTFont parser can't handle
        # (e.g. certain variable/complex OpenType builds) would fall
        # through to the Latin-only fallback with zero explanation. Now
        # it's printed to the console so a "why is my Hindi PDF blank"
        # report can be diagnosed from the server's own log output.
        print(f"[kertoons] font FAILED to load ({type(e).__name__}: {e}): {path}")
        return False


def resolve_fonts(script: str):
    """Returns (regular_font_name, bold_font_name, supports_script: bool)."""
    # 1. project-local fonts/ folder - filename containing the script name
    if script and os.path.isdir(FONTS_DIR):
        files = sorted(os.listdir(FONTS_DIR))
        regular_candidates = [f for f in files
                               if script in f.lower() and f.lower().endswith((".ttf", ".otf"))
                               and "bold" not in f.lower()]
        if regular_candidates:
            reg_path = os.path.join(FONTS_DIR, regular_candidates[0])
            reg_name = f"local_{script}"
            if _try_register(reg_name, reg_path):
                bold_name = reg_name
                bold_candidates = [f for f in files if script in f.lower() and "bold" in f.lower()]
                if bold_candidates:
                    bname = f"local_{script}_bold"
                    if _try_register(bname, os.path.join(FONTS_DIR, bold_candidates[0])):
                        bold_name = bname
                return reg_name, bold_name, True

    # 2. Windows Nirmala UI - single file, covers every script above
    if script:
        reg_path, bold_path = _WINDOWS_INDIC_FONT
        if _try_register("NirmalaUI", reg_path):
            bold_name = "NirmalaUI-Bold" if _try_register("NirmalaUI-Bold", bold_path) else "NirmalaUI"
            return "NirmalaUI", bold_name, True

        # 3. other OS-specific known locations
        for reg_path, bold_path in _OTHER_OS_INDIC_FONTS:
            name = "indic_" + os.path.basename(reg_path).replace(" ", "_")
            if _try_register(name, reg_path):
                bold_name = name
                if bold_path and _try_register(name + "_bold", bold_path):
                    bold_name = name + "_bold"
                return name, bold_name, True

    # 4. Any font dropped in fonts/ at all (helps accented Latin scripts
    #    too, e.g. Spanish/French), else DejaVu Sans, else Helvetica.
    if os.path.isdir(FONTS_DIR):
        any_fonts = [f for f in sorted(os.listdir(FONTS_DIR))
                     if f.lower().endswith((".ttf", ".otf")) and "bold" not in f.lower()]
        if any_fonts and _try_register("local_default", os.path.join(FONTS_DIR, any_fonts[0])):
            return "local_default", "local_default", False

    dejavu_reg, dejavu_bold = _DEJAVU_FALLBACK
    if _try_register("DejaVuSans", dejavu_reg):
        bold_name = "DejaVuSans-Bold" if _try_register("DejaVuSans-Bold", dejavu_bold) else "DejaVuSans"
        return "DejaVuSans", bold_name, False

    return "Helvetica", "Helvetica-Bold", False


# ------------------------------------------------------- storybook chrome
#
# Visual design so the exported PDF reads like an actual children's picture
# book (soft color pages, a decorative frame, a caption "card" under each
# illustration, a banner-style title, a page badge, a closing page) instead
# of plain black text under a square image. Branding text (footer, badges,
# "The End") is always drawn with the standard Helvetica family since it's
# always plain English/ASCII; only the story's own title/moral/page text -
# which might be Hindi, Tamil, etc. - uses the resolved Unicode font.

BRAND_URL = "https://www.kertoons.com"
BRAND_LABEL = "www.kertoons.com"

# (page background, accent/border color) - cycled per page for a playful,
# varied feel across the book; RGB values are 0-1 floats for reportlab.
_PALETTE = [
    ((1.00, 0.97, 0.90), (0.88, 0.52, 0.20)),  # cream / marigold
    ((0.90, 0.96, 1.00), (0.27, 0.52, 0.83)),  # sky blue / blue
    ((0.94, 1.00, 0.94), (0.28, 0.62, 0.38)),  # mint / green
    ((1.00, 0.92, 0.96), (0.83, 0.38, 0.53)),  # blush / rose
    ((0.97, 0.94, 1.00), (0.53, 0.38, 0.78)),  # lavender / violet
]


def _wrap_lines(c, text, font, size, max_width):
    """Word-wrap `text` into a list of lines that each fit `max_width` at
    `font`/`size`, without drawing anything - used to size a card/box
    before drawing it, and shared by every text-drawing helper below."""
    c.setFont(font, size)
    words = (text or "").split()
    lines = []
    line = ""
    for word in words:
        test = f"{line} {word}".strip()
        if c.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


def _draw_sparkle(c, x, y, rgb, size=5.5):
    """A tiny 4-pointed star, drawn as vector shapes rather than a font
    glyph - reportlab's standard fonts have no emoji/symbol coverage, so a
    real star character would just be a missing-glyph box."""
    c.saveState()
    c.setFillColorRGB(*rgb)
    c.translate(x, y)
    path = c.beginPath()
    path.moveTo(0, size)
    path.lineTo(size * 0.28, size * 0.28)
    path.lineTo(size, 0)
    path.lineTo(size * 0.28, -size * 0.28)
    path.lineTo(0, -size)
    path.lineTo(-size * 0.28, -size * 0.28)
    path.lineTo(-size, 0)
    path.lineTo(-size * 0.28, size * 0.28)
    path.close()
    c.drawPath(path, fill=1, stroke=0)
    c.restoreState()


def _draw_footer(c, W, accent_rgb):
    """Brand line on every page, centered at the bottom, drawn AND wired up
    as a real clickable PDF hyperlink to kertoons.com. Sits on a small
    opaque white pill so it stays legible regardless of what's behind it -
    a flat pastel page, or a busy full-bleed cover illustration."""
    font, size = "Helvetica-Oblique", 9.5
    text_w = c.stringWidth(BRAND_LABEL, font, size)
    text_x, text_y = W / 2, 24

    pad_x, pad_y = 10, 5
    pill_w = text_w + pad_x * 2
    pill_h = size + pad_y * 2
    c.setFillColorRGB(1, 1, 1)
    c.roundRect(text_x - pill_w / 2, text_y - pad_y + 1, pill_w, pill_h, pill_h / 2, fill=1, stroke=0)

    c.setFont(font, size)
    c.setFillColorRGB(*accent_rgb)
    c.drawCentredString(text_x, text_y, BRAND_LABEL)
    c.setStrokeColorRGB(*accent_rgb)
    c.setLineWidth(0.6)
    c.line(text_x - text_w / 2, text_y - 2, text_x + text_w / 2, text_y - 2)
    c.linkURL(
        BRAND_URL,
        (text_x - text_w / 2 - 3, text_y - 5, text_x + text_w / 2 + 3, text_y + size + 2),
        relative=0,
    )


def _truncate_to_width(c, text: str, font: str, size: float, max_width: float) -> str:
    """A single line, shortened with a trailing ellipsis if it wouldn't
    otherwise fit - used for the title eyebrow below, which must stay one
    line (wrapping it to two would crowd the illustration underneath it)."""
    if c.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and c.stringWidth(truncated + ellipsis, font, size) > max_width:
        truncated = truncated[:-1]
    return (truncated.rstrip() + ellipsis) if truncated else ellipsis


def _draw_title_eyebrow(c, W, H, title, font, accent_rgb):
    """A slim story-title line at the very top of a page, inside the frame -
    so a reader flipping through the exported book always knows which story
    they're in, on every page after the cover (which already carries the
    title prominently in its own big banner and doesn't need this too)."""
    size = 12.5
    max_width = W - 170  # stays clear of the corner sparkles on both sides
    text = _truncate_to_width(c, (title or "").upper(), font, size, max_width)
    c.setFont(font, size)
    c.setFillColorRGB(*accent_rgb)
    c.drawCentredString(W / 2, H - 40, text)


def _draw_page_badge(c, W, accent_rgb, label):
    """A small circular page-number badge in the bottom-right corner,
    instead of plain corner text."""
    r = 17
    cx, cy = W - 46, 46
    c.setFillColorRGB(*accent_rgb)
    c.circle(cx, cy, r, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(cx, cy - 3.5, label)


def _draw_frame_and_footer(c, W, H, accent_rgb):
    """Decorative rounded border frame + corner sparkles + the branded
    footer link - everything EXCEPT the background fill, so this can be
    layered on top of either a flat pastel color or a full-bleed cover
    illustration."""
    inset = 18
    c.setStrokeColorRGB(*accent_rgb)
    c.setLineWidth(3)
    c.roundRect(inset, inset, W - 2 * inset, H - 2 * inset, 16, fill=0, stroke=1)

    for corner_x in (inset + 12, W - inset - 12):
        for corner_y in (inset + 12, H - inset - 12):
            _draw_sparkle(c, corner_x, corner_y, accent_rgb)

    _draw_footer(c, W, accent_rgb)


def _draw_page_chrome(c, W, H, bg_rgb, accent_rgb):
    """Pastel background + the frame/sparkles/footer above - drawn first,
    under everything else, on every page including the cover (the cover
    additionally gets a scattered photo collage on top - see
    `_draw_polaroid` / build_pdf)."""
    c.setFillColorRGB(*bg_rgb)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    _draw_frame_and_footer(c, W, H, accent_rgb)


def _draw_polaroid(c, img_path: str, cx: float, cy: float, size: float, angle_deg: float):
    """Draw one square illustration as a small rotated, white-bordered
    'photo' centered at (cx, cy) - used up to 3 at a time, overlapping, to
    build the cover's scattered scrapbook-style collage."""
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle_deg)

    # soft drop shadow behind the card
    c.saveState()
    c.setFillAlpha(0.28)
    c.setFillColorRGB(0, 0, 0)
    c.roundRect(-size / 2 + 5, -size / 2 - 5, size, size, 8, fill=1, stroke=0)
    c.restoreState()

    border = 10
    c.setFillColorRGB(1, 1, 1)
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(1)
    c.roundRect(-size / 2, -size / 2, size, size, 8, fill=1, stroke=1)

    inner = size - border * 2
    c.drawImage(
        ImageReader(img_path), -size / 2 + border, -size / 2 + border,
        width=inner, height=inner, preserveAspectRatio=True, anchor="c",
    )
    c.restoreState()


def _fit_caption_font_size(c, text, font, max_width, max_height,
                            candidates=(17, 16, 15, 14, 13, 12)):
    """Pick the LARGEST font size from `candidates` whose wrapped caption
    card still fits within `max_height` - so story text renders as big as
    possible by default, but automatically steps down for a longer page
    of text rather than overflowing past the illustration/footer."""
    pad = 18
    for size in candidates:
        lines = _wrap_lines(c, text, font, size, max_width - 40)
        card_h = pad * 2 + (size * 1.45) * len(lines)
        if card_h <= max_height:
            return size
    return candidates[-1]


def _draw_caption_card(c, x, y_top, width, text, font, size, accent_rgb):
    """The page's story text, in a soft rounded 'caption card' under the
    illustration - centered lines, like a real picture-book caption -
    instead of plain left-aligned black text. Returns the card's bottom y."""
    lines = _wrap_lines(c, text, font, size, width - 40)
    line_h = size * 1.45
    pad = 18
    card_h = pad * 2 + line_h * len(lines)
    card_y = y_top - card_h

    c.setFillColorRGB(1, 1, 0.975)
    c.setStrokeColorRGB(*accent_rgb)
    c.setLineWidth(1.4)
    c.roundRect(x, card_y, width, card_h, 16, fill=1, stroke=1)

    c.setFillColorRGB(0.16, 0.13, 0.11)
    cx = x + width / 2
    yy = y_top - pad - size
    for line in lines:
        c.setFont(font, size)
        c.drawCentredString(cx, yy, line)
        yy -= line_h
    return card_y


def _draw_quote_card(c, W, y_top, quote_text, font, accent_rgb, size=13, max_width=None):
    """A centered rounded card for the story's moral - used on both the
    cover and the closing page."""
    if max_width is None:
        max_width = W - 160
    lines = _wrap_lines(c, quote_text, font, size, max_width - 40)
    line_h = size * 1.4
    pad = 16
    card_h = pad * 2 + line_h * len(lines)
    card_x = (W - max_width) / 2
    card_y = y_top - card_h

    c.setFillColorRGB(1, 1, 1)
    c.setStrokeColorRGB(*accent_rgb)
    c.setLineWidth(1.5)
    c.roundRect(card_x, card_y, max_width, card_h, 14, fill=1, stroke=1)

    c.setFillColorRGB(0.22, 0.18, 0.14)
    yy = y_top - pad - size
    for line in lines:
        c.setFont(font, size)
        c.drawCentredString(W / 2, yy, line)
        yy -= line_h
    return card_y


# -------------------------------------------------------------- exporters

def build_zip(job_dir: str) -> bytes:
    """Bundles story.json, every page image, AND every language's PDF
    storybook (English + each translation) into one ZIP."""
    with open(os.path.join(job_dir, "story.json"), "r", encoding="utf-8") as f:
        story = json.load(f)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        story_path = os.path.join(job_dir, "story.json")
        if os.path.exists(story_path):
            zf.write(story_path, arcname="story.json")
        for fname in sorted(os.listdir(job_dir)):
            if fname.lower().endswith(".png"):
                zf.write(os.path.join(job_dir, fname), arcname=fname)

        zf.writestr("storybook_en.pdf", build_pdf(job_dir, language=None))
        for lang in story.get("languages", []):
            safe = _safe_lang_slug(lang)
            zf.writestr(f"storybook_{safe}.pdf", build_pdf(job_dir, language=lang))
    buf.seek(0)
    return buf.getvalue()


def build_pdf(job_dir: str, language: str = None) -> bytes:
    """Build ONE storybook PDF. `language=None` (or "en"/"english") builds
    the original English book. Any other value must match a language the
    story was translated into (story["languages"]) and builds a book using
    ONLY that language's text - a separate, standalone book per language,
    not English-plus-translation on the same page."""
    with open(os.path.join(job_dir, "story.json"), "r", encoding="utf-8") as f:
        story = json.load(f)

    is_english = not language or language.strip().lower() in ("en", "english", "original")
    pages = story.get("pages", [])

    def text_for(page):
        if is_english:
            return page.get("text", "")
        return (page.get("translations") or {}).get(language) or page.get("text", "")

    sample_text = " ".join(text_for(p) for p in pages) + " " + story.get("title", "")
    script = "" if is_english else detect_script(sample_text)
    regular_font, bold_font, supports_script = resolve_fonts(script)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    cover_bg, cover_accent = _PALETTE[0]

    # ------------------------------------------------------------- cover
    #
    # Flat pastel background + frame, a title banner near the TOP (so it
    # never sits on top of the artwork), and below it a scattered "photo
    # pile" collage of up to 3 RANDOMLY chosen illustrations from the
    # story's (up to 5) generated pages, sized as large as the remaining
    # space allows - overlapping each other, but never the banner.
    _draw_page_chrome(c, W, H, cover_bg, cover_accent)

    # --- 1. compute the banner's geometry first (no drawing yet) - the
    # collage layout below needs to know exactly where the banner ends.
    title = story.get("title", "Kertoons Story")
    title_size = 34
    title_lines = _wrap_lines(c, title, bold_font, title_size, W - 200)
    title_line_h = title_size * 1.15

    subtitle = "A KERTOONS STORYBOOK"
    subtitle_size = 11
    region_line = f"Set in {story.get('region', '')}"
    if not is_english:
        region_line += f"   ({language} edition)"
    region_size = 13

    pad_top, pad_bottom = 24, 20
    gap_title_divider = 12
    gap_divider_subtitle = 14
    gap_subtitle_region = 10
    banner_h = (
        pad_top + title_line_h * len(title_lines) + gap_title_divider
        + gap_divider_subtitle + subtitle_size * 1.2 + gap_subtitle_region
        + region_size * 1.2 + pad_bottom
    )
    banner_w = W - 120
    banner_x = (W - banner_w) / 2
    banner_y_top = H - 58  # high up, just under the corner sparkles
    banner_y = banner_y_top - banner_h

    # --- 2. lay out the photo collage entirely BELOW the banner, sized to
    # fill the remaining vertical space as large as possible, and never
    # overlapping it.
    moral_zone_top = 215  # keep clear of the moral quote card near the bottom
    collage_top = banner_y - 18
    collage_bottom = moral_zone_top
    band_h = max(120, collage_top - collage_bottom)

    available_images = [
        os.path.join(job_dir, p.get("image_file", ""))
        for p in pages if p.get("image_file")
        and os.path.exists(os.path.join(job_dir, p.get("image_file", "")))
    ]
    chosen_images = random.sample(available_images, min(3, len(available_images)))

    # rotated squares need extra clearance so their corners don't poke past
    # the band - 1.18x covers up to ~10deg of rotation comfortably.
    photo_size = max(170, min(330, band_h / 1.18))
    band_mid = (collage_top + collage_bottom) / 2
    _COLLAGE_LAYOUT = [
        (W * 0.28, band_mid + band_h * 0.14, photo_size, -9),
        (W * 0.72, band_mid + band_h * 0.12, photo_size, 8),
        (W * 0.50, band_mid - band_h * 0.16, photo_size * 1.02, -4),
    ]
    for img_path, (cx, cy, size, angle) in zip(chosen_images, _COLLAGE_LAYOUT):
        try:
            _draw_polaroid(c, img_path, cx, cy, size, angle)
        except Exception:
            continue

    # --- 3. now draw the banner ON TOP, guaranteeing it's never covered by
    # (or covering) the collage below it.
    c.saveState()
    c.setFillAlpha(0.30)
    c.setFillColorRGB(0, 0, 0)
    c.roundRect(banner_x + 5, banner_y - 5, banner_w, banner_h, 22, fill=1, stroke=0)
    c.restoreState()

    c.setFillColorRGB(*cover_accent)
    c.roundRect(banner_x, banner_y, banner_w, banner_h, 22, fill=1, stroke=0)

    yy = banner_y_top - pad_top - title_size * 0.85
    c.setFillColorRGB(1, 1, 1)
    for line in title_lines:
        c.setFont(bold_font, title_size)
        c.drawCentredString(W / 2, yy, line)
        yy -= title_line_h

    yy -= gap_title_divider
    c.setStrokeColorRGB(1, 1, 1)
    c.setLineWidth(1)
    divider_w = banner_w * 0.35
    c.line(W / 2 - divider_w / 2, yy, W / 2 + divider_w / 2, yy)

    yy -= gap_divider_subtitle
    c.setFont("Helvetica-Bold", subtitle_size)
    c.drawCentredString(W / 2, yy, subtitle)

    yy -= gap_subtitle_region + region_size * 0.2
    c.setFont(regular_font, region_size)
    c.drawCentredString(W / 2, yy, region_line)

    if story.get("moral"):
        _draw_quote_card(c, W, 190, f'"{story["moral"]}"', regular_font, cover_accent, size=13)

    if script and not supports_script:
        c.setFont("Helvetica-Oblique", 8.5)
        c.setFillColorRGB(0.55, 0.2, 0.2)
        c.drawCentredString(
            W / 2, 95,
            f"Note: no {script.title()}-script font found on this system - "
            f"add one to the app's fonts/ folder for correct rendering (see README)."
        )
    c.showPage()

    # -------------------------------------------------------- story pages
    total_pages = len(pages)
    for i, page in enumerate(pages):
        bg_rgb, accent_rgb = _PALETTE[i % len(_PALETTE)]
        _draw_page_chrome(c, W, H, bg_rgb, accent_rgb)
        _draw_title_eyebrow(c, W, H, title, bold_font, accent_rgb)

        margin = 65
        img_path = os.path.join(job_dir, page.get("image_file", ""))
        img_w = W - 2 * margin
        img_h = img_w  # source illustrations are square - keep the frame
        img_y = H - 50 - img_h  # square too, or preserveAspectRatio letterboxes

        # soft drop "shadow" behind the illustration for a bit of depth
        c.setFillColorRGB(*_tint(bg_rgb, accent_rgb, 0.35))
        c.roundRect(margin + 4, img_y - 4, img_w, img_h, 10, fill=1, stroke=0)

        if os.path.exists(img_path):
            c.drawImage(ImageReader(img_path), margin, img_y, width=img_w, height=img_h,
                        preserveAspectRatio=True, anchor="n")
        else:
            c.setFillColorRGB(0.9, 0.9, 0.9)
            c.rect(margin, img_y, img_w, img_h, fill=1, stroke=0)
        c.setStrokeColorRGB(*accent_rgb)
        c.setLineWidth(2)
        c.roundRect(margin, img_y, img_w, img_h, 8, fill=0, stroke=1)

        # size the caption text as large as will still fit cleanly above the
        # footer/page-badge - bigger by default, auto-shrinking only for a
        # longer page of text instead of ever overflowing the box.
        caption_top = img_y - 16
        caption_max_h = caption_top - 78
        fitted_size = _fit_caption_font_size(c, text_for(page), regular_font, img_w, caption_max_h)
        _draw_caption_card(c, margin, caption_top, img_w, text_for(page), regular_font, fitted_size, accent_rgb)
        _draw_page_badge(c, W, accent_rgb, f"{page.get('page_number', i + 1)}/{total_pages}")
        c.showPage()

    # -------------------------------------------------------- closing page
    end_bg, end_accent = _PALETTE[(len(pages)) % len(_PALETTE)]
    _draw_page_chrome(c, W, H, end_bg, end_accent)
    _draw_title_eyebrow(c, W, H, title, bold_font, end_accent)
    c.setFont(bold_font, 40)
    c.setFillColorRGB(*end_accent)
    c.drawCentredString(W / 2, H / 2 + 70, "THE END")
    if story.get("moral"):
        c.setFont("Helvetica-Oblique", 13)
        c.setFillColorRGB(0.25, 0.22, 0.18)
        c.drawCentredString(W / 2, H / 2 + 25, "And remember...")
        _draw_quote_card(c, W, H / 2, f'"{story["moral"]}"', regular_font, end_accent, size=14)
    c.setFont("Helvetica", 11)
    c.setFillColorRGB(0.35, 0.3, 0.25)
    c.drawCentredString(W / 2, H / 2 - 110, "Thanks for reading a Kertoons story!")
    c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()


def _tint(bg_rgb, accent_rgb, amount):
    """Blend bg toward accent by `amount` (0-1) - used for a soft shadow
    behind the illustration that still matches that page's color scheme."""
    return tuple(bg * (1 - amount) + acc * amount for bg, acc in zip(bg_rgb, accent_rgb))


def _safe_lang_slug(lang: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in lang.strip()).strip("_").lower() or "lang"
