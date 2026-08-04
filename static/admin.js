// Admin panel: users, stories, coupons, reports. Every /api/admin/* call is
// re-checked server-side (see server.py's _require_admin) - the not-admin
// message shown here is just so a non-admin isn't left staring at empty
// tables, not the actual enforcement.
const ADMIN_PAGE_SIZE = 6;

let currentAdminId = null;
let usersData = [];
let usersPage = 1;
let storiesData = [];
let storiesPage = 1;
let couponsData = [];
let couponsPage = 1;
let footerLinksData = [];
let footerLinksPage = 1;

function _adminEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _adminFmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
}

function _adminShowBanner(text, variant) {
  const banner = document.getElementById("admin-banner");
  banner.textContent = text;
  banner.className = `banner show ${variant}`;
  setTimeout(() => { banner.className = "banner"; }, 4000);
}

function _adminPaginationHtml(items, page, changeFn) {
  const totalPages = Math.max(1, Math.ceil(items.length / ADMIN_PAGE_SIZE));
  if (items.length <= ADMIN_PAGE_SIZE) return "";
  return `
    <div class="pagination-row">
      <button type="button" class="btn-outline btn-page" onclick="${changeFn}(-1)" ${page <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="pagination-status">Page ${page} of ${totalPages}</span>
      <button type="button" class="btn-outline btn-page" onclick="${changeFn}(1)" ${page >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>`;
}

function _adminPageItems(items, page) {
  const totalPages = Math.max(1, Math.ceil(items.length / ADMIN_PAGE_SIZE));
  const clampedPage = Math.min(page, totalPages);
  const start = (clampedPage - 1) * ADMIN_PAGE_SIZE;
  return items.slice(start, start + ADMIN_PAGE_SIZE);
}

// --------------------------------------------------------------- users

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
  const totalPages = Math.max(1, Math.ceil(usersData.length / ADMIN_PAGE_SIZE));
  if (usersPage > totalPages) usersPage = totalPages;
  const pageItems = _adminPageItems(usersData, usersPage);

  const rowsHtml = pageItems.map(u => `
    <tr>
      <td>${_adminEscapeHtml(u.username)}</td>
      <td>${_adminEscapeHtml(u.role)}</td>
      <td><span class="status-badge ${u.status === "active" ? "published" : "unpublished"}">${_adminEscapeHtml(u.status)}</span></td>
      <td>${u.image_credits}</td>
      <td class="usage-date">${_adminEscapeHtml(_adminFmtDate(u.created_at))}</td>
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
    ${_adminPaginationHtml(usersData, usersPage, "changeUsersPage")}
  `;
}

function changeUsersPage(delta) {
  usersPage += delta;
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
    _adminShowBanner("User suspended.", "success");
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
    _adminShowBanner("User activated.", "success");
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
    _adminShowBanner("User deleted.", "success");
    loadUsers();
    loadStories();
  } catch (e) {
    alert(e.message);
  }
}

// -------------------------------------------------------------- stories

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
  const totalPages = Math.max(1, Math.ceil(storiesData.length / ADMIN_PAGE_SIZE));
  if (storiesPage > totalPages) storiesPage = totalPages;
  const pageItems = _adminPageItems(storiesData, storiesPage);

  const rowsHtml = pageItems.map(s => `
    <tr>
      <td>${s.ready ? `<a href="story.html?job_id=${s.job_id}">${_adminEscapeHtml(s.title)}</a>` : `<em>generating...</em>`}</td>
      <td>${_adminEscapeHtml(s.owner_username || "(deleted user)")}</td>
      <td><span class="status-badge ${s.published ? "published" : "unpublished"}">${s.published ? "published" : "unpublished"}</span></td>
      <td class="usage-date">${_adminEscapeHtml(_adminFmtDate(s.created_at))}</td>
      <td>
        <button type="button" class="btn-outline btn-admin-row" onclick="toggleStoryPublish('${s.job_id}', ${!s.published})">${s.published ? "Unpublish" : "Publish"}</button>
        <button type="button" class="btn-outline btn-admin-row" onclick="deleteStory('${s.job_id}')">Delete</button>
      </td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Title</th><th>Owner</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${_adminPaginationHtml(storiesData, storiesPage, "changeStoriesPage")}
  `;
}

function changeStoriesPage(delta) {
  storiesPage += delta;
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
    _adminShowBanner("Story deleted.", "success");
    loadStories();
  } catch (e) {
    alert(e.message);
  }
}

// -------------------------------------------------------------- coupons

async function loadCoupons() {
  try {
    const res = await fetch("api/admin/coupons");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load coupons");
    couponsData = data.coupons || [];
    couponsPage = 1;
    renderCouponsPage();
  } catch (e) {
    console.error(e);
  }
}

function renderCouponsPage() {
  const wrap = document.getElementById("coupons-table-wrap");
  if (!couponsData.length) {
    wrap.innerHTML = `<p class="hint">No coupons yet.</p>`;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(couponsData.length / ADMIN_PAGE_SIZE));
  if (couponsPage > totalPages) couponsPage = totalPages;
  const pageItems = _adminPageItems(couponsData, couponsPage);

  const rowsHtml = pageItems.map(c => `
    <tr>
      <td>${_adminEscapeHtml(c.code)}</td>
      <td>${c.credits}</td>
      <td><span class="status-badge ${c.active ? "published" : "unpublished"}">${c.active ? "active" : "inactive"}</span></td>
      <td>
        <button type="button" class="btn-outline btn-admin-row" onclick="toggleCoupon('${_adminEscapeHtml(c.code)}', ${!c.active})">${c.active ? "Deactivate" : "Activate"}</button>
      </td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Code</th><th>Credits</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${_adminPaginationHtml(couponsData, couponsPage, "changeCouponsPage")}
  `;
}

function changeCouponsPage(delta) {
  couponsPage += delta;
  renderCouponsPage();
}

async function toggleCoupon(code, active) {
  try {
    const res = await fetch("api/admin/coupons/toggle", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, active }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update coupon");
    loadCoupons();
  } catch (e) {
    alert(e.message);
  }
}

// --------------------------------------------------------- footer links

async function loadFooterLinks() {
  try {
    const res = await fetch("api/admin/footer_links");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load footer links");
    footerLinksData = data.footer_links || [];
    footerLinksPage = 1;
    renderFooterLinksPage();
  } catch (e) {
    console.error(e);
  }
}

function renderFooterLinksPage() {
  const wrap = document.getElementById("footer-links-table-wrap");
  if (!footerLinksData.length) {
    wrap.innerHTML = `<p class="hint">No footer links yet.</p>`;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(footerLinksData.length / ADMIN_PAGE_SIZE));
  if (footerLinksPage > totalPages) footerLinksPage = totalPages;
  const pageItems = _adminPageItems(footerLinksData, footerLinksPage);

  const rowsHtml = pageItems.map(l => `
    <tr>
      <td>${_adminEscapeHtml(l.name)}</td>
      <td>${_adminEscapeHtml(l.url)}</td>
      <td>${l.new_tab ? "New tab" : "Same page"}</td>
      <td>
        <button type="button" class="btn-outline btn-admin-row" onclick="deleteFooterLink(${l.id})">Delete</button>
      </td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Page name</th><th>Page URL</th><th>Opens in</th><th>Actions</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${_adminPaginationHtml(footerLinksData, footerLinksPage, "changeFooterLinksPage")}
  `;
}

function changeFooterLinksPage(delta) {
  footerLinksPage += delta;
  renderFooterLinksPage();
}

async function deleteFooterLink(id) {
  if (!confirm("Remove this footer link?")) return;
  try {
    const res = await fetch("api/admin/footer_links/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to remove footer link");
    loadFooterLinks();
  } catch (e) {
    alert(e.message);
  }
}

// -------------------------------------------------------------- settings

async function loadSettings() {
  try {
    // /api/config already carries site_name/footer_text (public - every
    // page reads it to brand itself), so no separate admin-only GET is
    // needed just to prefill this form.
    const res = await fetch("api/config");
    const data = await res.json();
    document.getElementById("settings-site-name").value = data.site_name || "";
    document.getElementById("settings-footer-text").value = data.footer_text || "";
    document.getElementById("settings-contact-email").value = data.contact_email || "";
    document.getElementById("settings-contact-phone").value = data.contact_phone || "";
    document.getElementById("settings-page-count").value = data.page_count || 5;
    // 0 is a valid, meaningful value here (no free credits) - only fall
    // back to 50 when the field is genuinely missing/undefined, not when
    // it's the number 0 (which `|| 50` would incorrectly override).
    document.getElementById("settings-signup-credits").value =
      data.signup_credits !== undefined ? data.signup_credits : 50;
  } catch (e) {
    console.error(e);
  }
}

// -------------------------------------------------------------- reports

async function loadReports() {
  try {
    const res = await fetch("api/admin/reports/summary");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load reports");

    const p = data.purchases || {};
    document.getElementById("reports-summary").innerHTML = `
      <div class="reports-grid">
        <div class="reports-stat"><div class="reports-stat-value">${p.count || 0}</div><div class="reports-stat-label">Purchases</div></div>
        <div class="reports-stat"><div class="reports-stat-value">$${Number(p.total_revenue_usd || 0).toFixed(2)}</div><div class="reports-stat-label">Total revenue</div></div>
        <div class="reports-stat"><div class="reports-stat-value">${p.total_credits_sold || 0}</div><div class="reports-stat-label">Credits sold</div></div>
        <div class="reports-stat"><div class="reports-stat-value">${data.images_total || 0}</div><div class="reports-stat-label">Images generated (all time)</div></div>
      </div>
    `;

    const usage = data.coupon_usage || [];
    const couponsWrap = document.getElementById("reports-coupons-wrap");
    if (!usage.length) {
      couponsWrap.innerHTML = `<p class="hint">No coupon redemptions yet.</p>`;
    } else {
      const rowsHtml = usage.map(u => `
        <tr>
          <td>${_adminEscapeHtml(u.code)}</td>
          <td>${u.count}</td>
          <td>${u.credits_granted}</td>
        </tr>`
      ).join("");
      couponsWrap.innerHTML = `
        <table class="usage-table">
          <thead><tr><th>Coupon code</th><th>Times redeemed</th><th>Credits granted</th></tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      `;
    }

    // "Month" here is a plain "YYYY-MM" string (see server.py) - already in
    // chronological sort order as a string, so no date parsing is needed to
    // display it oldest-first.
    const monthly = data.images_by_month || [];
    const monthlyWrap = document.getElementById("reports-images-by-month-wrap");
    if (!monthly.length) {
      monthlyWrap.innerHTML = `<p class="hint">No images generated yet.</p>`;
    } else {
      const monthRowsHtml = monthly.map(m => `
        <tr>
          <td>${_adminEscapeHtml(m.month)}</td>
          <td>${m.count}</td>
        </tr>`
      ).join("");
      monthlyWrap.innerHTML = `
        <table class="usage-table">
          <thead><tr><th>Month</th><th>Images generated</th></tr></thead>
          <tbody>${monthRowsHtml}</tbody>
          <tfoot><tr><td><strong>Total</strong></td><td><strong>${data.images_total || 0}</strong></td></tr></tfoot>
        </table>
      `;
    }
  } catch (e) {
    console.error(e);
  }
}

// ----------------------------------------------------------------- init

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
    _adminShowBanner(`User "${username}" created.`, "success");
    loadUsers();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("create-coupon-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const code = document.getElementById("new-coupon-code").value.trim();
  const credits = parseInt(document.getElementById("new-coupon-credits").value, 10);
  try {
    const res = await fetch("api/admin/coupons/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, credits }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to create coupon");
    document.getElementById("create-coupon-form").reset();
    _adminShowBanner(`Coupon "${code}" created.`, "success");
    loadCoupons();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("create-footer-link-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = document.getElementById("new-footer-link-name").value.trim();
  const url = document.getElementById("new-footer-link-url").value.trim();
  const new_tab = document.getElementById("new-footer-link-new-tab").checked;
  try {
    const res = await fetch("api/admin/footer_links/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url, new_tab }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to add footer link");
    document.getElementById("create-footer-link-form").reset();
    _adminShowBanner(`Footer link "${name}" added.`, "success");
    loadFooterLinks();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const site_name = document.getElementById("settings-site-name").value.trim();
  const footer_text = document.getElementById("settings-footer-text").value.trim();
  const contact_email = document.getElementById("settings-contact-email").value.trim();
  const contact_phone = document.getElementById("settings-contact-phone").value.trim();
  const page_count = parseInt(document.getElementById("settings-page-count").value, 10);
  const signup_credits = parseInt(document.getElementById("settings-signup-credits").value, 10);
  try {
    const res = await fetch("api/admin/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_name, footer_text, contact_email, contact_phone, page_count, signup_credits }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to save settings");
    _adminShowBanner("Settings saved - reload any open page to see the new branding.", "success");
    await renderNav(); // picks up the new name/footer on this page immediately too
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("banner-upload-btn").addEventListener("click", () => {
  const input = document.getElementById("banner-upload-input");
  const file = input.files[0];
  if (!file) {
    alert("Choose an image file first.");
    return;
  }
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const res = await fetch("api/admin/settings/banner", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_base64: reader.result }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to upload image");
      // Same filename every time (server always overwrites kertoons_bar.jpg),
      // so a cache-busting query param is the only way to see the new file
      // without a hard refresh.
      document.getElementById("banner-preview").src = `static/kertoons_bar.jpg?v=${Date.now()}`;
      input.value = "";
      _adminShowBanner("Main banner image updated.", "success");
    } catch (e) {
      alert(e.message);
    }
  };
  reader.readAsDataURL(file);
});

(async () => {
  const user = await renderNav();
  if (!user || user.role !== "admin") {
    document.getElementById("not-admin").style.display = "block";
    return;
  }
  currentAdminId = user.id;
  document.getElementById("admin-panel").style.display = "block";
  loadSettings();
  loadUsers();
  loadStories();
  loadCoupons();
  loadFooterLinks();
  loadReports();
})();
