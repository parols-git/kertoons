// Dedicated "Manage Stories" admin page - split out of admin.js/admin.html
// so the stories table gets its own page with real numbered pagination
// instead of sharing space with every other admin section. Every
// /api/admin/* call is re-checked server-side (see server.py's
// _require_admin) - the not-admin message shown here is just so a
// non-admin isn't left staring at an empty table, not the actual
// enforcement.
const STORIES_PAGE_SIZE = 10;

let storiesData = [];
let storiesPage = 1;

function _escapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
}

function _showBanner(text, variant) {
  const banner = document.getElementById("admin-banner");
  banner.textContent = text;
  banner.className = `banner show ${variant}`;
  setTimeout(() => { banner.className = "banner"; }, 4000);
}

// Full clickable page-NUMBER row (1 2 3 ...), not just Prev/Next - `page`
// is 1-indexed, `changeFn` is called with the target page number directly
// (not a delta). Admin-scale data (dozens of stories) never gets large
// enough to need ellipsis truncation, so every page number is rendered.
function _numberedPaginationHtml(totalItems, page, pageSize, changeFn) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  if (totalItems <= pageSize) return "";
  const numbersHtml = Array.from({ length: totalPages }, (_, i) => i + 1).map(p => `
    <button type="button" class="btn-outline btn-page${p === page ? " active" : ""}" onclick="${changeFn}(${p})">${p}</button>
  `).join("");
  return `
    <div class="pagination-row-numbered">
      <button type="button" class="btn-outline btn-page" onclick="${changeFn}(${page - 1})" ${page <= 1 ? "disabled" : ""}>‹ Prev</button>
      ${numbersHtml}
      <button type="button" class="btn-outline btn-page" onclick="${changeFn}(${page + 1})" ${page >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>`;
}

function _pageItems(items, page, pageSize) {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const clampedPage = Math.min(Math.max(page, 1), totalPages);
  const start = (clampedPage - 1) * pageSize;
  return items.slice(start, start + pageSize);
}

async function loadStories() {
  try {
    const res = await fetch("api/admin/stories");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load stories");
    storiesData = data.stories || [];
    storiesPage = 1;
    renderStoriesPage();
  } catch (e) {
    console.error(e);
  }
}

function renderStoriesPage() {
  const wrap = document.getElementById("stories-table-wrap");
  if (!storiesData.length) {
    wrap.innerHTML = `<p class="hint">No stories yet.</p>`;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(storiesData.length / STORIES_PAGE_SIZE));
  if (storiesPage > totalPages) storiesPage = totalPages;
  const pageItems = _pageItems(storiesData, storiesPage, STORIES_PAGE_SIZE);

  const rowsHtml = pageItems.map(s => `
    <tr>
      <td>
        <div>${s.ready ? `<a href="story.html?job_id=${s.job_id}">${_escapeHtml(s.title)}</a>` : `<em>generating...</em>`}</div>
        <div class="admin-row-actions">
          <button type="button" class="btn-outline btn-admin-row" onclick="toggleStoryPublish('${s.job_id}', ${!s.published})">${s.published ? "Unpublish" : "Publish"}</button>
          <button type="button" class="btn-outline btn-admin-row" onclick="deleteStory('${s.job_id}')">Delete</button>
        </div>
      </td>
      <td>${_escapeHtml(s.owner_username || "(deleted user)")}</td>
      <td><span class="status-badge ${s.published ? "published" : "unpublished"}">${s.published ? "published" : "unpublished"}</span></td>
      <td>${s.view_count || 0}</td>
      <td class="usage-date">${_escapeHtml(_fmtDate(s.created_at))}</td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Title</th><th>Owner</th><th>Status</th><th>Views</th><th>Created</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${_numberedPaginationHtml(storiesData.length, storiesPage, STORIES_PAGE_SIZE, "goToStoriesPage")}
  `;
}

function goToStoriesPage(page) {
  storiesPage = page;
  renderStoriesPage();
}

async function toggleStoryPublish(jobId, published) {
  try {
    const res = await fetch("api/story/publish", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, published }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update story");
    loadStories();
  } catch (e) {
    alert(e.message);
  }
}

async function deleteStory(jobId) {
  if (!confirm("Permanently delete this story?")) return;
  try {
    const res = await fetch("api/story/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to delete story");
    _showBanner("Story deleted.", "success");
    loadStories();
  } catch (e) {
    alert(e.message);
  }
}

// "superadmin" is a strict superset of "admin" (see server.py's
// _is_admin_role) - a superadmin can manage stories here too.
function _isAdminRole(role) {
  return role === "admin" || role === "superadmin";
}

(async () => {
  const user = await renderNav();
  if (!user || !_isAdminRole(user.role)) {
    document.getElementById("not-admin").style.display = "block";
    return;
  }
  document.getElementById("admin-panel").style.display = "block";
  loadStories();
})();
