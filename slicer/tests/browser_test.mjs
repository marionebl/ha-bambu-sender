// Slices the test plate in headless Chromium against the running app image, then slices it natively on the Pi path.
// env: APP (base URL of the app), FRAME_HOST (base URL of the parent page without COOP/COEP), PLAIN (base URL of a non-isolating proxy of the app), STL, RESULT (json out),
//      PW_DIR (folder with playwright installed). Prints the numbers; exits 1 on any failure, after all cases ran.
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';

const { APP, FRAME_HOST, PLAIN, STL, RESULT, PW_DIR } = process.env;
const { chromium } = createRequire(PW_DIR + '/')('playwright');
const failures = [];
const fail = (m) => { console.log('FAIL: ' + m); failures.push(m); };
const getJson = async (path) => (await fetch(APP + path)).json();

// The frame HOST is a different origin and sends neither COOP nor COEP, like Home Assistant's own page.
const server = http.createServer((req, res) => {
  res.setHeader('Content-Type', 'text/html');
  res.end(`<!doctype html><iframe src="${APP}/" style="width:1200px;height:2400px;border:0"></iframe>`);
}).listen(new URL(FRAME_HOST).port, '127.0.0.1');

const browser = await chromium.launch({ channel: 'chromium' });
console.log('Chromium ' + browser.version());

async function browserCase(name, { framed, query = '' }) {
  const page = await browser.newPage();
  page.on('console', (m) => ['error', 'warning'].includes(m.type()) && console.log(`  [${name} console ${m.type()}] ${m.text()}`));
  await page.goto(framed ? FRAME_HOST + '/' : APP + '/' + query);
  let root = page;
  let frame = page.mainFrame();
  if (framed) {
    await page.waitForSelector('iframe');
    root = page.frameLocator('iframe');
    await root.locator('#engine-info').waitFor();
    frame = page.frames().find((f) => f.url().startsWith(APP));
  }
  const isolated = await frame.evaluate(() => crossOriginIsolated);
  const info = root.locator('#engine-info');
  await root.locator('#engine-info[data-ready="1"], #engine-info[data-error]:not([data-error=""])').waitFor({ timeout: 180000 });
  if (await info.getAttribute('data-error')) { fail(`${name}: the engine did not load: ${await info.getAttribute('data-error')}`); return null; }
  const warmVariant = await info.getAttribute('data-variant');
  const defaultChip = (await root.locator('#where button.on').textContent()).trim();
  await root.locator('#file').setInputFiles(STL);
  await root.locator('#upload-msg:has-text("is ready")').waitFor();
  await root.locator('#printers button:has-text("Tilman")').click();
  await root.locator('#quality button:has-text("Better")').click();
  await root.locator('#trays button:has-text("PETG HF")').click();
  await root.locator('#slice').click();
  await root.locator('#result:not([hidden]), #slicing .err').first().waitFor({ timeout: 15 * 60000 });
  const err = await root.locator('#slicing .err').count() ? await root.locator('#slicing .err').textContent() : '';
  if (err) { fail(`${name}: ${err}`); return null; }
  const d = await root.locator('#slice-info').evaluate((e) => ({ ...e.dataset, text: e.textContent }));
  const jobId = await frame.evaluate(() => state.job.id);
  const r = { name, jobId, isolated, variant: d.variant, warmVariant, defaultChip, loadMs: +d.loadMs, sliceMs: +d.sliceMs, totalMs: +d.totalMs };
  console.log(`CASE ${name}: crossOriginIsolated=${isolated} engine=${d.variant} default-slice-on="${defaultChip}" ` +
    `engine-load=${(r.loadMs / 1000).toFixed(1)}s slice=${(r.sliceMs / 1000).toFixed(1)}s total-after-click=${(r.totalMs / 1000).toFixed(1)}s`);
  console.log('  page says: ' + d.text);
  if (defaultChip !== 'This device') fail(`${name}: isolated page must default to "This device", got "${defaultChip}"`);
  // The job is a normal job: stats from the API, the layer viewer's G-code served, a preview drawn.
  const job = await getJson('/api/jobs/' + jobId);
  console.log(`  GET /api/jobs/${jobId}: state=${job.state} info=${JSON.stringify({ seconds: job.info?.seconds, grams: job.info?.grams })}`);
  if (job.state !== 'done' || !(job.info.seconds > 0) || !(job.info.grams > 0)) fail(`${name}: job has no stats`);
  const g = await fetch(`${APP}/api/jobs/${jobId}/plate.gcode`);
  const text = await g.text();
  console.log(`  plate.gcode: HTTP ${g.status}, ${text.length} bytes`);
  if (g.status !== 200 || !text.includes('; FEATURE:')) fail(`${name}: plate.gcode not served`);
  if (!(await root.locator('#layer-label:has-text("Layer")').waitFor({ timeout: 60000 }).then(() => true, () => false))) fail(`${name}: layer view did not draw`);
  await page.close();
  return r;
}

// Not isolated: the same app behind a proxy that strips COOP, COEP, DIP and CORP (what Safari or Firefox get from
// the sidebar). It must default to the Pi, start no engine and show no suggestion to open a tab.
const proxy = http.createServer((req, res) => {
  const up = http.request(APP + req.url, { method: req.method, headers: { ...req.headers, host: new URL(APP).host } }, (r) => {
    const h = { ...r.headers };
    for (const k of ['cross-origin-opener-policy', 'cross-origin-embedder-policy', 'document-isolation-policy', 'cross-origin-resource-policy']) delete h[k];
    res.writeHead(r.statusCode, h);
    r.pipe(res);
  });
  req.pipe(up);
}).listen(new URL(PLAIN).port, '127.0.0.1');
async function plainCase() {
  const page = await browser.newPage();
  await page.goto(PLAIN + '/');
  const isolated = await page.evaluate(() => crossOriginIsolated);
  const chip = (await page.locator('#where button.on').textContent()).trim();
  const engineText = (await page.locator('#engine-info').textContent()).trim();
  const ownTab = await page.locator('a[target=_blank]:not(footer a)').count();
  const text = await page.locator('main').textContent();
  console.log(`CASE not-isolated: crossOriginIsolated=${isolated} default-slice-on="${chip}" engine-info="${engineText}" suggestion-links=${ownTab}`);
  if (isolated) fail('not-isolated case: the proxy did not remove isolation');
  if (chip !== 'Pi (Bambu Studio)') fail(`not-isolated case: expected default Pi, got "${chip}"`);
  if (engineText !== '' || /own tab/i.test(text)) fail('not-isolated case: no engine start and no own-tab suggestion expected');
  await page.locator('#where button:has-text("This device")').click();  // the manual choice still works, with st
  await page.locator('#engine-info[data-ready="1"]').waitFor({ timeout: 180000 });
  const v = await page.locator('#engine-info').getAttribute('data-variant');
  console.log(`  manual "This device" without isolation: engine=${v}`);
  if (v !== 'st') fail(`not-isolated case: manual choice used ${v}, expected st`);
  await page.close();
}
try { await plainCase(); } catch (e) { fail('not-isolated case: ' + e.message); }

const out = { chromium: browser.version(), cases: {} };
const cases = [
  ['top-level-mt', { framed: false }],
  ['iframe-no-coop', { framed: true }],
  ['top-level-st', { framed: false, query: '?engine=st' }],
];
for (const [name, opt] of cases) {
  try { out.cases[name] = await browserCase(name, opt); } catch (e) { fail(`${name}: ${e.message}`); }
}
await browser.close();
server.close();
proxy.close();

const top = out.cases['top-level-mt'], frame = out.cases['iframe-no-coop'], st = out.cases['top-level-st'];
if (top && !(top.isolated && top.variant === 'mt')) fail(`top-level: expected crossOriginIsolated and the mt engine, got isolated=${top.isolated} engine=${top.variant}`);
if (frame && !(frame.isolated && frame.variant === 'mt')) {
  fail(`iframe: expected crossOriginIsolated=true and mt (Document-Isolation-Policy), got isolated=${frame.isolated} engine=${frame.variant} on Chromium ${out.chromium}`);
}
if (st && st.variant !== 'st') fail(`forced st case ran the ${st.variant} engine`);
if (top && st && !(top.sliceMs < st.sliceMs)) fail(`mt slice (${top.sliceMs} ms) is not faster than st slice (${st.sliceMs} ms)`);

// Native Bambu Studio on the same runner, through the same API the Pi uses.
const t = Date.now();
const job = await (await fetch(`${APP}/api/jobs?name=test_plate_v3.1.stl`, { method: 'POST', body: fs.readFileSync(STL) })).json();
await fetch(`${APP}/api/jobs/${job.id}/slice`, { method: 'POST', body: JSON.stringify({ serial: 'H2S0001', quality: 'better', tray: '0:0' }) });
let j;
do { await new Promise((r) => setTimeout(r, 1000)); j = await getJson('/api/jobs/' + job.id); } while (!['done', 'failed'].includes(j.state));
out.pi = { jobId: job.id, state: j.state, ms: Date.now() - t };
console.log(`CASE pi-native-bambu-studio: ${j.state} in ${(out.pi.ms / 1000).toFixed(1)} s (POST /api/jobs/${job.id}/slice)`);
if (j.state !== 'done') fail('native slice failed: ' + j.error);

const f = (c) => (c ? `slice ${(c.sliceMs / 1000).toFixed(1)} s (engine start ${(c.loadMs / 1000).toFixed(1)} s)` : 'n/a');
console.log('SUMMARY (same image, same runner)');
console.log(`  browser mt top-level : ${f(top)}`);
console.log(`  browser ${frame?.variant} in iframe : ${f(frame)}  crossOriginIsolated=${frame?.isolated}`);
console.log(`  browser st top-level : ${f(st)}`);
console.log(`  native Bambu Studio  : ${(out.pi.ms / 1000).toFixed(1)} s`);
fs.writeFileSync(RESULT, JSON.stringify(out));
process.exit(failures.length ? 1 : 0);
