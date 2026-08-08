function _compEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _competitionCardHtml(c) {
  return `
    <div class="character-card">
      <h3>${_compEscapeHtml(c.title)}</h3>
      ${c.theme ? `<p class="hint">🎨 ${_compEscapeHtml(c.theme)}</p>` : ""}
      <p class="hint">${_compEscapeHtml(c.description || "")}</p>
      <p class="hint">📅 ${_compEscapeHtml(c.start_date)} &rarr; ${_compEscapeHtml(c.end_date)}</p>
      <a class="btn-outline" href="competition.html?competition_id=${c.id}">View &amp; Enter</a>
    </div>`;
}

async function loadCompetitions() {
  try {
    const [activeRes, closedRes] = await Promise.all([
      fetch("api/competitions?status=active"),
      fetch("api/competitions?status=closed"),
    ]);
    const active = (await activeRes.json()).competitions || [];
    const closed = (await closedRes.json()).competitions || [];

    document.getElementById("active-empty").style.display = active.length ? "none" : "block";
    document.getElementById("active-competitions-grid").innerHTML = active.map(_competitionCardHtml).join("");

    document.getElementById("closed-empty").style.display = closed.length ? "none" : "block";
    document.getElementById("closed-competitions-grid").innerHTML = closed.map(_competitionCardHtml).join("");
  } catch (e) {
    console.error(e);
  }
}

(async () => {
  await renderNav();
  loadCompetitions();
})();
