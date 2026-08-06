let galleryData = [];
let galleryPage = 1;
// Same page size as the "My Stories" / "Image usage" / "Payment history"
// lists (see create.js/usage.js), for consistency across the app.
const GALLERY_PAGE_SIZE = 6;

function _galleryEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function loadGallery() {
  const empty = document.getElementById("gallery-empty");
  try {
    const res = await fetch("api/stories/gallery");
    const data = await res.json();
    galleryData = data.stories || [];
    if (!galleryData.length) {
      empty.style.display = "block";
      document.getElementById("gallery-grid").innerHTML = "";
      document.getElementById("gallery-pagination").innerHTML = "";
      return;
    }
    empty.style.display = "none";
    galleryPage = 1;
    renderGalleryPage();
  } catch (e) {
    console.error(e);
  }
}

function renderGalleryPage() {
  const grid = document.getElementById("gallery-grid");
  const pagination = document.getElementById("gallery-pagination");

  const totalPages = Math.max(1, Math.ceil(galleryData.length / GALLERY_PAGE_SIZE));
  if (galleryPage > totalPages) galleryPage = totalPages;
  const start = (galleryPage - 1) * GALLERY_PAGE_SIZE;
  const pageItems = galleryData.slice(start, start + GALLERY_PAGE_SIZE);

  grid.innerHTML = pageItems.map(s => `
    <div class="gallery-card">
      <a class="gallery-card-link" href="story.html?job_id=${s.job_id}">
        <img src="api/story/image?job_id=${s.job_id}&page=1" alt="${_galleryEscapeHtml(s.title)} cover">
        <div class="gallery-card-body">
          <h3>${_galleryEscapeHtml(s.title)}</h3>
          <div class="gallery-card-meta">by ${_galleryEscapeHtml(s.author)}${s.region ? " · " + _galleryEscapeHtml(s.region) : ""} · 👁 ${s.view_count || 0}</div>
        </div>
      </a>
      <div class="gallery-card-actions">
        <a class="gallery-card-share" href="share.html?job_id=${s.job_id}" target="_blank" rel="noopener">🔗 Share</a>
      </div>
    </div>
  `).join("");

  pagination.innerHTML = galleryData.length > GALLERY_PAGE_SIZE ? `
    <div class="pagination-row">
      <button type="button" class="btn-outline btn-page" onclick="changeGalleryPage(-1)" ${galleryPage <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="pagination-status">Page ${galleryPage} of ${totalPages}</span>
      <button type="button" class="btn-outline btn-page" onclick="changeGalleryPage(1)" ${galleryPage >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>` : "";
}

function changeGalleryPage(delta) {
  galleryPage += delta;
  renderGalleryPage();
}

function _galleryShowCheckoutBanner(text, variant) {
  const banner = document.getElementById("checkout-banner");
  if (!banner) return;
  banner.textContent = text;
  banner.className = `banner show ${variant}`;
}

// The Stripe Payment Link's configured "after payment" redirect lands here
// (kertoons.com/stories -> this page, aliased in server.py), with the
// completed checkout session id in a "cid" query param (that param name is
// whatever was chosen when configuring the Payment Link in the Stripe
// dashboard - /api/credits/confirm itself doesn't care what it was called
// in the URL, only that a session id gets POSTed to it). Confirms
// immediately for instant feedback; Stripe's webhook independently confirms
// the same payment asynchronously as the durable fallback - both funnel
// through the same idempotent grant, so this is never double-credited.
async function handleCheckoutReturn() {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("cid");
  if (!sessionId) return;

  try {
    const res = await fetch("api/credits/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    if (res.ok && data.pending) {
      _galleryShowCheckoutBanner("Payment received - your credits will appear shortly.", "success");
    } else if (res.ok) {
      _galleryShowCheckoutBanner(`Payment received - ${data.credits} credits available now.`, "success");
    } else {
      _galleryShowCheckoutBanner(`Could not confirm payment: ${data.error || "unknown error"}`, "cancelled");
    }
  } catch (e) {
    console.error(e);
  }

  window.history.replaceState({}, "", window.location.pathname);
}

(async () => {
  await handleCheckoutReturn();
  await renderNav(); // after a successful purchase, refresh the credit count shown in the nav too
  loadGallery();
})();
