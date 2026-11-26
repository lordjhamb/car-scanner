// App shell: session, state, hash router, sidebar layout.
import { api, carName, html, mount, setCsrf, toast } from "./lib.js";
import * as auth from "./views/auth.js";
import * as garage from "./views/garage.js";
import * as onboarding from "./views/onboarding.js";
import * as fix from "./views/fix.js";
import * as run from "./views/run.js";
import * as report from "./views/report.js";
import * as mileage from "./views/mileage.js";
import * as photos from "./views/photos.js";
import * as assistant from "./views/assistant.js";
import * as settings from "./views/settings.js";

export const app = {
  user: null,
  adapter: { connected: false },
  flow: {},                       // onboarding scratch: odometer, goal, problem...
  get carId() { return Number(localStorage.getItem(`car:${this.user?.id}`)) || null; },
  set carId(v) { if (this.user) localStorage.setItem(`car:${this.user.id}`, v || ""); },
  car: null,
  async refreshCar() {
    if (!this.carId) { this.car = null; return null; }
    try { this.car = (await api(`/cars/${this.carId}`)).car; }
    catch { this.car = null; this.carId = null; }
    return this.car;
  },
  async refreshAdapter() {
    try { this.adapter = await api("/adapter/status"); } catch { this.adapter = { connected: false }; }
    return this.adapter;
  },
  unit() { return this.user?.unit_pref || "mi"; },
  go(hash) { location.hash = hash; },
};

const ROUTES = [
  [/^#\/login$/, auth.login, "bare"],
  [/^#\/register$/, auth.register, "bare"],
  [/^#\/garage$/, garage.view, "shell", "garage"],
  [/^#\/new\/connect$/, onboarding.connect, "bare"],
  [/^#\/new\/vehicle$/, onboarding.vehicle, "bare"],
  [/^#\/new\/goal$/, onboarding.goal, "bare"],
  [/^#\/scanning\/(\w+)$/, onboarding.scanning, "bare"],
  [/^#\/fix\/describe$/, fix.describe, "bare"],
  [/^#\/fix\/thinking$/, fix.thinking, "bare"],
  [/^#\/fix\/plan\/(\d+)$/, fix.plan, "bare"],
  [/^#\/run\/(\d+)$/, run.view, "bare"],
  [/^#\/car\/(\d+)$/, report.view, "shell", "summary"],
  [/^#\/car\/(\d+)\/live$/, report.live, "shell", "live"],
  [/^#\/car\/(\d+)\/mileage$/, mileage.view, "shell", "mileage"],
  [/^#\/car\/(\d+)\/photos$/, photos.capture, "shell", "photos"],
  [/^#\/car\/(\d+)\/photos\/report$/, photos.report, "shell", "photos"],
  [/^#\/assistant(?:\/(\d+))?$/, assistant.view, "shell", "assistant"],
  [/^#\/settings$/, settings.view, "shell", "settings"],
];

const root = document.getElementById("root");
let cleanup = null;

function shell(active) {
  const c = app.car;
  const cid = c?.id;
  const items = [
    ["garage", "#/garage", "Garage"],
    ...(cid ? [["summary", `#/car/${cid}`, "Report"], ["live", `#/car/${cid}/live`, "Live data"],
      ["mileage", `#/car/${cid}/mileage`, "Mileage check"], ["photos", `#/car/${cid}/photos`, "Photo inspection"]] : []),
    ["assistant", "#/assistant", "Assistant"], ["settings", "#/settings", "Settings"],
  ];
  return html`<div class="app">
    <aside class="sidebar">
      <a class="logo" href="#/garage">Car Scanner</a>
      ${c ? html`<div class="carcard"><div style="font-weight:500">${carName(c)}</div>
        <div class="mono tiny faint">${c.vin}</div>
        <div class="tiny mt8" style="color:${app.adapter.connected ? "var(--green)" : "var(--faint)"}">
          ${app.adapter.connected ? html`<span class="dot"></span> Adapter connected` : "Adapter not connected"}</div></div>` : ""}
      <a class="btn primary sm" href="#/new/connect">New scan</a>
      <nav class="nav" aria-label="Sections">${items.map(([k, href, label]) =>
        html`<a href="${href}" aria-current="${k === active ? "page" : "false"}">${label}</a>`)}</nav>
      <div class="right" style="margin-top:auto;margin-left:0;padding:0 10px">
        <div class="tiny faint">${app.user.email}</div>
        <button class="btn link tiny" data-logout>Sign out</button>
      </div>
    </aside>
    <main class="content"><div class="inner" id="view"></div></main></div>`;
}

async function route() {
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  const hash = location.hash || "#/garage";
  if (!app.user && !/^#\/(login|register)$/.test(hash)) { location.hash = "#/login"; return; }
  if (app.user && /^#\/(login|register)$/.test(hash)) { location.hash = "#/garage"; return; }
  const match = ROUTES.map((r) => [r, hash.match(r[0])]).find(([, m]) => m);
  if (!match) { location.hash = "#/garage"; return; }
  const [[, fn, layout, active], m] = match;
  const params = m.slice(1);
  let target = root;
  if (layout === "shell") {
    if (params[0] && /^#\/car\//.test(hash)) app.carId = Number(params[0]);
    await Promise.all([app.refreshCar(), app.refreshAdapter()]);
    mount(root, shell(active));
    root.querySelector("[data-logout]").addEventListener("click", logout);
    target = document.getElementById("view");
  }
  try {
    cleanup = (await fn(target, params, app)) || null;
  } catch (e) {
    mount(target, html`<div class="card"><h2>Something went wrong</h2><p class="muted mt8">${e.message}</p>
      <a class="btn mt16" href="#/garage">Back to the garage</a></div>`);
  }
  window.scrollTo(0, 0);
  const h1 = target.querySelector("h1");
  if (h1) document.title = `${h1.textContent.trim()} · Car Scanner`;
}

async function logout() {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* already gone */ }
  app.user = null; setCsrf(null); location.hash = "#/login";
}

export async function boot() {
  try {
    const me = await api("/auth/me");
    app.user = me.user; setCsrf(me.csrf);
  } catch { app.user = null; }
  window.addEventListener("hashchange", route);
  route();
}

export function signedIn(data) {
  app.user = data.user; setCsrf(data.csrf);
  toast(`Signed in as ${data.user.email}`);
  location.hash = "#/garage";
}

boot();
