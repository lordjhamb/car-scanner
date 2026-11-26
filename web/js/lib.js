// Shared helpers: escaped templating, API client, toast, units, loader animation.

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(String(s));

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

function render(v) {
  if (v === null || v === undefined || v === false) return "";
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(render).join("");
  return esc(String(v));
}

/** Tagged template: every interpolation is HTML-escaped unless wrapped in raw() / html``. */
export function html(strings, ...vals) {
  let out = "";
  strings.forEach((s, i) => { out += s; if (i < vals.length) out += render(vals[i]); });
  return new Raw(out);
}

export function mount(el, view) { el.innerHTML = view instanceof Raw ? view.s : render(view); }

/** Delegated event listener: on(root, "click", "[data-act]", (e, target) => ...) */
export function on(root, type, selector, fn) {
  root.addEventListener(type, (e) => {
    const t = e.target.closest(selector);
    if (t && root.contains(t)) fn(e, t);
  });
}

// ------------------------------------------------------------------ API
let csrf = null;
export const setCsrf = (t) => { csrf = t; };

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = { "X-Requested-With": "CarScanner" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const opts = { method, headers, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch("/api" + path, opts); }
  catch { throw new ApiError(0, "Can't reach the Car Scanner server. Is it running?"); }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    if (res.status === 401 && path !== "/auth/me" && !path.startsWith("/auth/")) location.hash = "#/login";
    throw new ApiError(res.status, msg || `Request failed (${res.status}).`);
  }
  return data;
}

// ------------------------------------------------------------------ toast
let toastTimer;
export function toast(msg, bad = false) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = bad ? "bad" : ""; t.style.display = "block";
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.style.display = "none"; }, 5000);
}

/** Run an async action with a busy button (optional busy label) and a toast on failure. */
export async function guard(fn, btn, busyText) {
  const label = btn && btn.textContent;
  if (btn) { btn.disabled = true; if (busyText) btn.textContent = busyText; }
  try { return await fn(); }
  catch (e) { toast(e.message || String(e), true); return undefined; }
  finally { if (btn) { btn.disabled = false; btn.textContent = label; } }
}

// ------------------------------------------------------------------ units
export const KM_PER_MI = 1.609344;
export function dist(km, unit, digits = 0) {
  if (km === null || km === undefined || isNaN(km)) return "—";
  const v = unit === "mi" ? km / KM_PER_MI : km;
  return `${v.toLocaleString(undefined, { maximumFractionDigits: digits })} ${unit}`;
}
export function when(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
export const carName = (c) => c ? [c.year, c.make, c.model].filter(Boolean).join(" ") || c.vin : "No car";

// ------------------------------------------------------------------ loader
const WIRES = [
  ["Engine", "M352 75 C392 80 418 110 452 110", 452, 110], ["ABS", "M352 70 C392 62 420 54 452 52", 452, 52],
  ["Trans.", "M352 75 C370 110 380 130 398 140", 398, 140], ["Body", "M345 70 C330 62 312 58 292 56", 292, 56],
  ["Airbag", "M346 76 C320 92 280 110 246 110", 246, 110], ["Gateway", "M348 77 C330 120 312 150 292 164", 292, 164],
  ["Rear", "M345 72 C260 66 120 62 62 110", 62, 110],
];

/** Animated top-down car with current flowing from the OBD port to each module, plus a progress bar. */
export function loader({ title = "Working…", detail = "", right = "", busy = false } = {}) {
  const dur = busy ? 1.6 : 2.4;
  const wires = WIRES.map(([name, d, x, y], i) => {
    const delay = (-(i * dur / WIRES.length)).toFixed(2) + "s";
    const ly = y < 100 ? y - 12 : y + 20;
    return html`<path d="${d}" fill="none" stroke="#E3E1DB" stroke-width="1.6" stroke-linecap="round"></path>
      <path class="cs-flow" d="${d}" pathLength="100" fill="none" stroke="#D9480F" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="9 91" style="animation-delay:${delay}"></path>
      <circle class="cs-glow" cx="${x}" cy="${y}" r="13" fill="rgba(217,72,15,0.18)" style="animation-delay:${delay}"></circle>
      <circle class="cs-node" cx="${x}" cy="${y}" r="6" fill="#fff" stroke="#191816" stroke-width="1.5" style="animation-delay:${delay}"></circle>
      <text x="${x}" y="${ly}" text-anchor="middle" font-size="10" fill="#75736C">${name}</text>`;
  });
  return html`<div class="loader" style="--cs-dur:${dur}s; max-width:640px; margin:0 auto">
    <svg viewBox="0 0 520 220" width="100%" role="img" aria-label="${title}" style="display:block;overflow:visible">
      <g fill="#E3E1DB"><rect x="88" y="20" width="58" height="14" rx="5"></rect><rect x="88" y="186" width="58" height="14" rx="5"></rect><rect x="372" y="20" width="58" height="14" rx="5"></rect><rect x="372" y="186" width="58" height="14" rx="5"></rect></g>
      <path d="M64 32 H392 C470 32 506 64 508 110 C506 156 470 188 392 188 H64 C34 188 16 162 16 110 C16 58 34 32 64 32 Z" fill="#fff" stroke="#CBC8C0" stroke-width="1.5"></path>
      <path d="M150 52 H338 C356 52 366 70 368 110 C366 150 356 168 338 168 H150 C138 168 132 150 132 110 C132 70 138 52 150 52 Z" fill="#FAF9F7" stroke="#E3E1DB" stroke-width="1.2"></path>
      ${wires}
      <rect class="cs-port" x="345" y="65" width="14" height="10" rx="2" fill="#191816"></rect>
      <text x="352" y="92" text-anchor="middle" font-size="10" font-weight="600" fill="#191816">OBD</text>
    </svg>
    <div class="mt16">
      <div class="row g12" style="justify-content:space-between"><span data-l="title" style="font-weight:500;font-size:15px">${title}</span><span data-l="right" class="mono tiny muted">${right}</span></div>
      <div class="bar mt8" role="progressbar" aria-label="${title}" aria-valuemin="0" aria-valuemax="100"><span data-l="bar" style="width:4%;overflow:hidden"><span class="cs-sweep"></span></span></div>
      <div data-l="detail" class="small muted mt8">${detail}</div>
    </div></div>`;
}

export function setLoader(root, { title, detail, right, progress }) {
  const q = (k) => root.querySelector(`[data-l="${k}"]`);
  if (title !== undefined && q("title")) q("title").textContent = title;
  if (detail !== undefined && q("detail")) q("detail").textContent = detail;
  if (right !== undefined && q("right")) q("right").textContent = right;
  if (progress !== undefined && q("bar")) {
    q("bar").style.width = Math.max(3, Math.min(100, progress)) + "%";
    q("bar").parentElement.setAttribute("aria-valuenow", Math.round(progress));
  }
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Only http(s) links from AI/web content; anything else (javascript:, data:) becomes inert. */
export const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? String(u) : "#");
