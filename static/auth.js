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

const loginForm = document.getElementById("login-form");
if (loginForm) {
  loginForm.addEventListener("submit", (e) => {
    e.preventDefault();
    errorBox.classList.remove("show");
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    submitAuth("api/login", { username, password }, document.getElementById("submit-btn"), "Log in");
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
