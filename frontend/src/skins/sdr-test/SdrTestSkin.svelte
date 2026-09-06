<!--
  SDR Test Skin — the v3 presentation entrypoint registered under the
  `sdr-test` layout manifest (MOR-1066, `presentation/layouts/declarations.ts`:
  `sdrTestLayout`). That manifest's registration, zones and
  requiredSemanticSurfaces are pinned by
  `presentation/layouts/__tests__/sdr-registration.test.ts`.

  App's persistent semantic host supplies the same instrument composition used
  by Standard. This replaceable entrypoint names the SDR layout and forwards
  those exact handles to RadioLayout, which owns placement and responsive
  geometry while retaining the manifest-driven legacy suppression rules.

  The deck reaches past the receiver row: the settings modal's
  `.settings-vfo-ops-row` retires with it too. Split/swap/equalize pair with
  `vfo`, not a zone of their own — `VfoSurface` owns those controls
  (MOR-1321) — so the row retires on `declared.has('vfo')`, the same shape as
  `<AgcPanel>` retiring on `declared.has('dsp')`.

  MOR-1346: the manifest also declares a `meters` zone, its own zone rather
  than folded into `main` (the same one-zone-per-surface shape `desktop-v2`
  uses, MOR-1341/S5), so the legacy meters dock retires here too, in favour
  of the semantic meters surface. The spectrum, the status bar and the rest
  of the sidebars come from the standard desktop layout, untouched.

  Skins may not import transport, audioManager or `$lib/stores/*`
  (eslint `FORBIDDEN_SKINS_IMPORTS`, the last of those added by MOR-2039);
  `__tests__/architecture-boundaries.test.ts` exercises that rule for this
  path.
-->
<script lang="ts">
  import type { InstrumentComposition } from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
  import '../desktop-v2/semantic-controls.css';

  let { instruments }: { instruments: InstrumentComposition } = $props();
</script>

<RadioLayout skinId="sdr-test" {instruments} />
