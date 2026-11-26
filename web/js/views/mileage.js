// Mileage verification.
import { api, carName, dist, guard, html, KM_PER_MI, mount, on, toast } from "../lib.js";

const RESULT = { agrees: ["var(--green)", "Agrees"], conflicts: ["var(--red)", "Conflicts"], neutral: ["var(--muted)", "Neutral"], note: ["var(--amber)", "Note"] };

function line(r, unit) {
  const entered = r.entered_km;
  const marks = [
    ...r.module_readings.map((m) => ({ km: m.km, kind: "m", name: `${m.name}: ${dist(m.km, unit)}` })),
    ...r.stamps.map((s) => ({ km: s.km, kind: "c", name: `${s.code} (${s.module_name}): ${dist(s.km, unit)}` })),
    ...r.past_readings.map((p) => ({ km: p.km, kind: "s", name: `Scan ${p.date}: ${dist(p.km, unit)}` })),
    ...r.service_records.map((p) => ({ km: p.km, kind: "s", name: `Service ${p.date}: ${dist(p.km, unit)}` })),
  ];
  if (!marks.length) return "";
  const all = [entered, ...marks.map((m) => m.km)];
  const lo = Math.min(...all) * 0.97, hi = Math.max(...all) * 1.02;
  const pct = (km) => `${((km - lo) / (hi - lo)) * 100}%`;
  const fill = (m) => m.km > entered + Math.max(80, entered * 0.004) ? "var(--red)" : { m: "var(--ink)", c: "var(--faint)", s: "var(--brand)" }[m.kind];
  return html`<section class="card"><div class="row g12" style="justify-content:space-between"><h2>Every mileage the car remembers</h2>
    <span class="small muted">Nothing should sit to the right of the dashboard reading</span></div>
    <div role="img" aria-label="Stored mileages compared with the dashboard reading" style="position:relative;height:150px;margin-top:18px">
      <span style="position:absolute;left:0;right:0;top:76px;height:2px;background:var(--line)"></span>
      <span style="position:absolute;left:${pct(entered)};top:10px;bottom:30px;border-left:2px solid var(--ink)"></span>
      <span class="small" style="position:absolute;left:${pct(entered)};top:-6px;transform:translateX(-50%);font-weight:600;background:#fff;padding:0 4px;white-space:nowrap">Dashboard ${dist(entered, unit)}</span>
      ${marks.map((m, i) => html`<span title="${m.name}" style="position:absolute;left:${pct(m.km)};top:${m.kind === "m" ? 48 : m.kind === "s" ? 30 : 90 + (i % 2) * 14}px;width:12px;height:12px;margin-left:-6px;border-radius:${m.kind === "m" ? "50%" : "2px"};background:${fill(m)};box-shadow:0 0 0 2px #fff"></span>`)}
      <div class="row g16 tiny muted" style="position:absolute;bottom:0;left:0;right:0">
        <span class="row g8"><span style="width:10px;height:10px;border-radius:50%;background:var(--ink)"></span>Module reading</span>
        <span class="row g8"><span style="width:10px;height:10px;border-radius:2px;background:var(--faint)"></span>Fault-code stamp</span>
        <span class="row g8"><span style="width:10px;height:10px;border-radius:2px;background:var(--brand)"></span>Past scan or service</span>
        <span class="row g8"><span style="width:10px;height:10px;border-radius:50%;background:var(--red)"></span>Higher than the dashboard</span></div>
    </div></section>`;
}

function result(r, unit) {
  const tone = r.band === "High" ? "var(--green)" : r.band === "Low" ? "var(--red)" : "var(--amber)";
  return html`<section class="card"><div class="row g32" style="align-items:flex-end">
    <div class="grow" style="flex:1 1 380px"><h1>${r.headline}</h1>
      <p class="lead mt8">${r.verdict === "rolled_back" ? `Stored records point to about ${dist(r.estimated_km, unit)}. Roughly ${r.missing_display} appear to be missing.`
        : r.verdict === "genuine" ? "Every stored mileage is consistent with your reading." : "Some evidence is missing or doesn't line up. See below."}</p>
      ${r.used_live_data ? "" : html`<p class="note info small mt12">The adapter wasn't connected, so the car's own records weren't read. Connect and check again for a stronger result.</p>`}</div>
    ${r.score !== null ? html`<div style="flex:0 1 300px;min-width:240px"><div class="small muted">Confidence the mileage is genuine</div>
      <div class="row g8" style="align-items:baseline"><span style="font-size:34px;font-weight:600;color:${tone}">${r.score}</span><span class="muted">/ 100 · <b style="color:${tone}">${r.band}</b></span></div>
      <div style="position:relative;height:8px;margin-top:10px;border-radius:99px;background:linear-gradient(90deg,#F3D3CF 0 40%,#F4E3CC 40% 70%,#D7E8D9 70% 100%)">
        <span style="position:absolute;top:-5px;left:${r.score}%;width:3px;height:18px;margin-left:-1.5px;background:var(--ink);border-radius:2px"></span></div>
      <div class="row tiny faint mt4" style="justify-content:space-between"><span>Low</span><span>Medium</span><span>High</span></div></div>` : ""}
  </div></section>
  ${line(r, unit)}
  <section class="card"><h2>Evidence</h2><p class="small muted">Strong evidence counts most. One strong conflict caps the score at Low.</p>
    <div class="scroll-x"><table class="t mt8" style="min-width:640px"><thead><tr><th>Check</th><th>What we found</th><th>Weight</th><th style="text-align:right">Result</th></tr></thead><tbody>
    ${r.evidence.map((e) => html`<tr><th scope="row">${e.check}<div class="tiny faint" style="font-weight:400">${e.source}</div></th><td class="muted">${e.finding}</td>
      <td class="small muted">${e.weight}</td><td style="text-align:right;font-weight:500;color:${RESULT[e.result][0]}">${RESULT[e.result][1]}</td></tr>`)}</tbody></table></div></section>`;
}

export async function view(root, [carId], app) {
  const { car, reports } = await api(`/cars/${carId}`);
  const { records } = await api(`/cars/${carId}/service-records`);
  const st = await app.refreshAdapter();
  const unit = app.unit();
  const last = reports.mileage;
  const preset = app.flow.odometer && app.carId === Number(carId) ? app.flow.odometer : null;
  mount(root, html`
    <div><p class="small muted">${carName(car)}</p><h1 class="mt4">Mileage check</h1>
      <p class="lead mt8">Type what the dashboard shows. We compare it with the mileage stored in other modules, stamped on past fault codes, in earlier scans and service records, and with wear in your photos.</p></div>
    <form class="card row g12" style="align-items:flex-end" data-form>
      <label class="field">Odometer reading<input class="input mono" name="reading" inputmode="numeric" required style="width:180px"
        value="${preset ? preset.reading : last ? last.entered : ""}"></label>
      <label class="field">Unit<select class="input" name="unit" style="width:90px">
        <option ${(preset?.unit || last?.unit || unit) === "mi" ? "selected" : ""}>mi</option><option ${(preset?.unit || last?.unit || unit) === "km" ? "selected" : ""}>km</option></select></label>
      <span class="small muted grow">${st.connected ? "The adapter is connected: the car's own records will be read." : "Adapter not connected: only saved records will be used."}</span>
      <button class="btn primary lg" type="submit">Check the mileage</button>
    </form>
    <div data-result class="stack g16">${last ? result(last, last.unit || unit) : ""}</div>
    <section class="card"><h2>Service records</h2><p class="small muted">Stickers, invoices and inspection reports with a date and mileage make the check stronger.</p>
      ${records.length ? html`<table class="t mt8"><tbody>${records.map((r) => html`<tr><td class="small" style="width:140px">${r.date}</td><td class="num" style="text-align:left">${dist(r.km, unit)}</td><td class="muted">${r.note}</td></tr>`)}</tbody></table>` : ""}
      <form class="row g12 mt12" style="align-items:flex-end" data-service>
        <label class="field">Date<input class="input" type="date" name="date" required></label>
        <label class="field">Mileage<input class="input mono" name="km" inputmode="numeric" required style="width:140px"></label>
        <label class="field grow">Note<input class="input" name="note" maxlength="200" placeholder="e.g. Oil change sticker"></label>
        <button class="btn" type="submit">Add record</button></form></section>`);
  root.querySelector("[data-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    const reading = Number(String(f.reading).replace(/[^\d.]/g, ""));
    if (!reading) { toast("Enter the odometer reading.", true); return; }
    const out = root.querySelector("[data-result]");
    const btn = e.target.querySelector("[type=submit]");
    guard(async () => {
      const r = await api(`/cars/${carId}/mileage`, { method: "POST", body: { reading, unit: f.unit } });
      mount(out, result(r, f.unit));
    }, btn, "Checking…");
  });
  root.querySelector("[data-service]").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    const n = Number(String(f.km).replace(/[^\d.]/g, ""));
    guard(async () => {
      await api(`/cars/${carId}/service-records`, { method: "POST", body: { date: f.date, km: unit === "mi" ? n * KM_PER_MI : n, note: f.note } });
      toast("Record added"); view(root, [carId], app);
    }, e.target.querySelector("[type=submit]"));
  });
}
