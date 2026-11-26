// Sign in / create account.
import { api, guard, html, mount } from "../lib.js";
import { signedIn } from "../app.js";

function page(kind) {
  const isLogin = kind === "login";
  return html`<header class="topbar"><span class="logo">Car Scanner</span></header>
  <main class="flow" style="max-width:960px">
    <section class="main" style="max-width:420px">
      <h1>${isLogin ? "Sign in" : "Create your account"}</h1>
      <p class="lead mt8">${isLogin ? "Your garage, past scans and reports are saved to your account."
        : "Save every car you scan, its history, mileage checks and photo reports."}</p>
      <form class="stack g16 mt32" novalidate>
        ${isLogin ? "" : html`<label class="field">Name <span class="hint">optional</span>
          <input class="input" name="name" autocomplete="name" maxlength="80"></label>`}
        <label class="field">Email<input class="input" name="email" type="email" autocomplete="email" required></label>
        <label class="field">Password
          <input class="input" name="password" type="password" required minlength="${isLogin ? 1 : 10}"
            autocomplete="${isLogin ? "current-password" : "new-password"}">
          ${isLogin ? "" : html`<span class="hint">At least 10 characters, mixing letters with numbers or symbols.</span>`}
        </label>
        <p class="error-text" data-err role="alert"></p>
        <button class="btn primary lg" type="submit">${isLogin ? "Sign in" : "Create account"}</button>
      </form>
      <p class="small muted mt24">${isLogin ? html`New here? <a href="#/register">Create an account</a>`
        : html`Already have an account? <a href="#/login">Sign in</a>`}</p>
    </section>
    <aside class="side panel">
      <h2 class="small muted" style="font-weight:500">What you can do</h2>
      <ul class="mt12 stack g8" style="padding-left:18px;margin:0">
        <li>Read codes from every module, not just the engine</li>
        <li>Follow guided tests made for your car and your problem</li>
        <li>Check whether the mileage is genuine</li>
        <li>Get a condition report from photos</li>
        <li>Ask Claude or Codex, using your own API key</li>
      </ul>
    </aside>
  </main>`;
}

function bind(root, kind) {
  const form = root.querySelector("form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = Object.fromEntries(fd.entries());
    const err = root.querySelector("[data-err]");
    err.textContent = "";
    guard(async () => {
      try {
        signedIn(await api(`/auth/${kind}`, { method: "POST", body }));
      } catch (ex) { err.textContent = ex.message; }
    }, form.querySelector("button[type=submit]"));
  });
}

export function login(root) { mount(root, page("login")); bind(root, "login"); }
export function register(root) { mount(root, page("register")); bind(root, "register"); }
