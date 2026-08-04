/**
 * Architecture boundary tests (MOR-1061).
 *
 * Proves the eslint.config.js import-boundary rules for the v3 package
 * split — semantic/, presentation/, presentation/workspace/, and
 * lib/runtime/adapters/ — actually reject forbidden imports and accept
 * legal ones. Lints virtual fixture files (the paths need not exist on
 * disk) through the real flat config, so a rule regression here fails the
 * same way `npm run lint` would.
 *
 * See docs/plans/2026-07-25-ui-composition-architecture-v3.md
 * ("Dependency and product invariants", "Target ownership") and
 * docs/plans/2026-04-12-target-frontend-architecture.md.
 */
import { describe, it, expect } from 'vitest';
import { ESLint } from 'eslint';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const FRONTEND_ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..');

/**
 * Counts import-boundary violations under EITHER rule id. The adapters zone
 * (MOR-1065 ruling 2) swaps the base rule for `@typescript-eslint/
 * no-restricted-imports` so it can express `allowTypeImports`; a filter on the
 * base id alone would silently score every adapter-zone ban as "0 hits" and
 * turn the pre-existing adapter assertions below into vacuous passes.
 */
const RESTRICTED_IMPORT_RULES = new Set([
  'no-restricted-imports', '@typescript-eslint/no-restricted-imports',
]);

async function restrictedImportHits(code: string, filePath: string): Promise<number> {
  const eslint = new ESLint({ cwd: FRONTEND_ROOT });
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((m) => RESTRICTED_IMPORT_RULES.has(m.ruleId ?? '')).length;
}

describe('v3 package boundaries (MOR-1061)', () => {
  it('rejects semantic importing skins', async () => {
    const hits = await restrictedImportHits(
      `import LcdSkin from '../skins/amber-lcd/LcdSkin.svelte';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('allows semantic importing primitives', async () => {
    const hits = await restrictedImportHits(
      `import Button from '../primitives/SegmentedButton.svelte';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBe(0);
  });

  it('rejects presentation (layouts/design languages) importing transport', async () => {
    const hits = await restrictedImportHits(
      `import { sendCommand } from '$lib/transport/ws-client';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects presentation importing commands', async () => {
    const hits = await restrictedImportHits(
      `import { makeVfoHandlers } from '$lib/runtime/commands/panel-commands';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects presentation importing the raw capabilities store', async () => {
    const hits = await restrictedImportHits(
      `import { getCapabilities } from '$lib/stores/capabilities.svelte';`,
      'src/presentation/languages/RigplaneModern.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects workspace importing a skin module path', async () => {
    const hits = await restrictedImportHits(
      `import LcdSkin from '../../skins/amber-lcd/LcdSkin.svelte';`,
      'src/presentation/workspace/preferences.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('workspace still inherits the presentation transport ban', async () => {
    const hits = await restrictedImportHits(
      `import { sendCommand } from '$lib/transport/ws-client';`,
      'src/presentation/workspace/preferences.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('allows adapters to consume runtime and the capabilities store', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '../frontend-runtime';\n` +
        `import { getCapabilities } from '$lib/stores/capabilities.svelte';`,
      'src/lib/runtime/adapters/vfo-adapter.ts',
    );
    expect(hits).toBe(0);
  });

  it('rejects adapters importing components-v2 (pre-existing isolation rule, still live)', async () => {
    const hits = await restrictedImportHits(
      `import VfoPanel from '../../../components-v2/panels/VfoPanel.svelte';`,
      'src/lib/runtime/adapters/vfo-adapter.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  // ── Review cycle 1, F1: runtime-barrel bypass ──────────────────────────
  // $lib/runtime and $lib/runtime/frontend-runtime aggregate transport,
  // audioManager, and stores behind one import; banning only the individual
  // specifiers left this open. Cover alias, barrel (index), and relative
  // forms, and re-confirm adapters keep full access after the tightened ban.

  it('rejects semantic importing the runtime barrel (alias)', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '$lib/runtime';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects semantic importing frontend-runtime directly (alias)', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '$lib/runtime/frontend-runtime';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects presentation importing the runtime barrel (relative)', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '../../lib/runtime';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects presentation importing frontend-runtime directly (relative)', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '../../lib/runtime/frontend-runtime';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('still allows adapters to import frontend-runtime (alias) after the F1 tightening', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '$lib/runtime/frontend-runtime';`,
      'src/lib/runtime/adapters/vfo-adapter.ts',
    );
    expect(hits).toBe(0);
  });

  // ── Review cycle 1, F2: runtime -> presentation/semantic (reverse) ─────
  // ADR invariant 1: "Runtime imports no adapters or presentation."

  it('rejects lib/runtime importing presentation', async () => {
    const hits = await restrictedImportHits(
      `import { SpectrumFirst } from '../../presentation/layouts/SpectrumFirst';`,
      'src/lib/runtime/frontend-runtime.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects lib/runtime importing semantic', async () => {
    const hits = await restrictedImportHits(
      `import VfoDisplay from '../../semantic/VfoDisplay.svelte';`,
      'src/lib/runtime/frontend-runtime.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('still allows lib/runtime to import its own adapters (legal neighbor, no false positive on "presentation-capabilities")', async () => {
    const hits = await restrictedImportHits(
      `import { derivePresentationCapabilities } from './adapters/presentation-capabilities';`,
      'src/lib/runtime/frontend-runtime.ts',
    );
    expect(hits).toBe(0);
  });

  // ── Review cycle 1, F3: primitives had no dedicated zone ────────────────
  // April ADR: primitives may import only themes/Svelte/types; forbidden
  // from runtime, adapters, transport, audio, stores.

  it('rejects primitives importing transport', async () => {
    const hits = await restrictedImportHits(
      `import { sendCommand } from '$lib/transport/ws-client';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects primitives importing stores', async () => {
    const hits = await restrictedImportHits(
      `import { getCapabilities } from '$lib/stores/capabilities.svelte';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects primitives importing the runtime barrel', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '$lib/runtime';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects primitives importing adapters', async () => {
    const hits = await restrictedImportHits(
      `import { toVfoProps } from '$lib/runtime/adapters/vfo-adapter';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('allows primitives importing themes and sibling primitives', async () => {
    const hits = await restrictedImportHits(
      `import '../themes/tokens.css';\nimport Icon from './Icon.svelte';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBe(0);
  });

  // ── Review cycle 2, C1-A: group-as-directory false positive ────────────
  // The gitignore-style `group` glob for the runtime-barrel ban matched a
  // bare final segment as a DIRECTORY prefix, so it also caught the
  // sanctioned lib/runtime/adapters/* and lib/runtime/props/* paths. Fixed
  // by switching the relative-path ban to a `$`-anchored `regex`. Pin both
  // the fix (sanctioned paths pass) and that the ban itself still holds.

  it('allows presentation to import lib/runtime/adapters/* (the C1-A sanctioned path)', async () => {
    const hits = await restrictedImportHits(
      `import { toVfoProps } from '../../lib/runtime/adapters/vfo-adapter';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBe(0);
  });

  it('allows presentation to import lib/runtime/props/*', async () => {
    const hits = await restrictedImportHits(
      `import type { VfoProps } from '../../lib/runtime/props/vfo-props';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBe(0);
  });

  it('allows semantic to import lib/runtime/adapters/*', async () => {
    const hits = await restrictedImportHits(
      `import { toVfoProps } from '../lib/runtime/adapters/vfo-adapter';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBe(0);
  });

  it('allows semantic to import lib/runtime/props/*', async () => {
    const hits = await restrictedImportHits(
      `import type { VfoProps } from '../lib/runtime/props/vfo-props';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBe(0);
  });

  // ── Review cycle 2, C1-B: ADR INV-6 was only partially encoded ─────────
  // system-controller, scope-controller, and the TX-authority
  // tx-controller/app-host (INV-11) were still reachable from
  // presentation/semantic despite the F1 barrel ban.

  it('rejects semantic importing system-controller directly', async () => {
    const hits = await restrictedImportHits(
      `import { systemController } from '$lib/runtime/system-controller';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects semantic importing scope-controller directly', async () => {
    const hits = await restrictedImportHits(
      `import { scopeController } from '$lib/runtime/scope-controller.svelte';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects presentation importing tx-controller/app-host (alias) — the TX authority', async () => {
    const hits = await restrictedImportHits(
      `import { getAppTxController } from '$lib/runtime/tx-controller/app-host';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects presentation importing tx-controller/app-host (relative)', async () => {
    const hits = await restrictedImportHits(
      `import { getAppTxController } from '../../lib/runtime/tx-controller/app-host';`,
      'src/presentation/layouts/SpectrumFirst.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('still allows adapters to import tx-controller/app-host after C1-B', async () => {
    const hits = await restrictedImportHits(
      `import { getAppTxController } from '$lib/runtime/tx-controller/app-host';`,
      'src/lib/runtime/adapters/vfo-adapter.ts',
    );
    expect(hits).toBe(0);
  });

  // ── Review cycle 2, minor 3: pin the $lib/runtime/index ban ────────────
  // Mutation testing (verifier's M4) showed this entry was banned but had
  // no dedicated assertion — removing it left the suite green.

  it('rejects semantic importing $lib/runtime/index specifically', async () => {
    const hits = await restrictedImportHits(
      `import { runtime } from '$lib/runtime/index';`,
      'src/semantic/VfoDisplay.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  // ── Review cycle 2, minor 1: primitives commands/semantic/components-v2 ─

  it('rejects primitives importing commands', async () => {
    const hits = await restrictedImportHits(
      `import { makeVfoHandlers } from '$lib/runtime/commands/panel-commands';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects primitives importing semantic', async () => {
    const hits = await restrictedImportHits(
      `import VfoDisplay from '../semantic/VfoDisplay.svelte';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects primitives importing components-v2', async () => {
    const hits = await restrictedImportHits(
      `import VfoPanel from '../components-v2/panels/VfoPanel.svelte';`,
      'src/primitives/Knob.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  // ── Review cycle 2, minor 2: workspace .svelte + plural workspaces/ ─────

  it('rejects a workspace .svelte file importing transport', async () => {
    // .svelte fixtures need a <script> block — svelte-eslint-parser only
    // treats code inside it as a Program with ImportDeclaration nodes;
    // a bare top-level import is parsed as template text, not JS.
    const hits = await restrictedImportHits(
      `<script lang="ts">\n  import { sendCommand } from '$lib/transport/ws-client';\n</script>`,
      'src/presentation/workspace/WorkspaceSwitcher.svelte',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects the plural workspaces/ path variant importing a skin module path', async () => {
    const hits = await restrictedImportHits(
      `import LcdSkin from '../../skins/amber-lcd/LcdSkin.svelte';`,
      'src/presentation/workspaces/preferences.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  // ── MOR-1065 ruling 2: the adapters' type-only contract exception ───────
  // The v3 ADR puts the view model on the adapter side of the seam, but
  // MOR-1062 placed `RadioViewModel` under src/semantic/. Adapters may name
  // that contract; they may not depend on presentation at runtime. The whole
  // value of the exception is the asymmetry, so pin both halves.

  it('allows adapters a TYPE-ONLY import of the view-model contract', async () => {
    const hits = await restrictedImportHits(
      `import type { RadioViewModel } from '../../../semantic/radio-view-model';\n`
        + `export type X = RadioViewModel;`,
      'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    );
    expect(hits).toBe(0);
  });

  it('still rejects adapters VALUE-importing from semantic/', async () => {
    const hits = await restrictedImportHits(
      `import { validateRadioViewModel } from '../../../semantic/radio-view-model';`,
      'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('still rejects adapters importing a semantic component', async () => {
    const hits = await restrictedImportHits(
      `import VfoSurface from '../../../semantic/VfoSurface.svelte';`,
      'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('confines the exception to adapters — other lib/runtime files still cannot name semantic', async () => {
    const hits = await restrictedImportHits(
      `import type { RadioViewModel } from '../../semantic/radio-view-model';\n`
        + `export type X = RadioViewModel;`,
      'src/lib/runtime/frontend-runtime.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('leaves the adapters zone\'s own __tests__ free to import anything', async () => {
    // The `ignores:` line on the adapters block is what makes this pass: the
    // "tests may import anything" block only disables the BASE rule, so
    // without it the typescript-eslint rule would still reject this.
    const hits = await restrictedImportHits(
      `import { validateRadioViewModel } from '../../../../semantic/radio-view-model';`,
      'src/lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts',
    );
    expect(hits).toBe(0);
  });

  it('keeps the components-v2 ban live inside the adapters zone', async () => {
    const hits = await restrictedImportHits(
      `import VfoPanel from '../../../components-v2/panels/VfoPanel.svelte';`,
      'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('keeps the presentation ban live inside the adapters zone', async () => {
    const hits = await restrictedImportHits(
      `import { SpectrumFirst } from '../../../presentation/layouts/SpectrumFirst';`,
      'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    );
    expect(hits).toBeGreaterThan(0);
  });

  // ── MOR-1093: the sdr-test entrypoint's own import boundary ────────────
  // `src/skins/**/*.svelte` (and components-v2/layout/**) carry the looser
  // FORBIDDEN_RUNTIME_IMPORTS ban (transport + audioManager only — unlike
  // the presentation/ zone above, capabilities and commands are legal here)
  // since MOR-1061, but nothing in this file had ever run that rule through
  // the real config for the sdr-test path. SdrTestSkin.svelte is a pure
  // RadioLayout delegate with no other import; these pin the rule that keeps
  // it that way, and confirm the capabilities import SdrVfoScreen.svelte
  // gained under MOR-1093 (replacing a hardcoded manufacturer-specific
  // fallback table) stays inside the boundary this zone actually enforces.

  // .svelte fixtures need a <script> block (see the workspace .svelte test
  // above) — svelte-eslint-parser only treats code inside it as a Program
  // with ImportDeclaration nodes; a bare top-level import is parsed as
  // template text, not JS, and would silently score every case below as
  // "0 hits" regardless of which import it names.

  it('rejects the sdr-test entrypoint importing transport', async () => {
    const hits = await restrictedImportHits(
      `<script lang="ts">\n  import { sendCommand } from '$lib/transport/ws-client';\n</script>`,
      'src/skins/sdr-test/SdrTestSkin.svelte',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('rejects the sdr-test entrypoint importing audioManager (relative)', async () => {
    const hits = await restrictedImportHits(
      `<script lang="ts">\n  import { audioManager } from '../../lib/audio/audio-manager';\n</script>`,
      'src/skins/sdr-test/SdrTestSkin.svelte',
    );
    expect(hits).toBeGreaterThan(0);
  });

  it('allows the sdr-test entrypoint to import RadioLayout (its actual import)', async () => {
    const hits = await restrictedImportHits(
      `<script lang="ts">\n  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';\n</script>`,
      'src/skins/sdr-test/SdrTestSkin.svelte',
    );
    expect(hits).toBe(0);
  });

  it('allows the sdr-test diagnostic renderer to import capabilities (unlike the presentation/ zone)', async () => {
    const hits = await restrictedImportHits(
      `<script lang="ts">\n  import { getAgcLabels, getAttValues } from '$lib/stores/capabilities.svelte';\n</script>`,
      'src/skins/sdr-test/SdrVfoScreen.svelte',
    );
    expect(hits).toBe(0);
  });
});
