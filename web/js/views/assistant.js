// AI assistant: conversations scoped to the current car, with tool log, sources, plans and approvals.
import { api, carName, guard, html, loader, mount, on, safeUrl, toast } from "../lib.js";

function message(m, carId) {
  if (m.role === "user") return html`<div class="bubble">${m.text}</div>`;
  const meta = m.meta || {};
  return html`<div class="stack g12">
    <div class="small muted">${meta.provider === "openai" ? "Codex" : "Claude"} · ${meta.model || ""}</div>
    ${meta.tool_log?.length ? html`<ul class="toollog">${meta.tool_log.map((t) => html`<li class="${t.ok ? "" : "fail"}"><span style="color:var(--ink)">${t.label}</span> ${t.detail}</li>`)}</ul>` : ""}
    <div class="answer">${m.text}</div>
    ${meta.sources?.length ? html`<div class="row g8">${meta.sources.slice(0, 8).map((s, i) => html`<a class="source" href="${safeUrl(s.url)}" target="_blank" rel="noopener noreferrer">${i + 1} · ${s.title}</a>`)}</div>` : ""}
    ${meta.plan_run_id ? html`<div class="card" style="padding:14px 16px;box-shadow:inset 0 0 0 1px var(--line)">
      <div class="tiny faint">Guided plan built for this conversation</div>
      <div class="row g12 mt4"><b class="grow">${meta.plan?.title || "Guided plan"}</b><span class="small muted">${meta.plan?.steps?.length || ""} checks</span></div>
      <div class="row g8 mt12"><a class="btn primary sm" href="#/fix/plan/${meta.plan_run_id}">Review and start</a></div></div>` : ""}
    ${meta.pending_action_ids?.length ? html`<p class="note">The assistant wants to clear codes. Nothing has been cleared yet.
      <a href="#/car/${carId}">Review and approve on the report</a>.</p>` : ""}
  </div>`;
}

export async function view(root, [convId], app) {
  const [settings, car] = [await api("/ai/settings"), await app.refreshCar()];
  const carId = car?.id || null;
  const { conversations } = await api(`/conversations${carId ? `?car_id=${carId}` : ""}`);
  let conv = null;
  if (convId) conv = await api(`/conversations/${convId}`);
  let provider = settings.provider;
  const keyFor = (p) => (p === "claude" ? settings.has_anthropic_key : settings.has_openai_key);
  const memories = carId ? (await api(`/cars/${carId}/memories`)).memories : [];

  const draw = () => mount(root, html`
    <div class="row g12"><h1 class="grow">${conv ? conv.title : "Assistant"}</h1>
      <div class="seg" role="radiogroup" aria-label="Model">${[["claude", "Claude"], ["openai", "Codex"]].map(([id, l]) =>
        html`<button role="radio" aria-checked="${provider === id}" data-provider="${id}">${l}</button>`)}</div>
      <button class="btn sm" data-new>New conversation</button></div>
    ${!keyFor(provider) ? html`<p class="note">Add your ${provider === "claude" ? "Anthropic" : "OpenAI"} API key in <a href="#/settings">Settings</a> to use ${provider === "claude" ? "Claude" : "Codex"}.</p>` : ""}
    <div class="row g16" style="align-items:flex-start">
      <section class="card stack" style="flex:999 1 520px;min-width:0;min-height:520px;padding:0">
        <div class="stack g24" style="padding:24px;flex:1" data-thread>
          ${conv?.messages?.length ? conv.messages.map((m) => message(m, carId))
            : html`<div class="muted" style="max-width:56ch">Ask about ${car ? carName(car) : "your car"}: a code, a noise, a warning light, or what to check before buying.
              The assistant reads this car's data, searches the web for known issues, and can build a step-by-step plan.</div>`}
        </div>
        <form data-form style="padding:14px 18px 18px;border-top:1px solid var(--line-soft)">
          <label for="msg" class="sr">Message</label>
          <textarea id="msg" class="input" rows="2" maxlength="8000" placeholder="Ask about this car, a code, or a noise" style="resize:none"></textarea>
          <div class="row g8 mt8"><span class="tiny faint grow">Uses your own ${provider === "claude" ? "Anthropic" : "OpenAI"} key. Limited to diagnosing cars. Confirm safety-critical repairs with a mechanic.</span>
            <button class="btn primary" type="submit">Send</button></div>
        </form>
      </section>
      <aside class="card stack g24" style="flex:1 1 260px">
        <section><h2 class="small muted" style="font-weight:500">What it can see</h2>
          <p class="mt8">${car ? carName(car) : "No car selected"}</p>
          <p class="small muted">${app.adapter.connected ? "Live data from the adapter" : "Saved scans only (adapter not connected)"}</p></section>
        <section><h2 class="small muted" style="font-weight:500">Remembers about this car</h2>
          ${memories.length ? html`<ul class="mt8 stack g4" style="padding-left:16px;margin-bottom:0">${memories.map((m) => html`<li>${m.fact}
            <button class="btn link tiny" data-forget="${m.id}" aria-label="Forget: ${m.fact}">Forget</button></li>`)}</ul>` : html`<p class="small faint mt8">Nothing yet.</p>`}</section>
        <section><h2 class="small muted" style="font-weight:500">Allowed to</h2>
          <table class="t mt4"><tbody>${Object.entries({ read_data: "Read car data", web_search: "Search the web", start_tests: "Build guided tests", clear_codes: "Clear codes" }).map(([k, l]) =>
            html`<tr><td>${l}</td><td style="text-align:right" class="small">${settings.perms[k] === "ask" ? "Asks first" : settings.perms[k] === "always" ? "Always" : "Never"}</td></tr>`)}</tbody></table>
          <a class="small" href="#/settings">Change in settings</a></section>
        ${conversations.length ? html`<section><h2 class="small muted" style="font-weight:500">Conversations</h2>
          <ul class="mt8 stack g4" style="list-style:none;padding:0;margin:0">${conversations.map((c) => html`<li><a href="#/assistant/${c.id}" style="font-weight:${conv?.id === c.id ? 600 : 400}">${c.title}</a></li>`)}</ul></section>` : ""}
      </aside>
    </div>`);
  draw();

  on(root, "click", "[data-provider]", (e, b) => { provider = b.dataset.provider; draw(); });
  on(root, "click", "[data-new]", (e, b) => guard(async () => {
    const c = await api("/conversations", { method: "POST", body: { car_id: carId } });
    app.go(`#/assistant/${c.id}`);
  }, b));
  on(root, "click", "[data-forget]", (e, b) => guard(async () => {
    await api(`/memories/${b.dataset.forget}`, { method: "DELETE" }); view(root, [convId], app);
  }, b));
  root.addEventListener("keydown", (e) => {
    if (e.target.id === "msg" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.target.form.requestSubmit(); }
  });
  root.addEventListener("submit", (e) => {
    if (!e.target.matches("[data-form]")) return;
    e.preventDefault();
    const ta = e.target.querySelector("textarea");
    const text = ta.value.trim();
    if (!text) return;
    if (!keyFor(provider)) { toast("Add an API key in Settings first.", true); return; }
    const btn = e.target.querySelector("[type=submit]");
    guard(async () => {
      if (!conv) conv = { ...(await api("/conversations", { method: "POST", body: { car_id: carId } })), messages: [] };
      conv.messages.push({ role: "user", text });
      ta.value = "";
      draw();
      const thread = root.querySelector("[data-thread]");
      const wait = document.createElement("div");
      mount(wait, loader({ title: "Thinking", detail: "Reading the car and searching", busy: true }));
      thread.appendChild(wait);
      wait.scrollIntoView({ behavior: "smooth", block: "end" });
      try {
        const reply = await api(`/conversations/${conv.id}/messages`, { method: "POST", body: { text, provider } });
        conv.messages.push(reply);
        if (location.hash !== `#/assistant/${conv.id}`) history.replaceState(null, "", `#/assistant/${conv.id}`);
      } finally { draw(); }
    }, btn);
  });
}
