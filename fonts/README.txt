Drop Unicode TrueType/OpenType fonts (.ttf / .otf) in this folder to control
exactly which font is used to render each language's PDF storybook.

Why this folder exists
-----------------------
reportlab's built-in PDF fonts (Helvetica, Times, Courier) only cover
Latin-1. A Hindi, Tamil, Bengali, etc. translation rendered with those fonts
comes out as blank boxes, not real text. story_engine/book_export.py fixes
this by registering a real Unicode font before drawing each page - but it
needs to find one on disk first.

Lookup order (see resolve_fonts() in story_engine/book_export.py):
  1. A matching font in THIS folder (checked first, works on any OS).
  2. Windows' built-in "Nirmala UI" (C:\Windows\Fonts\Nirmala.ttf) - already
     covers Devanagari, Bengali, Gujarati, Gurmukhi, Kannada, Malayalam,
     Odia, Tamil, and Telugu with zero setup, since this app runs locally on
     Windows. If you're on Windows, you likely don't need to put anything
     here at all.
  3. A couple of known Linux/Mac Indic font locations, if present.
  4. DejaVu Sans / Helvetica as a last resort (Latin-only - Indic text will
     not render correctly, and the generated PDF's cover page will show a
     note saying so).

What to put here (recommended if Hindi/Tamil/Malayalam/etc. are showing up
as blank boxes - this guarantees correct rendering regardless of what's
going on with your OS's own fonts)
--------------------------------------------------------------------------
Download the free "Noto Sans <Script>" font for the language you need and
place the .ttf file here with the script name in the filename, e.g.:

  fonts/NotoSansDevanagari-Regular.ttf   (Hindi, Marathi, Nepali, Sanskrit)
  fonts/NotoSansBengali-Regular.ttf      (Bengali, Assamese)
  fonts/NotoSansTamil-Regular.ttf
  fonts/NotoSansTelugu-Regular.ttf
  fonts/NotoSansKannada-Regular.ttf
  fonts/NotoSansMalayalam-Regular.ttf
  fonts/NotoSansGujarati-Regular.ttf
  fonts/NotoSansGurmukhi-Regular.ttf     (Punjabi)
  fonts/NotoSansOriya-Regular.ttf        (Odia)

Easiest way to get them: paste each URL below directly into your browser's
address bar - it downloads the .ttf immediately, no zip/extract needed.
Then just rename the downloaded file to match the names above and move it
into this folder.

  Devanagari (Hindi): https://fonts.gstatic.com/s/notosansdevanagari/v30/TuGoUUFzXI5FBtUq5a8bjKYTZjtRU6Sgv3NaV_SNmI0b8QQCQmHn6B2OHjbL_08AlXQly-A.ttf
  Tamil:              https://fonts.gstatic.com/s/notosanstamil/v31/ieVc2YdFI3GCY6SyQy1KfStzYKZgzN1z4LKDbeZce-0429tBManUktuex7vGo70R.ttf
  Malayalam:          https://fonts.gstatic.com/s/notosansmalayalam/v29/sJoi3K5XjsSdcnzn071rL37lpAOsUThnDZIfPdbeSNzVakglNM-Qw8EaeB8Nss-_RuD9BA.ttf

(These are versioned Google Fonts CDN URLs - if one ever 404s, go to
fonts.google.com, search the family name, click "Download family," and use
the .ttf from inside the zip instead - the filename just needs to contain
the script name.)

Add a "...-Bold" file alongside any of these for bold title text; it's
optional and falls back to the regular weight if missing.

The matching is a simple case-insensitive substring check against the
script's name (e.g. "devanagari"), so any filename containing that word
works. This folder is checked FIRST, before your OS's own fonts, so a file
placed here always wins.
