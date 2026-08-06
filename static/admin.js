// Admin panel: settings, coupons, footer links, reports. Users and Stories
// each moved to their own dedicated page (admin-users.html/admin-users.js,
// admin-stories.html/admin-stories.js) with real numbered pagination - see
// the "Manage" section below linking to both. Every /api/admin/* call is
// re-checked server-side (see server.py's _require_admin) - the not-admin
// message shown here is just so a non-admin isn't left staring at empty
// tables, not the actual enforcement.
const ADMIN_PAGE_SIZE = 6;

let couponsData = [];
let couponsPage = 1;
let footerLinksData = [];
let footerLinksPage = 1;
let reportsImagesByMonth = [];

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
        <div class="reports-stat"><div class="reports-stat-value">$${Number(data.total_cost || 0).toFixed(2)}</div><div class="reports-stat-label">Total cost (all time)</div></div>
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
    // display it oldest-first. The year/month filters below operate on this
    // same list client-side (the admin-scale data here is tiny - no need
    // for a server round-trip per filter change).
    reportsImagesByMonth = data.images_by_month || [];
    _populateReportsYearFilter();
    renderImagesByMonthTable();
  } catch (e) {
    console.error(e);
  }
}

function _populateReportsYearFilter() {
  const select = document.getElementById("reports-year-filter");
  const years = [...new Set(reportsImagesByMonth.map(m => m.month.slice(0, 4)))].sort();
  const previousChoice = select.value;
  select.innerHTML = `<option value="">All years</option>` +
    years.map(y => `<option value="${y}">${y}</option>`).join("");
  if (years.includes(previousChoice)) select.value = previousChoice;
}

function renderImagesByMonthTable() {
  const wrap = document.getElementById("reports-images-by-month-wrap");
  if (!reportsImagesByMonth.length) {
    wrap.innerHTML = `<p class="hint">No images generated yet.</p>`;
    return;
  }

  const yearFilter = document.getElementById("reports-year-filter").value;
  const monthFilter = document.getElementById("reports-month-filter").value;
  const filtered = reportsImagesByMonth.filter(m => {
    const [year, month] = m.month.split("-");
    if (yearFilter && year !== yearFilter) return false;
    if (monthFilter && month !== monthFilter) return false;
    return true;
  });

  if (!filtered.length) {
    wrap.innerHTML = `<p class="hint">No images generated in the selected period.</p>`;
    return;
  }

  const isFiltered = !!(yearFilter || monthFilter);
  const filteredTotal = filtered.reduce((sum, m) => sum + m.count, 0);
  // Per-month image cost only (count x cost-per-image) - the flat server
  // fee is a one-time/all-time cost, not something that belongs to any
  // single month or filtered slice, so it's shown separately in the
  // "Total cost (all time)" stat above instead of being folded in here.
  const filteredImageCost = filtered.reduce((sum, m) => sum + (m.image_cost || 0), 0);
  const rowsHtml = filtered.map(m => `
    <tr>
      <td>${_adminEscapeHtml(m.month)}</td>
      <td>${m.count}</td>
      <td>$${Number(m.image_cost || 0).toFixed(2)}</td>
    </tr>`
  ).join("");
  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Month</th><th>Images generated</th><th>Image cost</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
      <tfoot><tr>
        <td><strong>${isFiltered ? "Total (filtered)" : "Total"}</strong></td>
        <td><strong>${filteredTotal}</strong></td>
        <td><strong>$${filteredImageCost.toFixed(2)}</strong></td>
      </tr></tfoot>
    </table>
  `;
}

// ----------------------------------------------------------------- init

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

document.getElementById("logo-upload-btn").addEventListener("click", () => {
  const input = document.getElementById("logo-upload-input");
  const file = input.files[0];
  if (!file) {
    alert("Choose an image file first.");
    return;
  }
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const res = await fetch("api/admin/settings/logo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_base64: reader.result }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to upload image");
      // Same filename every time (server always overwrites static/logo.png),
      // so a cache-busting query param is the only way to see the new file
      // without a hard refresh.
      document.getElementById("logo-preview").src = `static/logo.png?v=${Date.now()}`;
      input.value = "";
      _adminShowBanner("Site logo updated.", "success");
    } catch (e) {
      alert(e.message);
    }
  };
  reader.readAsDataURL(file);
});

document.getElementById("reports-year-filter").addEventListener("change", renderImagesByMonthTable);
document.getElementById("reports-month-filter").addEventListener("change", renderImagesByMonthTable);

// "superadmin" is a strict superset of "admin" (see server.py's
// _is_admin_role) - a superadmin sees this panel too, plus the separate
// cost-settings dashboard at /superadmin.html that regular admins can't
// reach.
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
  loadSettings();
  loadCoupons();
  loadFooterLinks();
  loadReports();
})();
