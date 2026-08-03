// Shared top nav (Gallery / Create a story / account) rendered into
// #nav-bar on every page. Callers `await renderNav()` during their own init
// and get back the current user (or null), so they can decide what else to
// show/gate without a second /api/me round-trip.
function _navEscapeHtml(str) {
  if (str === undefined || str === null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Replaces every occurrence of the literal word "Kertoons" - in the page
// <title> and in every body text node (the header's "🧸 Kertoons", FAQ/Help
// marketing copy, button labels, etc.) - with the admin's configured site
// name (see the admin panel's Settings section / db.get_site_settings()).
// A generic text-node walk rather than editing each page individually, so
// every current AND future mention of the brand name stays in sync from
// one place. No-ops entirely when the name is still the default, so the
// common case costs nothing.
function _applyBranding(siteName) {
  if (!siteName || siteName === "Kertoons") return;
  if (document.title.includes("Kertoons")) {
    document.title = document.title.replace(/Kertoons/g, siteName);
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const tag = node.parentElement && node.parentElement.tagName;
      if (tag === "SCRIPT" || tag === "STYLE") return NodeFilter.FILTER_REJECT;
      return node.nodeValue.includes("Kertoons") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) textNodes.push(node);
  textNodes.forEach((n) => {
    n.nodeValue = n.nodeValue.replace(/Kertoons/g, siteName);
  });
}

// The trailing footer message (default "kertoons.com - Another Elisda AI
// project") is a fully separate, admin-editable string, not just a name
// substitution - every page's footer has a #site-footer-text span for it.
function _applyFooterText(footerText) {
  const el = document.getElementById("site-footer-text");
  if (el && footerText) el.textContent = footerText;
}

// Contact info is opt-in (see db.DEFAULT_CONTACT_EMAIL/_PHONE, both blank
// by default) - only shown on faq.html's #contact-card, and only the
// email/phone rows that actually have a value, rather than a
// half-empty-looking "Contact us" card if only one is set.
function _applyContactInfo(email, phone) {
  const card = document.getElementById("contact-card");
  if (!card) return;
  const emailRow = document.getElementById("contact-email-row");
  const phoneRow = document.getElementById("contact-phone-row");
  if (email && emailRow) {
    document.getElementById("contact-email").textContent = email;
    document.getElementById("contact-email-link").href = `mailto:${email}`;
    emailRow.style.display = "block";
  }
  if (phone && phoneRow) {
    document.getElementById("contact-phone").textContent = phone;
    phoneRow.style.display = "block";
  }
  if (email || phone) card.style.display = "block";
}

async function renderNav() {
  let user = null;
  let cfg = {};
  try {
    const [meRes, cfgRes] = await Promise.all([fetch("api/me"), fetch("api/config")]);
    user = (await meRes.json()).user;
    cfg = await cfgRes.json();
  } catch (e) {
    console.error(e);
  }

  _applyBranding(cfg.site_name);
  _applyFooterText(cfg.footer_text);
  _applyContactInfo(cfg.contact_email, cfg.contact_phone);

  const nav = document.getElementById("nav-bar");
  if (!nav) return user;

  if (user) {
    const addCreditsLabel = cfg.mock_payments
      ? `➕ Add ${cfg.credit_pack_credits || 20} credits (demo)`
      : `➕ Add ${cfg.credit_pack_credits || 50} credits ($${(cfg.credit_pack_price_usd || 5).toFixed(2)})`;
    nav.innerHTML = `
      <a href="index.html">Gallery</a>
      <a href="create.html">Create a story</a>
      <a href="usage.html">🖼️ ${user.image_credits} image credits</a>
      <button type="button" class="btn-outline btn-nav" id="add-credits-btn">${addCreditsLabel}</button>
      ${user.role === "admin" ? `<a href="admin.html">🛠 Admin</a>` : ""}
      <span class="nav-user">Hi, ${_navEscapeHtml(user.username)}</span>
      <button type="button" class="btn-outline btn-nav" id="logout-btn">Log out</button>
    `;
    document.getElementById("logout-btn").addEventListener("click", async () => {
      await fetch("api/logout", { method: "POST" });
      window.location.href = "index.html";
    });
    document.getElementById("add-credits-btn").addEventListener("click", async () => {
      const btn = document.getElementById("add-credits-btn");
      const originalLabel = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Please wait...";
      try {
        const res = await fetch("api/credits/checkout", { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to start checkout");
        if (data.checkout_url) {
          // Real payment: hand off to Stripe's own hosted page - this app
          // never sees card details, only Stripe's redirect back afterward.
          window.location.href = data.checkout_url;
        } else {
          // Mock mode: credits were already granted instantly server-side.
          // Reloading is the simplest way to get every bit of page state
          // (the credits link text, a greyed-out Create button, etc.) back
          // in sync with the new balance.
          window.location.reload();
        }
      } catch (e) {
        console.error(e);
        alert(e.message);
        btn.disabled = false;
        btn.textContent = originalLabel;
      }
    });
  } else {
    nav.innerHTML = `
      <a href="index.html">Gallery</a>
      <a href="login.html">Log in</a>
      <a href="register.html">Register</a>
    `;
  }
  return user;
}
