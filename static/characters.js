// Public Character Library browser - same client-side filter/paginate
// pattern as gallery.js (fetch the full published list once, filter/sort in
// the browser), rather than a round trip per filter change.
let charactersData = [];
let charactersPage = 1;
const CHARACTERS_PAGE_SIZE = 8;

function _charEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function loadCharacterLibrary() {
  const empty = document.getElementById("characters-empty");
  try {
    const res = await fetch("api/characters/library?sort=recent");
    const data = await res.json();
    charactersData = data.characters || [];
    if (!charactersData.length) {
      empty.style.display = "block";
      document.getElementById("characters-grid").innerHTML = "";
      document.getElementById("characters-pagination").innerHTML = "";
      return;
    }
    empty.style.display = "none";
    charactersPage = 1;
    renderCharactersPage();
  } catch (e) {
    console.error(e);
  }
}

function _filteredCharacters() {
  const query = document.getElementById("character-search").value.trim().toLowerCase();
  const category = document.getElementById("character-filter-category").value.trim().toLowerCase();
  const age = document.getElementById("character-filter-age").value.trim().toLowerCase();
  const school = document.getElementById("character-filter-school").value.trim().toLowerCase();
  const sort = document.getElementById("character-sort").value;

  let filtered = charactersData.filter(c => {
    if (query && !(c.name.toLowerCase().includes(query) || (c.description || "").toLowerCase().includes(query))) return false;
    if (category && (c.category || "").toLowerCase() !== category) return false;
    if (age && (c.age_group || "").toLowerCase() !== age) return false;
    if (school && (c.school || "").toLowerCase() !== school) return false;
    return true;
  });

  filtered = filtered.slice().sort((a, b) => {
    if (sort === "popular") return (b.use_count || 0) - (a.use_count || 0);
    return new Date(b.created_at) - new Date(a.created_at);
  });
  return filtered;
}

function renderCharactersPage() {
  const grid = document.getElementById("characters-grid");
  const pagination = document.getElementById("characters-pagination");
  const noMatches = document.getElementById("characters-no-matches");

  const filtered = _filteredCharacters();
  if (!filtered.length) {
    grid.innerHTML = "";
    pagination.innerHTML = "";
    noMatches.style.display = "block";
    return;
  }
  noMatches.style.display = "none";

  const totalPages = Math.max(1, Math.ceil(filtered.length / CHARACTERS_PAGE_SIZE));
  if (charactersPage > totalPages) charactersPage = totalPages;
  const start = (charactersPage - 1) * CHARACTERS_PAGE_SIZE;
  const pageItems = filtered.slice(start, start + CHARACTERS_PAGE_SIZE);

  grid.innerHTML = pageItems.map(c => `
    <div class="character-card">
      <img src="api/characters/image?character_id=${c.id}" alt="${_charEscapeHtml(c.name)}">
      <h3>${_charEscapeHtml(c.name)}</h3>
      <p class="hint">${_charEscapeHtml(c.description || "")}</p>
      <div class="hint">
        ${c.category ? `🏷️ ${_charEscapeHtml(c.category)}` : ""}
        ${c.age_group ? ` · 👶 ${_charEscapeHtml(c.age_group)}` : ""}
        ${c.school ? ` · 🏫 ${_charEscapeHtml(c.school)}` : ""}
      </div>
      <div class="hint">👁 ${c.view_count || 0} · ✨ used in ${c.use_count || 0} ${c.use_count === 1 ? "story" : "stories"}</div>
      <a class="btn-outline" href="create.html?character_id=${c.id}">Use in a story</a>
    </div>
  `).join("");

  pagination.innerHTML = filtered.length > CHARACTERS_PAGE_SIZE ? `
    <div class="pagination-row">
      <button type="button" class="btn-outline btn-page" onclick="changeCharactersPage(-1)" ${charactersPage <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="pagination-status">Page ${charactersPage} of ${totalPages}</span>
      <button type="button" class="btn-outline btn-page" onclick="changeCharactersPage(1)" ${charactersPage >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>` : "";
}

function changeCharactersPage(delta) {
  charactersPage += delta;
  renderCharactersPage();
}

function _onCharacterFilterChange() {
  charactersPage = 1;
  renderCharactersPage();
}

(async () => {
  await renderNav();
  ["character-search", "character-filter-category", "character-filter-age", "character-filter-school", "character-sort"]
    .forEach(id => {
      const el = document.getElementById(id);
      el.addEventListener(el.tagName === "SELECT" ? "change" : "input", _onCharacterFilterChange);
    });
  loadCharacterLibrary();
})();
