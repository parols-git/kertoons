// Admin "Manage Competitions" page - same structure/helper conventions as
// admin-stories.js/admin-characters.js.
let competitionsData = [];

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

async function loadCompetitions() {
  try {
    const res = await fetch("api/admin/competitions");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load competitions");
    competitionsData = data.competitions || [];
    renderCompetitions();
  } catch (e) {
    console.error(e);
  }
}

function renderCompetitions() {
  const wrap = document.getElementById("competitions-table-wrap");
  if (!competitionsData.length) {
    wrap.innerHTML = `<p class="hint">No competitions yet.</p>`;
    return;
  }
  const rowsHtml = competitionsData.map(c => `
    <tr>
      <td>
        <strong>${_escapeHtml(c.title)}</strong>
        ${c.theme ? `<div class="hint">${_escapeHtml(c.theme)}</div>` : ""}
      </td>
      <td>${_escapeHtml(c.start_date)} &rarr; ${_escapeHtml(c.end_date)}</td>
      <td><span class="status-badge ${c.status === "active" ? "published" : "unpublished"}">${c.status}</span></td>
      <td class="usage-date">${_escapeHtml(_fmtDate(c.created_at))}</td>
      <td>
        <div class="admin-row-actions">
          <button type="button" class="btn-outline btn-admin-row" onclick="viewEntries(${c.id}, '${c.title.replace(/'/g, "\\'")}')">View entries</button>
          ${c.status === "active" ? `<button type="button" class="btn-outline btn-admin-row" onclick="finalizeCompetition(${c.id})">Close &amp; Finalize</button>` : ""}
        </div>
      </td>
    </tr>`
  ).join("");

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Competition</th><th>Dates</th><th>Status</th><th>Created</th><th></th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
}

async function viewEntries(competitionId, title) {
  try {
    const res = await fetch(`api/admin/competitions/entries?competition_id=${competitionId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load entries");
    const entries = data.entries || [];
    document.getElementById("entries-card").style.display = "block";
    document.getElementById("entries-title").textContent = `Entries: ${title}`;
    const wrap = document.getElementById("entries-table-wrap");
    if (!entries.length) {
      wrap.innerHTML = `<p class="hint">No entries yet.</p>`;
    } else {
      wrap.innerHTML = `
        <table class="usage-table">
          <thead><tr><th>Entrant</th><th>Story</th><th>Score</th><th>Rank</th><th>Winner</th><th>Submitted</th></tr></thead>
          <tbody>
            ${entries.map(e => `
              <tr>
                <td>${_escapeHtml(e.username || "(deleted user)")}</td>
                <td>${e.story_title ? `<a href="story.html?job_id=${e.job_id}">${_escapeHtml(e.story_title)}</a>` : e.job_id}</td>
                <td>${e.score_total !== null && e.score_total !== undefined ? e.score_total + " / 50" : "not scored"}</td>
                <td>${e.rank ?? "—"}</td>
                <td>${e.is_winner ? "🏆" : ""}</td>
                <td class="usage-date">${_escapeHtml(_fmtDate(e.submitted_at))}</td>
              </tr>`).join("")}
          </tbody>
        </table>`;
    }
    document.getElementById("entries-card").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    alert(e.message);
  }
}

async function finalizeCompetition(competitionId) {
  if (!confirm("Close this competition and finalize rankings and certificates? Entries can no longer be submitted after this.")) return;
  try {
    const res = await fetch("api/admin/competitions/finalize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ competition_id: competitionId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to finalize competition");
    _showBanner("Competition closed and finalized.", "success");
    loadCompetitions();
  } catch (e) {
    alert(e.message);
  }
}

function _fillThisMonth() {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const fmt = (d) => d.toISOString().slice(0, 10);
  document.getElementById("new-competition-start").value = fmt(start);
  document.getElementById("new-competition-end").value = fmt(end);
}

async function handleCreateCompetition(e) {
  e.preventDefault();
  try {
    const res = await fetch("api/admin/competitions/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("new-competition-title").value,
        theme: document.getElementById("new-competition-theme").value,
        description: document.getElementById("new-competition-description").value,
        start_date: document.getElementById("new-competition-start").value,
        end_date: document.getElementById("new-competition-end").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to create competition");
    document.getElementById("create-competition-form").reset();
    _showBanner("Competition created.", "success");
    loadCompetitions();
  } catch (e) {
    alert(e.message);
  }
}

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
  document.getElementById("fill-this-month-btn").addEventListener("click", _fillThisMonth);
  document.getElementById("create-competition-form").addEventListener("submit", handleCreateCompetition);
  loadCompetitions();
})();
