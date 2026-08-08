"""
Participation and winner PDF certificates for Monthly Story Competitions -
built with the exact same reportlab primitives book_export.py already
solved (Unicode/Indic font resolution, word wrapping, sparkle decorations,
the branded footer), imported directly from that module rather than
duplicating any of it.
"""
import io

from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas

from . import db
from .book_export import BRAND_URL, _PALETTE, _draw_footer, _draw_sparkle, _wrap_lines, detect_script, resolve_fonts

_GOLD = (0.83, 0.68, 0.21)
_SILVER = (0.65, 0.65, 0.68)
_BRONZE = (0.72, 0.45, 0.20)
_PLACEMENT_LABEL = {1: "1st Place", 2: "2nd Place", 3: "3rd Place"}
_PLACEMENT_COLOR = {1: _GOLD, 2: _SILVER, 3: _BRONZE}


def _entrant_name(entry: dict) -> str:
    user = db.get_user_by_id(entry.get("user_id"))
    return user["username"] if user else "A Kertoons Storyteller"


def _draw_base_certificate(c, W, H, accent_rgb, bg_rgb, eyebrow: str, headline: str, story: dict, competition: dict):
    c.setFillColorRGB(*bg_rgb)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    margin = 28
    c.setStrokeColorRGB(*accent_rgb)
    c.setLineWidth(3)
    c.rect(margin, margin, W - margin * 2, H - margin * 2, fill=0, stroke=1)
    c.setLineWidth(1)
    c.rect(margin + 8, margin + 8, W - (margin + 8) * 2, H - (margin + 8) * 2, fill=0, stroke=1)

    for x, y in ((margin + 30, H - margin - 30), (W - margin - 30, H - margin - 30),
                 (margin + 30, margin + 30), (W - margin - 30, margin + 30)):
        _draw_sparkle(c, x, y, accent_rgb, size=9)

    c.setFillColorRGB(*accent_rgb)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(W / 2, H - 90, eyebrow.upper())

    story_script = detect_script((story or {}).get("title", ""))
    title_font, title_bold_font, _ = resolve_fonts(story_script)

    c.setFillColorRGB(0.2, 0.16, 0.12)
    c.setFont(title_bold_font, 34)
    c.drawCentredString(W / 2, H - 140, headline)

    return title_font, title_bold_font


def generate_participation_certificate(entry: dict, story: dict, competition: dict) -> bytes:
    W, H = landscape(letter)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    palette_idx = (competition.get("id", 0)) % len(_PALETTE)
    bg_rgb, accent_rgb = _PALETTE[palette_idx]

    title_font, title_bold_font = _draw_base_certificate(
        c, W, H, accent_rgb, bg_rgb, "Certificate of Participation", "Kertoons Story Competition",
        story, competition,
    )

    entrant = _entrant_name(entry)
    c.setFillColorRGB(0.2, 0.16, 0.12)
    c.setFont("Helvetica", 15)
    c.drawCentredString(W / 2, H - 190, "This certificate is proudly presented to")

    c.setFont(title_bold_font, 26)
    c.setFillColorRGB(*accent_rgb)
    c.drawCentredString(W / 2, H - 230, entrant)

    body = (
        f'for entering "{story.get("title", "their story")}" into the '
        f'{competition.get("title", "Kertoons")} story competition'
        + (f', themed "{competition.get("theme")}"' if competition.get("theme") else "") + "."
    )
    c.setFillColorRGB(0.25, 0.2, 0.16)
    c.setFont("Helvetica", 13)
    for i, line in enumerate(_wrap_lines(c, body, "Helvetica", 13, W - 220)):
        c.drawCentredString(W / 2, H - 270 - i * 18, line)

    c.setFont("Helvetica-Oblique", 11)
    c.setFillColorRGB(0.4, 0.35, 0.3)
    c.drawCentredString(W / 2, 90, "Every entrant is a storyteller worth celebrating.")

    site_settings = db.get_site_settings()
    _draw_footer(c, W, accent_rgb, site_settings["site_name"],
                 site_settings["contact_email"], site_settings["contact_phone"])
    c.showPage()
    c.save()
    return buf.getvalue()


def generate_winner_certificate(entry: dict, story: dict, competition: dict, placement: int) -> bytes:
    W, H = landscape(letter)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    accent_rgb = _PLACEMENT_COLOR.get(placement, _GOLD)
    bg_rgb = (1.0, 0.98, 0.90)
    placement_label = _PLACEMENT_LABEL.get(placement, f"{placement}th Place")

    title_font, title_bold_font = _draw_base_certificate(
        c, W, H, accent_rgb, bg_rgb, f"Winner - {placement_label}", "Kertoons Story Competition",
        story, competition,
    )

    entrant = _entrant_name(entry)
    c.setFillColorRGB(0.2, 0.16, 0.12)
    c.setFont("Helvetica", 15)
    c.drawCentredString(W / 2, H - 190, "Awarded to")

    c.setFont(title_bold_font, 28)
    c.setFillColorRGB(*accent_rgb)
    c.drawCentredString(W / 2, H - 232, entrant)

    body = (
        f'for winning {placement_label} in the {competition.get("title", "Kertoons")} story competition '
        f'with "{story.get("title", "their story")}"'
        + (f', themed "{competition.get("theme")}"' if competition.get("theme") else "") + "."
    )
    c.setFillColorRGB(0.25, 0.2, 0.16)
    c.setFont("Helvetica", 13)
    for i, line in enumerate(_wrap_lines(c, body, "Helvetica", 13, W - 220)):
        c.drawCentredString(W / 2, H - 272 - i * 18, line)

    score_total = entry.get("score_total")
    if score_total is not None:
        c.setFont("Helvetica-Oblique", 12)
        c.setFillColorRGB(0.4, 0.35, 0.3)
        c.drawCentredString(W / 2, 90, f"Final score: {score_total} / 50")

    site_settings = db.get_site_settings()
    _draw_footer(c, W, accent_rgb, site_settings["site_name"],
                 site_settings["contact_email"], site_settings["contact_phone"])
    c.showPage()
    c.save()
    return buf.getvalue()
