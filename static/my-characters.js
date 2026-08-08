let myCharactersData = [];
let editingCharacterId = null;

function _mcEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _mcShowBanner(text, variant) {
  const banner = document.getElementById("my-characters-banner");
  banner.textContent = text;
  banner.className = `banner show ${variant}`;
  setTimeout(() => { banner.className = "banner"; }, 4000);
}

async function loadMyCharacters() {
  try {
    const res = await fetch("api/characters/mine");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load your characters");
    myCharactersData = data.characters || [];
    renderMyCharacters();
  } catch (e) {
    console.error(e);
  }
}

function renderMyCharacters() {
  const wrap = document.getElementById("my-characters-wrap");
  if (!myCharactersData.length) {
    wrap.innerHTML = `<p class="hint">You haven't created any characters yet.</p>`;
    return;
  }
  wrap.innerHTML = myCharactersData.map(c => {
    if (editingCharacterId === c.id) return _editRowHtml(c);
    return `
      <div class="character-card" style="flex-direction:row; align-items:flex-start; gap:16px;">
        <img src="api/characters/image?character_id=${c.id}" alt="${_mcEscapeHtml(c.name)}" style="width:96px; height:96px; flex-shrink:0;">
        <div style="flex:1;">
          <div><strong>${_mcEscapeHtml(c.name)}</strong> <span class="status-badge ${c.status}">${c.status}</span></div>
          <p class="hint">${_mcEscapeHtml(c.description || "")}</p>
          <p class="hint">${_mcEscapeHtml(c.appearance)}</p>
          ${c.moderation_note ? `<p class="hint"><strong>Moderator note:</strong> ${_mcEscapeHtml(c.moderation_note)}</p>` : ""}
          <div class="admin-row-actions">
            ${(c.status === "draft" || c.status === "rejected") ? `<button type="button" class="btn-outline btn-admin-row" onclick="submitCharacter(${c.id})">Submit for publication</button>` : ""}
            <button type="button" class="btn-outline btn-admin-row" onclick="startEditCharacter(${c.id})">Edit</button>
            <button type="button" class="btn-outline btn-admin-row" onclick="deleteCharacter(${c.id})">Delete</button>
          </div>
        </div>
      </div>`;
  }).join("");
}

function _editRowHtml(c) {
  return `
    <div class="character-card">
      <div><strong>Editing ${_mcEscapeHtml(c.name)}</strong> <span class="status-badge ${c.status}">${c.status}</span></div>
      ${c.status === "published" ? `<p class="hint">Saving changes will send this character back for admin review before the update goes live.</p>` : ""}
      <label>Name <input type="text" id="edit-name-${c.id}" value="${_mcEscapeHtml(c.name)}"></label>
      <label>Description <textarea id="edit-description-${c.id}" rows="2" style="width:100%; box-sizing:border-box;">${_mcEscapeHtml(c.description || "")}</textarea></label>
      <label>Appearance <textarea id="edit-appearance-${c.id}" rows="3" style="width:100%; box-sizing:border-box;">${_mcEscapeHtml(c.appearance)}</textarea></label>
      <label>Personality <input type="text" id="edit-personality-${c.id}" value="${_mcEscapeHtml(c.personality || "")}"></label>
      <div class="admin-inline-form">
        <input type="text" id="edit-category-${c.id}" placeholder="Category" value="${_mcEscapeHtml(c.category || "")}">
        <input type="text" id="edit-age-${c.id}" placeholder="Age group" value="${_mcEscapeHtml(c.age_group || "")}">
        <input type="text" id="edit-school-${c.id}" placeholder="School" value="${_mcEscapeHtml(c.school || "")}">
      </div>
      <div class="admin-row-actions">
        <button type="button" class="btn-secondary btn-admin-row" onclick="saveCharacterEdit(${c.id})">Save</button>
        <button type="button" class="btn-outline btn-admin-row" onclick="cancelEditCharacter()">Cancel</button>
      </div>
    </div>`;
}

function startEditCharacter(characterId) {
  editingCharacterId = characterId;
  renderMyCharacters();
}

function cancelEditCharacter() {
  editingCharacterId = null;
  renderMyCharacters();
}

async function saveCharacterEdit(characterId) {
  try {
    const res = await fetch("api/characters/update", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        character_id: characterId,
        name: document.getElementById(`edit-name-${characterId}`).value,
        description: document.getElementById(`edit-description-${characterId}`).value,
        appearance: document.getElementById(`edit-appearance-${characterId}`).value,
        personality: document.getElementById(`edit-personality-${characterId}`).value,
        category: document.getElementById(`edit-category-${characterId}`).value,
        age_group: document.getElementById(`edit-age-${characterId}`).value,
        school: document.getElementById(`edit-school-${characterId}`).value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to save character");
    editingCharacterId = null;
    _mcShowBanner("Character updated.", "success");
    loadMyCharacters();
  } catch (e) {
    alert(e.message);
  }
}

async function submitCharacter(characterId) {
  try {
    const res = await fetch("api/characters/submit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to submit character");
    _mcShowBanner(
      data.character.status === "published"
        ? "Character published."
        : "Character submitted for review.",
      "success"
    );
    loadMyCharacters();
  } catch (e) {
    alert(e.message);
  }
}

async function deleteCharacter(characterId) {
  if (!confirm("Permanently delete this character?")) return;
  try {
    const res = await fetch("api/characters/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to delete character");
    _mcShowBanner("Character deleted.", "success");
    loadMyCharacters();
  } catch (e) {
    alert(e.message);
  }
}

async function handleCreateCharacter(e) {
  e.preventDefault();
  const btn = document.getElementById("create-character-btn");
  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = "Creating...";
  try {
    const res = await fetch("api/characters/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt_text: document.getElementById("new-character-prompt").value,
        type: document.getElementById("new-character-type").value,
        category: document.getElementById("new-character-category").value,
        age_group: document.getElementById("new-character-age").value,
        school: document.getElementById("new-character-school").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to create character");
    document.getElementById("create-character-form").reset();
    _mcShowBanner(`Created "${data.character.name}" - review it below and submit when ready.`, "success");
    loadMyCharacters();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

(async () => {
  const user = await renderNav();
  if (!user) {
    document.getElementById("not-logged-in").style.display = "block";
    return;
  }
  document.getElementById("logged-in-panel").style.display = "block";
  document.getElementById("create-character-form").addEventListener("submit", handleCreateCharacter);
  loadMyCharacters();
})();
