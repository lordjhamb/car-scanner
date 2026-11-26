// Photo inspection: guided shot list, uploads, AI condition report.
import { api, carName, guard, html, loader, mount, on, setLoader, toast } from "../lib.js";

export async function capture(root, [carId], app) {
  const [{ slots }, { car }] = await Promise.all([api("/photos/slots"), api(`/cars/${carId}`)]);
  let { photos, coverage } = await api(`/cars/${carId}/photos`);
  const input = document.createElement("input");
  input.type = "file"; input.accept = "image/jpeg,image/png,image/webp"; input.className = "hidden";
  let targetSlot = null;

  const groups = [];
  for (const s of slots) {
    const g = groups.find((x) => x[0] === s.group);
    g ? g[1].push(s) : groups.push([s.group, [s]]);
  }

  const draw = () => {
    const bySlot = Object.fromEntries(photos.map((p) => [p.slot, p]));
    mount(root, html`
      <div class="row g24" style="align-items:flex-end"><div class="grow"><p class="small muted">${carName(car)}</p><h1 class="mt4">Photograph the car</h1>
        <p class="lead mt8">The AI looks for repairs, damage, rust, leaks and wear, and checks the wear against the mileage. Daylight works best.</p></div>
        <div><div class="small muted">${coverage.done} of ${coverage.total} photos</div>
          <div class="row mt8" style="gap:2px;width:260px">${slots.map((s) => html`<span style="flex:1;height:6px;border-radius:2px;background:${bySlot[s.slot] ? "var(--green)" : "var(--line)"}"></span>`)}</div></div></div>
      <section class="card row g16" data-drop style="outline:1.5px dashed var(--line-strong);outline-offset:-6px">
        <div class="grow"><div style="font-weight:500">Drop photos here, in any order</div>
          <div class="small muted">They're added as unsorted; the AI places them when it analyses. Or click a slot below to add a specific shot.</div></div>
        <label class="btn">Choose photos<input type="file" accept="image/jpeg,image/png,image/webp" multiple class="hidden" data-multi></label>
      </section>
      <div class="row g16" style="align-items:flex-start">
        <div class="stack g16" style="flex:999 1 560px;min-width:0">${groups.map(([name, items]) => html`<section class="card">
          <div class="row" style="justify-content:space-between"><h2 style="font-size:15px">${name}</h2>
            <span class="tiny faint">${items.filter((s) => bySlot[s.slot]).length} of ${items.length}</span></div>
          <div class="slots mt12">${items.map((s) => {
            const p = bySlot[s.slot];
            return html`<button class="slot ${p ? "filled" : ""}" data-slot="${s.slot}" aria-label="${p ? "Replace" : "Add"} photo: ${s.name}">
              <span class="img">${p ? html`<img src="${p.url}" alt="" loading="lazy">` : "+"}</span>
              <span class="n">${s.name}</span><span class="h">${p ? "Added · click to replace" : s.how}</span></button>`;
          })}</div></section>`)}
          ${coverage.unsorted ? html`<p class="small muted">${coverage.unsorted} unsorted photo(s) will also be analysed.</p>` : ""}
        </div>
        <aside class="card" style="flex:1 1 300px;position:sticky;top:16px;box-shadow:var(--shadow-up)" data-side>
          <h2>The AI still needs</h2><p class="small muted mt4">The report is less reliable without these.</p>
          <ol style="list-style:none;margin:8px 0 0;padding:0">${coverage.missing.slice(0, 8).map((m) => html`<li style="padding:9px 0;border-top:1px solid var(--line-soft)">
            <div style="font-weight:500">${m.name}</div><div class="small muted">${m.why}</div></li>`)}</ol>
          ${coverage.missing.length > 8 ? html`<p class="small muted">and ${coverage.missing.length - 8} more</p>` : ""}
          <button class="btn primary block lg mt16" data-analyse ${photos.length ? "" : "disabled"}>${coverage.missing.length ? `Analyse the ${photos.length} photos so far` : "Analyse all photos"}</button>
          <p class="tiny faint mt8" style="text-align:center">Photos are stored on this server, with location data removed, and sent to your AI provider only for analysis.</p>
        </aside>
      </div>`);
  };

  async function upload(files, slot) {
    for (const f of files) {
      const form = new FormData();
      form.append("slot", slot); form.append("file", f);
      const r = await api(`/cars/${carId}/photos`, { method: "POST", form });
      coverage = r.coverage;
    }
    ({ photos, coverage } = await api(`/cars/${carId}/photos`));
    draw();
  }

  draw();
  root.appendChild(input);
  input.addEventListener("change", () => {
    if (input.files.length && targetSlot) guard(() => upload([input.files[0]], targetSlot).then(() => toast("Photo added")));
    input.value = "";
  });
  on(root, "click", "[data-slot]", (e, b) => { targetSlot = b.dataset.slot; input.click(); });
  root.addEventListener("change", (e) => {
    if (e.target.matches("[data-multi]") && e.target.files.length) {
      const files = [...e.target.files];
      guard(() => upload(files, "unsorted").then(() => toast(`${files.length} photo(s) added`)));
    }
  });
  root.addEventListener("dragover", (e) => { if (e.target.closest("[data-drop]")) e.preventDefault(); });
  root.addEventListener("drop", (e) => {
    if (!e.target.closest("[data-drop]")) return;
    e.preventDefault();
    const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
    if (files.length) guard(() => upload(files, "unsorted").then(() => toast(`${files.length} photo(s) added`)));
  });
  on(root, "click", "[data-analyse]", (e, b) => {
    const side = root.querySelector("[data-side]");
    mount(side, loader({ title: "Analysing photos", detail: "Looking for repairs, rust, leaks and wear", busy: true }));
    let p = 5;
    const t = setInterval(() => { p = Math.min(92, p + 2); setLoader(side, { progress: p }); }, 600);
    const odoKm = app.flow.odometer?.km ?? null;
    api(`/cars/${carId}/photos/analyze`, { method: "POST", body: { odometer_km: odoKm } })
      .then(() => app.go(`#/car/${carId}/photos/report`))
      .catch((err) => { toast(err.message, true); draw(); })
      .finally(() => clearInterval(t));
  });
}

const KIND = { repair_sign: ["var(--amber)", "Repair sign"], damage: ["var(--red)", "Damage"], rust: ["var(--amber)", "Rust"],
  leak: ["var(--amber)", "Possible leak"], wear: ["var(--muted)", "Normal wear"], warning_light: ["var(--red)", "Warning light"], ok: ["var(--green)", "OK"] };

export async function report(root, [carId], app) {
  const [{ report: r }, { car }, { photos }] = await Promise.all([api(`/cars/${carId}/photos/report`), api(`/cars/${carId}`), api(`/cars/${carId}/photos`)]);
  if (!r) { app.go(`#/car/${carId}/photos`); return; }
  const bySlot = Object.fromEntries(photos.map((p) => [p.slot, p]));
  const word = (n) => ["", "Poor", "Needs work", "Fair", "Good", "Very good"][n];
  const tone = (n) => (n <= 2 ? "var(--red)" : n === 3 ? "var(--amber)" : "var(--green)");
  mount(root, html`
    <div class="row g12"><div class="grow"><p class="small muted">${carName(car)} · ${r.photo_count} photos analysed</p><h1 class="mt4">${r.headline}</h1></div>
      <a class="btn" href="#/car/${carId}/photos">Add photos</a><button class="btn" data-print>Print / save PDF</button></div>
    <section class="card"><p class="lead" style="color:var(--ink-2)">${r.summary}</p>
      <table class="t mt16"><tbody>${r.areas.map((a) => html`<tr><th scope="row" style="white-space:nowrap">${a.name}</th>
        <td style="white-space:nowrap"><span role="img" aria-label="${a.rating} out of 5" class="row" style="display:inline-flex;gap:3px">${[1, 2, 3, 4, 5].map((i) =>
          html`<span style="width:8px;height:8px;border-radius:50%;background:${i <= a.rating ? tone(a.rating) : "var(--line)"}"></span>`)}</span>
          <span class="small" style="margin-left:8px;font-weight:500;color:${tone(a.rating)}">${word(a.rating)}</span></td>
        <td class="muted">${a.note}</td></tr>`)}</tbody></table></section>
    <section class="card"><h2>Findings</h2>${r.findings.map((f, i) => html`<article class="row g16" style="align-items:flex-start;padding:16px 0;border-top:1px solid var(--line-soft)">
      <div style="flex:none;width:132px;aspect-ratio:4/3;border-radius:8px;background:var(--sunken);overflow:hidden;display:flex;align-items:center;justify-content:center">
        ${bySlot[f.slot] ? html`<img src="${bySlot[f.slot].url}" alt="${f.title}" style="width:100%;height:100%;object-fit:cover">` : html`<span class="tiny faint">${i + 1}</span>`}</div>
      <div class="grow" style="min-width:240px"><div class="row g8" style="align-items:baseline"><h3>${f.title}</h3>
        <span class="tiny" style="font-weight:500;color:${KIND[f.kind]?.[0]}">${KIND[f.kind]?.[1] || f.kind}</span><span class="tiny faint">· ${f.confidence} confidence</span></div>
        <p class="mt4" style="color:var(--ink-2);max-width:70ch">${f.what}</p>
        ${f.next ? html`<p class="small muted mt4"><b style="color:var(--ink);font-weight:500">Next:</b> ${f.next}</p>` : ""}</div></article>`)}</section>
    ${r.missing?.length || r.retakes?.length ? html`<section class="panel row g16"><div class="grow">
      ${r.missing?.length ? html`<p><b>Couldn't check ${r.missing.length} area(s):</b> <span class="muted">${r.missing.slice(0, 6).join(", ")}${r.missing.length > 6 ? "…" : ""}</span></p>` : ""}
      ${r.retakes?.length ? html`<p class="mt8"><b>Retake:</b> <span class="muted">${r.retakes.map((x) => `${x.slot} (${x.reason})`).join("; ")}</span></p>` : ""}</div>
      <a class="btn" href="#/car/${carId}/photos">Add the missing photos</a></section>` : ""}`);
  on(root, "click", "[data-print]", () => window.print());
}
