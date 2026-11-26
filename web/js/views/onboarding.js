// Onboarding: connect adapter -> confirm vehicle -> choose goal -> scanning.
import { api, carName, guard, html, KM_PER_MI, loader, mount, on, setLoader, sleep, toast } from "../lib.js";

const STEPS = ["Adapter", "Vehicle", "Goal"];
function topbar(current, right = "") {
  return html`<header class="topbar"><a class="logo" href="#/garage">Car Scanner</a>
    <ol class="steps" aria-label="Setup">${STEPS.map((s, i) => html`<li class="${i < current ? "done" : ""}"
      aria-current="${i === current ? "step" : "false"}">${i + 1} · ${s}</li>`)}</ol>
    <span class="right small muted">${right}</span></header>`;
}

// ------------------------------------------------------------------ 1. connect
export async function connect(root, _p, app) {
  let ports = [];
  try { ports = (await api("/adapter/ports")).ports; } catch (e) { toast(e.message, true); }
  const status = await app.refreshAdapter();
  const firstObd = ports.find((p) => p.likely_obd);
  mount(root, html`${topbar(0)}
  <main class="flow">
    <section class="main">
      <h1>Choose an adapter</h1>
      <p class="lead mt8">Plug it into the OBD port under the dashboard and turn the ignition on. The engine can stay off.</p>
      ${status.in_use_by_other ? html`<p class="note bad mt16">The adapter is in use by another account on this computer.</p>` : ""}
      <fieldset class="mt32" style="border:0;padding:0;margin-inline:0">
        <legend class="small muted" style="font-weight:500;margin-bottom:10px;padding:0">Found on this computer</legend>
        <div class="rlist">
          ${ports.map((p) => html`<label><input type="radio" name="port" value="${p.device}" ${p === firstObd ? "checked" : ""}>
            <span class="grow"><span class="t">${p.likely_obd ? "OBD adapter" : p.description || "Serial port"}</span>
            <span class="d mono">${p.device}</span></span>${p.likely_obd ? html`<span class="tag ok">Likely</span>` : ""}</label>`)}
          <label><input type="radio" name="port" value="__demo" ${!firstObd ? "checked" : ""}>
            <span class="grow"><span class="t">Demo car</span><span class="d">A simulated 2013 pickup. Try everything without a car.</span></span>
            <select class="input" name="scenario" style="width:auto;height:34px" aria-label="Demo scenario">
              <option value="genuine">Genuine mileage</option><option value="rollback">Rolled-back mileage</option></select></label>
        </div>
      </fieldset>
      ${!ports.length ? html`<p class="small muted mt12">No serial ports found. On macOS the vLinker FS appears as
        <span class="mono">/dev/cu.usbserial-…</span>; install the FTDI driver if it doesn't.</p>` : ""}
      <div class="row g16 mt32"><button class="btn primary lg arrow" data-connect>Connect </button>
        <a class="small" href="#/garage">Back to the garage</a></div>
    </section>
    <aside class="side panel" aria-live="polite">
      <h2 class="small muted" style="font-weight:500">Connection</h2>
      <ol class="stack mt16" style="list-style:none;margin:0;padding:0" data-chain>
        ${["This computer", "Adapter", "OBD-II port", "Engine computer"].map((n) => html`<li class="row g12" style="padding:10px 0;border-top:1px solid var(--line)">
          <span class="dot" style="background:var(--line-strong)"></span><span class="grow">${n}</span><span class="small faint">waiting</span></li>`)}
      </ol>
      <p class="small muted mt12">Read-only until you choose to clear codes.</p>
    </aside>
  </main>`);
  const chain = root.querySelector("[data-chain]");
  const mark = (i, text, ok = true) => {
    const li = chain.children[i];
    li.querySelector(".dot").style.background = ok ? "var(--green)" : "var(--amber)";
    li.lastElementChild.textContent = text;
    li.lastElementChild.style.color = ok ? "var(--green)" : "var(--amber)";
  };
  on(root, "click", "[data-connect]", (e, btn) => {
    const choice = root.querySelector("input[name=port]:checked")?.value;
    if (!choice) { toast("Choose an adapter.", true); return; }
    const demo = choice === "__demo";
    guard(async () => {
      mark(0, "ready");
      const info = await api("/adapter/connect", { method: "POST", body: demo
        ? { simulate: true, scenario: root.querySelector("[name=scenario]").value } : { port: choice } });
      mark(1, info.identity || "responding"); mark(2, info.voltage ? `${info.voltage} V` : "powered");
      mark(3, info.protocol_name || "answering");
      app.flow = { info };
      await sleep(400);
      app.go("#/new/vehicle");
    }, btn, "Connecting… (up to 15 s)");
  });
}

// ------------------------------------------------------------------ 2. vehicle
export async function vehicle(root, _p, app) {
  const st = await app.refreshAdapter();
  if (!st.connected) { app.go("#/new/connect"); return; }
  const info = st.info;
  const vin = info.vin || "";
  const decoded = vin ? await api(`/vin/${encodeURIComponent(vin)}`) : null;
  const { cars } = await api("/cars");
  const known = cars.find((c) => c.vin === vin);
  const unit = app.unit();
  const pre = known || { make: decoded?.make || "", year: decoded?.year || "", model: "", engine: "" };
  mount(root, html`${topbar(1, info.simulated ? "Demo car" : info.port)}
  <main class="flow">
    <section class="main">
      <p class="small muted">${vin ? "Read from the engine computer" : "This car didn't report its VIN"}</p>
      <h1 class="mt4">${known ? carName(known) : vin ? (decoded?.make ? `${decoded.year || ""} ${decoded.make}` : "Is this your car?") : "Enter the vehicle"}</h1>
      ${known ? html`<p class="lead mt8">In your garage · ${known.scan_count} earlier scan${known.scan_count === 1 ? "" : "s"}.</p>` : ""}
      <form class="mt24 stack g16" novalidate data-form>
        <label class="field">VIN<input class="input mono" name="vin" value="${vin}" maxlength="17" required ${vin ? "readonly" : ""}></label>
        <div class="form-grid">
          <label class="field">Make<input class="input" name="make" value="${pre.make}" maxlength="60"></label>
          <label class="field">Model<input class="input" name="model" value="${pre.model}" maxlength="60" placeholder="e.g. F-150"></label>
          <label class="field">Year<input class="input" name="year" value="${pre.year || ""}" inputmode="numeric" maxlength="4"></label>
          <label class="field">Engine<input class="input" name="engine" value="${pre.engine}" maxlength="80" placeholder="e.g. 3.5 L V6"></label>
        </div>
        <div>
          <label class="field" for="odo">Odometer reading <span class="hint">What the dashboard shows. Used for the mileage check.</span></label>
          <div class="row g8 mt8" style="max-width:280px">
            <input class="input mono grow" id="odo" name="odo" inputmode="numeric" placeholder="e.g. 92,140">
            <select class="input" name="unit" style="width:80px" aria-label="Unit"><option ${unit === "mi" ? "selected" : ""}>mi</option><option ${unit === "km" ? "selected" : ""}>km</option></select>
          </div>
        </div>
        <div class="row g16 mt16"><button class="btn primary lg arrow" type="submit">Continue to goal </button>
          <a class="small" href="#/new/connect">Back</a></div>
      </form>
    </section>
    ${decoded?.valid ? html`<aside class="side panel">
      <h2 class="small muted" style="font-weight:500">What the VIN says</h2>
      <div class="row g8 mt16" style="align-items:flex-start">${decoded.parts.map((p) => html`<div class="stack g8">
        <div class="row" style="gap:2px">${[...p.text].map((ch) => html`<span class="mono" style="width:20px;height:32px;display:flex;align-items:center;justify-content:center;background:#fff;border-radius:4px;font-size:16px;font-weight:500;color:${p.label === "Year" ? "var(--brand-ink)" : "var(--ink)"}">${ch}</span>`)}</div>
        <span style="height:2px;border-radius:2px;background:${p.label === "Year" ? "var(--brand)" : "var(--ink)"}"></span></div>`)}</div>
      <dl class="mt24" style="display:grid;grid-template-columns:auto 1fr;column-gap:16px;margin-bottom:0">${decoded.parts.map((p) => html`
        <dt class="mono small" style="padding:8px 0;border-top:1px solid var(--line);font-weight:500">${p.text}</dt>
        <dd style="margin:0;padding:8px 0;border-top:1px solid var(--line)"><span class="muted">${p.label}</span> · ${p.meaning}</dd>`)}</dl>
      ${decoded.check_digit_ok === false ? html`<p class="note mt12">The VIN check digit doesn't match. Compare it with the plate on the car.</p>` : ""}
    </aside>` : ""}
  </main>`);
  root.querySelector("[data-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    const odo = Number(String(f.odo || "").replace(/[^\d.]/g, ""));
    guard(async () => {
      const { car } = await api("/cars", { method: "POST", body: {
        vin: f.vin, make: f.make, model: f.model, engine: f.engine, year: f.year ? Number(f.year) : null } });
      app.carId = car.id; app.car = car;
      app.flow.odometer = odo ? { reading: odo, unit: f.unit, km: f.unit === "mi" ? odo * KM_PER_MI : odo } : null;
      app.go("#/new/goal");
    }, e.target.querySelector("[type=submit]"));
  });
}

// ------------------------------------------------------------------ 3. goal
const GOALS = [
  ["Buying or receiving a car", [
    ["inspection", "Inspect a used car", "Every module, cleared codes, mileage check, test drive", "3–4 min scan", "Start inspection",
      ["Scan every module", "Read current and past codes", "Verify the mileage", "Guided test drive", "Photograph the car"]],
    ["delivery", "Check a new car on delivery", "No stored faults, every module answering", "3 min", "Start delivery check",
      ["Scan every module", "Read stored codes", "Check the dash warning lights"]]]],
  ["Your own car", [
    ["fix", "Fix a problem with the car", "A warning light, noise, vibration, smell, anything", "5–30 min", "Describe the problem",
      ["Describe it in your own words", "AI reads the car and looks it up", "Follow a plan made for it"]],
    ["health", "Routine health check", "Codes, battery and charging, idle", "2 min", "Run health check",
      ["Read codes", "Battery and charging", "Idle quality"]],
    ["emissions", "Check it will pass an emissions test", "Readiness monitors and check-engine status", "30 s", "Check readiness",
      ["Check-engine status", "Readiness monitors"]]]],
  ["Tools", [
    ["live", "Live sensor data", "RPM, temperatures, fuel trims", "ongoing", "Open live data", ["Stream live readings"]],
    ["expert", "Expert report", "Every code and module, nothing pre-run", "manual", "Open the report", ["Open the full report"]]]],
];

export async function goal(root, _p, app) {
  const car = await app.refreshCar();
  if (!car) { app.go("#/new/connect"); return; }
  const all = GOALS.flatMap(([, g]) => g);
  const preview = (id) => {
    const g = all.find((x) => x[0] === id);
    return html`<p class="small muted" style="font-weight:500">What happens next</p>
      <h2 class="mt4" style="font-size:19px">${g[1]}</h2>
      <ol class="mt16" style="list-style:none;margin:0;padding:0">${g[5].map((s, i) => html`<li class="row g12" style="padding:10px 0;border-top:1px solid var(--line)">
        <span class="mono tiny faint" style="width:14px">${i + 1}</span><span>${s}</span></li>`)}</ol>
      <button class="btn primary lg block mt24 arrow" data-start="${id}">${g[4]} </button>
      <p class="tiny faint mt8" style="text-align:center">Read-only. Nothing on the car changes.</p>`;
  };
  mount(root, html`${topbar(2, carName(car))}
  <main class="flow">
    <section class="main">
      <h1>What do you need to find out?</h1>
      <p class="lead mt8">This sets which checks run and what you see first. Every tool stays available.</p>
      ${GOALS.map(([name, items]) => html`<fieldset class="mt24" style="border:0;padding:0;margin-inline:0">
        <legend class="small muted" style="font-weight:500;margin-bottom:10px;padding:0">${name}</legend>
        <div class="rlist">${items.map((g) => html`<label><input type="radio" name="goal" value="${g[0]}" ${g[0] === "inspection" ? "checked" : ""}>
          <span class="grow"><span class="t">${g[1]}</span><span class="d">${g[2]}</span></span><span class="mono tiny faint">${g[3]}</span></label>`)}</div>
      </fieldset>`)}
    </section>
    <aside class="side panel" style="position:sticky;top:16px" aria-live="polite" data-preview>${preview("inspection")}</aside>
  </main>`);
  const prev = root.querySelector("[data-preview]");
  root.addEventListener("change", (e) => { if (e.target.name === "goal") mount(prev, preview(e.target.value)); });
  on(root, "click", "[data-start]", (e, b) => {
    const id = b.dataset.start;
    if (id === "fix") app.go("#/fix/describe");
    else if (id === "live") app.go(`#/car/${car.id}/live`);
    else if (id === "expert") app.go(`#/car/${car.id}`);
    else app.go(`#/scanning/${id}`);
  });
}

// ------------------------------------------------------------------ scanning
export async function scanning(root, [goalId], app) {
  const car = await app.refreshCar();
  const st = await app.refreshAdapter();
  if (!car || !st.connected) { app.go("#/new/connect"); return; }
  let stopped = false;
  mount(root, html`<header class="topbar"><a class="logo" href="#/garage">Car Scanner</a>
    <span class="muted">${carName(car)}</span><span class="right small muted"><span class="dot"></span> ${st.info.protocol_name}</span></header>
  <main style="max-width:760px;margin:0 auto;padding:40px 24px 64px">
    <h1 style="text-align:center">Scanning the car</h1>
    <p class="lead mt8" style="text-align:center;margin-inline:auto">Keep the ignition on and the adapter plugged in.</p>
    <div class="card mt32" style="padding:28px 36px" data-loader>${loader({ title: "Reading engine and emissions codes" })}</div>
    <div class="mt16" data-mods style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:32px"></div>
    <p class="row g12 mt24" style="justify-content:center"><button class="btn" data-stop>Stop scan</button></p>
  </main>`);
  const L = root.querySelector("[data-loader]");
  root.querySelector("[data-stop]").addEventListener("click", async () => {
    stopped = true; try { await api("/adapter/scan/cancel", { method: "POST" }); } catch { /* not running */ }
    app.go(`#/car/${car.id}`);
  });
  try {
    setLoader(L, { progress: 8, detail: "Mode 03, 07 and 0A from the engine and transmission" });
    await api(`/cars/${car.id}/scans/obd`, { method: "POST", body: { odometer_km: app.flow.odometer?.km ?? null } });
    if (goalId !== "emissions") {
      await api(`/cars/${car.id}/scans/modules`, { method: "POST", body: { full: goalId === "inspection" } });
      setLoader(L, { title: "Reading every module" });
      for (;;) {
        if (stopped) return;
        await sleep(500);
        const s = await api("/adapter/scan");
        const pct = s.total ? (s.done / s.total) * 100 : 0;
        setLoader(L, { progress: 10 + pct * 0.85, right: `${s.found || 0} found · ${Math.round(pct)} %`,
          detail: s.running ? `Checking address ${s.current || "…"}` : "Finished" });
        const mods = s.result || [];
        mount(root.querySelector("[data-mods]"), mods.map((m) => html`<div class="row g12" style="padding:8px 0;border-top:1px solid var(--line)">
          <span class="grow">${m.name} <span class="mono tiny faint">${m.request_id}</span></span>
          <span class="small">${m.dtcs.length ? `${m.dtcs.length} code${m.dtcs.length > 1 ? "s" : ""}` : "no codes"}</span></div>`));
        if (!s.running) { if (s.error) throw new Error(s.error); break; }
      }
    }
    setLoader(L, { progress: 100, title: "Scan saved", detail: "Opening the guided tests…" });
    const r = await api("/runs", { method: "POST", body: { goal: goalId, car_id: car.id } });
    await sleep(500);
    if (!stopped) app.go(`#/run/${r.id}`);
  } catch (e) {
    if (!stopped) { toast(e.message, true); setLoader(L, { title: "The scan stopped", detail: e.message }); }
  }
  return () => { stopped = true; };
}
