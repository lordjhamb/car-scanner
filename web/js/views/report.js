// Car report (summary of saved scans, runs, mileage and photo reports) and live data.
import { api, carName, dist, guard, html, mount, on, toast, when } from "../lib.js";

const STATE_TAG = { ACTIVE: ["bad", "Active"], PENDING: ["warn", "Pending"], HISTORY: ["neutral", "History"],
  PERMANENT: ["warn", "Permanent"], STORED: ["bad", "Stored"] };

function collect(scans) {
  const obd = scans.find((s) => s.kind === "obd");
  const mods = scans.find((s) => s.kind === "modules");
  const codes = [];
  if (mods) for (const m of mods.data.modules || []) for (const d of m.dtcs) {
    codes.push({ code: d.full, state: d.state, desc: d.description, module: m.name });
  }
  if (obd) {
    for (const [key, state] of [["stored", "STORED"], ["pending", "PENDING"], ["permanent", "PERMANENT"]]) {
      for (const c of obd.data[key] || []) {
        if (!codes.some((x) => x.code.startsWith(c.code))) codes.push({ code: c.code, state, desc: c.description, module: c.ecu });
      }
    }
  }
  return { obd, mods, codes };
}

function checklist(c, obd, mods, reports, unit) {
  const rows = [];
  const active = c.codes.filter((x) => x.state === "ACTIVE" || x.state === "STORED").length;
  const pending = c.codes.filter((x) => x.state === "PENDING").length;
  if (obd || mods) rows.push(["Fault codes", `${active} active or stored, ${pending} pending, ${c.codes.length - active - pending} past or permanent`,
    active ? ["bad", "Attention"] : pending ? ["warn", "Check"] : ["ok", "Pass"]]);
  const stats = obd?.data.vehicle?.stats || [];
  const since = stats.find((s) => s.pid === "31");
  if (since) rows.push(["Codes last cleared", `${dist(since.value, unit)} ago`, since.value < 1500 ? ["warn", "Ask why"] : ["ok", "Normal"]]);
  const mon = obd?.data.vehicle?.monitors?.[0];
  if (mon) {
    const inc = mon.monitors.filter((m) => !m.complete);
    rows.push(["Emissions readiness", `${mon.monitors.length - inc.length} of ${mon.monitors.length} complete${inc.length ? `. Not ready: ${inc.map((m) => m.name).join(", ")}` : ""}. Check-engine light ${mon.mil_on ? "on" : "off"}.`,
      inc.length || mon.mil_on ? ["warn", "Not ready"] : ["ok", "Pass"]]);
  }
  const v = obd?.data.vehicle?.voltage;
  if (v) rows.push(["Battery and charging", `${v} V`, v < 12.2 ? ["warn", "Low"] : ["ok", "Pass"]]);
  const m = reports.mileage;
  if (m) rows.push(["Mileage", m.score === null ? m.headline : `Confidence ${m.score}/100. ${m.headline}.`,
    m.band === "High" ? ["ok", "Pass"] : m.band === "Low" ? ["bad", "Fail"] : ["warn", "Uncertain"]]);
  const p = reports.photos;
  if (p) rows.push(["Condition (photos)", p.headline, (p.areas || []).some((a) => a.rating <= 2) ? ["bad", "Attention"] : ["ok", "Reviewed"]]);
  return rows;
}

export async function view(root, [carId], app) {
  const data = await api(`/cars/${carId}`);
  const { car, scans, reports, runs } = data;
  const unit = app.unit();
  const c = collect(scans);
  const { actions } = await api("/actions");
  const mine = actions.filter((a) => a.car_id === car.id);
  let filter = "all";
  const rows = checklist(c, c.obd, c.mods, reports, unit);
  const codeRows = () => c.codes.filter((x) => filter === "all" || x.state === filter);
  const runsDone = [];
  for (const r of runs.slice(0, 5)) runsDone.push(await api(`/runs/${r.id}`));

  const draw = () => mount(root, html`
    <div class="row g12">
      <div class="grow"><p class="small muted">${scans.length ? `Last scan ${when(scans[0].created_at)}` : "No scans yet"} · <span class="mono">${car.vin}</span></p>
        <h1 class="mt4">${car.nickname || carName(car)}</h1></div>
      <a class="btn" href="#/new/connect">Scan again</a><button class="btn" data-print>Print / save PDF</button>
    </div>
    ${mine.length ? html`<section class="card" style="box-shadow:0 0 0 1.5px var(--amber)">
      <h2>Waiting for your approval</h2>
      ${mine.map((a) => html`<div class="row g12 mt12" style="align-items:flex-end">
        <p class="grow">The assistant asked to clear codes (${a.payload.target === "obd" ? "engine and emissions" : `module ${a.payload.target}`}). ${a.payload.reason || ""}
          <span class="small muted">This erases the codes and freeze frames and resets readiness monitors.</span></p>
        <label class="field small">Type CLEAR<input class="input" data-confirm="${a.id}" style="width:120px"></label>
        <button class="btn danger" data-approve="${a.id}">Clear codes</button><button class="btn" data-decline="${a.id}">Don't clear</button></div>`)}
    </section>` : ""}
    ${rows.length ? html`<section class="card"><h2>Checklist</h2><div class="scroll-x"><table class="t mt8"><tbody>
      ${rows.map(([k, r, [cls, label]]) => html`<tr><th scope="row" style="width:24%">${k}</th><td class="muted">${r}</td>
        <td style="text-align:right;width:130px"><span class="tag ${cls}">${label}</span></td></tr>`)}</tbody></table></div></section>` : ""}
    <div class="row g16" style="align-items:stretch">
      <a class="card grow" style="flex:1 1 220px;text-decoration:none" href="#/car/${car.id}/mileage"><h2>Mileage check</h2>
        <p class="small muted mt4">${reports.mileage ? `${reports.mileage.band} · ${reports.mileage.score ?? "—"}/100` : "Verify the odometer against the car's own records."}</p></a>
      <a class="card grow" style="flex:1 1 220px;text-decoration:none" href="#/car/${car.id}/photos"><h2>Photo inspection</h2>
        <p class="small muted mt4">${reports.photos ? reports.photos.headline : "Photograph the car for a condition report."}</p></a>
      <a class="card grow" style="flex:1 1 220px;text-decoration:none" href="#/assistant"><h2>Ask the assistant</h2>
        <p class="small muted mt4">Claude or Codex, with this car's data.</p></a>
    </div>
    ${c.codes.length ? html`<section class="card"><div class="row g16"><h2 class="grow">Fault codes</h2>
      <div class="seg" role="tablist" aria-label="Filter codes">${[["all", "All"], ["ACTIVE", "Active"], ["PENDING", "Pending"], ["HISTORY", "History"]].map(([id, l]) =>
        html`<button role="tab" aria-selected="${filter === id}" data-filter="${id}">${l} <span class="mono tiny faint">${id === "all" ? c.codes.length : c.codes.filter((x) => x.state === id).length}</span></button>`)}</div></div>
      <div class="scroll-x"><table class="t mt8" style="min-width:640px"><thead><tr><th>Code</th><th>State</th><th>Meaning</th><th>Module</th></tr></thead><tbody>
      ${codeRows().map((x) => html`<tr><td class="mono" style="font-weight:500">${x.code}</td><td><span class="tag ${STATE_TAG[x.state]?.[0] || "neutral"}">${STATE_TAG[x.state]?.[1] || x.state}</span></td>
        <td>${x.desc}</td><td class="muted">${x.module}</td></tr>`)}</tbody></table></div>
      <p class="small faint mt12">Active: failing now. Pending: seen once, not confirmed. History: happened before, stored, not failing now.</p></section>` : ""}
    ${c.mods ? html`<section class="card"><h2>Modules</h2><p class="small muted">${c.mods.data.modules.length} answered</p>
      <table class="t mt8"><thead><tr><th>Module</th><th>Address</th><th>Protocol</th><th style="text-align:right">Codes</th></tr></thead><tbody>
      ${c.mods.data.modules.map((m) => html`<tr><td>${m.name}${m.error ? html`<div class="tiny faint">${m.error}</div>` : ""}</td><td class="mono small muted">${m.request_id}</td>
        <td class="small muted">${m.protocol}</td><td class="num">${m.dtcs.length || "none"}</td></tr>`)}</tbody></table></section>` : ""}
    ${c.obd?.data.freeze_frame?.dtc ? html`<section class="card"><h2>Freeze frame for ${c.obd.data.freeze_frame.dtc}</h2>
      <p class="small muted">What the engine recorded when the code was set.</p>
      <table class="t mt8"><tbody>${c.obd.data.freeze_frame.values.map((v) => html`<tr><th scope="row" style="font-weight:400">${v.name}</th><td class="num">${v.value} ${v.unit}</td></tr>`)}</tbody></table></section>` : ""}
    ${runsDone.length ? html`<section class="card"><h2>Guided tests</h2>${runsDone.map((r) => html`<div class="mt12">
      <div class="row g8"><a href="#/run/${r.id}" style="font-weight:500">${r.plan.title}</a><span class="small muted">${when(r.created_at)} · ${r.progress.done} done, ${r.progress.skipped} skipped</span></div>
      <table class="t mt4"><tbody>${r.plan.steps.filter((s) => r.results[s.id]).map((s) => {
        const res = r.results[s.id];
        return html`<tr><th scope="row" style="font-weight:400;width:40%">${s.short}</th><td class="small muted">${res.status === "skipped" ? "Skipped" : res.answer || (res.readings || []).map((x) => `${x.name} ${x.value} ${x.unit}`).join(" · ") || "Done"}</td></tr>`;
      })}</tbody></table></div>`)}</section>` : ""}
    ${app.adapter.connected ? html`<section class="card"><h2>Clear codes</h2>
      <p class="note mt8">Clearing turns off warning lights, erases freeze frames and resets readiness monitors; a car can fail an inspection until they finish. Faults that are still present come back.</p>
      <div class="row g12 mt12" style="align-items:flex-end"><label class="field grow">What to clear<select class="input" data-target>
        <option value="obd">Engine and emission codes (all brands)</option>
        ${(c.mods?.data.modules || []).filter((m) => m.protocol === "UDS").map((m) => html`<option value="${m.request_id}">Module: ${m.name}</option>`)}</select></label>
        <label class="field">Type CLEAR<input class="input" data-clear-confirm style="width:120px"></label>
        <button class="btn danger" data-clear>Clear</button></div></section>` : ""}
    <section class="card"><h2>Scan history</h2><table class="t mt8"><tbody>
      ${scans.map((s) => html`<tr><td class="small muted" style="width:200px">${when(s.created_at)}</td><td>${{ obd: "Engine and emissions codes", modules: "All-module scan", clear: "Codes cleared" }[s.kind] || s.kind}</td>
        <td class="num">${dist(s.odometer_km, unit)}</td></tr>`)}</tbody></table></section>`);
  draw();
  on(root, "click", "[data-filter]", (e, b) => { filter = b.dataset.filter; draw(); });
  on(root, "click", "[data-print]", () => window.print());
  on(root, "click", "[data-clear]", (e, b) => {
    const confirmText = root.querySelector("[data-clear-confirm]").value;
    if (confirmText !== "CLEAR") { toast("Type CLEAR to confirm.", true); return; }
    guard(async () => {
      const res = await api(`/cars/${car.id}/clear`, { method: "POST", body: { target: root.querySelector("[data-target]").value, confirm: confirmText } });
      toast(res.ok ? "Codes cleared. Scan again to confirm nothing comes back." : `Not cleared: ${res.error || "no response"}`, !res.ok);
    }, b);
  });
  on(root, "click", "[data-approve]", (e, b) => {
    const id = b.dataset.approve;
    const confirmText = root.querySelector(`[data-confirm="${id}"]`).value;
    guard(async () => {
      await api(`/actions/${id}/approve`, { method: "POST", body: { confirm: confirmText } });
      toast("Codes cleared."); view(root, [carId], app);
    }, b);
  });
  on(root, "click", "[data-decline]", (e, b) => guard(async () => {
    await api(`/actions/${b.dataset.decline}/decline`, { method: "POST" }); view(root, [carId], app);
  }, b));
}

export async function live(root, [carId], app) {
  const st = await app.refreshAdapter();
  mount(root, html`<div class="row g12"><h1 class="grow">Live data</h1><span class="small muted" data-rate></span></div>
    ${st.connected ? html`<section class="card"><div data-grid style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px">
      <p class="muted">Reading…</p></div></section>`
      : html`<p class="note info">Connect the adapter to see live data. <a href="#/new/connect">Connect</a></p>`}`);
  if (!st.connected) return null;
  let alive = true;
  const grid = root.querySelector("[data-grid]");
  (async function loop() {
    while (alive) {
      const t0 = performance.now();
      try {
        const r = await api("/adapter/live");
        if (!alive) break;
        mount(grid, r.values.map((v) => html`<div style="padding:14px;border-radius:12px;box-shadow:inset 0 0 0 1px var(--line)">
          <div class="small muted">${v.name}</div><div style="font-size:26px;font-weight:600;margin-top:4px">${v.value} <span class="small muted" style="font-weight:400">${v.unit}</span></div></div>`));
        root.querySelector("[data-rate]").textContent = `${Math.round(performance.now() - t0)} ms per refresh`;
      } catch (e) { root.querySelector("[data-rate]").textContent = e.message; }
      await new Promise((r) => setTimeout(r, 500));
    }
  })();
  return () => { alive = false; };
}
