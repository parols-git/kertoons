const errorBox = document.getElementById("error-box");
const bookSection = document.getElementById("book-section");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("show");
}

async function loadStory() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job_id");
  if (!jobId) {
    showError("No story specified.");
    return;
  }
  try {
    const res = await fetch(`api/story/view?job_id=${encodeURIComponent(jobId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Story not found");
    renderBook(bookSection, jobId, data.story, {
      isOwner: data.is_owner,
      published: data.published,
      author: data.author,
    });
    bookSection.classList.add("show");
  } catch (err) {
    showError(err.message);
  }
}

(async () => {
  await renderNav();
  loadStory();
})();
