/**
 * MOR-2676 — skin witness entry: mounts the prop-less skins no other
 * fixtures-server page reaches (`LcdCockpitSkin`, `LcdScopeSkin`,
 * `FlagshipProbeSkin`, `DualSdrFaceSkin`) plus the full desktop shell
 * (`DesktopSkin` through `DesktopShellWitness.svelte`, the same
 * `SemanticRadioSurfaces` children-snippet shape `App.svelte` composes).
 *
 * Same recipe as `mobile-witness.ts` (MOR-2240): a standalone page in the
 * fixtures server — not a new fixture id in `catalog.ts` — over the SAME four
 * stubbed seams (`vite.fixtures.config.ts`'s table: `$lib/runtime`,
 * `tx-controller/managed-app-host`, `mod-input-tx-guard`, and the
 * SemanticRadioSurfaces-only `panel-adapters` stub). Everything else — the
 * real skins, the real `RadioLayout`/`LcdLayout` chrome, the real panels, the
 * real i18n catalog, the real theme layer — is shipped code.
 *
 * Query parameters:
 *   ?skin=<id>      one of SKINS below (required)
 *   &fixture=<id>   one of `catalog.ts`'s FIXTURES ids (default
 *                   `topology-2-main-sub`) — the state axis the
 *                   MOR-2676 smoke test drives lives there
 *                   (`<face>--all-unread`, `<face>--all-unsupported`, …)
 *   &theme=v2|none  the components-v2 theme layer (default `v2`)
 *   &language=<id>  a design-language overlay (MOR-2676; default: none)
 *   &mode=light     the language's light variant
 */
import { flushSync, mount } from 'svelte';
import '../src/app.css';
import LcdCockpitSkin from '../src/skins/lcd-cockpit/LcdCockpitSkin.svelte';
import LcdScopeSkin from '../src/skins/lcd-scope/LcdScopeSkin.svelte';
import FlagshipProbeSkin from '../src/skins/flagship-probe/FlagshipProbeSkin.svelte';
import DualSdrFaceSkin from '../src/skins/dual-sdr-face/DualSdrFaceSkin.svelte';
import DesktopShellWitness from './DesktopShellWitness.svelte';
import { fixtureById } from './catalog';
import { DEFAULT_AUDIO_RUNTIME, harness, IDLE_TX } from './harness-state';
import { clearCapabilities, setCapabilities } from '../src/lib/stores/capabilities.svelte';
import { applyDesignLanguage } from './languages';

const SKINS = {
  'lcd-cockpit': LcdCockpitSkin,
  'lcd-scope': LcdScopeSkin,
  'flagship-probe': FlagshipProbeSkin,
  'dual-sdr-face': DualSdrFaceSkin,
  'desktop-v2': DesktopShellWitness,
} as const;
type WitnessSkin = keyof typeof SKINS;

const params = new URLSearchParams(window.location.search);
const skinParam = params.get('skin');
if (skinParam === null || !(skinParam in SKINS)) {
  throw new Error(`MOR-2676 skin witness: unknown skin "${skinParam}"`);
}
const skin = skinParam as WitnessSkin;
const id = params.get('fixture') ?? 'topology-2-main-sub';
const fixture = fixtureById(id);
if (!fixture) {
  throw new Error(`MOR-2676 skin witness: unknown fixture '${id}'`);
}

// Same holder priming as `mobile-witness.ts`: the stubbed runtime state, the
// real capabilities singleton (S-meter, quick modes, TX chip gating), the TX
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

// The design-language overlay is part of the smoke test's face × state ×
// language matrix; no page-specific default here (unlike index.html's
// peer-split, none of these skins is one language's own).
await applyDesignLanguage(params);

mount(SKINS[skin], { target: document.getElementById('app')! });
flushSync();
document.body.dataset.harnessReady = 'true';
