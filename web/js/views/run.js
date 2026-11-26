// Guided run: one step at a time, with live readings, a hold timer for targets, skip and back.
import { api, carName, guard, html, mount, on, toast } from "../lib.js";
import { phases } from "./fix.js";

export async function view(root, [runId], app) {
  let run = await api(`/runs/${runId}`);
  const car = await app.refreshCar();
  let idx = run.progress.next_index ?? run.plan.steps.length;
  let answer = null, poll = null, held = 0, lastSample = null;

  const stopPoll = () => { if (poll) clearInterval(poll); poll = null; };

  function stepList() {
    return phases(run.plan.steps).map(([name, steps]) => html`<div class="mt16"><div class="tiny faint" style="font-weight:500;margin-left:8px">${name}</div>
      <ol class="steplist" style="list-style:none;margin:6px 0 0;padding:0">${steps.map((s) => {
        const i = run.plan.steps.indexOf(s);
        const st = run.results[s.id]?.status;
        return html`<li class="${st || ""}"><button data-goto="${i}" aria-current="${i === idx ? "step" : "false"}">
          <span class="mark" aria-hidden="true">${st === "done" ? "✓" : ""}</span><span class="grow">${s.short}</span>
          ${st === "skipped" ? html`<span class="tiny faint">skipped</span>` : ""}</button></li>`;
      })}</ol></div>`);
  }

  function readings(values) {
    if (!values?.length) return html`<p class="small faint mt12">${app.adapter.connected ? "Waiting for readings…" : "Connect the adapter to read live values."}</p>`;
    return html`<table class="t mt8"><tbody>${values.map((v) => html`<tr><th scope="row" class="muted" style="font-weight:400">${v.name}</th>
      <td class="num">${v.value} ${v.unit}</td></tr>`)}</tbody></table>`;
  }

  function meter(step, sample) {
    const t = step.target;
    const v = sample?.values?.find((x) => x.pid === t.pid)?.value;
    const span = Math.max(t.max * 1.6, t.max + 10);
    const pct = (x) => `${Math.max(0, Math.min(100, (x / span) * 100))}%`;
    return html`<div class="mt12"><div class="row g8" style="align-items:baseline"><span style="font-size:40px;font-weight:600;letter-spacing:-.02em;line-height:1">${v ?? "—"}</span>
      <span class="muted">${t.label || `target ${t.min}–${t.max}`}</span></div>
      <div class="meter"><span class="band" style="left:${pct(t.min)};width:calc(${pct(t.max)} - ${pct(t.min)})"></span>
        ${v !== undefined ? html`<span class="needle" style="left:${pct(v)}"></span>` : ""}</div>
      <div class="row g12 mt12"><div class="bar brand grow"><span style="width:${Math.min(100, (held / t.hold_s) * 100)}%"></span></div>
        <span class="small muted">Held ${Math.min(held, t.hold_s)} of ${t.hold_s} s</span></div></div>`;
  }

  function stepView() {
    if (idx >= run.plan.steps.length) {
      const p = run.progress;
      return html`<div style="max-width:640px;padding-top:24px"><h1>All tests finished</h1>
        <p class="lead mt8">${p.done} of ${p.total} tests completed, ${p.skipped} skipped. Results are saved to ${carName(car)}.</p>
        <div class="row g12 mt24"><a class="btn primary lg" href="#/car/${run.car_id || car?.id}">See the report</a>
          ${p.skipped ? html`<button class="btn lg" data-goto="${run.plan.steps.findIndex((s) => run.results[s.id]?.status === "skipped")}">Run skipped tests</button>` : ""}</div></div>`;
    }
    const s = run.plan.steps[idx];
    const res = run.results[s.id];
    const auto = s.kind === "auto";
    const ready = !s.target || held >= s.target.hold_s || !app.adapter.connected;
    return html`<div style="max-width:1000px">
      <p class="small muted">Step ${idx + 1} of ${run.plan.steps.length} · ${s.phase}</p>
      <h1 class="mt4" style="font-size:30px">${s.title}</h1>
      ${s.why ? html`<p class="lead mt8">${s.why}</p>` : ""}
      ${res ? html`<p class="note info mt12 small">Already ${res.status}${res.answer ? `: ${res.answer}` : ""}. You can redo it.</p>` : ""}
      <div class="row g16 mt24" style="align-items:flex-start">
        <section class="card grow" style="flex:1 1 360px">
          <h2 class="small muted" style="font-weight:500">How to do it</h2>
          <ol style="list-style:none;margin:8px 0 0;padding:0">${s.how.map((h, i) => html`<li class="row g12" style="align-items:flex-start;padding:10px 0;border-top:1px solid var(--line-soft);font-size:15px">
            <span class="mono tiny faint" style="width:14px;padding-top:3px">${i + 1}</span><span class="grow">${h}</span></li>`)}</ol>
          ${s.safety ? html`<p class="note mt12">${s.safety}</p>` : ""}
          ${s.kind === "question" ? html`<fieldset class="mt16" style="border:0;padding:0;margin:0"><legend style="font-weight:500;padding:0">Your answer</legend>
            <div class="row g8 mt8">${s.choices.map((c) => html`<button type="button" class="chip" data-answer="${c}" aria-pressed="${answer === c}">${c}</button>`)}</div></fieldset>` : ""}
        </section>
        <section class="card grow" style="flex:1 1 340px" aria-live="polite">
          <div class="row g8" style="justify-content:space-between"><h2 class="small muted" style="font-weight:500">${s.target ? "Live" : "Readings"}</h2>
            <span class="tiny" style="font-weight:500;color:${auto ? "var(--green)" : "var(--amber)"}">${auto ? "Reading automatically" : "Waiting for you"}</span></div>
          <div data-live>${s.target ? meter(s, lastSample) : ""}${readings(lastSample?.values)}</div>
        </section>
      </div>
      <div class="row g12 mt24">
        <button class="btn primary lg" data-done ${ready ? "" : "disabled"}>${s.kind === "question" ? "Save answer" : auto ? "Continue" : "Done"}</button>
        <button class="btn lg" data-skip>Skip this test</button>
        ${idx > 0 ? html`<button class="btn link" data-goto="${idx - 1}">Back</button>` : ""}
        <span class="right small muted">Unsure how? <a href="#/assistant">Ask the assistant</a></span>
      </div></div>`;
  }

  function draw() {
    mount(root, html`<header class="topbar" style="border-bottom:1px solid var(--line)"><a class="logo" href="#/garage">Car Scanner</a>
      <span class="muted">${run.plan.title} · ${carName(car)}</span>
      <span class="right row g12 small muted">${run.progress.done} done · ${run.progress.skipped} skipped · ${run.progress.total} tests
        <span class="bar" style="width:140px"><span style="width:${((run.progress.done + run.progress.skipped) / run.progress.total) * 100}%"></span></span>
        <a class="muted" href="#/car/${run.car_id || car?.id}">Stop and see results</a></span></header>
      <div class="row" style="align-items:stretch;flex-wrap:wrap">
        <nav aria-label="Test steps" style="flex:1 1 260px;padding:12px 18px 24px;border-right:1px solid var(--line)">${stepList()}</nav>
        <main style="flex:999 1 600px;min-width:0;padding:32px 40px 48px" data-main>${stepView()}</main></div>`);
    startPoll();
  }

  function startPoll() {
    stopPoll(); held = 0; lastSample = null;
    const s = run.plan.steps[idx];
    if (!s || !(s.capture.length || s.target) || !app.adapter.connected) return;
    const sampleOnce = async () => {
      try {
        lastSample = await api(`/runs/${runId}/steps/${encodeURIComponent(s.id)}/sample`);
        if (s.target) held = lastSample.in_target ? held + 1 : 0;
        const live = root.querySelector("[data-live]");
        if (live) mount(live, html`${s.target ? meter(s, lastSample) : ""}${readings(lastSample.values)}`);
        const done = root.querySelector("[data-done]");
        if (done && s.target) done.disabled = held < s.target.hold_s;
      } catch { stopPoll(); }
    };
    sampleOnce();
    poll = setInterval(sampleOnce, 1000);
  }

  async function submit(status, btn) {
    const s = run.plan.steps[idx];
    if (status === "done" && s.kind === "question" && !answer) { toast("Pick an answer first.", true); return; }
    await guard(async () => {
      stopPoll();
      run = await api(`/runs/${runId}/steps/${encodeURIComponent(s.id)}`, { method: "POST", body: { status, answer: status === "done" ? answer : null } });
      answer = null;
      const next = run.plan.steps.findIndex((x, i) => i > idx && !run.results[x.id]);
      idx = next >= 0 ? next : (run.progress.next_index ?? run.plan.steps.length);
      draw();
    }, btn);
  }

  await app.refreshAdapter();
  draw();
  on(root, "click", "[data-goto]", (e, b) => { idx = Number(b.dataset.goto); answer = null; draw(); });
  on(root, "click", "[data-answer]", (e, b) => {
    answer = b.dataset.answer;
    root.querySelectorAll("[data-answer]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  });
  on(root, "click", "[data-done]", (e, b) => submit("done", b));
  on(root, "click", "[data-skip]", (e, b) => submit("skipped", b));
  return stopPoll;
}
