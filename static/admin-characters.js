// Admin "Manage Characters" page - the Character Library moderation queue.
// Same structure as admin-stories.js (its literal template): every
// /api/admin/* call is re-checked server-side (see server.py's
// _require_admin) - the not-admin message shown here is just so a
// non-admin isn't left staring at an empty table, not the actual
// enforcement.
const PENDING_PAGE_SIZE = 10;

let pendingData = [];
let pendingPage = 1;

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

async function loadPendingCharacters() {
  try {
    const res = await fetch("api/admin/characters/pending");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load pending characters");
    pendingData = data.characters || [];
    pendingPage = 1;
    renderPendingPage();
  } catch (e) {
    console.error(e);
  }
}

function renderPendingPage() {
  const wrap = document.getElementById("pending-characters-wrap");
  if (!pendingData.length) {
    wrap.innerHTML = `<p class="hint">No characters awaiting review.</p>`;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(pendingData.length / PENDING_PAGE_SIZE));
  if (pendingPage > totalPages) pendingPage = totalPages;
  const pageItems = _pageItems(pendingData, pendingPage, PENDING_PAGE_SIZE);

  const rowsHtml = pageItems.map(c => `
    <tr>
      <td><img src="api/characters/image?character_id=${c.id}" alt="${_escapeHtml(c.name)}" class="character-thumb"></td>
      <td>
        <div><strong>${_escapeHtml(c.name)}</strong> <span class="hint">(${_escapeHtml(c.type)})</span></div>
        <div class="hint">${_escapeHtml(c.appearance)}</div>
      </td>
      <td>${_escapeHtml(c.owner_username || "(deleted user)")}</td>
      <td>
        ${c.category ? _escapeHtml(c.category) + "<br>" : ""}
        ${c.age_group ? _escapeHtml(c.age_group) + "<br>" : ""}
        ${c.school ? _escapeHtml(c.school) : ""}
      </td>
      <td class="usage-date">${_escapeHtml(_fmtDate(c.created_at))}</td>
      <td>
        <div class="admin-row-actions">
          <button type="button" class="btn-outline btn-admin-row" onclick="moderateCharacter(${c.id}, 'approve')">Approve</button>
          <button type="button" class="btn-outline btn-admin-row" onclick="moderateCharacter(${c.id}, 'request_changes')">Request changes</button>
          <button type="button" class="btn-outline btn-admin-row" onclick="moderateCharacter(${c.id}, 'reject')">Reject</button>
        </div>
      </td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th></th><th>Character</th><th>Submitted by</th><th>Tags</th><th>Submitted</th><th></th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${_numberedPaginationHtml(pendingData.length, pendingPage, PENDING_PAGE_SIZE, "goToPendingPage")}
  `;
}

function goToPendingPage(page) {
  pendingPage = page;
  renderPendingPage();
}

async function moderateCharacter(characterId, action) {
  let note = null;
  if (action === "reject" || action === "request_changes") {
    note = prompt(
      action === "reject"
        ? "Optional note explaining why this character was rejected:"
        : "What should the creator change?"
    );
    if (note === null) return; // cancelled
  }
  try {
    const res = await fetch("api/admin/characters/moderate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId, action, note: note || "" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update character");
    _showBanner(
      action === "approve" ? "Character approved and published." : "Character sent back to its creator.",
      "success"
    );
    loadPendingCharacters();
  } catch (e) {
    alert(e.message);
  }
}

// "superadmin" is a strict superset of "admin" (see server.py's
// _is_admin_role) - a superadmin can manage characters here too.
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
  loadPendingCharacters();
})();
