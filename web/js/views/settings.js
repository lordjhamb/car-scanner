// Settings: profile, units, password, AI providers and API keys, permissions, memory, account.
import { api, guard, html, mount, on, setCsrf, toast } from "../lib.js";

const PERMS = [
  ["read_data", "Read car data", "Codes, live readings, freeze frames, past scans", ["always", "never"]],
  ["web_search", "Search the web", "Looks up codes, service bulletins and known issues", ["always", "never"]],
  ["start_tests", "Build guided tests", "Creates step-by-step plans you can start", ["ask", "never"]],
  ["clear_codes", "Clear codes", "Erases codes and freeze frames. Always asks you first.", ["ask", "never"]],
];
const LABEL = { always: "Always", ask: "Asks first", never: "Never" };

export async function view(root, _p, app) {
  const s = await api("/ai/settings");
  const u = app.user;
  mount(root, html`
    <h1>Settings</h1>
    <section class="card"><h2>Profile</h2>
      <form class="row g12 mt12" style="align-items:flex-end" data-profile>
        <label class="field grow">Name<input class="input" name="name" value="${u.name}" maxlength="80"></label>
        <label class="field">Distance units<select class="input" name="unit_pref" style="width:140px">
          <option value="mi" ${u.unit_pref === "mi" ? "selected" : ""}>Miles</option><option value="km" ${u.unit_pref === "km" ? "selected" : ""}>Kilometres</option></select></label>
        <button class="btn" type="submit">Save</button></form>
      <p class="small muted mt8">Signed in as ${u.email}</p></section>

    <section class="card"><h2>AI assistant</h2>
      <p class="small muted mt4">The assistant runs on your own API key. Each person using this app adds their own. Keys are encrypted on the server and never shown again.</p>
      <div class="stack g24 mt16">
        <form class="stack g12" data-key="claude">
          <div class="row g8"><h3 class="grow">Claude <span class="muted" style="font-weight:400">· Anthropic</span></h3>
            <span class="tiny" style="color:${s.has_anthropic_key ? "var(--green)" : "var(--faint)"}">${s.has_anthropic_key ? `Key saved ${s.anthropic_key}` : "Not set up"}</span></div>
          <div class="form-grid">
            <label class="field">API key<input class="input mono" type="password" name="key" autocomplete="off" placeholder="${s.has_anthropic_key ? "Leave empty to keep the saved key" : "sk-ant-…"}"></label>
            <label class="field">Model<select class="input" name="model">${s.claude_models.map((m) => html`<option ${m === s.claude_model ? "selected" : ""}>${m}</option>`)}</select></label>
          </div>
          <div class="row g8"><button class="btn" type="submit">Save</button><button class="btn" type="button" data-test="claude" ${s.has_anthropic_key ? "" : "disabled"}>Test</button>
            ${s.has_anthropic_key ? html`<button class="btn link" type="button" data-remove="claude">Remove key</button>` : ""}</div>
        </form>
        <form class="stack g12" data-key="openai">
          <div class="row g8"><h3 class="grow">Codex <span class="muted" style="font-weight:400">· OpenAI</span></h3>
            <span class="tiny" style="color:${s.has_openai_key ? "var(--green)" : "var(--faint)"}">${s.has_openai_key ? `Key saved ${s.openai_key}` : "Not set up"}</span></div>
          <div class="form-grid">
            <label class="field">API key<input class="input mono" type="password" name="key" autocomplete="off" placeholder="${s.has_openai_key ? "Leave empty to keep the saved key" : "sk-…"}"></label>
            <label class="field">Model<input class="input mono" name="model" value="${s.openai_model}" maxlength="80"></label>
          </div>
          <div class="row g8"><button class="btn" type="submit">Save</button><button class="btn" type="button" data-test="openai" ${s.has_openai_key ? "" : "disabled"}>Test</button>
            ${s.has_openai_key ? html`<button class="btn link" type="button" data-remove="openai">Remove key</button>` : ""}</div>
        </form>
        <fieldset style="border:0;padding:0;margin:0"><legend style="font-weight:600;padding:0">Default assistant</legend>
          <div class="seg mt8" role="radiogroup">${[["claude", "Claude"], ["openai", "Codex"]].map(([id, l]) =>
            html`<button type="button" role="radio" aria-checked="${s.provider === id}" data-default="${id}">${l}</button>`)}</div></fieldset>
      </div></section>

    <section class="card"><h2>What the assistant may do</h2>
      <p class="small muted mt4">It's limited to diagnosing cars. Anything that changes the car always needs your confirmation.</p>
      <table class="t mt8"><tbody>${PERMS.map(([k, l, d, opts]) => html`<tr><td><div style="font-weight:500">${l}</div><div class="small muted">${d}</div></td>
        <td style="text-align:right"><select class="input" data-perm="${k}" style="width:170px">${opts.map((o) => html`<option value="${o}" ${s.perms[k] === o ? "selected" : ""}>${LABEL[o]}</option>`)}</select></td></tr>`)}
      </tbody></table></section>

    <section class="card"><h2>Memory and privacy</h2>
      <label class="row g12 mt12" style="align-items:flex-start;cursor:pointer"><input type="checkbox" data-flag="memory_enabled" ${s.memory_enabled ? "checked" : ""} style="accent-color:var(--brand);margin-top:3px">
        <span><span style="display:block;font-weight:500">Remember facts about each car</span><span class="small muted">Repairs you mention, past codes. Stored with your account.</span></span></label>
      <label class="row g12 mt12" style="align-items:flex-start;cursor:pointer"><input type="checkbox" data-flag="hide_vin" ${s.hide_vin ? "checked" : ""} style="accent-color:var(--brand);margin-top:3px">
        <span><span style="display:block;font-weight:500">Hide the VIN from the assistant</span><span class="small muted">Sends make, model, year and engine only.</span></span></label>
      <button class="btn link mt12" data-forget-all style="color:var(--red)">Delete all memory</button></section>

    <section class="card"><h2>Password</h2>
      <form class="row g12 mt12" style="align-items:flex-end" data-password>
        <label class="field">Current password<input class="input" type="password" name="current" autocomplete="current-password" required></label>
        <label class="field">New password<input class="input" type="password" name="new" autocomplete="new-password" minlength="10" required></label>
        <button class="btn" type="submit">Change password</button></form>
      <p class="small muted mt8">Changing it signs you out everywhere else.</p></section>

    <section class="card" style="box-shadow:0 0 0 1px var(--red-bg)"><h2>Delete account</h2>
      <p class="small muted mt4">Deletes your account, garage, scans, photos, reports, conversations and saved API keys. This can't be undone.</p>
      <form class="row g12 mt12" style="align-items:flex-end" data-delete>
        <label class="field">Password<input class="input" type="password" name="password" autocomplete="current-password" required></label>
        <button class="btn danger" type="submit">Delete my account</button></form></section>`);

  const save = (body) => api("/ai/settings", { method: "PUT", body });
  const reload = () => view(root, _p, app);

  root.querySelector("[data-profile]").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    guard(async () => { app.user = (await api("/auth/me", { method: "PATCH", body: f })).user; toast("Saved"); }, e.target.querySelector("button"));
  });
  root.querySelectorAll("[data-key]").forEach((form) => form.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = form.dataset.key;
    const key = form.key.value.trim();
    const body = p === "claude" ? { claude_model: form.model.value } : { openai_model: form.model.value.trim() };
    if (key) body[p === "claude" ? "anthropic_key" : "openai_key"] = key;
    guard(async () => { await save(body); toast("Saved"); reload(); }, form.querySelector("[type=submit]"));
  }));
  on(root, "click", "[data-remove]", (e, b) => {
    if (!confirm("Remove this API key?")) return;
    guard(async () => { await save({ [b.dataset.remove === "claude" ? "anthropic_key" : "openai_key"]: "" }); reload(); }, b);
  });
  on(root, "click", "[data-test]", (e, b) => {
    guard(async () => { const r = await api("/ai/test", { method: "POST", body: { provider: b.dataset.test } }); toast(`Works · ${r.model}`); }, b, "Testing…");
  });
  on(root, "click", "[data-default]", (e, b) => guard(async () => { await save({ provider: b.dataset.default }); reload(); }, b));
  root.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.perm) guard(async () => { await save({ perms: { [t.dataset.perm]: t.value } }); toast("Saved"); });
    if (t.dataset.flag) guard(async () => { await save({ [t.dataset.flag]: t.checked }); toast("Saved"); });
  });
  on(root, "click", "[data-forget-all]", (e, b) => {
    if (!confirm("Delete everything the assistant remembers about all your cars?")) return;
    guard(async () => { await api("/memories", { method: "DELETE" }); toast("Memory deleted"); }, b);
  });
  root.querySelector("[data-password]").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    guard(async () => { await api("/auth/password", { method: "POST", body: f }); e.target.reset(); toast("Password changed"); }, e.target.querySelector("button"));
  });
  root.querySelector("[data-delete]").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!confirm("Delete your account and everything in it? This can't be undone.")) return;
    const f = Object.fromEntries(new FormData(e.target).entries());
    guard(async () => {
      await api("/auth/me", { method: "DELETE", body: f });
      app.user = null; setCsrf(null); location.hash = "#/login";
    }, e.target.querySelector("button"));
  });
}
