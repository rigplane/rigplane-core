/**
 * MOR-1088 — mobile PTT gesture/orientation safety capture runner.
 *
 * Verification-only. Drives real `PointerEvent`s (not `.click()`) against
 * `fixtures/ptt-harness.html` over the fixtures vite server, one fresh
 * `page.goto()` per scenario for full isolation, and asserts on the harness's
 * `window.__ptt` readback of the REAL `TxController`/`createPttGesture`
 * state — never a wall-clock value in a recorded detail (MOR-1087 F-A).
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
const snap = (page) => page.evaluate(() => ({
  guardId: window.__ptt.guardId(), intent: window.__ptt.intent(), calls: window.__ptt.callCount(),
}));

const SCENARIOS = [
  {
    name: 'press-hold-release-momentary',
    async run(page) {
      await pointer(page, '[data-testid="ptt-fab"], .ptt-fab', 'pointerdown');
      await wait(page, 70);
      const held = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 320);
      const released = await snap(page);
      return {
        ok: held.guardId !== null && held.intent === 'momentary'
          && released.guardId === null,
        detail: `held guard=${held.guardId !== null} intent=${held.intent} · `
          + `after release guard=${released.guardId !== null}`,
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
      const latched = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerup');
      await wait(page, 320);
      const after = await snap(page);
      return {
        ok: latched.intent === 'latched' && latched.guardId !== null
          && after.intent === 'latched' && after.guardId !== null,
        detail: `after double-tap intent=${latched.intent} guard=${latched.guardId !== null} · `
          + `unrelated up() 320ms later still intent=${after.intent} guard=${after.guardId !== null}`,
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
      const before = await snap(page);
      await pointer(page, '.ptt-fab', 'pointerdown'); // tap while latched — releases immediately, no delay
      const after = await snap(page);
      return {
        ok: before.intent === 'latched' && after.guardId === null && after.intent === null,
        detail: `before tap intent=${before.intent} · after single tap guard=${after.guardId !== null} intent=${after.intent}`,
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
      const after = await snap(page);
      return { ok: after.guardId === null && after.calls === 0,
        detail: `guard=${after.guardId !== null} calls=${after.calls} · expected never keyed` };
    },
  },
  {
    name: 'pointercancel-while-held-releases-fail-safe',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const held = await snap(page);
      await pointer(page, '.ptt-fab', 'pointercancel');
      await wait(page, 320);
      const after = await snap(page);
      return { ok: held.guardId !== null && after.guardId === null,
        detail: `held guard=${held.guardId !== null} · after cancel+320ms guard=${after.guardId !== null}` };
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
      const latched = await snap(page);
      // No matching pointerdown session on this component — a bare stray cancel.
      await pointer(page, '.ptt-fab', 'pointercancel');
      await wait(page, 320);
      const after = await snap(page);
      return {
        ok: latched.intent === 'latched' && after.intent === 'latched' && after.guardId !== null,
        detail: `latched intent=${latched.intent} · after spurious cancel+320ms intent=${after.intent} guard=${after.guardId !== null}`,
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
      const after = await snap(page);
      return { ok: after.guardId === null && after.calls === 0,
        detail: `guard=${after.guardId !== null} calls=${after.calls} · a scroll-sized move must cancel, not key` };
    },
  },
  {
    name: 'orientation-swap-idle-recreates-recognizer',
    async run(page) {
      const before = await page.evaluate(() => window.__ptt.generation());
      await page.evaluate(() => window.__ptt.setSurface('landscape'));
      const after = await page.evaluate(() => ({
        generation: window.__ptt.generation(), calls: window.__ptt.callCount(),
      }));
      return {
        ok: after.generation === before + 1 && after.calls === 0,
        detail: `generation ${before} -> ${after.generation} · calls=${after.calls} (idle rotation must not touch the authority path)`,
      };
    },
  },
  {
    name: 'orientation-swap-while-held-releases-cleanly-no-strand',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const held = await snap(page);
      await page.evaluate(() => window.__ptt.setSurface('landscape'));
      const after = await snap(page);
      const calls = await page.evaluate(() => window.__ptt.callsSince(0));
      return {
        ok: held.guardId !== null && after.guardId === null
          && calls.filter((c) => c === 'tx.release').length === 1,
        detail: `held=${held.guardId !== null} · after rotation guard=${after.guardId !== null} · `
          + `release calls=${calls.filter((c) => c === 'tx.release').length} (must be exactly 1: no strand, no double-release)`,
      };
    },
  },
  {
    name: 'orientation-swap-during-pending-release-window-no-surviving-timer',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      await pointer(page, '.ptt-fab', 'pointerup'); // arms the 300ms deferred-release window
      await wait(page, 50); // still pending
      await page.evaluate(() => window.__ptt.setSurface('landscape')); // destroy() must cancel it, not just abandon it
      await wait(page, 320); // past where the abandoned timer would have fired a SECOND release
      const calls = await page.evaluate(() => window.__ptt.callsSince(0));
      return {
        ok: calls.filter((c) => c === 'tx.release').length === 1,
        detail: `release calls=${calls.filter((c) => c === 'tx.release').length} (a surviving pending-release timer would double-fire)`,
      };
    },
  },
  {
    // MOR-1088 FINDING (verification-only — NOT fixed in this slice, file a
    // follow-up ticket): a double orientation flip within the 50ms hold-delay
    // window lets an ABANDONED older-surface press spuriously key a BRAND NEW
    // recognizer generation that the operator never actually pressed.
    // `fabDown`/`fabUp`'s liveness guard (`MobileRadioLayout.svelte` ~L396-406,
    // mirrored here) checks only the coarse `surface` label ('portrait' vs
    // 'landscape'), not which SPECIFIC recognizer generation armed the timer —
    // the ResourceDemand handle-identity trap class (see standing memory: a
    // stale async callback acts on whatever a shared mutable slot currently
    // points to, without checking it is still ITS OWN handle). This assertion
    // PINS the currently observed (unsafe) behavior as a regression floor —
    // it must fail loudly if a future change makes it WORSE (more than one
    // spurious call, or a guard belonging to neither generation) — it does
    // NOT assert the ticket's ideal "a newer key owner survives" property,
    // which this code does not currently meet.
    name: 'orientation-double-flip-ghost-press-known-gap',
    async run(page) {
      // Abandoned press on the FIRST portrait surface — never released, still
      // inside its 50ms hold-delay when we rotate away from it twice.
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 15);
      await page.evaluate(() => window.__ptt.setSurface('landscape')); // generation 2
      await wait(page, 5);
      await page.evaluate(() => window.__ptt.setSurface('portrait')); // generation 3 — a NEW, unrelated recognizer
      const g3guardBeforeGhost = await page.evaluate(() => window.__ptt.guardId());
      // The FIRST press's 50ms timer (armed at t=0) fires around now (~t=50),
      // long after generation 3 replaced generation 1 — the guard checked at
      // fire time is only the current LIVE surface label, and we are back in
      // portrait, so it passes even though generation 3 never pressed anything.
      await wait(page, 60);
      const afterGhostWindow = await snap(page);
      return {
        ok: g3guardBeforeGhost === null // generation 3 starts clean, as expected
          && afterGhostWindow.guardId !== null && afterGhostWindow.calls === 1, // KNOWN GAP: ghost keyed it anyway
        detail: `generation-3 guard before ghost=${g3guardBeforeGhost !== null} (expected false) · `
          + `after the abandoned generation-1 press's delayed timer fires: `
          + `guard=${afterGhostWindow.guardId !== null} calls=${afterGhostWindow.calls} `
          + '(expected true/1 — KNOWN GAP, not the ideal "newer owner survives"; regression floor only, see comment above)',
      };
    },
  },
  {
    name: 'touch-targets-44px-both-surfaces',
    async run(page) {
      const portrait = await page.evaluate(() => {
        const r = document.querySelector('.ptt-fab').getBoundingClientRect();
        return { w: r.width, h: r.height };
      });
      await page.evaluate(() => window.__ptt.setSurface('landscape'));
      const landscape = await page.evaluate(() => {
        const r = document.querySelector('[data-testid="ls-ptt"]').getBoundingClientRect();
        return { w: r.width, h: r.height };
      });
      const ok = portrait.w >= 44 && portrait.h >= 44 && landscape.w >= 44 && landscape.h >= 44;
      return { ok, detail: `portrait ${portrait.w}x${portrait.h} · landscape ${landscape.w}x${landscape.h} · expected >=44x44` };
    },
  },
  {
    name: 'connection-loss-epoch-bump-releases-held-tx',
    async run(page) {
      await pointer(page, '.ptt-fab', 'pointerdown');
      await wait(page, 70);
      const held = await snap(page);
      await page.evaluate(() => window.__ptt.epochBump());
      const after = await snap(page);
      return { ok: held.guardId !== null && after.guardId === null,
        detail: `held guard=${held.guardId !== null} · after an authority-epoch bump (session reconnect) guard=${after.guardId !== null}` };
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
      'src/lib/runtime/tx-controller/controller.ts (TxController, unmodified)',
      'src/lib/runtime/tx-controller/model.ts (transition(), unmodified)',
      'src/components-v2/wiring/tx-ptt-gesture.ts (createPttGesture, unmodified)',
      'src/components-v2/controls/PttFab.svelte (unmodified)',
    ],
    intentionalDifferences: [
      'Eligibility/PttObservation come from a fixed fake authority, not '
      + 'tx-controller/browser-dependencies.ts\'s WS-session projector — there is no live radio.',
      'The orientation-swap effect (one recognizer per surface, shared live-reading fabDown/fabUp) '
      + 'is a hand-mirror of MobileRadioLayout.svelte\'s own $effect, not an import of it — that '
      + 'component pulls in $lib/runtime/stores singletons this harness does not construct. Scenarios '
      + '10/11 therefore prove the PATTERN is load-bearing where mirrored faithfully, not that '
      + 'MobileRadioLayout.svelte\'s own copy is byte-identical (verified by direct reading instead; '
      + 'see build report). A follow-up worth ticketing: extract that wiring into a reusable function '
      + 'the same way tx-ptt-gesture.ts already is, so a future harness can mount the real thing.',
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
