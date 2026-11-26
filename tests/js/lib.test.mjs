// Frontend helper tests (node --test). lib.js is a browser ES module, loaded via a data: URL.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const src = await readFile(new URL("../../web/js/lib.js", import.meta.url), "utf8");
const lib = await import("data:text/javascript," + encodeURIComponent(src));

test("html escapes every interpolation", () => {
  const evil = `<img src=x onerror="alert(1)">`;
  const out = lib.html`<p title="${evil}">${evil}</p>`.toString();
  assert.ok(!out.includes("<img"));
  assert.ok(out.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
});

test("html keeps nested templates and arrays, drops null/false", () => {
  const items = ["a", "<b>"];
  const out = lib.html`<ul>${items.map((i) => lib.html`<li>${i}</li>`)}${null}${false}${undefined}</ul>`.toString();
  assert.equal(out, "<ul><li>a</li><li>&lt;b&gt;</li></ul>");
  assert.equal(lib.html`${lib.raw("<hr>")}`.toString(), "<hr>");
  assert.equal(lib.html`${0}`.toString(), "0");
});

test("esc handles quotes", () => {
  assert.equal(lib.esc(`'"&`), "&#39;&quot;&amp;");
});

test("safeUrl only allows http(s)", () => {
  assert.equal(lib.safeUrl("https://example.org/a"), "https://example.org/a");
  assert.equal(lib.safeUrl("javascript:alert(1)"), "#");
  assert.equal(lib.safeUrl("data:text/html,x"), "#");
  assert.equal(lib.safeUrl(undefined), "#");
});

test("distance formatting in miles and km", () => {
  assert.equal(lib.dist(160.9344, "mi"), "100 mi");
  assert.equal(lib.dist(1500, "km"), "1,500 km");
  assert.equal(lib.dist(null, "km"), "—");
});

test("carName", () => {
  assert.equal(lib.carName({ year: 2013, make: "Ford", model: "F-150", vin: "X" }), "2013 Ford F-150");
  assert.equal(lib.carName({ vin: "VIN123" }), "VIN123");
});

test("loader renders an accessible progress bar and escapes its title", () => {
  const out = lib.loader({ title: "<b>Scanning</b>", busy: true }).toString();
  assert.ok(out.includes('role="progressbar"') && out.includes("cs-flow") && !out.includes("<b>Scanning"));
});

test("guard shows the busy label, then restores the original label", async () => {
  const btn = { textContent: "Check", disabled: false };
  let during;
  const result = await lib.guard(async () => { during = [btn.textContent, btn.disabled]; return 42; }, btn, "Checking…");
  assert.deepEqual(during, ["Checking…", true]);
  assert.equal(result, 42);
  assert.equal(btn.textContent, "Check");
  assert.equal(btn.disabled, false);
});
