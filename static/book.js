// Shared story-rendering: page cards (image + selected-language text), the
// language switcher, "regenerate image" (owner only), and the
// publish/unpublish toggle (owner only). Used by both create.js (right
// after generating a fresh story) and story-view.js (viewing any story by
// job_id - one's own, or someone else's published one).

let _bookState = null; // { jobId, story, isOwner, published, pdfAvailable }

// The server keys pdf_available by "en" for the original-language book and
// by the exact language string (as in story.languages) for every
// translation - this maps a downloadPdf()-style language argument to that
// same key so the two sides always agree on which entry to read/update.
function _pdfAvailabilityKey(language) {
  const norm = (language || "").trim().toLowerCase();
  return (!norm || ["en", "english", "original"].includes(norm)) ? "en" : language;
}

function _pdfButtonLabel(available, label) {
  return available ? `📥 Download ${label} PDF` : `⚙️ Generate ${label} PDF`;
}

// Re-labels every PDF button from _bookState.pdfAvailable against whichever
// quality tier is currently selected - high and low are cached separately
// (see book_export.get_pdf), so a language can be "Download" at one quality
// and "Generate" at the other.
function _updateAllPdfButtonLabels() {
  if (!_bookState) return;
  const quality = _selectedPdfQuality();
  const availability = _bookState.pdfAvailable || {};
  document.querySelectorAll("[data-pdf-lang]").forEach(btn => {
    if (btn.disabled) return;
    const avail = availability[btn.dataset.pdfLang];
    btn.textContent = _pdfButtonLabel(!!(avail && avail[quality]), btn.dataset.pdfLabel);
  });
}

function escapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pageTextFor(page, lang) {
  if (lang === "English") return page.text;
  return (page.translations || {})[lang] || page.text;
}

function buildPagesHtml(story, jobId, lang, isOwner) {
  return (story.pages || []).map(p => `
    <div class="page-card">
      <div class="page-image-wrap">
        <img id="page-img-${p.page_number}" src="api/story/image?job_id=${jobId}&page=${p.page_number}" alt="Page ${p.page_number} illustration">
        ${isOwner ? `
                <textarea class="prompt-textarea" id="prompt-input-${p.page_number}"
                    placeholder="Image prompt...">${escapeHtml(p.image_prompt || "")}</textarea>
                <button type="button" class="btn-outline btn-regen" id="regen-btn-${p.page_number}"
                onclick="regenerateImage(${p.page_number})">🔄 Regenerate image</button>` : ""}
      </div>
      <div class="page-text">
        <div class="page-num">PAGE ${p.page_number}</div>
        <p class="en">${escapeHtml(pageTextFor(p, lang))}</p>
      </div>
    </div>`
  ).join("");
}

async function regenerateImage(pageNumber) {
  if (!_bookState) return;
  const { jobId } = _bookState;
  const btn = document.getElementById(`regen-btn-${pageNumber}`);
  const img = document.getElementById(`page-img-${pageNumber}`);
  const promptBox = document.getElementById(`prompt-input-${pageNumber}`);
  if (!btn || !img) return;

  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Regenerating...";

  try {
    const res = await fetch("api/story/regenerate_image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, page_number: pageNumber, prompt: promptBox ? promptBox.value : undefined }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to regenerate image");
    // Cache-bust: same filename on the server, so the browser must be told
    // this is a new image rather than reusing its cached copy.
    img.src = `api/story/image?job_id=${jobId}&page=${pageNumber}&v=${Date.now()}`;
    // Keep the in-memory story object in sync so switching languages (which
    // re-renders this page's card from _bookState.story) doesn't revert the
    // textarea back to the pre-edit prompt.
    const page = (_bookState.story.pages || []).find(pg => pg.page_number === pageNumber);
    if (page && promptBox && promptBox.value.trim()) page.image_prompt = promptBox.value.trim();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

function selectLanguage(lang) {
  if (!_bookState) return;
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  document.getElementById("pages-container").innerHTML =
    buildPagesHtml(_bookState.story, _bookState.jobId, lang, _bookState.isOwner);
}

function _updatePublishButton() {
  const btn = document.getElementById("publish-btn");
  if (btn && _bookState) {
    if (_bookState.published) {
      btn.textContent = "🔒 Unpublish";
      btn.classList.add("published");
    } else {
      btn.textContent = "📢 Publish story";
      btn.classList.remove("published");
    }
  }

  // Share only makes sense once published - keep it in sync with the
  // publish toggle above without requiring a page reload.
  const shareSlot = document.getElementById("share-btn-slot");
  if (shareSlot && _bookState) {
    shareSlot.innerHTML = _bookState.published
      ? `<a class="btn-outline" href="share.html?job_id=${_bookState.jobId}" target="_blank" rel="noopener">🔗 Share</a>`
      : "";
  }
}

function _setPdfStatus(text, variant) {
  const el = document.getElementById("pdf-status");
  if (!el) return;
  el.textContent = text;
  el.className = variant ? `banner show ${variant}` : "banner";
}

// "High quality" embeds every illustration at a true 300 DPI (print-ready,
// larger/slower); "low quality" resizes them to a ~96 DPI screen target
// instead (smaller/faster, meant for on-screen reading only) - see
// book_export.py's `quality` param. Whichever radio is checked applies to
// every "Download ... PDF" button, not just the one clicked.
function _selectedPdfQuality() {
  const checked = document.querySelector('input[name="pdf-quality"]:checked');
  return checked ? checked.value : "high";
}

// Fetches the PDF via JS (rather than a plain window.open(url)) so a status
// bar can track the request's lifecycle - "Generating a story's images are
// high-resolution (300 DPI) since a request was made for print-quality
// downloads, so this can take a few seconds, whereas a direct window.open
// gives no feedback at all while that's happening and can look like a dead
// click. Buffers the whole response as a blob before triggering the actual
// file download, since that's what makes "done" knowable in the first place.
async function downloadPdf(btn, language, label) {
  if (!_bookState) return;
  const { jobId } = _bookState;
  const quality = _selectedPdfQuality();
  const langKey = _pdfAvailabilityKey(language);
  btn.disabled = true;
  btn.textContent = "Generating...";
  _setPdfStatus(
    quality === "low"
      ? `⏳ Generating the ${label} PDF (low quality, smaller file)...`
      : `⏳ Generating the ${label} PDF... this can take a moment for print-quality (300 DPI) art.`,
    "info",
  );

  try {
    const res = await fetch(`api/story/download?job_id=${jobId}&format=pdf&language=${encodeURIComponent(language)}&quality=${quality}`);
    if (!res.ok) {
      let message = `Failed to generate PDF (HTTP ${res.status})`;
      try { message = (await res.json()).error || message; } catch (e) { /* not JSON - use default */ }
      throw new Error(message);
    }
    const blob = await res.blob();

    // Prefer the server's own filename (Content-Disposition) over a guess,
    // matching what a plain browser-native download would have shown.
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `${label.replace(/\s+/g, "_")}.pdf`;

    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 4000);

    _setPdfStatus(`✅ ${label} PDF ready - download started.`, "success");
    setTimeout(() => _setPdfStatus("", null), 4000);

    // This exact (language, quality) is now cached on disk by get_pdf(), so
    // every button showing it - not just this one - should read "Download"
    // from here on, without needing a page reload.
    if (!_bookState.pdfAvailable) _bookState.pdfAvailable = {};
    if (!_bookState.pdfAvailable[langKey]) _bookState.pdfAvailable[langKey] = {};
    _bookState.pdfAvailable[langKey][quality] = true;
  } catch (err) {
    _setPdfStatus(`❌ ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    _updateAllPdfButtonLabels();
  }
}

async function togglePublish() {
  if (!_bookState) return;
  const btn = document.getElementById("publish-btn");
  if (!btn) return;
  const nextPublished = !_bookState.published;
  btn.disabled = true;
  try {
    const res = await fetch("api/story/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: _bookState.jobId, published: nextPublished }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update publish state");
    _bookState.published = nextPublished;
    _updatePublishButton();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
}

// container: the DOM element to render into.
// story: the full story object (title, region, moral, characters, pages, languages).
// opts: { isOwner, published, author }
function renderBook(container, jobId, story, opts = {}) {
  const isOwner = !!opts.isOwner;
  const charsHtml = (story.characters || []).map(c => (
    `<div><strong>${escapeHtml(c.name)}</strong> (${escapeHtml(c.type)}) - ${escapeHtml(c.appearance)}. ${escapeHtml(c.personality)}</div>`
  )).join("");

  const languages = story.languages || [];
  const allLanguages = ["English", ...languages];

  _bookState = { jobId, story, isOwner, published: !!opts.published, pdfAvailable: opts.pdfAvailable || {} };

  const langButtonsHtml = allLanguages.map(lang => (
    `<button type="button" class="lang-btn${lang === "English" ? " active" : ""}" data-lang="${escapeHtml(lang)}" onclick="selectLanguage('${lang.replace(/'/g, "\\'")}')">${escapeHtml(lang)}</button>`
  )).join("");

  // One PDF download button for English, plus one more per translated
  // language - each is its own standalone storybook (not English+translation
  // mixed on the same page), per-book so a family can print/share just the
  // language they want. Initial label reflects whichever quality tier is
  // checked by default ("high") via _bookState.pdfAvailable, set just above;
  // _updateAllPdfButtonLabels() re-derives it from data-pdf-lang/-label
  // whenever the quality selector changes or a download completes.
  const initialQuality = "high";
  const pdfButtonSpec = [{ key: "en", arg: "en", label: "English" },
    ...languages.map(lang => ({ key: lang, arg: lang, label: lang }))];
  const pdfButtons = pdfButtonSpec.map(({ key, arg, label }) => {
    const avail = _bookState.pdfAvailable[key];
    const text = _pdfButtonLabel(!!(avail && avail[initialQuality]), label);
    return `<button class="btn-secondary" data-pdf-lang="${escapeHtml(key)}" data-pdf-label="${escapeHtml(label)}" onclick="downloadPdf(this, '${arg.replace(/'/g, "\\'")}', '${label.replace(/'/g, "\\'")}')">${escapeHtml(text)}</button>`;
  }).join("");

  // Sharing only makes sense once a story is published - an unpublished
  // one isn't visible to anyone a share link would be sent to (see
  // Handler._can_view in server.py), so the button is hidden rather than
  // shown-but-broken.
  const shareButtonHtml = opts.published
    ? `<a class="btn-outline" href="share.html?job_id=${jobId}" target="_blank" rel="noopener">🔗 Share</a>`
    : "";

  container.innerHTML = `
    <div class="card">
      <div class="book-header">
        <h2>${escapeHtml(story.title)}</h2>
        <div>Region: ${escapeHtml(story.region)}</div>
        ${opts.author ? `<div class="author-line">by ${escapeHtml(opts.author)}</div>` : ""}
        <div class="moral">"${escapeHtml(story.moral)}"</div>
      </div>
      <div class="pdf-quality-row">
        <span class="pdf-quality-label">PDF quality:</span>
        <label><input type="radio" name="pdf-quality" value="high" checked onchange="_updateAllPdfButtonLabels()"> High (print, 300 DPI)</label>
        <label><input type="radio" name="pdf-quality" value="low" onchange="_updateAllPdfButtonLabels()"> Low (screen, smaller file)</label>
      </div>
      <div class="download-row">
        ${pdfButtons}
        <span id="share-btn-slot">${shareButtonHtml}</span>
      </div>
      <div id="pdf-status" class="banner"></div>
      ${isOwner ? `<div class="publish-row"><button type="button" class="btn-outline" id="publish-btn" onclick="togglePublish()"></button></div>` : ""}
      <div class="chars-box">
        <h4>Characters (locked, identical wording used on every page)</h4>
        ${charsHtml}
      </div>
      <div class="lang-switch">${langButtonsHtml}</div>
      <div id="pages-container">${buildPagesHtml(story, jobId, "English", isOwner)}</div>
    </div>
  `;

  if (isOwner) _updatePublishButton();
}
