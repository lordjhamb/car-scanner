// Fix a problem: describe it -> AI analyses -> review the plan.
import { api, carName, guard, html, loader, mount, on, safeUrl, setLoader, toast } from "../lib.js";

const WHEN = ["Over bumps", "Turning", "Braking", "Accelerating", "At highway speed", "When cold", "At idle", "All the time"];
function topbar(current, car) {
  return html`<header class="topbar"><a class="logo" href="#/garage">Car Scanner</a>
    <ol class="steps" aria-label="Fix a problem">${["Describe", "Analyse", "Guided checks"].map((s, i) =>
      html`<li class="${i < current ? "done" : ""}" aria-current="${i === current ? "step" : "false"}">${i + 1} · ${s}</li>`)}</ol>
    <span class="right small muted">${carName(car)} · <a href="#/new/goal" class="muted">change goal</a></span></header>`;
}

export async function describe(root, _p, app) {
  const car = await app.refreshCar();
  if (!car) { app.go("#/new/connect"); return; }
  const picked = new Set(app.flow.when || []);
  mount(root, html`${topbar(0, car)}
  <main class="flow">
    <form class="main" data-form novalidate>
      <h1>What's wrong with the car?</h1>
      <p class="lead mt8">Describe it the way you'd tell a mechanic. A light, a noise, a vibration, a smell, something electrical: anything counts.</p>
      <label class="field mt32" for="problem">The problem</label>
      <textarea class="input mt8" id="problem" name="problem" rows="5" minlength="5" maxlength="2000" required
        placeholder="e.g. Clunk from the front when I go over bumps, mostly on the driver's side.">${app.flow.problem || ""}</textarea>
      <fieldset class="mt24" style="border:0;padding:0;margin-inline:0">
        <legend style="font-weight:500;padding:0">When does it happen? <span class="hint">optional</span></legend>
        <div class="row g8 mt8">${WHEN.map((w) => html`<button type="button" class="chip" aria-pressed="${picked.has(w)}" data-when="${w}">${w}</button>`)}</div>
      </fieldset>
      <div class="row g16 mt32"><button class="btn primary lg arrow" type="submit">Analyse the problem </button>
        <span class="small faint">Uses your AI key · <a href="#/settings" class="muted">settings</a></span></div>
    </form>
    <aside class="side panel">
      <h2 class="small muted" style="font-weight:500">What happens next</h2>
      <ol class="mt16" style="list-style:none;margin:0;padding:0">${[
        ["The AI reads the car", "Codes from every module, live data and this car's scan history."],
        ["It looks up known issues", "Searches the web for this model, engine and symptom."],
        ["You get a step-by-step plan", "Checks the app reads, plus checks you do by hand. Skip any you can't do."]].map(([t, d], i) =>
        html`<li class="row g12" style="align-items:flex-start;padding:12px 0;border-top:1px solid var(--line)"><span class="mono tiny faint" style="width:14px;padding-top:2px">${i + 1}</span>
          <span><span style="display:block;font-weight:500">${t}</span><span class="small muted">${d}</span></span></li>`)}</ol>
    </aside>
  </main>`);
  on(root, "click", "[data-when]", (e, b) => {
    const w = b.dataset.when;
    picked.has(w) ? picked.delete(w) : picked.add(w);
    b.setAttribute("aria-pressed", picked.has(w));
  });
  root.querySelector("[data-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const problem = e.target.problem.value.trim();
    if (problem.length < 5) { toast("Describe the problem in a few words.", true); return; }
    app.flow.problem = problem; app.flow.when = [...picked];
    app.go("#/fix/thinking");
  });
}

const PHASES = [
  [0, "Reading the car", "Codes from every module"], [18, "Checking this car's history", "Past scans and related faults"],
  [38, "Searching for known issues", "This make, model and engine"], [66, "Ranking likely causes", "Against your description"],
  [84, "Building your plan", "Steps you can do and steps the app reads"],
];

export async function thinking(root, _p, app) {
  const car = await app.refreshCar();
  if (!car || !app.flow.problem) { app.go("#/fix/describe"); return; }
  mount(root, html`${topbar(1, car)}
  <main class="flow">
    <section class="main card" style="max-width:640px;padding:28px 36px">
      <h1 style="font-size:24px">The AI is working on your problem</h1>
      <div class="mt24" data-loader>${loader({ title: PHASES[0][1], busy: true })}</div>
    </section>
    <aside class="side"><blockquote class="muted" style="margin:0">“${app.flow.problem}”</blockquote>
      <ol class="mt16" style="list-style:none;margin:0;padding:0" data-phases>${PHASES.map(([, t, d]) => html`<li class="row g12" style="align-items:flex-start;padding:11px 0;border-top:1px solid var(--line);color:var(--ghost)">
        <span class="dot" style="background:var(--line-strong);margin-top:7px"></span><span><span style="display:block;font-weight:500">${t}</span><span class="small">${d}</span></span></li>`)}</ol>
      <p class="tiny faint mt16">This usually takes 20–60 seconds.</p></aside>
  </main>`);
  const L = root.querySelector("[data-loader]");
  const items = root.querySelectorAll("[data-phases] li");
  let p = 0, alive = true;
  const tick = setInterval(() => {
    p = Math.min(95, p + (p < 60 ? 1.2 : 0.4));
    let cur = 0; PHASES.forEach(([at], i) => { if (p >= at) cur = i; });
    setLoader(L, { progress: p, title: PHASES[cur][1], detail: PHASES[cur][2], right: `step ${cur + 1} of 5` });
    items.forEach((li, i) => {
      li.style.color = i <= cur ? "var(--ink)" : "var(--ghost)";
      li.querySelector(".dot").style.background = i < cur ? "var(--green)" : i === cur ? "var(--brand)" : "var(--line-strong)";
    });
  }, 300);
  try {
    const run = await api("/runs/ai", { method: "POST", body: { car_id: car.id, problem: app.flow.problem, when: app.flow.when || [] } });
    if (alive) { setLoader(L, { progress: 100, title: "Plan ready" }); app.go(`#/fix/plan/${run.id}`); }
  } catch (e) {
    if (alive) mount(root.querySelector("main .main"), html`<h1 style="font-size:24px">The AI couldn't build a plan</h1>
      <p class="note bad mt16">${e.message}</p>
      <div class="row g12 mt24"><a class="btn primary" href="#/fix/describe">Edit the description</a><a class="btn" href="#/settings">Check AI settings</a></div>`);
  } finally { clearInterval(tick); }
  return () => { alive = false; clearInterval(tick); };
}

export async function plan(root, [runId], app) {
  const run = await api(`/runs/${runId}`);
  const car = await app.refreshCar();
  const P = run.plan;
  const skip = new Set();
  const render = () => html`${topbar(1, car)}
  <main class="flow" style="max-width:1240px">
    <section style="flex:999 1 520px;min-width:0" class="stack g16">
      ${app.flow.problem ? html`<blockquote class="muted" style="margin:0">“${app.flow.problem}” <a href="#/fix/describe" class="tiny faint">Edit</a></blockquote>` : ""}
      <h1>${P.title}</h1>
      ${P.summary ? html`<p class="lead">${P.summary}</p>` : ""}
      ${P.causes.length ? html`<div class="card"><h2>Possible causes</h2>
        ${P.causes.map((c) => html`<div style="display:grid;grid-template-columns:100px 1fr;gap:16px;padding:12px 0;border-top:1px solid var(--line-soft)">
          <span class="tiny" style="font-weight:500;color:${c.fit === "Most likely" ? "var(--ink)" : "var(--muted)"}">${c.fit}</span>
          <span><span style="display:block;font-weight:500">${c.title}</span><span class="muted">${c.why}</span></span></div>`)}
        ${P.sources.length ? html`<div class="row g8 mt8">${P.sources.map((s, i) => html`<a class="source" href="${safeUrl(s.url)}" target="_blank" rel="noopener noreferrer">${i + 1} · ${s.title}</a>`)}</div>` : ""}
      </div>` : ""}
    </section>
    <section class="card" style="flex:1 1 400px;box-shadow:var(--shadow-up)">
      <div class="row g8" style="justify-content:space-between"><h2 style="font-size:19px">Your guided plan</h2>
        <span class="small muted">${P.steps.length - skip.size} checks</span></div>
      ${P.needs.length ? html`<p class="small muted mt4">You'll need: ${P.needs.join(", ")}</p>` : ""}
      ${phases(P.steps).map(([name, steps]) => html`<div class="mt12"><div class="tiny faint" style="font-weight:500">${name}</div>
        ${steps.map((s) => html`<div class="row g8" style="padding:7px 0;border-top:1px solid var(--line-soft);opacity:${skip.has(s.id) ? 0.5 : 1}">
          <span class="grow" style="text-decoration:${skip.has(s.id) ? "line-through" : "none"}">${s.title}</span>
          <span class="tiny" style="font-weight:500;color:${s.kind === "auto" ? "var(--green)" : "var(--muted)"}">${s.kind === "auto" ? "app reads" : "you check"}</span>
          <button class="btn link tiny" data-toggle="${s.id}" aria-label="${skip.has(s.id) ? "Add back" : "Skip"}: ${s.title}">${skip.has(s.id) ? "Add" : "Skip"}</button></div>`)}</div>`)}
      <button class="btn primary lg block mt24 arrow" data-go>Start guided checks </button>
      <p class="tiny faint mt8">Made by AI from your description, this car's data and web sources. It can be wrong. Stop if anything feels unsafe.</p>
    </section>
  </main>`;
  const draw = () => mount(root, render());
  draw();
  on(root, "click", "[data-toggle]", (e, b) => { const id = b.dataset.toggle; skip.has(id) ? skip.delete(id) : skip.add(id); draw(); });
  on(root, "click", "[data-go]", (e, b) => guard(async () => {
    await api(`/runs/${runId}/start`, { method: "POST", body: { skip_steps: [...skip] } });
    app.go(`#/run/${runId}`);
  }, b));
}

export function phases(steps) {
  const out = [];
  for (const s of steps) {
    const last = out[out.length - 1];
    if (last && last[0] === s.phase) last[1].push(s); else out.push([s.phase, [s]]);
  }
  return out;
}
