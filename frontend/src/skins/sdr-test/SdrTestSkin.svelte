<!--
  SDR Test Skin — the v3 presentation entrypoint registered under the
  `sdr-test` layout manifest (MOR-1066, `presentation/layouts/declarations.ts`:
  `sdrTestLayout`). That manifest's registration, zones and
  requiredSemanticSurfaces are pinned by
  `presentation/layouts/__tests__/sdr-registration.test.ts`.

  It stays a thin delegate to RadioLayout — the same shape DesktopSkin has —
  because RadioLayout is where the v3 resolution happens: since MOR-1313 it
  reads THIS entrypoint's manifest and suppresses, per declared zone, the
  legacy twin of every semantic surface the manifest mounts. `sdr-test`
  declares the VFO/RX-TX pair, so the semantic surfaces replace `<VfoHeader>` and the sidebars' `<TxPanel>` does
  not render (MOR-1065). Why the TX twin follows the deck rather than its
  own zone declaration is the R9 key/unkey argument on RadioLayout's
  `declared` / `semanticRxTx` derivations, and is not repeated here.

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

  The skin's job is to name the entrypoint id; the manifest says what that id
  composes. Skins may not import transport, audioManager or `$lib/stores/*`
  (eslint `FORBIDDEN_SKINS_IMPORTS`, the last of those added by MOR-2039);
  `__tests__/architecture-boundaries.test.ts` exercises that rule for this
  path.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="sdr-test" />
