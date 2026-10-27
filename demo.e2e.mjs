// End to end test of the Netlify demo in a real browser (Chromium via Playwright).
//
// It serves a demo build with Netlify's production headers (including the Content Security
// Policy from netlify.toml), emulates the /api/fda edge proxy in front of the openFDA emulator
// (backend/tests/openfda_emulator.py, fixture data, no network), and drives the app: engine boot
// in a Web Worker, recommendations, compare view, pharmacist review, note export, share links,
// insights, calculators, mobile and dark layouts. Any console error fails the run.
//
//   E2E_PYODIDE_DIR=/path/to/pyodide-0.28.3 npm run test:e2e
//
// E2E_PYODIDE_DIR must hold the full Pyodide distribution (the CDN is used in production).

import { spawn, execFileSync } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import http from "node:http";
import { dirname, extname, join, resolve } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

// createRequire also resolves a globally installed Playwright (npm i -D playwright works too).
const { chromium } = createRequire(import.meta.url)("playwright");

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const repo = resolve(root, "..");
const PYODIDE_DIR = process.env.E2E_PYODIDE_DIR;
const OUT = resolve(here, "artifacts");
const EMU_PORT = 8765, WEB_PORT = 4789;
if (!PYODIDE_DIR || !existsSync(join(PYODIDE_DIR, "pyodide.mjs"))) {
  console.error("Set E2E_PYODIDE_DIR to a full Pyodide 0.28.3 distribution.");
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

// 1. Build the demo exactly as Netlify does, but with Pyodide served locally.
execFileSync("node", ["scripts/bundle-python.mjs"], { cwd: root, stdio: "inherit" });
execFileSync("npx", ["vite", "build", "--outDir", "dist-e2e", "--emptyOutDir"], {
  cwd: root, stdio: "inherit",
  env: { ...process.env, VITE_ENGINE: "browser", VITE_PYODIDE_URL: "/pyodide/", VITE_OPENFDA_BASES: "/api/fda" },
});

// 2. openFDA emulator and a Netlify-like static server.
const emu = spawn("python3", [join(repo, "backend/tests/openfda_emulator.py"), "--port", String(EMU_PORT), "--delay-ms", "40"],
  { stdio: "ignore" });
const toml = readFileSync(join(repo, "netlify.toml"), "utf8");
const csp = /Content-Security-Policy = "([^"]+)"/.exec(toml)[1].replaceAll("https://cdn.jsdelivr.net", "");
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".wasm": "application/wasm", ".zip": "application/zip", ".whl": "application/zip",
  ".map": "application/json", ".tar": "application/x-tar" };
let proxied = 0;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${WEB_PORT}`);
  if (url.pathname.startsWith("/api/fda/")) {
    proxied++;
    const up = http.get(`http://127.0.0.1:${EMU_PORT}${url.pathname.replace("/api/fda", "")}${url.search}`, (r) => {
      res.writeHead(r.statusCode, { "Content-Type": "application/json" });
      r.pipe(res);
    });
    up.on("error", () => { res.writeHead(502); res.end(); });
    return;
  }
  const base = url.pathname.startsWith("/pyodide/") ? PYODIDE_DIR : join(root, "dist-e2e");
  const rel = url.pathname.startsWith("/pyodide/") ? url.pathname.slice("/pyodide/".length) : url.pathname.slice(1) || "index.html";
  const file = join(base, decodeURIComponent(rel));
  if (!file.startsWith(base) || !existsSync(file) || statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream", "Content-Security-Policy": csp,
    "X-Content-Type-Options": "nosniff", "Cross-Origin-Opener-Policy": "same-origin" });
  createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(WEB_PORT, r));
await new Promise((r) => setTimeout(r, 800));

const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? `: ${detail}` : ""}`); };
const browser = await chromium.launch();
const errors = [];
try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, acceptDownloads: true });
  // Web fonts are cosmetic and may be unreachable from CI sandboxes; answer them locally.
  const noFonts = (c) => c.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await noFonts(ctx);
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  const t0 = Date.now();
  await page.goto(`http://localhost:${WEB_PORT}/#/workbench`);
  await page.locator(".engine-bar--ready").waitFor({ timeout: 180_000 });
  check("engine boots in a worker under the production CSP", true, `${((Date.now() - t0) / 1000).toFixed(1)} s`);
  check("data source is the same origin edge proxy", (await page.locator(".engine-bar").innerText()).includes("edge cache"));

  // Recommendation from a starter
  await page.getByRole("button", { name: "IV furosemide, CKD stage 3b" }).click();
  await page.locator("ol.options").waitFor({ timeout: 60_000 });
  const heading = await page.locator("#results-title").innerText();
  check("starter runs a recommendation", heading.includes("furosemide"), heading);
  const names = await page.locator(".options > li h3").allInnerTexts();
  check("ranked options include IV bumetanide above oral torsemide",
    names.indexOf("Bumetanide") > -1 && names.indexOf("Bumetanide") < names.indexOf("Torsemide"), names.join(" | "));
  check("A rated equivalent ranks first", names[0].startsWith("Furosemide (Another Manufacturer)"), names[0]);
  await page.screenshot({ path: join(OUT, "demo_workbench_light.png") });

  // Score breakdown and evidence drawer
  await page.locator(".option").first().locator("summary", { hasText: "Why this rank" }).click();
  check("score breakdown visible", await page.locator(".breakdown__table").first().isVisible());
  await page.locator(".option__body .btn--secondary").first().click();
  await page.locator(".drawer").waitFor();
  check("evidence drawer opens with label passages", (await page.locator(".evidence__item").count()) > 0);
  await page.screenshot({ path: join(OUT, "demo_evidence_drawer.png") });
  await page.keyboard.press("Escape");

  // Compare matrix
  await page.locator(".segmented--small label", { hasText: "Compare" }).click();
  check("compare matrix shows 8 gate columns", (await page.locator(".matrix thead th").count()) === 10);
  await page.screenshot({ path: join(OUT, "demo_compare_matrix.png") });
  await page.locator(".segmented--small label", { hasText: "Details" }).click();

  // Pharmacist review
  await page.locator(".option").first().getByRole("button", { name: "Agree" }).click();
  await page.locator(".review-done").first().waitFor();
  check("pharmacist review recorded", (await page.locator(".review-done").first().innerText()).includes("agreed"));

  // Note download
  await page.locator(".ack input[type=checkbox]").check();
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /Full note/ }).click()]);
  const notePath = join(OUT, "note.md");
  await dl.saveAs(notePath);
  const note = readFileSync(notePath, "utf8");
  check("clinical note downloads with audit hash", note.startsWith("# Shortage substitution review: furosemide") && /hash [0-9a-f]{64}/.test(note));

  // Interaction block and renal estimate
  await page.getByRole("button", { name: "Clear" }).click();
  await page.goto(`http://localhost:${WEB_PORT}/#/workbench?drug=atorvastatin&route=oral&dose=20&freq=once%20daily&age=84&wt=52&ht=160&sex=female&scr=2.4&meds=clarithromycin&run=1`);
  await page.locator("#results-title", { hasText: "atorvastatin" }).waitFor({ timeout: 60_000 });
  await page.locator("ol.options").waitFor({ timeout: 60_000 });
  check("share link prefills and runs", (await page.locator("#results-title").innerText()).includes("atorvastatin"));
  check("renal estimate shown", (await page.locator(".notes__renal").innerText()).includes("Cockcroft-Gault"));
  await page.locator(".blocked > summary").click();
  check("simvastatin not recommended with clarithromycin",
    (await page.locator(".blocked").innerText()).toLowerCase().includes("simvastatin"));

  // Insights, calculators, method
  await page.goto(`http://localhost:${WEB_PORT}/#/insights`);
  await page.locator(".bars").first().waitFor({ timeout: 30_000 });
  check("insights render", (await page.locator(".stats dd").first().innerText()) === "2");
  await page.screenshot({ path: join(OUT, "demo_insights.png"), fullPage: true });
  await page.goto(`http://localhost:${WEB_PORT}/#/calculators`);
  await page.locator("select").first().waitFor();
  await page.getByLabel("Current dose, mg").fill("40");
  await page.getByRole("button", { name: "Convert" }).click();
  await page.locator(".calc .dose-card h3").first().waitFor();
  check("dose converter works", (await page.locator(".calc .dose-card h3").first().innerText()).length > 0,
    await page.locator(".calc .dose-card h3").first().innerText());
  await page.goto(`http://localhost:${WEB_PORT}/#/method`);
  await page.locator(".verify").waitFor({ timeout: 30_000 });
  check("audit chain verified in browser", (await page.locator(".verify").innerText()).includes("intact"));
  await page.screenshot({ path: join(OUT, "demo_method.png"), fullPage: true });

  // Dark and mobile
  const dark = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, colorScheme: "dark" });
  await noFonts(dark);
  const dp = await dark.newPage();
  dp.on("pageerror", (e) => errors.push(String(e)));
  await dp.goto(`http://localhost:${WEB_PORT}/#/workbench?drug=furosemide&route=intravenous&dose=40&freq=twice%20daily&crcl=42&meds=lisinopril&run=1`);
  await dp.locator("ol.options").waitFor({ timeout: 180_000 });
  await dp.screenshot({ path: join(OUT, "demo_workbench_dark.png") });
  check("dark theme renders", true);
  // isMobile is off on purpose: mobile emulation zooms out to fit wide content, which would hide overflow.
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true });
  await noFonts(mobile);
  const mp = await mobile.newPage();
  mp.on("pageerror", (e) => errors.push(String(e)));
  await mp.goto(`http://localhost:${WEB_PORT}/#/workbench`);
  await mp.locator(".engine-bar--ready").waitFor({ timeout: 180_000 });
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - 390);
  check("no horizontal scroll at 390 px", overflow <= 0, `overflow ${overflow}px`);
  await mp.screenshot({ path: join(OUT, "demo_mobile.png") });
  await mp.getByRole("button", { name: "IV furosemide, CKD stage 3b" }).click();
  await mp.locator("ol.options").waitFor({ timeout: 60_000 });
  const overflow2 = await mp.evaluate(() => document.documentElement.scrollWidth - 390);
  check("no horizontal scroll with results at 390 px", overflow2 <= 0, `overflow ${overflow2}px`);
  await mp.locator("#results-title").scrollIntoViewIfNeeded();
  await mp.screenshot({ path: join(OUT, "demo_mobile_results.png") });
  check("requests went through the edge proxy", proxied > 0, `${proxied} proxied`);
} catch (e) {
  check("unexpected failure", false, String(e));
} finally {
  check("no console or page errors", errors.length === 0, errors.slice(0, 3).join(" || "));
  await browser.close();
  server.close();
  emu.kill();
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
