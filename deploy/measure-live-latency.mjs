import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8080';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function openLiveGrid(page, layout = '5x5') {
  await page.goto(`${BASE}/live`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(2000);
  const siteSelect = page.locator('select').nth(0);
  const options = await siteSelect.locator('option').allTextContents();
  const idx = options.findIndex((t) => t.includes('RML - 1'));
  if (idx >= 0) await siteSelect.selectOption({ index: idx });
  await page.waitForTimeout(3000);
  const layoutBtn = page.locator('button', { hasText: layout }).first();
  if (await layoutBtn.count()) await layoutBtn.click();
  await page.waitForTimeout(2000);
}

async function main() {
  const cookie = sessionCookie();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  await context.addCookies([
    { name: cookie.name, value: cookie.value, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
  const page = await context.newPage();

  const out = {};

  // Test 1: cold starts x3 on small site (RML-1, 22 cams), 2x2 for fewer concurrent
  await openLiveGrid(page, '2x2');
  await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
  for (let i = 0; i < 3; i += 1) {
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    const siteSelect = page.locator('select').nth(0);
    const options = await siteSelect.locator('option').allTextContents();
    const idx = options.findIndex((t) => t.includes('RML - 1'));
    if (idx >= 0) await siteSelect.selectOption({ index: idx });
    await page.waitForTimeout(18000);
  }
  out.coldReloads = await page.evaluate(() => window.__nvrLiveMetrics?.getAll?.().complete);

  // Test 2: warm reopen
  await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
  await page.waitForTimeout(5000);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  await page.locator('select').nth(0).evaluate((sel) => {
    const opts = [...sel.options];
    const idx = opts.findIndex((o) => o.text.includes('RML - 1'));
    if (idx >= 0) sel.selectedIndex = idx;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.waitForTimeout(18000);
  out.warmReopen = await page.evaluate(() => window.__nvrLiveMetrics?.getAll?.().complete);

  // Test 3: worker comparison from accumulated samples
  out.byWorker = await page.evaluate(() => {
    const all = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
    const groups = {};
    for (const s of all) {
      const w = String(s.workerId ?? 'null');
      groups[w] = groups[w] || [];
      groups[w].push(s);
    }
    return groups;
  });

  // Test 4: 5x5 on RML-1
  await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
  await openLiveGrid(page, '5x5');
  await page.waitForTimeout(35000);
  out.grid5x5 = await page.evaluate(() => ({
    summary: window.__nvrLiveMetrics?.summary?.(),
    cancelled: window.__nvrLiveMetrics?.getAll?.().cancelled?.length ?? 0,
  }));

  // Test 5: fast scroll
  await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) for (let y = 0; y < 8000; y += 300) el.scrollTop = y;
  });
  await page.waitForTimeout(10000);
  out.fastScroll = await page.evaluate(() => ({
    summary: window.__nvrLiveMetrics?.summary?.(),
    cancelled: window.__nvrLiveMetrics?.getAll?.().cancelled?.length ?? 0,
    complete: window.__nvrLiveMetrics?.getAll?.().complete?.length ?? 0,
  }));

  console.log(JSON.stringify(out, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
