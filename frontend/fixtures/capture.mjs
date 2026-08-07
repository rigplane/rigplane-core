/**
 * MOR-1070 — cockpit baseline capture runner.
 *
 * Verification-only tooling. Starts the additive fixtures vite config on
 * 127.0.0.1:5199, drives Playwright's bundled Chromium over the fixture
 * matrix, runs the fixture's BEHAVIOR ASSERTIONS in the page BEFORE taking any
 * screenshot, and writes both the PNGs and a manifest that records build
 * identity, media emulation, every assertion result and the intentional
 * differences.
 *
 *   node fixtures/capture.mjs [--out <dir>] [--only <substring>]
 *
 * The server is always closed, including on failure.
 */
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

const FRONTEND = path.resolve(import.meta.dirname, '..');
const REPO = path.resolve(FRONTEND, '..');
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : fallback;
};
const OUT = path.resolve(arg('--out', path.join(FRONTEND, 'fixtures-baselines')));
const ONLY = arg('--only', null);
// MOR-1085: multiple worktrees run this tool concurrently against the same
// hardcoded port, which collides ("Port 5199 is already in use") whenever two
// agent sessions capture at once. `--port` (default unchanged) lets a
// parallel run pick its own port without touching the single-port default any
// script or doc already assumes.
const PORT = Number(arg('--port', '5199'));

/* ── viewports ─────────────────────────────────────────────────────────── */
const VIEWPORTS = {
  desktop: { width: 1280, height: 800, arrangement: 'side-by-side' },
  'tablet-portrait': { width: 768, height: 1024, arrangement: 'stacked' },
  'tablet-landscape': { width: 1024, height: 768, arrangement: 'side-by-side' },
  'phone-portrait': { width: 375, height: 812, arrangement: 'stacked' },
  // 812px wide falls in the 768-1023 tablet band, and the band stacks in
  // PORTRAIT only — so a phone on its side keeps two columns. Captured
  // deliberately: it is the boundary the MOR-1069 policy actually draws.
  'phone-landscape': { width: 812, height: 375, arrangement: 'side-by-side' },
};

/* ── the capture matrix ────────────────────────────────────────────────── */
/** { name, fixture, viewport, media?, touch?, theme?, focusTabs?, note? } */
const MATRIX = [
  // A. the reference dual state across every viewport + orientation
  ...Object.keys(VIEWPORTS).map((v) => ({
    name: `dual-main-sub--${v}`, fixture: 'topology-2-main-sub', viewport: v,
  })),
  // B. the four topology pairs
  { name: 'topology-1-single--desktop', fixture: 'topology-1-single', viewport: 'desktop' },
  { name: 'topology-1-ab--desktop', fixture: 'topology-1-ab', viewport: 'desktop' },
  { name: 'topology-2-ab-shared--desktop', fixture: 'topology-2-ab-shared', viewport: 'desktop' },
  {
    name: 'topology-2-ab-shared--phone-portrait',
    fixture: 'topology-2-ab-shared', viewport: 'phone-portrait',
  },
  // C. the orthogonal audio-only scope condition
  { name: 'audio-only-scope--desktop', fixture: 'audio-only-scope', viewport: 'desktop' },
  // D. startup window
  { name: 'sub-unobserved--desktop', fixture: 'sub-unobserved', viewport: 'desktop' },
  // E. operational gating (MOR-1256)
  { name: 'dual-rx-unavailable--desktop', fixture: 'dual-rx-unavailable', viewport: 'desktop' },
  {
    name: 'dual-rx-unavailable--phone-portrait',
    fixture: 'dual-rx-unavailable', viewport: 'phone-portrait',
  },
  // F. RX / pending / TX / fault
  { name: 'tx-phase-rx--desktop', fixture: 'tx-phase-rx', viewport: 'desktop' },
  { name: 'tx-phase-pending--desktop', fixture: 'tx-phase-pending', viewport: 'desktop' },
  { name: 'tx-phase-tx--desktop', fixture: 'tx-phase-tx', viewport: 'desktop' },
  { name: 'tx-phase-fault--desktop', fixture: 'tx-phase-fault', viewport: 'desktop' },
  { name: 'tx-phase-tx--phone-portrait', fixture: 'tx-phase-tx', viewport: 'phone-portrait' },
  // G. connection loss, both honest readings
  {
    name: 'connection-loss-stale--desktop',
    fixture: 'connection-loss-stale', viewport: 'desktop',
  },
  {
    name: 'connection-loss-state-null--desktop',
    fixture: 'connection-loss-state-null', viewport: 'desktop',
  },
  {
    name: 'connection-loss-state-null--phone-portrait',
    fixture: 'connection-loss-state-null', viewport: 'phone-portrait',
  },
  { name: 'caps-unloaded--desktop', fixture: 'caps-unloaded', viewport: 'desktop' },
  // H. acceptance gate (b): the three conditional zone-less controls
  { name: 'tx-adjacent-alerts--desktop', fixture: 'tx-adjacent-alerts', viewport: 'desktop' },
  // I. media emulation on the reference dual state
  {
    name: 'dual-main-sub--desktop--reduced-motion', fixture: 'topology-2-main-sub',
    viewport: 'desktop', media: { reducedMotion: 'reduce' },
  },
  {
    name: 'dual-main-sub--desktop--contrast-more', fixture: 'topology-2-main-sub',
    viewport: 'desktop', media: { contrast: 'more' },
  },
  {
    name: 'dual-main-sub--desktop--forced-colors', fixture: 'topology-2-main-sub',
    viewport: 'desktop', media: { forcedColors: 'active' },
  },
  {
    name: 'dual-main-sub--phone-portrait--reduced-motion', fixture: 'topology-2-main-sub',
    viewport: 'phone-portrait', media: { reducedMotion: 'reduce' },
  },
  // J. keyboard focus — real `:focus-visible`, reached by Tab, not by .focus()
  {
    name: 'focus-visible-key-control--desktop', fixture: 'topology-2-main-sub',
    viewport: 'desktop', focusTabs: 6,
  },
  {
    name: 'focus-visible-first-select--desktop', fixture: 'topology-2-main-sub',
    viewport: 'desktop', focusTabs: 1,
  },
  // K. coarse pointer — the 44px hit-size floor, on laid-out boxes
  {
    name: 'touch-targets--phone-portrait', fixture: 'topology-2-main-sub',
    viewport: 'phone-portrait', touch: true,
  },
  // L. the honest un-themed reading (see fixtures/main.ts)
  {
    name: 'dual-main-sub--desktop--no-v2-theme', fixture: 'topology-2-main-sub',
    viewport: 'desktop', theme: 'none',
  },
  // M. MOR-1085 — new topology×state cells (both dual topologies now have
  // "unsupported controls" / "selection fallback" coverage, and 1/ab gets a
  // per-slot selection-fallback state; see catalog.ts for the fixtures).
  {
    name: 'topology-2-ab-shared-unsupported-controls--desktop',
    fixture: 'topology-2-ab-shared-unsupported-controls', viewport: 'desktop',
  },
  {
    name: 'topology-2-ab-shared-selection-fallback--desktop',
    fixture: 'topology-2-ab-shared-selection-fallback', viewport: 'desktop',
  },
  {
    name: 'topology-1-ab-selection-fallback--desktop',
    fixture: 'topology-1-ab-selection-fallback', viewport: 'desktop',
  },
  // N. MOR-1085 — the reference-layout twin of every cell above that has
  // one (every `CORE_FIXTURES` entry except `tx-adjacent-alerts`, see
  // `toReferenceFixture` in catalog.ts). One capture per state at `desktop`
  // — the reflow/media/focus/touch dimensions (sections I-L) are properties
  // of the CSS the cockpit shell alone owns (`DualReceiverCockpit.svelte`'s
  // `@media` blocks), not of the shared wiring, so they are not re-swept
  // per layout.
  ...[
    'topology-1-single', 'topology-1-ab', 'topology-1-ab-selection-fallback',
    'topology-2-ab-shared', 'topology-2-ab-shared-unsupported-controls',
    'topology-2-ab-shared-selection-fallback', 'topology-2-main-sub',
    'audio-only-scope', 'sub-unobserved', 'dual-rx-unavailable',
    'tx-phase-rx', 'tx-phase-pending', 'tx-phase-tx', 'tx-phase-fault',
    'connection-loss-stale', 'connection-loss-state-null', 'caps-unloaded',
  ].map((id) => ({
    name: `${id}--reference--desktop`, fixture: `${id}--reference`, viewport: 'desktop',
  })),
  // O. MOR-1087 item 2 — focus restoration across an orientation change.
  {
    name: 'focus-restored-orientation-change--phone-portrait-to-landscape',
    fixture: 'topology-2-main-sub', viewport: 'phone-portrait', focusTabs: 6,
    resizeTo: 'phone-landscape',
  },
  // P. MOR-1087 item 3 — native Space/Enter activation of a real <button>,
  // proven via the same command-bus recording every click already uses.
  {
    name: 'keyboard-activation-vfo-split--desktop', fixture: 'topology-2-main-sub',
    viewport: 'desktop',
    keyboardActivate: { selector: '[data-vfo-split]', key: 'Space', expectCall: 'vfo.split' },
  },
  {
    name: 'keyboard-activation-rx-tx-key--desktop', fixture: 'tx-phase-rx',
    viewport: 'desktop',
    keyboardActivate: { selector: '[data-testid="rx-tx-key"]', key: 'Enter', expectCall: 'tx.start' },
  },
  // Q. MOR-1087 items 5/7 — per-language contrast + unmistakable RX/TX/fault
  // indication, over both registered design languages (section A covers
  // "default" already).
  ...['studioline', 'fieldline'].flatMap((language) => [
    { name: `dual-main-sub--desktop--${language}`, fixture: 'topology-2-main-sub',
      viewport: 'desktop', language },
    { name: `dual-main-sub--desktop--${language}--light`, fixture: 'topology-2-main-sub',
      viewport: 'desktop', language, languageMode: 'light' },
    { name: `tx-phase-tx--desktop--${language}`, fixture: 'tx-phase-tx',
      viewport: 'desktop', language },
  ]),
  // R. MOR-1355 — the harness's one PLAN-FUL capture: `main.ts` resolves a
  // real `SurfacePlan` for this fixture (`catalog.ts`'s `planned: true`), so
  // `tx-aux` is genuinely bound rather than merely declared. Same radio as
  // section A's `topology-2-main-sub`; the only variable is plan-ful vs
  // plan-less.
  {
    name: 'topology-2-main-sub--planned--desktop', fixture: 'topology-2-main-sub--planned',
    viewport: 'desktop',
  },
  // S. MOR-1392 — a controlled audio-runtime axis on the reference layout.
  // These are behavior-assertion cells, not PNG hash baselines (MOR-1390).
  ...['rx-audio-live-link-down', 'rx-audio-live-link-up', 'rx-audio-muted-link-down']
    .map((id) => ({
      name: `${id}--reference--desktop`, fixture: `${id}--reference`, viewport: 'desktop',
    })),
];

/* ── build identity ────────────────────────────────────────────────────── */
const git = (...args) =>
  execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8' }).trim();
const PRODUCTION_SOURCES = [
  'frontend/src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte',
  'frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte',
  'frontend/src/components-v2/wiring/dual-receiver-strips.ts',
  'frontend/src/semantic/VfoSurface.svelte',
  'frontend/src/semantic/RxTxSurface.svelte',
  'frontend/src/semantic/rx-tx-surface.ts',
  'frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts',
  'frontend/src/lib/runtime/adapters/presentation-capabilities.ts',
  'frontend/src/presentation/layouts/dual-receiver-cockpit.ts',
  // MOR-1355: the plan-ful capture genuinely exercises these two — the
  // resolution seam and the default-workspace path `resolveSurfacePlan` reads.
  'frontend/src/presentation/workspace/resolution.ts',
  'frontend/src/presentation/workspace/contract.ts',
  'frontend/src/app.css',
  'frontend/src/styles/tokens.css',
  'frontend/src/styles/animations.css',
  'frontend/src/components-v2/theme/tokens.css',
];
const sha256 = (file) =>
  createHash('sha256').update(readFileSync(path.join(REPO, file))).digest('hex');
const buildIdentity = {
  repo: 'rigplane-core',
  headSha: git('rev-parse', 'HEAD'),
  headShaShort: git('rev-parse', '--short=8', 'HEAD'),
  branch: git('rev-parse', '--abbrev-ref', 'HEAD'),
  headSubject: git('log', '-1', '--pretty=%s'),
  worktreeClean: git('status', '--porcelain', '--', 'frontend/src', 'src') === '',
  node: process.version,
  playwright: JSON.parse(
    readFileSync(path.join(FRONTEND, 'node_modules/@playwright/test/package.json'), 'utf8'),
  ).version,
  svelte: JSON.parse(
    readFileSync(path.join(FRONTEND, 'node_modules/svelte/package.json'), 'utf8'),
  ).version,
  productionSourceDigests: Object.fromEntries(
    PRODUCTION_SOURCES.map((f) => [f, sha256(f).slice(0, 16)]),
  ),
};

/* ── run ───────────────────────────────────────────────────────────────── */
mkdirSync(OUT, { recursive: true });
const server = await createServer({
  configFile: path.join(FRONTEND, 'vite.fixtures.config.ts'),
  root: FRONTEND,
  logLevel: 'warn',
  server: { port: PORT, strictPort: true, host: '127.0.0.1' },
});
await server.listen();

/**
 * This host has the shared ms-playwright cache populated by a NEWER
 * playwright than the repo's devDependency, so `chromium.launch()` cannot find
 * the exact revision it wants. Rather than pull a second browser build down,
 * fall back to the newest headless shell already installed and record which
 * binary produced the baselines. Override with MOR1070_CHROMIUM=<path>.
 */
async function launchChromium() {
  try {
    return { browser: await chromium.launch(), executable: 'playwright-bundled' };
  } catch (bundledError) {
    const explicit = process.env.MOR1070_CHROMIUM;
    const candidates = explicit ? [explicit] : (() => {
      const cache = path.join(process.env.HOME ?? '', 'Library/Caches/ms-playwright');
      if (!existsSync(cache)) return [];
      return readdirSync(cache)
        .filter((d) => d.startsWith('chromium_headless_shell-') || d.startsWith('chromium-'))
        .sort((a, b) => Number(b.split('-').pop()) - Number(a.split('-').pop()))
        // Full builds first: on this host the version-mismatched headless
        // SHELL launches but fails `Page.captureScreenshot`, which would make
        // every baseline silently unobtainable.
        .flatMap((d) => [
          path.join(cache, d, 'chrome-mac-arm64',
            'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
          path.join(cache, d, 'chrome-mac-arm64', 'Chromium.app/Contents/MacOS/Chromium'),
          path.join(cache, d, 'chrome-linux', 'chrome'),
          path.join(cache, d, 'chrome-headless-shell-mac-arm64', 'chrome-headless-shell'),
          path.join(cache, d, 'chrome-headless-shell-linux', 'chrome-headless-shell'),
        ]);
    })();
    const executablePath = candidates.find((p) => existsSync(p));
    if (!executablePath) throw bundledError;
    return {
      browser: await chromium.launch({ executablePath }),
      executable: executablePath,
    };
  }
}

const { browser, executable } = await launchChromium();
buildIdentity.chromium = executable;
const captures = [];
let failures = 0;

try {
  for (const spec of MATRIX) {
    if (ONLY && !spec.name.includes(ONLY)) continue;
    const vp = VIEWPORTS[spec.viewport];
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 1,
      hasTouch: Boolean(spec.touch),
      isMobile: false,
      colorScheme: 'dark',
      reducedMotion: spec.media?.reducedMotion,
      timezoneId: 'UTC',
      locale: 'en-US',
    });
    const page = await context.newPage();
    const consoleErrors = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
    if (spec.media) await page.emulateMedia(spec.media);
    // MOR-1087 item 6: count `requestAnimationFrame` CALLS — synchronous, so
    // already meaningful by `harnessReady`. `createSmoother().start()`
    // (`LinearSMeter`/`BarGauge`, via `onMount`) schedules one rAF tick
    // UNLESS `prefers-reduced-motion` is active (`smoothing.svelte.ts`) — the
    // JS/rAF half of MOR-1233/1249/1252 a CSS-only check can't see.
    await page.addInitScript(() => {
      window.__rafCount = 0;
      const real = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = (cb) => { window.__rafCount += 1; return real(cb); };
    });

    const theme = spec.theme ?? 'v2';
    const url = `http://127.0.0.1:${PORT}/fixtures/index.html`
      + `?fixture=${spec.fixture}&theme=${theme}`
      // items 5/7: `main.ts` already supports `&language=`/`&mode=light`
      // (MOR-1074) — this file just adds the MATRIX entries that use it.
      + (spec.language ? `&language=${spec.language}` : '')
      + (spec.languageMode ? `&mode=${spec.languageMode}` : '');
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForSelector('body[data-harness-ready="true"]', { timeout: 15_000 });

    // ── keyboard focus, before assertions so the tab order is recorded too
    let focusPath = null;
    let focusedControl = null;
    if (spec.focusTabs) {
      focusPath = [];
      for (let i = 0; i < spec.focusTabs; i += 1) {
        await page.keyboard.press('Tab');
        focusPath.push(await page.evaluate(() => {
          const el = document.activeElement;
          if (!el || el === document.body) return 'BODY';
          const zone = el.closest('[data-zone-id]')?.dataset.zoneId ?? 'NO-ZONE';
          const strip = el.closest('[data-testid^="channel-strip-"]')?.dataset.stripReceiver;
          const name = el.dataset.testid
            ?? (el.hasAttribute('data-vfo-select') ? 'vfo-select'
              : el.hasAttribute('data-vfo-split') ? 'vfo-split'
                : el.hasAttribute('data-vfo-dual-watch') ? 'vfo-dual-watch'
                  : el.tagName.toLowerCase());
          return `${zone}${strip ? `/${strip}` : ''}:${name}`;
        }));
      }
      focusedControl = focusPath[focusPath.length - 1];
    }

    // MOR-1087 item 2: focus restoration across an orientation change — the
    // one live-reflow event this static, one-component-per-load harness can
    // script. Focus via Tab, resize under it, prove focus doesn't drop to body.
    let focusRestoration = null;
    if (spec.resizeTo) {
      const before = await page.evaluate(() => window.__harness.activeControl());
      const target = VIEWPORTS[spec.resizeTo];
      await page.setViewportSize({ width: target.width, height: target.height });
      const after = await page.evaluate(() => window.__harness.activeControl());
      focusRestoration = { before, after, resizedTo: spec.resizeTo };
    }

    // MOR-1087 item 3: the only bespoke shortcut system (KeyboardHandler) is
    // RadioLayout-only, out of reach here — what the semantic surfaces DO
    // declare is native Space/Enter button semantics. `.focus()` by selector
    // keeps this independent of tab-order elsewhere in the tree.
    let keyboardActivation = null;
    if (spec.keyboardActivate) {
      await page.evaluate((sel) => {
        (document.querySelector(sel))?.focus();
      }, spec.keyboardActivate.selector);
      await page.keyboard.press(spec.keyboardActivate.key);
      const calls = await page.evaluate(() => window.__harness.calls());
      keyboardActivation = {
        selector: spec.keyboardActivate.selector,
        key: spec.keyboardActivate.key,
        reachedCommandBus: calls.some((c) => c.fn === spec.keyboardActivate.expectCall),
      };
    }

    // ── BEHAVIOR ASSERTIONS FIRST ────────────────────────────────────────
    const resizedVp = spec.resizeTo ? VIEWPORTS[spec.resizeTo] : vp;
    const options = {
      arrangement: resizedVp.arrangement,
      touchTargets: Boolean(spec.touch),
      reducedMotion: spec.media?.reducedMotion === 'reduce',
      focusVisible: Boolean(spec.focusTabs),
    };
    const assertions = await page.evaluate((o) => window.__harness.assert(o), options);
    const tokens = await page.evaluate(() => window.__harness.tokens());
    const paint = await page.evaluate(() => window.__harness.paint());
    const domFocusOrder = await page.evaluate(() => window.__harness.focusOrder());
    const metersPresent = await page.evaluate(() =>
      document.querySelector('[data-testid="meters-surface"]') !== null);
    const rafCount = await page.evaluate(() => window.__rafCount);
    if (metersPresent) {
      const reduced = spec.media?.reducedMotion === 'reduce';
      const rafScheduled = rafCount > 0;
      assertions.push({
        name: 'meter-ballistics-honor-reduced-motion',
        ok: reduced ? rafCount === 0 : rafCount > 0,
        detail: `rafScheduled=${rafScheduled} · reducedMotion=${reduced} · `
          + `expected ${reduced ? 'false (no ballistics loop scheduled)' : 'true (the loop runs normally)'}`,
      });
    }
    if (focusRestoration) {
      assertions.push({
        name: 'focus-restored-after-layout-change',
        ok: focusRestoration.after !== 'NONE' && focusRestoration.after === focusRestoration.before,
        detail: `before="${focusRestoration.before}" after resize to `
          + `${focusRestoration.resizedTo}="${focusRestoration.after}"`,
      });
    }
    if (keyboardActivation) {
      assertions.push({
        name: 'keyboard-activation-reaches-command-bus',
        ok: keyboardActivation.reachedCommandBus,
        detail: `${keyboardActivation.key} on ${keyboardActivation.selector} · `
          + `reached command bus=${keyboardActivation.reachedCommandBus}`,
      });
    }
    const passed = assertions.every((a) => a.ok) && consoleErrors.length === 0;
    if (!passed) failures += 1;

    const file = `${spec.name}.png`;
    await page.screenshot({
      path: path.join(OUT, file), fullPage: false, animations: 'disabled', caret: 'hide',
    });

    captures.push({
      name: spec.name,
      file,
      valid: passed,
      fixture: spec.fixture,
      what: await page.evaluate(() => window.__harness.what),
      viewport: spec.resizeTo
        ? { id: spec.viewport, ...vp, resizedTo: { id: spec.resizeTo, ...resizedVp } }
        : { id: spec.viewport, ...vp },
      media: {
        reducedMotion: spec.media?.reducedMotion ?? 'no-preference',
        contrast: spec.media?.contrast ?? 'no-preference',
        forcedColors: spec.media?.forcedColors ?? 'none',
        colorScheme: 'dark',
        pointer: spec.touch ? 'coarse' : 'fine',
      },
      language: spec.language ?? 'none',
      languageMode: spec.languageMode ?? 'dark',
      themeLayer: theme === 'v2' ? 'components-v2/theme (tokens + themes)' : 'none (fallbacks)',
      url,
      assertions,
      assertionsPassed: assertions.filter((a) => a.ok).length,
      assertionsTotal: assertions.length,
      consoleErrors,
      focusPath,
      focusedControl,
      focusRestoration,
      keyboardActivation,
      domFocusOrder,
      tokens,
      paint,
    });
    // eslint-disable-next-line no-console
    console.log(
      `${passed ? 'PASS' : 'FAIL'}  ${spec.name}`
      + `  (${assertions.filter((a) => a.ok).length}/${assertions.length} assertions)`,
    );
    if (!passed) {
      for (const a of assertions.filter((x) => !x.ok)) {
        // eslint-disable-next-line no-console
        console.log(`        ✗ ${a.name}: ${a.detail}`);
      }
      for (const e of consoleErrors) console.log(`        ✗ console: ${e}`);
    }
    await context.close();
  }
} finally {
  await browser.close();
  await server.close();
}

const manifest = {
  ticket: 'MOR-1070',
  generatedAt: new Date().toISOString(),
  buildIdentity,
  harness: {
    entry: 'frontend/fixtures/index.html + frontend/fixtures/main.ts',
    config: 'frontend/vite.fixtures.config.ts (additive; vite.config.ts untouched)',
    stubbedSeams: [
      '$lib/runtime',
      '$lib/runtime/tx-controller/app-host',
      '$lib/runtime/adapters/mod-input-tx-guard.svelte',
      'components-v2/wiring/command-bus',
    ],
    productionFilesChanged: 0,
  },
  intentionalDifferences: [
    'The cockpit is mounted DIRECTLY (fixtures/main.ts) — resolveSkinId() has no '
    + 'cockpit branch on this commit, so no navigation path can produce these views.',
    'Four live seams are stubbed (see harness.stubbedSeams); every other module in the '
    + 'render path — adapter, capability derivation, semantic surfaces, i18n, CSS — is shipped code.',
    'Screenshots are taken with Playwright `animations: "disabled"` and `caret: "hide"` for '
    + 'determinism. The cockpit declares no animation of its own; the only transition in the '
    + 'tree is ModInputTxWarning\'s `.set-lan { transition: all .15s }`.',
    'deviceScaleFactor is 1 for every capture — baselines are CSS-pixel exact, not Retina.',
    'colorScheme is `dark` and no `data-theme` is set, so the components-v2 base token layer '
    + 'applies. The `--no-v2-theme` capture records what the cockpit looks like WITHOUT that '
    + 'layer, which is what it actually gets when mounted as the only skin (code-splitting: '
    + 'components-v2/theme/index is imported by RadioLayout/LcdLayout, never by the cockpit).',
    'The page chrome is a bare 100dvh box — no caption, padding or harness UI is composited '
    + 'into any baseline.',
    'The `--reference--` capture family (MOR-1085) mounts `SemanticRadioSurfaces` directly '
    + '(production-identical semantic subtree) but omits RadioLayout\'s chrome, legacy twins, and '
    + 'the MOR-1313 per-zone suppression arm — so the R9 rule decided at RadioLayout\'s '
    + 'semanticRxTx derivation is NOT exercised by reference captures (it is pinned separately in '
    + 'jsdom component tests).',
    'MOR-1087: `--studioline`/`--fieldline` captures activate a language by setting '
    + '`document.documentElement.dataset.designLanguage` directly (fixtures/main.ts\'s pre-existing '
    + '`&language=` param, MOR-1074), NOT the real `workspace/activation.ts` gate (no reachable UI '
    + 'path in the app yet — MOR-1048/1263 cutover work). The rendered subtree is production-identical.',
    '`focus-restored-orientation-change--*` resizes the SAME page mid-capture rather than reloading — '
    + 'the harness cannot switch mounted components without a reload, so this is restricted to a '
    + 'viewport/orientation change, not a cockpit-to-reference layout switch.',
    '`keyboard-activation-*` focuses its target via `element.focus()` by selector rather than counting '
    + 'real `Tab` presses — it proves native Space/Enter semantics on an already-focused control, not '
    + 'Tab reachability (covered separately by the focus-order assertions).',
    '`meter-ballistics-honor-reduced-motion` counts `requestAnimationFrame` calls page-wide, not scoped '
    + 'to the meters subtree — correct today (nothing else in the tree calls rAF).',
    'MOR-1355: exactly ONE capture (`topology-2-main-sub--planned--desktop`) supplies a real resolved '
    + '`SurfacePlan` (`resolveSurfacePlan(dualReceiverCockpitLayout, …)`, via `catalog.ts`\'s `planned: '
    + 'true`); every other capture stays plan-less on purpose — those still model the pre-navigation '
    + 'reference path this harness mounts directly (see the `main.ts` header). Only '
    + '`dualReceiverCockpitLayout` is ever resolved here; the `--reference` family (desktop-v2/sdr-test '
    + 'wiring) has no plan-ful twin in this slice — its manifest declares a different zone set and is '
    + 'separately scoped follow-up work.',
  ],
  viewports: VIEWPORTS,
  summary: {
    captures: captures.length,
    valid: captures.filter((c) => c.valid).length,
    invalid: failures,
    assertionsRun: captures.reduce((n, c) => n + c.assertionsTotal, 0),
    assertionsPassed: captures.reduce((n, c) => n + c.assertionsPassed, 0),
  },
  captures,
};
writeFileSync(path.join(OUT, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
// eslint-disable-next-line no-console
console.log(`\n${captures.length} captures → ${OUT}  (${failures} invalid)`);
process.exit(failures === 0 ? 0 : 1);
