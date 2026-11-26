// Garage: the user's saved cars from past scans.
import { api, carName, dist, guard, html, mount, on, toast, when } from "../lib.js";

export async function view(root, _p, app) {
  const { cars } = await api("/cars");
  const unit = app.unit();
  mount(root, html`
    <div class="row g12">
      <div class="grow"><h1>Your garage</h1>
        <p class="lead mt8">${cars.length ? "Cars you've scanned. Pick one to see its history and reports."
          : "No cars yet. Plug in the adapter and run your first scan."}</p></div>
      <a class="btn primary" href="#/new/connect">New scan</a>
    </div>
    ${cars.length ? html`<section class="card flush"><table class="t">
      <thead><tr><th style="padding-left:22px">Car</th><th>VIN</th><th>Scans</th><th>Last scan</th><th>Last mileage</th><th></th></tr></thead>
      <tbody>${cars.map((c) => html`<tr>
        <th scope="row" style="padding-left:22px"><a href="#/car/${c.id}">${c.nickname || carName(c)}</a>
          ${c.nickname ? html`<div class="tiny faint">${carName(c)}</div>` : ""}</th>
        <td class="mono small">${c.vin}</td><td>${c.scan_count}</td><td class="small muted">${when(c.last_scan_at)}</td>
        <td class="mono small">${dist(c.last_odometer_km, unit)}</td>
        <td style="padding-right:22px;text-align:right"><button class="btn link tiny" data-del="${c.id}" data-name="${carName(c)}">Remove</button></td>
      </tr>`)}</tbody></table></section>`
    : html`<section class="panel"><h2>How it works</h2><ol class="mt12 stack g8" style="padding-left:18px;margin:0">
        <li>Plug the vLinker FS into the OBD port under the dashboard and connect USB.</li>
        <li>Turn the ignition on. We identify the car from its VIN.</li>
        <li>Pick what you want to find out. We run the right checks and save the results here.</li></ol>
        <p class="small muted mt12">No car nearby? Start a new scan and choose “Try the demo car”.</p></section>`}`);
  on(root, "click", "[data-del]", (e, b) => {
    if (!confirm(`Remove ${b.dataset.name} and all its scans, photos and reports? This can't be undone.`)) return;
    guard(async () => {
      await api(`/cars/${b.dataset.del}`, { method: "DELETE" });
      if (app.carId === Number(b.dataset.del)) app.carId = null;
      toast("Car removed"); view(root, _p, app);
    }, b);
  });
}
