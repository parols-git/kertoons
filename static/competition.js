function _cEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _cFmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
}

function _cShowBanner(text, variant) {
  const banner = document.getElementById("competition-banner");
  banner.textContent = text;
  banner.className = `banner show ${variant}`;
  setTimeout(() => { banner.className = "banner"; }, 5000);
}

function _competitionIdFromQuery() {
  return new URLSearchParams(window.location.search).get("competition_id");
}

async function loadCompetitionInfo(competitionId) {
  const res = await fetch(`api/competitions/detail?competition_id=${competitionId}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Competition not found");
  const c = data.competition;
  document.title = `${c.title} - Kertoons`;
  document.getElementById("competition-title").textContent = c.title;
  document.getElementById("competition-theme").textContent = c.theme ? `🎨 Theme: ${c.theme}` : "";
  document.getElementById("competition-description").textContent = c.description || "";
  document.getElementById("competition-dates").textContent =
    `📅 ${c.start_date} through ${c.end_date} · ${c.status === "active" ? "Open for entries" : "Closed"}`;
  return c;
}

async function loadLeaderboard(competitionId) {
  const res = await fetch(`api/competitions/leaderboard?competition_id=${competitionId}`);
  const data = await res.json();
  const wrap = document.getElementById("leaderboard-wrap");
  const board = data.leaderboard || [];
  if (!board.length) {
    wrap.innerHTML = `<p class="hint">No entries yet - be the first!</p>`;
    return;
  }
  wrap.innerHTML = `
    <table class="usage-table leaderboard-table">
      <thead><tr><th>Rank</th><th>Storyteller</th><th>Story</th><th>Score</th></tr></thead>
      <tbody>
        ${board.map((e, i) => `
          <tr>
            <td data-label="Rank">${e.rank ?? (i + 1)}${e.is_winner ? " 🏆" : ""}</td>
            <td data-label="Storyteller">${_cEscapeHtml(e.username)}</td>
            <td data-label="Story">${e.story_title ? `<a href="story.html?job_id=${e.job_id}">${_cEscapeHtml(e.story_title)}</a>` : "(still generating)"}</td>
            <td data-label="Score">${e.score_total !== null && e.score_total !== undefined ? e.score_total + " / 50" : "not scored yet"}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

async function loadMyEntry(competitionId) {
  const res = await fetch(`api/competitions/mine?competition_id=${competitionId}`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.entry;
}

function _renderMyEntry(entry) {
  const wrap = document.getElementById("my-entry-summary");
  const formWrap = document.getElementById("enter-form-wrap");
  if (!entry) {
    wrap.innerHTML = "";
    formWrap.style.display = "block";
    return;
  }
  formWrap.style.display = "block"; // still allow submitting another story
  wrap.innerHTML = `
    <div class="character-card">
      <p><strong>Your entry is in.</strong></p>
      <p class="hint">Score: ${entry.score_total !== null && entry.score_total !== undefined ? entry.score_total + " / 50" : "not scored yet"}
      ${entry.rank ? ` · Rank: ${entry.rank}` : ""} ${entry.is_winner ? " · 🏆 Winner!" : ""}</p>
      ${entry.score_feedback ? `<p class="hint">${_cEscapeHtml(entry.score_feedback)}</p>` : ""}
      <div class="admin-row-actions">
        <a class="btn-outline btn-admin-row" href="api/competitions/certificate?entry_id=${entry.id}&type=participation" target="_blank" rel="noopener">📄 Participation certificate</a>
        ${entry.is_winner ? `<a class="btn-outline btn-admin-row" href="api/competitions/certificate?entry_id=${entry.id}&type=winner" target="_blank" rel="noopener">🏆 Winner certificate</a>` : ""}
      </div>
    </div>`;
}

async function loadMyStoriesForSelect() {
  const select = document.getElementById("existing-story-select");
  try {
    const res = await fetch("api/stories/mine");
    const data = await res.json();
    const ready = (data.stories || []).filter(s => s.ready);
    if (!ready.length) {
      select.innerHTML = `<option value="">You have no finished stories yet</option>`;
      return;
    }
    select.innerHTML = ready.map(s => `<option value="${s.job_id}">${_cEscapeHtml(s.title)}</option>`).join("");
  } catch (e) {
    console.error(e);
  }
}

async function submitExistingStory(competitionId) {
  const jobId = document.getElementById("existing-story-select").value;
  if (!jobId) {
    _cShowBanner("Choose a finished story first.", "error");
    return;
  }
  const btn = document.getElementById("submit-existing-btn");
  btn.disabled = true;
  btn.textContent = "Submitting...";
  try {
    const res = await fetch("api/competitions/enter", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ competition_id: competitionId, job_id: jobId, entry_type: "existing" }),
    });
    const data = await res.json();
    if (!res.ok) {
      console.error("competitions/enter failed:", res.status, data);
      throw new Error(data.error || `Failed to enter competition (HTTP ${res.status})`);
    }
    if (data.scored === false) {
      // Entry was recorded, but the AI scoring call itself failed server-side
      // (see server.py's POST /api/competitions/enter) - still a real entry,
      // just not yet scored, so this is worth surfacing distinctly rather
      // than as a plain, unqualified success.
      console.warn("Entry recorded but scoring failed:", data.score_error);
      _cShowBanner("Story entered, but scoring is still pending - it will show once retried.", "success");
    } else {
      _cShowBanner("Story entered into the competition - see \"Your entry is in\" below.", "success");
    }
    await refresh(competitionId);
    // Confirmation card renders into #my-entry-summary, ABOVE this form -
    // scroll it into view so a successful submission is never mistaken for
    // nothing having happened, which is exactly what a fixed-position
    // banner alone risks if the page is already scrolled down to the form.
    document.getElementById("my-entry-summary").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) {
    console.error("submitExistingStory error:", e);
    _cShowBanner(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Submit";
  }
}

async function refresh(competitionId) {
  await Promise.all([
    loadLeaderboard(competitionId),
    loadMyEntry(competitionId).then(_renderMyEntry),
  ]);
}

(async () => {
  const competitionId = _competitionIdFromQuery();
  if (!competitionId) {
    document.getElementById("competition-title").textContent = "Competition not found";
    return;
  }
  document.getElementById("create-new-link").href = `create.html?competition_id=${competitionId}`;

  const user = await renderNav();
  try {
    await loadCompetitionInfo(competitionId);
  } catch (e) {
    _cShowBanner(e.message, "error");
    return;
  }

  if (!user) {
    document.getElementById("login-required").style.display = "block";
  } else {
    document.getElementById("enter-card").style.display = "block";
    await loadMyStoriesForSelect();
    document.getElementById("submit-existing-btn").addEventListener(
      "click", () => submitExistingStory(Number(competitionId))
    );
  }

  await refresh(Number(competitionId));
})();
