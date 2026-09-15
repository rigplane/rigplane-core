/**
 * MOR-1088 — mobile PTT gesture/orientation safety capture runner.
 *
 * Verification-only. Drives real `PointerEvent`s (not `.click()`) against
 * `fixtures/ptt-harness.html` over the fixtures vite server, one fresh
 * `page.goto()` per scenario for full isolation, and asserts on the harness's
 * `window.__ptt` delivery trace from the real managed gesture. Each stateful
 * scenario checks delivery first, then emits a server-shaped snapshot before
 * checking the rendered state.
 *
 *   node fixtures/capture-ptt.mjs [--port <n>] [--only <substring>]
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

/** Same host-Chromium-version fallback as `fixtures/capture.mjs` (MOR-1070). */
async function launchChromium() {
  try { return await chromium.launch(); } catch (bundledError) {
    const cache = path.join(process.env.HOME ?? '', 'Library/Caches/ms-playwright');
    if (!existsSync(cache)) throw bundledError;
    const executablePath = readdirSync(cache)
      .filter((d) => d.startsWith('chromium_headless_shell-') || d.startsWith('chromium-'))
      .sort((a, b) => Number(b.split('-').pop()) - Number(a.split('-').pop()))
      .flatMap((d) => [
        path.join(cache, d, 'chrome-mac-arm64', 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
        path.join(cache, d, 'chrome-mac-arm64', 'Chromium.app/Contents/MacOS/Chromium'),
        path.join(cache, d, 'chrome-headless-shell-mac-arm64', 'chrome-headless-shell'),
      ])
      .find((p) => existsSync(p));
    if (!executablePath) throw bundledError;
    return chromium.launch({ executablePath });
  }
}

const FRONTEND = path.resolve(import.meta.dirname, '..');
const REPO = path.resolve(FRONTEND, '..');
const argv = process.argv.slice(2);
const arg = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const PORT = Number(arg('--port', '5299'));
const ONLY = arg('--only', null);
const OUT = path.resolve(FRONTEND, 'fixtures-baselines');
mkdirSync(OUT, { recursive: true });

const git = (...args) => execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8' }).trim();

const server = await createServer({
  configFile: path.join(FRONTEND, 'vite.fixtures.config.ts'),
  root: FRONTEND, logLevel: 'warn',
  server: { port: PORT, strictPort: true, host: '127.0.0.1' },
});
await server.listen();
const browser = await launchChromium();
const URL = `http://127.0.0.1:${PORT}/fixtures/ptt-harness.html`;

/** Real PointerEvent (not `.click()`) — `setPointerCapture` needs a real pointerId. */
async function pointer(page, selector, type, opts = {}) {
  await page.evaluate(([sel, t, o]) => {
    const el = document.querySelector(sel);
    el.dispatchEvent(new PointerEvent(t, {
      bubbles: true, cancelable: true, pointerId: 1, pointerType: 'touch', isPrimary: true,
      clientX: o.x ?? 10, clientY: o.y ?? 10,
    }));
  }, [selector, type, opts]);
}
const wait = (page, ms) => page.waitForTimeout(ms);
const fresh = async (page) => {
  await page.goto(URL, { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]', { timeout: 10_000 });
};
const SERVER = {
  rx: { phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, faultDetail: null, fresh: true, releaseRequired: false, remainingMs: null, lastOperation: 'force_receive' },
  held: { phase: 'key-confirm-pending', intent: 'momentary', radioTx: 'unknown', txRisk: 'uncertain', fault: null, faultDetail: null, fresh: true, releaseRequired: true, remainingMs: null, lastOperation: 'ptt_on' },
  latched: { phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on', fault: null, faultDetail: null, fresh: true, releaseRequired: true, remainingMs: null, lastOperation: 'transmit_on' },
  stale: { phase: 'idle', intent: null, radioTx: 'unknown', txRisk: 'none', fault: null, faultDetail: null, fresh: false, releaseRequired: false, remainingMs: null, lastOperation: null },
};
const trace = (page) => page.evaluate(() => window.__ptt.deliveryTrace());
const emit = (page, snapshot) => page.evaluate((next) => window.__ptt.emitServerSnapshot(next), snapshot);
const snap = (page) => page.evaluate(() => window.__ptt.snapshot());
const matches = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);

const SCENARIOS = [
  {
    name: 'press-hold-release-momentary',
    async run(page) {
      await pointer(page, '[data-testid="ptt-fab"], .ptt-fab', 'pointerdown');
      await wait(page, 70);
      const heldTrace = await trace(page);
      await emit(page, SERVER.held);
      const held = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 320);
      const releaseTrace = await trace(page);
      await emit(page, SERVER.rx);
      const released = await snap(page);
      return {
        ok: matches(heldTrace, ['ws.ptt_on']) && held.intent === 'momentary'
          && matches(releaseTrace, ['ws.ptt_on', 'ws.ptt_off']) && released.intent === null,
        detail: `delivery=${releaseTrace.join(' -> ')} · server intent ${held.intent} -> ${released.intent}`,
      };
    },
  },
  {
    name: 'double-tap-to-latch-held-vs-latched-intent',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 100);
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const delivery = await trace(page);
      await emit(page, SERVER.latched);
      const latched = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 320);
      const afterTrace = await trace(page);
      await emit(page, SERVER.latched);
      const after = await snap(page);
      return {
        ok: matches(delivery, ['ws.ptt_on', 'ws.ptt_off', 'http.transmit_on']) && latched.intent === 'latched'
          && matches(afterTrace, delivery) && after.intent === 'latched',
        detail: `delivery=${afterTrace.join(' -> ')} · server intent=${after.intent}`,
      };
    },
  },
  {
    name: 'tap-to-unlatch',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 100);
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      await pointer(page, '.ptt-fab', 'pointerup');
      const latchTrace = await trace(page);
      await emit(page, SERVER.latched);
      const before = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerdown'); // tap while latched — releases immediately, no delay
      const releaseTrace = await trace(page);
      await emit(page, SERVER.rx);
      const after = await snap(page);
      return {
        ok: matches(latchTrace, ['ws.ptt_on', 'ws.ptt_off', 'http.transmit_on']) && before.intent === 'latched'
          && matches(releaseTrace, [...latchTrace, 'http.force_off']) && after.intent === null,
        detail: `delivery=${releaseTrace.join(' -> ')} · server intent ${before.intent} -> ${after.intent}`,
      };
    },
  },
  {
    name: 'pointercancel-before-threshold-never-keys',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 20); // inside the 50ms hold-delay
      await pointer(page, '.ptt-fab', 'pointercancel');
      await wait(page, 80); // past where the delayed engage would have fired
      const delivery = await trace(page);
      await emit(page, SERVER.rx);
      const after = await snap(page);
      return { ok: matches(delivery, []) && after.intent === null,
        detail: `delivery=${delivery.join(' -> ') || '(none)'} · server intent=${after.intent}` };
    },
  },
  {
    name: 'pointercancel-while-held-releases-fail-safe',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const heldTrace = await trace(page);
      await emit(page, SERVER.held);
      const held = await snap(page);
      await pointer(page, '.ptt-fab', 'pointercancel');
      await wait(page, 320);
      const releaseTrace = await trace(page);
      await emit(page, SERVER.rx);
      const after = await snap(page);
      return { ok: matches(heldTrace, ['ws.ptt_on']) && held.intent === 'momentary'
          && matches(releaseTrace, ['ws.ptt_on', 'ws.ptt_off']) && after.intent === null,
        detail: `delivery=${releaseTrace.join(' -> ')} · server intent ${held.intent} -> ${after.intent}` };
    },
  },
  {
    name: 'spurious-cancel-while-latched-does-not-release',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 100);
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      await pointer(page, '.ptt-fab', 'pointerup');
      const latchTrace = await trace(page);
      await emit(page, SERVER.latched);
      const latched = await snap(page);
      // No matching pointerdown session on this component — a bare stray cancel.
      await pointer(page, '.ptt-fab', 'pointercancel');
      await wait(page, 320);
      const afterTrace = await trace(page);
      await emit(page, SERVER.latched);
      const after = await snap(page);
      return {
        ok: matches(latchTrace, ['ws.ptt_on', 'ws.ptt_off', 'http.transmit_on']) && latched.intent === 'latched'
          && matches(afterTrace, latchTrace) && after.intent === 'latched',
        detail: `delivery=${afterTrace.join(' -> ')} · server intent=${after.intent}`,
      };
    },
  },
  {
    name: 'scroll-gesture-conflict-8px-move-cancel',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown', { x: 10, y: 10 });
      await wait(page, 20); // still inside the 50ms hold-delay
      await pointer(page, '.ptt-fab', 'pointermove', { x: 30, y: 10 }); // 20px, past the 8px guard
      await wait(page, 80);
      const delivery = await trace(page);
      await emit(page, SERVER.rx);
      const after = await snap(page);
      return { ok: matches(delivery, []) && after.intent === null,
        detail: `delivery=${delivery.join(' -> ') || '(none)'} · server intent=${after.intent}` };
    },
  },
  {
    name: 'orientation-swap-idle-recreates-recognizer',
    async run(page) {
      const before = await page.evaluate(() => window.__ptt.generation());
      await page.evaluate(() => window.__ptt.setSurface('landscape'));
      const after = await page.evaluate(() => ({
        generation: window.__ptt.generation(), delivery: window.__ptt.deliveryTrace(),
      }));
      await emit(page, SERVER.rx);
      const server = await snap(page);
      return {
        ok: after.generation === before + 1 && matches(after.delivery, []) && server.intent === null,
        detail: `generation ${before} -> ${after.generation} · delivery=${after.delivery.join(' -> ') || '(none)'}`,
      };
    },
  },
  {
    name: 'orientation-swap-while-held-releases-cleanly-no-strand',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const heldTrace = await trace(page);
      await emit(page, SERVER.held);
      const held = await snap(page);
      await page.evaluate(() => window.__ptt.setSurface('landscape'));
      const releaseTrace = await trace(page);
      await emit(page, SERVER.rx);
      const after = await snap(page);
      return {
        ok: matches(heldTrace, ['ws.ptt_on']) && held.intent === 'momentary'
          && matches(releaseTrace, ['ws.ptt_on', 'ws.ptt_off']) && after.intent === null,
        detail: `delivery=${releaseTrace.join(' -> ')} · server intent ${held.intent} -> ${after.intent}`,
      };
    },
  },
  {
    name: 'orientation-swap-during-pending-release-window-no-surviving-timer',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const heldTrace = await trace(page);
      await emit(page, SERVER.held);
      await pointer(page, '.ptt-fab', 'pointerup'); // arms the 300ms deferred-release window
      await wait(page, 50); // still pending
      await page.evaluate(() => window.__ptt.setSurface('landscape')); // destroy() must cancel it, not just abandon it
      await wait(page, 320); // past where the abandoned timer would have fired a SECOND release
      const releaseTrace = await trace(page);
      await emit(page, SERVER.rx);
      const after = await snap(page);
      return {
        ok: matches(heldTrace, ['ws.ptt_on'])
          && matches(releaseTrace, ['ws.ptt_on', 'ws.ptt_off']) && after.intent === null,
        detail: `delivery=${releaseTrace.join(' -> ')} · server intent=${after.intent}`,
      };
    },
  },
  {
    // MOR-1376: a destroyed surface generation must not deliver its delayed
    // intent, while the surviving generation must remain usable.
    name: 'orientation-double-flip-newer-owner-survives',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 15);
      await page.evaluate(() => window.__ptt.setSurface('landscape')); // generation 2
      await wait(page, 5);
      await page.evaluate(() => window.__ptt.setSurface('portrait')); // generation 3 — a NEW, unrelated recognizer
      await wait(page, 60);
      const ghostTrace = await trace(page);
      await emit(page, SERVER.rx);
      const afterGhostWindow = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const realTrace = await trace(page);
      await emit(page, SERVER.held);
      const afterRealPress = await snap(page);
      return {
        ok: matches(ghostTrace, []) && afterGhostWindow.intent === null
          && matches(realTrace, ['ws.ptt_on']) && afterRealPress.intent === 'momentary',
        detail: `ghost delivery=${ghostTrace.join(' -> ') || '(none)'} · `
          + `surviving delivery=${realTrace.join(' -> ')} · server intent=${afterRealPress.intent}`,
      };
    },
  },
  {
    name: 'touch-targets-44px-both-surfaces',
    async run(page) {
      const delivery = await trace(page);
      await emit(page, SERVER.rx);
      const portrait = await page.evaluate(() => {
        const r = document.querySelector('.ptt-fab').getBoundingClientRect();
        return { w: r.width, h: r.height };
      });
      await page.evaluate(() => window.__ptt.setSurface('landscape'));
      const landscape = await page.evaluate(() => {
        const r = document.querySelector('[data-testid="ls-ptt"]').getBoundingClientRect();
        return { w: r.width, h: r.height };
      });
      const ok = matches(delivery, []) && portrait.w >= 44 && portrait.h >= 44
        && landscape.w >= 44 && landscape.h >= 44;
      return { ok, detail: `delivery=${delivery.join(' -> ') || '(none)'} · portrait ${portrait.w}x${portrait.h} · landscape ${landscape.w}x${landscape.h} · expected >=44x44` };
    },
  },
  {
    name: 'connection-loss-emits-stale-server-snapshot',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const delivery = await trace(page);
      await emit(page, SERVER.held);
      const held = await snap(page);
      const beforeLossTrace = await trace(page);
      await emit(page, SERVER.stale);
      const after = await snap(page);
      return { ok: matches(delivery, ['ws.ptt_on']) && matches(beforeLossTrace, delivery)
          && held.intent === 'momentary' && after.intent === null && after.fresh === false
          && after.radioTx === 'unknown',
        detail: `delivery=${delivery.join(' -> ')} · stale server intent=${after.intent} fresh=${after.fresh} radioTx=${after.radioTx}` };
    },
  },
];

const results = [];
let failures = 0;
const page = await browser.newPage();
try {
  for (const s of SCENARIOS) {
    if (ONLY && !s.name.includes(ONLY)) continue;
    await fresh(page);
    const { ok, detail } = await s.run(page);
    results.push({ name: s.name, ok, detail });
    if (!ok) failures += 1;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${s.name}${ok ? '' : `\n        ✗ ${detail}`}`);
  }
} finally {
  await page.close();
  await browser.close();
  await server.close();
}

const manifest = {
  ticket: 'MOR-1088',
  generatedAt: new Date().toISOString(),
  headSha: git('rev-parse', 'HEAD'),
  worktreeClean: git('status', '--porcelain', '--', 'frontend/src', 'src') === '',
  harness: {
    entry: 'frontend/fixtures/ptt-harness.html + ptt-main.ts + ptt-state.svelte.ts',
    realModules: [
      'src/components-v2/wiring/managed-tx-gesture.ts (managed gesture, unmodified)',
      'src/components-v2/controls/PttFab.svelte (unmodified)',
    ],
    intentionalDifferences: [
      'The harness records managed WS/HTTP delivery and renders only explicitly emitted '
      + 'server-shaped snapshots; there is no live radio.',
      'The orientation-swap effect (one recognizer per surface, shared live-reading fabDown/fabUp) '
      + 'is a hand-mirror of MobileRadioLayout.svelte\'s own $effect, not an import of it — that '
      + 'component pulls in $lib/runtime/stores singletons this harness does not construct. Scenarios '
      + '10/11 therefore prove the PATTERN is load-bearing where mirrored faithfully, not that '
      + 'MobileRadioLayout.svelte\'s own copy is byte-identical (verified by direct reading instead; '
      + 'see build report). A follow-up worth ticketing: extract that wiring into a reusable function '
      + 'the same way managed-tx-gesture.ts already is, so a future harness can mount the real thing.',
      'The landscape "role" is a plain button with direct handlers (matching the real m-ls-ptt '
      + 'strip\'s undelayed handlers), not a second PttFab instance — production does not reuse '
      + 'PttFab for landscape either.',
    ],
  },
  summary: { total: results.length, passed: results.filter((r) => r.ok).length, failed: failures },
  results,
};
writeFileSync(path.join(OUT, 'ptt-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`\n${results.length} scenarios (${failures} failed) → ${OUT}/ptt-manifest.json`);
process.exit(failures === 0 ? 0 : 1);
