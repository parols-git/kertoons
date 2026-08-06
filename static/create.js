const form = document.getElementById("story-form");
const submitBtn = document.getElementById("submit-btn");
const progressSection = document.getElementById("progress-section");
const progressFill = document.getElementById("progress-fill");
const progressMessage = document.getElementById("progress-message");
const bookSection = document.getElementById("book-section");
const errorBox = document.getElementById("error-box");
const modeBanner = document.getElementById("mode-banner");
const loginRequired = document.getElementById("login-required");
const createFormCard = document.getElementById("create-form-card");
const myStoriesSection = document.getElementById("my-stories-section");
const myStoriesList = document.getElementById("my-stories-list");
const creditWarning = document.getElementById("credit-warning");

// Must match server.py's MIN_CREDITS_TO_GENERATE - the button greys out as
// soon as the balance goes negative.
const MIN_CREDITS_TO_GENERATE = 0;

let pollTimer = null;
let myStoriesData = [];
let myStoriesPage = 1;
let storyPageCount = 5; // synced from /api/config below; 5 is the app's fixed page count
const MY_STORIES_PAGE_SIZE = 6;

async function loadConfig() {
  try {
    const res = await fetch("api/config");
    const cfg = await res.json();
    if (cfg.page_count) storyPageCount = cfg.page_count;
    const notes = [];
    if (cfg.mock_story) notes.push("story text is MOCK (set OPENAI_API_KEY to use real OpenAI)");
    if (cfg.mock_translation) {
      const providerKey = cfg.translation_provider === "openai" ? "OPENAI_API_KEY" : "GEMINI_API_KEY";
      notes.push(`translation is MOCK (set ${providerKey} to use real ${cfg.translation_provider === "openai" ? "OpenAI" : "Gemini"})`);
    }
    if (cfg.mock_images) notes.push("images are MOCK placeholders (set DEEPAI_API_KEY to use real art)");
    if (cfg.mock_payments) notes.push("\"Add credits\" is a free MOCK top-up (set STRIPE_SECRET_KEY to sell real credits)");
    if (notes.length) {
      modeBanner.textContent = "Running in demo mode: " + notes.join(" · ");
      modeBanner.classList.add("show");
    }
  } catch (e) {
    console.error(e);
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("show");
}

function clearError() {
  errorBox.textContent = "";
  errorBox.classList.remove("show");
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  bookSection.classList.remove("show");
  bookSection.innerHTML = "";

  const initial_text = document.getElementById("initial_text").value.trim();
  const region = document.getElementById("region").value.trim();
  const secondary_language = document.getElementById("secondary_language").value.trim();
  const photoInput = document.getElementById("character_photo");

  if (!initial_text) {
    showError("Please write a short story idea first.");
    return;
  }

  let character_photo_base64 = null;
  if (photoInput.files && photoInput.files[0]) {
    try {
      character_photo_base64 = await fileToBase64(photoInput.files[0]);
    } catch (e) {
      console.error(e);
    }
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Creating...";
  progressSection.classList.add("show");
  progressFill.style.width = "2%";
  progressMessage.textContent = "Sending your idea to Kertoons...";

  try {
    const res = await fetch("api/story", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        initial_text, region, secondary_language, character_photo_base64,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Failed to start story generation");
    }
    pollStatus(data.job_id);
  } catch (err) {
    finishWithError(err.message);
  }
});

function pollStatus(jobId) {
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`api/story/status?job_id=${jobId}`);
      const job = await res.json();
      if (!res.ok) throw new Error(job.error || "Status check failed");

      progressFill.style.width = `${job.progress || 0}%`;
      progressMessage.textContent = job.message || "";

      if (job.status === "done") {
        clearInterval(pollTimer);
        renderBook(bookSection, jobId, job.story, { isOwner: true, published: false });
        bookSection.classList.add("show");
        bookSection.scrollIntoView({ behavior: "smooth" });
        resetForm();
        loadMyStories(true);
        renderNav().then(updateCreditGate); // credit balance just dropped by 5 - refresh the gate
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        finishWithError(job.error || "Something went wrong generating the story.");
      }
    } catch (err) {
      clearInterval(pollTimer);
      finishWithError(err.message);
    }
  }, 900);
}

function resetForm() {
  submitBtn.disabled = false;
  submitBtn.textContent = "Create my Kertoons story ✨";
  progressSection.classList.remove("show");
}

function finishWithError(msg) {
  resetForm();
  showError(msg);
}

async function loadMyStories(resetPage = false) {
  if (!myStoriesList) return;
  try {
    const res = await fetch("api/stories/mine");
    if (!res.ok) return;
    const data = await res.json();
    myStoriesData = data.stories || [];
    if (resetPage) myStoriesPage = 1;
    myStoriesSection.style.display = "block";
    renderMyStoriesPage();
  } catch (e) {
    console.error(e);
  }
}

function _myStoryRowHtml(s) {
  const label = s.ready ? s.title : s.job_id;
  const safeLabel = label.replace(/'/g, "\\'");
  // A different random page each render - just a cosmetic preview of the
  // book, not tied to any particular page, so no need to keep it stable.
  const randomPage = 1 + Math.floor(Math.random() * storyPageCount);
  const thumbHtml = s.ready
    ? `<img class="my-story-thumb" src="api/story/image?job_id=${s.job_id}&page=${randomPage}" alt="">`
    : `<div class="my-story-thumb my-story-thumb-placeholder"></div>`;
  return `
    <div class="my-story-row">
      ${thumbHtml}
      <div class="my-story-body">
        <div class="my-story-title">
          ${s.ready
            ? `<a href="story.html?job_id=${s.job_id}">${escapeHtml(s.title)}</a>`
            : `<span>${escapeHtml(s.job_id)} (still generating...)</span>`}
        </div>
        <div class="my-story-actions">
          <span class="status-badge ${s.published ? "published" : "unpublished"}">${s.published ? "Published" : "Unpublished"}</span>
          <button type="button" class="btn-outline btn-delete" onclick="deleteStory('${s.job_id}', '${safeLabel}')">🗑 Delete</button>
        </div>
      </div>
    </div>`;
}

function renderMyStoriesPage() {
  if (!myStoriesData.length) {
    myStoriesList.innerHTML = `<p class="hint">You haven't created any stories yet.</p>`;
    return;
  }

  const totalPages = Math.max(1, Math.ceil(myStoriesData.length / MY_STORIES_PAGE_SIZE));
  if (myStoriesPage > totalPages) myStoriesPage = totalPages;
  const start = (myStoriesPage - 1) * MY_STORIES_PAGE_SIZE;
  const pageItems = myStoriesData.slice(start, start + MY_STORIES_PAGE_SIZE);

  const rowsHtml = pageItems.map(_myStoryRowHtml).join("");
  const paginationHtml = myStoriesData.length > MY_STORIES_PAGE_SIZE ? `
    <div class="pagination-row">
      <button type="button" class="btn-outline btn-page" onclick="changeMyStoriesPage(-1)" ${myStoriesPage <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="pagination-status">Page ${myStoriesPage} of ${totalPages}</span>
      <button type="button" class="btn-outline btn-page" onclick="changeMyStoriesPage(1)" ${myStoriesPage >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>` : "";

  myStoriesList.innerHTML = rowsHtml + paginationHtml;
}

function changeMyStoriesPage(delta) {
  myStoriesPage += delta;
  renderMyStoriesPage();
}

async function deleteStory(jobId, label) {
  if (!confirm(`Delete "${label}"? This permanently removes the story and its images and cannot be undone.`)) {
    return;
  }
  try {
    const res = await fetch("api/story/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to delete story");
    loadMyStories();
  } catch (err) {
    showError(err.message);
  }
}

function updateCreditGate(user) {
  if (!user) return;
  const outOfCredits = user.image_credits < MIN_CREDITS_TO_GENERATE;
  submitBtn.disabled = outOfCredits;
  creditWarning.style.display = outOfCredits ? "block" : "none";
}

(async () => {
  const user = await renderNav();
  await loadConfig();
  if (!user) {
    loginRequired.style.display = "block";
    createFormCard.style.display = "none";
  } else {
    loadMyStories();
    updateCreditGate(user);
  }
})();
