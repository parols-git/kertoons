let usageData = [];
let usagePage = 1;
const USAGE_PAGE_SIZE = 6;

let paymentsData = [];
let paymentsPage = 1;
const PAYMENTS_PAGE_SIZE = 6;

function _usageEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _usageFmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
}

async function loadUsage() {
  const loginRequired = document.getElementById("login-required");
  const usageCard = document.getElementById("usage-card");
  try {
    const res = await fetch("api/image-usage/mine");
    if (res.status === 401) {
      loginRequired.style.display = "block";
      usageCard.style.display = "none";
      return;
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load usage");

    usageCard.style.display = "block";
    document.getElementById("credits-pill").textContent = `${data.credits} credits remaining`;
    usageData = data.usage || [];
    usagePage = 1;
    renderUsagePage();
  } catch (e) {
    console.error(e);
  }
}

function renderUsagePage() {
  const wrap = document.getElementById("usage-table-wrap");
  if (!usageData.length) {
    wrap.innerHTML = `<p class="hint">No images generated yet.</p>`;
    return;
  }

  const totalPages = Math.max(1, Math.ceil(usageData.length / USAGE_PAGE_SIZE));
  if (usagePage > totalPages) usagePage = totalPages;
  const start = (usagePage - 1) * USAGE_PAGE_SIZE;
  const pageItems = usageData.slice(start, start + USAGE_PAGE_SIZE);

  const rowsHtml = pageItems.map(u => `
    <tr>
      <td><img class="usage-thumb" src="api/story/image?job_id=${u.job_id}&page=${u.page_number}" alt=""></td>
      <td><a href="story.html?job_id=${u.job_id}">Page ${u.page_number}</a></td>
      <td class="usage-prompt" title="${_usageEscapeHtml(u.prompt)}">${_usageEscapeHtml(u.prompt)}</td>
      <td class="usage-date">${_usageEscapeHtml(_usageFmtDate(u.created_at))}</td>
    </tr>`
  ).join("");

  const paginationHtml = usageData.length > USAGE_PAGE_SIZE ? `
    <div class="pagination-row">
      <button type="button" class="btn-outline btn-page" onclick="changeUsagePage(-1)" ${usagePage <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="pagination-status">Page ${usagePage} of ${totalPages}</span>
      <button type="button" class="btn-outline btn-page" onclick="changeUsagePage(1)" ${usagePage >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>` : "";

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Image</th><th>Story page</th><th>Prompt</th><th>Generated</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${paginationHtml}
  `;
}

function changeUsagePage(delta) {
  usagePage += delta;
  renderUsagePage();
}

async function loadPayments() {
  const card = document.getElementById("payments-card");
  try {
    const res = await fetch("api/payments/mine");
    if (!res.ok) return; // not logged in - login-required section from loadUsage() already covers this
    const data = await res.json();
    card.style.display = "block";
    paymentsData = data.payments || [];
    paymentsPage = 1;
    renderPaymentsPage();
  } catch (e) {
    console.error(e);
  }
}

function renderPaymentsPage() {
  const wrap = document.getElementById("payments-table-wrap");
  if (!paymentsData.length) {
    wrap.innerHTML = `<p class="hint">No purchases yet.</p>`;
    return;
  }

  const totalPages = Math.max(1, Math.ceil(paymentsData.length / PAYMENTS_PAGE_SIZE));
  if (paymentsPage > totalPages) paymentsPage = totalPages;
  const start = (paymentsPage - 1) * PAYMENTS_PAGE_SIZE;
  const pageItems = paymentsData.slice(start, start + PAYMENTS_PAGE_SIZE);

  const rowsHtml = pageItems.map(p => `
    <tr>
      <td>+${p.credits} credits</td>
      <td>${p.amount_usd != null ? "$" + Number(p.amount_usd).toFixed(2) : "—"}</td>
      <td class="usage-date">${_usageEscapeHtml(_usageFmtDate(p.created_at))}</td>
    </tr>`
  ).join("");

  const paginationHtml = paymentsData.length > PAYMENTS_PAGE_SIZE ? `
    <div class="pagination-row">
      <button type="button" class="btn-outline btn-page" onclick="changePaymentsPage(-1)" ${paymentsPage <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="pagination-status">Page ${paymentsPage} of ${totalPages}</span>
      <button type="button" class="btn-outline btn-page" onclick="changePaymentsPage(1)" ${paymentsPage >= totalPages ? "disabled" : ""}>Next ›</button>
    </div>` : "";

  wrap.innerHTML = `
    <table class="usage-table">
      <thead><tr><th>Credits</th><th>Amount paid</th><th>Date</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${paginationHtml}
  `;
}

function changePaymentsPage(delta) {
  paymentsPage += delta;
  renderPaymentsPage();
}

function _showCheckoutBanner(text, variant) {
  const banner = document.getElementById("checkout-banner");
  banner.textContent = text;
  banner.className = `banner show ${variant}`;
}

// If the browser just returned from Stripe's hosted checkout page, confirm
// the payment and show a banner. Also strips the query string afterward so
// refreshing the page doesn't re-trigger a confirm call every time.
async function handleCheckoutReturn() {
  const params = new URLSearchParams(window.location.search);
  const checkout = params.get("checkout");
  if (!checkout) return;

  if (checkout === "success") {
    const sessionId = params.get("session_id");
    if (sessionId) {
      try {
        const res = await fetch("api/credits/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await res.json();
        if (res.ok && data.pending) {
          _showCheckoutBanner("Payment received - your credits will appear shortly.", "success");
        } else if (res.ok) {
          _showCheckoutBanner(`Payment received - ${data.credits} credits available now.`, "success");
        } else {
          _showCheckoutBanner(`Could not confirm payment: ${data.error || "unknown error"}`, "cancelled");
        }
      } catch (e) {
        console.error(e);
      }
    }
  } else if (checkout === "cancel") {
    _showCheckoutBanner("Checkout cancelled - no charge was made.", "cancelled");
  }

  window.history.replaceState({}, "", window.location.pathname);
}

document.getElementById("redeem-coupon-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = document.getElementById("coupon-code-input");
  const code = input.value.trim();
  if (!code) return;
  try {
    const res = await fetch("api/coupons/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to redeem coupon");
    input.value = "";
    _showCheckoutBanner(`Coupon redeemed - ${data.credits} credits available now.`, "success");
    await renderNav();
    loadUsage();
  } catch (e) {
    _showCheckoutBanner(e.message, "cancelled");
  }
});

(async () => {
  await handleCheckoutReturn();
  await renderNav(); // after a successful purchase, refresh the credit count shown in the nav too
  loadUsage();
  loadPayments();
})();
