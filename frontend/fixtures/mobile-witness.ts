/**
 * Visual-witness entry for `components-v2/layout/MobileRadioLayout.svelte`
 * (MOR-2240).
 *
 * WHY THIS EXISTS. `fixtures/index.html` mounts the cockpit / reference /
 * peer-split / LCD skins; none of them mounts `MobileRadioLayout`, which
 * `RadioLayout` composes only for `skinId === 'mobile'` — so until this
 * entry there was no approved visual baseline for the phone layout at all.
 * This is the same shape as `spectrum-witness`/`ptt-harness`: a standalone
 * page in the same fixtures server, not a new fixture id in `catalog.ts`.
 *
 * WHICH SEAMS. The four live seams come from the SAME
 * `vite.fixtures.config.ts` table as the cockpit entries (`$lib/runtime`,
 * `tx-controller/managed-app-host`, `mod-input-tx-guard`, and the
 * SemanticRadioSurfaces-only `panel-adapters` stub) plus the REAL stores
 * (`capabilities.svelte`, panel projections) — the same mix `main.ts`
 * establishes. Everything else — the real panel components, the real i18n
 * catalog, the real theme layer — is shipped code.
 *
 * WHAT IS FROZEN. One catalog fixture (`topology-2-main-sub` by default)
 * feeds the stubbed seams exactly like `main.ts`, so the pinned portrait
 * (375x812) and landscape (812x375) captures in
 * `tests/e2e/visual/visual-baselines.spec.ts` see the same radio at both
 * orientations. Orientation, fullscreen and wake-lock experiments stay
 * guarded inside the component itself (`.catch`-wrapped requestFullscreen,
 * 'wakeLock' feature check).
 */
import { flushSync, mount } from 'svelte';
import '../src/app.css';
import MobileRadioLayout from '../src/components-v2/layout/MobileRadioLayout.svelte';
import { fixtureById } from './catalog';
import { DEFAULT_AUDIO_RUNTIME, harness, IDLE_TX } from './harness-state';
import { clearCapabilities, setCapabilities } from '../src/lib/stores/capabilities.svelte';
import { applyDesignLanguage } from './languages';

const params = new URLSearchParams(window.location.search);
const id = params.get('fixture') ?? 'topology-2-main-sub';
const fixture = fixtureById(id);
if (!fixture) {
  throw new Error(`mobile-witness: unknown fixture '${id}'`);
}

// Same holder priming as `main.ts`: the stubbed runtime state, the real
// capabilities singleton (S-meter, quick modes, TX chip gating), the TX
// snapshot, the MOD-input guard banner geometry, and the audio runtime.
harness.state = fixture.state();
harness.caps = fixture.caps();
const fixtureCaps = fixture.caps();
if (fixtureCaps) setCapabilities(fixtureCaps);
else clearCapabilities();
harness.tx = { ...IDLE_TX, ...fixture.tx };
harness.modGuard = fixture.modGuard ?? { visible: false, sourceLabel: null };
harness.audioRuntime = { ...DEFAULT_AUDIO_RUNTIME, ...fixture.audioRuntime };
harness.calls = [];
harness.frameAuthority = null;
harness.frameEvidence = null;
harness.presentationAcquires = [];

// The theme layer is opt-in exactly like the sibling entries: the default
// `?theme=v2` carries the `--v2-*` token resolution any baseline needs.
if ((params.get('theme') ?? 'v2') === 'v2') {
  await import('../src/components-v2/theme/index');
}

// MOR-2676: the design-language overlay is part of the smoke test's face ×
// state × language matrix, so this page takes the same `?language=`/
// `?mode=light` contract the catalog entries already have.
await applyDesignLanguage(params);

// MOR-2713: the same font settle as `main.ts`, before the mount.
await Promise.all(Array.from(document.fonts, (face) => face.load()));

mount(MobileRadioLayout, { target: document.getElementById('app')! });
flushSync();
document.body.dataset.harnessReady = 'true';
