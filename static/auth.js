// Shared by login.html and register.html - whichever form is present on
// the page is the one that gets wired up.
const errorBox = document.getElementById("error-box");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("show");
}

async function submitAuth(url, payload, submitBtn, originalLabel) {
  submitBtn.disabled = true;
  submitBtn.textContent = "Please wait...";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Something went wrong");
    window.location.href = "create.html";
  } catch (err) {
    showError(err.message);
    submitBtn.disabled = false;
    submitBtn.textContent = originalLabel;
  }
}

// Login-only 4-digit captcha, meant to slow down scripted brute-force
// login attempts (see server.py's _issue_captcha/_consume_captcha) - not
// present on the register form, which isn't a credential-guessing target
// the same way login is.
let currentCaptchaId = null;

async function loadCaptcha() {
  const img = document.getElementById("captcha-img");
  if (!img) return; // not on this page (e.g. register.html)
  try {
    const res = await fetch("api/captcha");
    const data = await res.json();
    currentCaptchaId = data.captcha_id;
    img.src = `api/captcha/image?captcha_id=${data.captcha_id}&t=${Date.now()}`;
    document.getElementById("captcha-input").value = "";
  } catch (e) {
    console.error(e);
  }
}

const loginForm = document.getElementById("login-form");
if (loginForm) {
  loadCaptcha();
  document.getElementById("captcha-refresh").addEventListener("click", loadCaptcha);

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorBox.classList.remove("show");
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const captcha_answer = document.getElementById("captcha-input").value.trim();
    await submitAuth(
      "api/login",
      { username, password, captcha_id: currentCaptchaId, captcha_answer },
      document.getElementById("submit-btn"), "Log in",
    );
    // Whether the attempt failed on a wrong captcha, wrong credentials, or
    // anything else, the captcha was already single-use-consumed server-
    // side - always load a fresh one for the next attempt rather than
    // leaving a now-dead code displayed.
    loadCaptcha();
  });
}

const registerForm = document.getElementById("register-form");
if (registerForm) {
  registerForm.addEventListener("submit", (e) => {
    e.preventDefault();
    errorBox.classList.remove("show");
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const confirmPassword = document.getElementById("confirm_password").value;
    if (password !== confirmPassword) {
      showError("Passwords don't match.");
      return;
    }
    submitAuth("api/register", { username, password }, document.getElementById("submit-btn"), "Create account");
  });
}

renderNav();
