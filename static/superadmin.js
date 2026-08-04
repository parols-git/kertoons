// Standalone superadmin login + cost-settings dashboard. Deliberately NOT
// using nav.js/renderNav() - no site chrome, no branding, nothing that
// hints at this page's existence from anywhere else in the app. The real
// protection is server-side role gating (_require_superadmin in
// server.py) - this page being unlinked is just defense in depth, not the
// actual security boundary, so logging in with valid-but-wrong-role
// credentials must still be rejected here.
const errorBox = document.getElementById("error-box");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("show");
}

function clearError() {
  errorBox.classList.remove("show");
}

let currentCaptchaId = null;

async function loadCaptcha() {
  try {
    const res = await fetch("api/captcha");
    const data = await res.json();
    currentCaptchaId = data.captcha_id;
    document.getElementById("captcha-img").src = `api/captcha/image?captcha_id=${data.captcha_id}&t=${Date.now()}`;
    document.getElementById("captcha-input").value = "";
  } catch (e) {
    console.error(e);
  }
}

function showDashboard() {
  document.getElementById("login-section").style.display = "none";
  document.getElementById("dashboard-section").style.display = "block";
  loadCosts();
}

function showLogin() {
  document.getElementById("login-section").style.display = "block";
  document.getElementById("dashboard-section").style.display = "none";
  loadCaptcha();
}

function renderCostSummary(data) {
  document.getElementById("cost-summary").innerHTML = `
    <div class="reports-grid">
      <div class="reports-stat"><div class="reports-stat-value">${data.images_total}</div><div class="reports-stat-label">Images generated (all time)</div></div>
      <div class="reports-stat"><div class="reports-stat-value">$${Number(data.image_cost_total).toFixed(2)}</div><div class="reports-stat-label">Image costs</div></div>
      <div class="reports-stat"><div class="reports-stat-value">$${Number(data.server_fee).toFixed(2)}</div><div class="reports-stat-label">Server fee</div></div>
      <div class="reports-stat"><div class="reports-stat-value">$${Number(data.total_cost).toFixed(2)}</div><div class="reports-stat-label">Total cost</div></div>
    </div>
  `;
}

async function loadCosts() {
  try {
    const res = await fetch("api/superadmin/costs");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load cost settings");
    document.getElementById("cost-per-image").value = data.cost_per_image;
    document.getElementById("server-fee").value = data.server_fee;
    renderCostSummary(data);
  } catch (e) {
    showError(e.message);
  }
}

document.getElementById("superadmin-login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const captcha_answer = document.getElementById("captcha-input").value.trim();
  const submitBtn = document.getElementById("submit-btn");
  const originalLabel = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = "Please wait...";
  try {
    const res = await fetch("api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, captcha_id: currentCaptchaId, captcha_answer }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Login failed");
    // A successful login alone doesn't mean superadmin access - a regular
    // user's or even a regular admin's correct credentials must NOT reach
    // this dashboard. Log straight back out rather than leaving an
    // authenticated-but-unauthorized session sitting in the browser.
    if (data.user.role !== "superadmin") {
      await fetch("api/logout", { method: "POST" });
      throw new Error("Access denied.");
    }
    showDashboard();
  } catch (err) {
    showError(err.message);
    loadCaptcha();
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalLabel;
  }
});

document.getElementById("captcha-refresh").addEventListener("click", loadCaptcha);

document.getElementById("costs-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  const cost_per_image = parseFloat(document.getElementById("cost-per-image").value);
  const server_fee = parseFloat(document.getElementById("server-fee").value);
  try {
    const res = await fetch("api/superadmin/costs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cost_per_image, server_fee }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to save cost settings");
    renderCostSummary(data);
  } catch (e) {
    showError(e.message);
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("api/logout", { method: "POST" });
  showLogin();
});

// On load, reuse an already-active superadmin session (e.g. a page reload)
// instead of forcing a fresh login every time.
(async () => {
  try {
    const res = await fetch("api/me");
    const data = await res.json();
    if (data.user && data.user.role === "superadmin") {
      showDashboard();
      return;
    }
  } catch (e) {
    console.error(e);
  }
  showLogin();
})();
