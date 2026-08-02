"""
Server-rendered "share" page for one story - GET /share.html?job_id=... in
server.py. Unlike every other page in static/ (a static file whose content
is filled in client-side via fetch() after load), this one is generated
here in Python and returned as a complete HTML document, because social
media crawlers (Facebook, X/Twitter, WhatsApp, Slack, iMessage, etc.) read
a page's Open Graph / Twitter Card <meta> tags from the INITIAL HTML
response only - they don't run JavaScript, so tags added client-side after
a fetch() would never be seen, and the link would preview as a bare URL
with no title/image/description.

Deliberately a plain, static read of the whole story - title, every page's
image + text, header, and footer - with NO interactive controls at all (no
download/regenerate/publish/language-switch, no share-intent buttons):
that's what story.html/create.html (book.js) are for; this page exists
purely to be the thing a shared link actually shows someone, with nothing
on it that only makes sense for the story's own owner. Each page's image +
text reuses the exact same .page-card markup/CSS as book.js's storybook
layout, so it looks identical without needing any client-side JS to render.
"""
import html

from . import config

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ - Kertoons</title>

<meta property="og:type" content="website">
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__DESCRIPTION__">
<meta property="og:image" content="__IMAGE_URL__">
<meta property="og:url" content="__SHARE_URL__">
<meta property="og:site_name" content="Kertoons">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__TITLE__">
<meta name="twitter:description" content="__DESCRIPTION__">
<meta name="twitter:image" content="__IMAGE_URL__">

<link rel="stylesheet" href="static/style.css">
</head>
<body>
<header>
  <h1>🧸 Kertoons</h1>
  <nav id="nav-bar" class="nav-bar"></nav>
</header>

<main>
  <section class="card">
    <div class="book-header">
      <h2>__TITLE__</h2>
      <div>Region: __REGION__</div>
      <div class="author-line">by __AUTHOR__</div>
      __MORAL_HTML__
    </div>

__PAGES_HTML__
  </section>
</main>

<script src="static/nav.js"></script>
<script>renderNav();</script>

<footer class="site-footer">
  <p><a href="faq.html">FAQ</a> · <a href="help.html">Help</a> ·
  <a href="https://kertoons.com" target="_blank" rel="noopener">kertoons.com</a> - Another Elisda AI project</p>
</footer>
</body>
</html>
"""

_PAGE_CARD_TEMPLATE = """    <div class="page-card">
      <div class="page-image-wrap">
        <img src="api/story/image?job_id=__JOB_ID__&page=__PAGE_NUM__" alt="Page __PAGE_NUM__ illustration">
      </div>
      <div class="page-text">
        <div class="page-num">PAGE __PAGE_NUM__</div>
        <p class="en">__PAGE_TEXT__</p>
      </div>
    </div>"""


def _build_pages_html(job_id: str, pages: list) -> str:
    cards = []
    for p in pages:
        page_num = str(p.get("page_number", ""))
        card = _PAGE_CARD_TEMPLATE
        card = card.replace("__JOB_ID__", html.escape(job_id))
        card = card.replace("__PAGE_NUM__", html.escape(page_num))
        card = card.replace("__PAGE_TEXT__", html.escape(p.get("text", "")))
        cards.append(card)
    return "\n".join(cards)


def render_share_page(job_id: str, story: dict, author: str) -> str:
    """Returns the complete HTML document for a story's public share page.
    Caller (server.py) is responsible for the visibility check (owner or
    published) before calling this - this function itself has no opinion on
    who's allowed to see it."""
    title = story.get("title") or "A Kertoons Story"
    region = story.get("region") or ""
    moral = story.get("moral") or ""
    pages = story.get("pages") or []
    first_page_text = pages[0].get("text", "") if pages else ""

    description = moral or first_page_text or "A warm, region-based cartoon story, made with Kertoons."
    if len(description) > 200:
        description = description[:197].rstrip() + "..."

    image_url = f"{config.PUBLIC_BASE_URL}/api/story/image?job_id={job_id}&page=1"
    share_url = f"{config.PUBLIC_BASE_URL}/share.html?job_id={job_id}"

    moral_html = f'<div class="moral">"{html.escape(moral)}"</div>' if moral else ""

    replacements = {
        "__TITLE__": html.escape(title),
        "__DESCRIPTION__": html.escape(description),
        "__IMAGE_URL__": html.escape(image_url),
        "__SHARE_URL__": html.escape(share_url),
        "__REGION__": html.escape(region),
        "__AUTHOR__": html.escape(author),
        "__MORAL_HTML__": moral_html,
        "__PAGES_HTML__": _build_pages_html(job_id, pages),
    }

    page = _PAGE_TEMPLATE
    for token, value in replacements.items():
        page = page.replace(token, value)
    return page
