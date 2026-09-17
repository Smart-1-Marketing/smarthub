/**
 * The build screen, actually clicked.
 *
 * Everything else that guards public/build.html reads it as text: jscheck
 * parses it, test_display_ads.py pins strings, editor-startup.test.ts runs
 * the script in a stub DOM. None of them presses a button. This starts the
 * real renderer against a copy of the sample campaign, opens the page in a
 * real headless Chromium and walks the things a person does in the first
 * minute: the six sections, a copy edit, a color Use, an arrow nudge, Undo,
 * the Done-with-this-size button and a switch of size -- and fails on any
 * page error or console error along the way.
 *
 * It needs a browser. `PUPPETEER_EXECUTABLE_PATH` names one; failing that
 * the Playwright and Chrome locations a dev box or CI runner usually has are
 * tried. With none found the test is skipped BY NAME rather than passed, so
 * a green run without a browser reads as "not run" and not "fine".
 *
 * Two ways to run it. With nothing set it starts its own renderer against a
 * copy of the sample campaign, which is what every pull request does. With
 * `E2E_BASE_URL` set (the build screen's base, e.g.
 * `https://staging.example/tools/display-ads`) it drives THAT screen instead:
 * `E2E_HUB_PASSWORD` signs in at the Hub's /login first, `E2E_REQUEST` names
 * the campaign to open, and `E2E_TOKEN` is the renderer's own token for a
 * renderer reached without a Hub in front. That is the nightly run: the same
 * clicks, through the Hub's login, proxy and base-path shim, against a
 * campaign kept for the purpose (the test moves a line and saves a colour on
 * it).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { spawn, type ChildProcess } from 'node:child_process';
import puppeteer, { type Browser, type Page } from 'puppeteer-core';
import { seedCampaign, E2E_REQUEST } from './seed';

const ROOT = path.resolve(__dirname, '..');
const PORT = 3000 + Math.floor(Math.random() * 2000);
const TOKEN = 'e2e-token-e2e-token-1234';
const REQUEST = E2E_REQUEST;

/** A live screen to drive instead of a renderer started here. */
const REMOTE = (process.env.E2E_BASE_URL || '').replace(/\/$/, '');
const REMOTE_REQUEST = process.env.E2E_REQUEST || REQUEST;
const REMOTE_TOKEN = process.env.E2E_TOKEN || '';
const HUB_PASSWORD = process.env.E2E_HUB_PASSWORD || '';

/** Sign in at the Hub in front of the screen, when there is one. */
async function hubLogin(page: Page, base: string): Promise<void> {
  if (!HUB_PASSWORD) return;
  const origin = new URL(base).origin;
  await page.goto(`${origin}/login`, { waitUntil: 'networkidle0', timeout: 60_000 });
  await page.waitForSelector('#password', { timeout: 30_000 });
  // The form asks for an email as well and the browser will not submit
  // without one; the shared password signs in whatever the email says.
  if (await page.$('#email')) await page.type('#email', process.env.E2E_HUB_EMAIL || 'nightly@smart1.test');
  await page.type('#password', HUB_PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0', timeout: 60_000 }),
    page.click('#login-form button[type=submit], #login-form [type=submit]'),
  ]);
  assert.doesNotMatch(page.url(), /\/login/, `still on the login page after signing in: ${page.url()}`);
}

function findBrowser(): string | null {
  const candidates = [
    process.env.PUPPETEER_EXECUTABLE_PATH,
    process.env.CHROME_PATH,
    ...(process.env.PLAYWRIGHT_BROWSERS_PATH
      ? globChrome(process.env.PLAYWRIGHT_BROWSERS_PATH) : []),
    ...globChrome(path.join(os.homedir(), '.cache', 'puppeteer')),
    ...globChrome('/opt/puppeteer-cache'),
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter((p): p is string => !!p);
  for (const c of candidates) if (fs.existsSync(c)) return c;
  return null;
}

/** chrome binaries under a cache dir, any version, newest first. */
function globChrome(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const out: string[] = [];
  const walk = (d: string, depth: number) => {
    if (depth > 4) return;
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      let st; try { st = fs.statSync(p); } catch { continue; }
      if (st.isDirectory()) walk(p, depth + 1);
      else if (name === 'chrome' || name === 'chromium' || name === 'headless_shell') out.push(p);
    }
  };
  walk(dir, 0);
  return out.sort().reverse();
}

async function startServer(outDir: string): Promise<ChildProcess> {
  seedCampaign(outDir);
  const child = spawn(process.execPath, [path.join(ROOT, 'node_modules', 'tsx', 'dist', 'cli.mjs'), path.join(ROOT, 'src', 'server.ts')], {
    cwd: ROOT,
    env: { ...process.env, ADMIN_TOKEN: TOKEN, OUTPUT_DIR: outDir, PORT: String(PORT), HOST: '127.0.0.1', NODE_ENV: 'test' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout?.on('data', (d) => { log += d; });
  child.stderr?.on('data', (d) => { log += d; });
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/healthz`);
      if (r.ok) return child;
    } catch { /* not up yet */ }
    if (child.exitCode !== null) throw new Error(`renderer exited early:\n${log}`);
    await new Promise((r) => setTimeout(r, 300));
  }
  child.kill();
  throw new Error(`renderer did not answer /healthz in 60s:\n${log}`);
}

const executable = findBrowser();

test('the build screen can be worked from start to the next size', { skip: executable ? false : 'no Chromium found (set PUPPETEER_EXECUTABLE_PATH)' }, async (t) => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-e2e-'));
  const server = REMOTE ? null : await startServer(outDir);
  let browser: Browser | null = null;
  t.after(async () => { try { await browser?.close(); } catch { /* gone */ } server?.kill(); });
  const base = REMOTE || `http://127.0.0.1:${PORT}`;
  const request = REMOTE ? REMOTE_REQUEST : REQUEST;
  const token = REMOTE ? REMOTE_TOKEN : TOKEN;

  browser = await puppeteer.launch({ executablePath: executable!, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  const page: Page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });
  // What counts as a problem: a JavaScript exception, a console error the
  // page itself wrote, or a 5xx from a route the flow calls. Not counted,
  // because each is the standalone renderer's designed answer: a Hub-only
  // `/_hub/` route 404ing, the Google Fonts stylesheet not loading (there is
  // no internet in CI), and /api/diagnostics answering 503 for "broken" on a
  // box with no Cloudinary or OpenAI configured.
  const problems: string[] = [];
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    if (/Failed to load resource/.test(m.text())) return;
    problems.push(`console: ${m.text()}`);
  });
  page.on('response', (r) => {
    if (r.status() >= 500 && !/\/api\/diagnostics/.test(r.url())) problems.push(`${r.status()} ${r.url()}`);
  });

  await hubLogin(page, base);
  await page.goto(`${base}/build?request=${encodeURIComponent(request)}${token ? '&token=' + encodeURIComponent(token) : ''}`,
    { waitUntil: 'networkidle0', timeout: 90_000 });

  // The first preview lands and the six sections are there, in order.
  await page.waitForFunction(() => !!(document.getElementById('preview') as HTMLImageElement)?.src, { timeout: 90_000 });
  const navs = await page.$$eval('[data-nav]', (els) => els.map((e) => (e as HTMLElement).dataset.nav));
  assert.deepEqual(navs, ['layout', 'copy', 'background', 'type', 'text', 'logo']);
  const steps = await page.$$eval('.steps [data-step]', (els) => els.length);
  assert.equal(steps, 6, 'the start-here strip lists the six steps');

  // Opening one section closes the rest.
  await page.click('[data-nav="copy"] > summary');
  await page.waitForFunction(() => (document.querySelector('[data-nav="copy"]') as HTMLDetailsElement).open);
  const openCount = await page.$$eval('[data-nav]', (els) => els.filter((e) => (e as HTMLDetailsElement).open).length);
  assert.equal(openCount, 1, 'one section open at a time');

  // A copy edit lands on every size with no dialog, and the preview follows.
  const before = await page.$eval('#preview', (img) => (img as HTMLImageElement).src);
  await page.click('#copy-headline', { clickCount: 3 });
  await page.type('#copy-headline', 'Weddings, done right');
  assert.equal(await page.$('.ask'), null, 'no scope dialog on the first keystroke');
  await page.waitForFunction((was) => {
    const img = document.getElementById('preview') as HTMLImageElement;
    return img.src && img.src !== was && !document.getElementById('canvas')!.classList.contains('busy');
  }, { timeout: 60_000 }, before);
  const scope = await page.$eval('[data-scopefor="headline"] [data-scope="all"]', (b) => b.getAttribute('aria-pressed'));
  assert.equal(scope, 'true', 'the toggle under the field says every size');

  // Undo takes it back.
  await page.click('#undo');
  await page.waitForFunction(() => (document.getElementById('copy-headline') as HTMLTextAreaElement).value !== 'Weddings, done right', { timeout: 20_000 });

  // A color waits for Use, then lands. Text Boxes > Headline.
  await page.click('[data-nav="text"] > summary');
  await page.click('[data-acc="headline"] > summary');
  await page.waitForSelector('[data-cprow="headline:color"] input[type=color]');
  await page.$eval('[data-cprow="headline:color"] input[type=color]', (el) => {
    (el as HTMLInputElement).value = '#ff0000';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const useDisabled = await page.$eval('[data-cp="headline:color"][data-cpact="use"]', (b) => (b as HTMLButtonElement).disabled);
  assert.equal(useDisabled, false, 'Use lights up once a color is picked');
  await page.click('[data-cp="headline:color"][data-cpact="save"]');
  await page.waitForFunction(() => !!document.querySelector('.inkchip.saved'), { timeout: 20_000 });
  const savedChips = await page.$$eval('.inkchip.saved', (els) => els.length);
  assert.ok(savedChips >= 1, 'Use and save keeps the color as a chip');

  // An arrow moves a line by the chosen speed.
  await page.click('[data-pad="headline"] [data-nudge="down"]');
  await page.click('[data-sp="fast"]');
  await page.click('[data-pad="headline"] [data-nudge="down"]');
  const speed = await page.$eval('[data-pad="headline"] ~ .speed [data-sp="fast"], .speed [data-sp="fast"]', (b) => b.getAttribute('aria-pressed'));
  assert.equal(speed, 'true', 'the speed stays lit');

  // The arrow keys move the same line: focus the pad and press. The number
  // box only redraws with the panel, so the evidence is the preview, which
  // every nudge invalidates and redraws.
  const settled = () => {
    const img = document.getElementById('preview') as HTMLImageElement;
    return !!img.src && !document.getElementById('canvas')!.classList.contains('busy');
  };
  await page.waitForFunction(settled, { timeout: 60_000 });
  const beforeKey = await page.$eval('#preview', (img) => (img as HTMLImageElement).src);
  await page.focus('[data-pad="headline"] [data-nudge="down"]');
  await page.keyboard.press('ArrowDown');
  await page.waitForFunction((was) => {
    const img = document.getElementById('preview') as HTMLImageElement;
    return !!img.src && img.src !== was && !document.getElementById('canvas')!.classList.contains('busy');
  }, { timeout: 60_000 }, beforeKey);

  // Hold to see before: the chip shows once the picture differs from the
  // one this size opened with, and holding it swaps the picture back.
  await page.waitForFunction(() => {
    const b = document.getElementById('beforeBtn') as HTMLElement;
    return b && b.style.display !== 'none' && !document.getElementById('canvas')!.classList.contains('busy');
  }, { timeout: 60_000 });
  const now = await page.$eval('#preview', (img) => (img as HTMLImageElement).src);
  const chip = (await page.$('#beforeBtn'))!;
  const box = (await chip.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForFunction((was) => (document.getElementById('preview') as HTMLImageElement).src !== was, { timeout: 10_000 }, now);
  assert.equal(await page.$eval('#beforeBtn', (b) => b.getAttribute('aria-pressed')), 'true');
  await page.mouse.up();
  await page.waitForFunction((was) => (document.getElementById('preview') as HTMLImageElement).src === was, { timeout: 10_000 }, now);


  // Switching size saves and goes: no dialog.
  const sizes = await page.$$eval('#rail [data-size]', (els) => els.map((e) => (e as HTMLElement).dataset.size));
  assert.ok(sizes.length >= 2, `the rail lists sizes: ${sizes.join(', ')}`);
  assert.ok(await page.$('.mm'), 'a Meta size wears the blue M');
  await page.click(`#rail [data-size="${sizes[1]}"]`);
  // With edits behind us the click saves, and the advice layer may then ask
  // "would you like to see what this would look like?" about an open check.
  // That question is the design; a "save first?" question is not.
  const landed = (want: string) => {
    const on = document.querySelector('#rail .size.on [data-size]') as HTMLElement | null;
    return (on && on.dataset.size === want) || !!document.querySelector('.ask');
  };
  await page.waitForFunction(landed, { timeout: 60_000 }, sizes[1]);
  const dialog = await page.$('.ask');
  if (dialog) {
    const text = await dialog.evaluate((el) => el.textContent || '');
    assert.doesNotMatch(text, /Save your changes first/, 'no save dialog on the way');
    assert.match(text, /Would you like to see what this would look like/, 'the only question is the advice');
    const buttons = await dialog.$$('button');
    for (const b of buttons) {
      const label = await b.evaluate((el) => el.textContent || '');
      if (/No, save and continue/.test(label)) { await b.click(); break; }
    }
  }
  await page.waitForFunction((want) => {
    const on = document.querySelector('#rail .size.on [data-size]') as HTMLElement | null;
    return on && on.dataset.size === want;
  }, { timeout: 60_000 }, sizes[1]);

  // The Done button is under the checks.
  await page.waitForSelector('#doneSize', { timeout: 60_000 });

  // This size is carried from the first, so Text Boxes offers to make it the
  // one the rest follow -- and asks before doing so. Cancel leaves it alone.
  await page.waitForSelector('#adoptLook', { timeout: 20_000 });
  await page.click('#adoptLook');
  await page.waitForFunction(() => /Use this size.s look on every size\?/.test(document.querySelector('.ask')?.textContent || ''), { timeout: 60_000 });
  for (const b of await page.$$('.ask button')) {
    if (/^Cancel$/.test(await b.evaluate((el) => (el.textContent || '').trim()))) { await b.click(); break; }
  }
  await page.waitForFunction(() => !document.querySelector('.ask'), { timeout: 10_000 });
  assert.ok(await page.$('#adoptLook'), 'cancelling keeps the offer');

  assert.deepEqual(problems, [], 'no page errors, console errors or server errors');
});
