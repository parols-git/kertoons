// Dedicated "Manage Users" admin page - split out of admin.js/admin.html
// so the users table gets its own page with real numbered pagination
// instead of sharing space with every other admin section. Every
// /api/admin/* call is re-checked server-side (see server.py's
// _require_admin) - the not-admin message shown here is just so a
// non-admin isn't left staring at an empty table, not the actual
// enforcement.
const USERS_PAGE_SIZE = 10;

let currentAdminId = null;
let usersData = [];
let usersPage = 1;

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
// (not a delta). Admin-scale data (a handful of users) never gets large
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

async function loadUsers() {
  try {
    const res = await fetch("api/admin/users");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load users");
    usersData = data.users || [];
    usersPage = 1;
    renderUsersPage();
  } catch (e) {
    console.error(e);
  }
}

function renderUsersPage() {
  const wrap = document.getElementById("users-table-wrap");
  if (!usersData.length) {
    wrap.innerHTML = `<p class="hint">No users yet.</p>`;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(usersData.length / USERS_PAGE_SIZE));
  if (usersPage > totalPages) usersPage = totalPages;
  const pageItems = _pageItems(usersData, usersPage, USERS_PAGE_SIZE);

  const rowsHtml = pageItems.map(u => `
    <tr>
      <td>${_escapeHtml(u.username)}</td>
      <td>${_escapeHtml(u.role)}</td>
      <td><span class="status-badge ${u.status === "active" ? "published" : "unpublished"}">${_escapeHtml(u.status)}</span></td>
      <td>${u.image_credits}</td>
      <td class="usage-date">${_escapeHtml(_fmtDate(u.created_at))}</td>
      <td>
        ${u.id === currentAdminId ? `<span class="hint">(you)</span>` : `
        ${u.status === "active"
          ? `<button type="button" class="btn-outline btn-admin-row" onclick="suspendUser(${u.id})">Suspend</button>`
          : `<button type="button" class="btn-outline btn-admin-row" onclick="activateUser(${u.id})">Activate</button>`}
        <button type="button" class="btn-outline btn-admin-row" onclick="deleteUser(${u.id})">Delete</button>
        `}
      </td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Username</th><th>Role</th><th>Status</th><th>Credits</th><th>Created</th><th>Actions</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${_numberedPaginationHtml(usersData.length, usersPage, USERS_PAGE_SIZE, "goToUsersPage")}
  `;
}

function goToUsersPage(page) {
  usersPage = page;
  renderUsersPage();
}

async function suspendUser(userId) {
  try {
    const res = await fetch("api/admin/users/suspend", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to suspend user");
    _showBanner("User suspended.", "success");
    loadUsers();
  } catch (e) {
    alert(e.message);
  }
}

async function activateUser(userId) {
  try {
    const res = await fetch("api/admin/users/activate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to activate user");
    _showBanner("User activated.", "success");
    loadUsers();
  } catch (e) {
    alert(e.message);
  }
}

async function deleteUser(userId) {
  if (!confirm("Delete this user account? Their stories will remain but become permanently hidden.")) return;
  try {
    const res = await fetch("api/admin/users/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to delete user");
    _showBanner("User deleted.", "success");
    loadUsers();
  } catch (e) {
    alert(e.message);
  }
}

document.getElementById("create-user-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const username = document.getElementById("new-user-username").value.trim();
  const password = document.getElementById("new-user-password").value;
  try {
    const res = await fetch("api/admin/users/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to create user");
    document.getElementById("create-user-form").reset();
    _showBanner(`User "${username}" created.`, "success");
    loadUsers();
  } catch (e) {
    alert(e.message);
  }
});

// "superadmin" is a strict superset of "admin" (see server.py's
// _is_admin_role) - a superadmin can manage users here too.
function _isAdminRole(role) {
  return role === "admin" || role === "superadmin";
}

(async () => {
  const user = await renderNav();
  if (!user || !_isAdminRole(user.role)) {
    document.getElementById("not-admin").style.display = "block";
    return;
  }
  currentAdminId = user.id;
  document.getElementById("admin-panel").style.display = "block";
  loadUsers();
})();
