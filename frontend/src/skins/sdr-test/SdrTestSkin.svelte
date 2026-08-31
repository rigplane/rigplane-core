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
  declares one zone (`main: [vfo, rxTx]`), so the semantic surfaces replace
  `<VfoHeader>` and the sidebars' `<TxPanel>` does not render (MOR-1065). Why
  the TX twin follows the deck rather than its own zone declaration is the R9
  key/unkey argument on RadioLayout's `declared` / `semanticRxTx` derivations,
  and is not repeated here.

  Not every suppression keys on a declared zone. Mounting the deck also
  retires the settings modal's `.settings-vfo-ops-row` — split/swap/equalize,
  which the semantic `VfoSurface` has owned since MOR-1321 — and that one is
  gated on `semanticDeck` itself, not on any zone.

  What this manifest does not subtract: it declares no `meters` zone, so the
  legacy meters dock still renders, and the spectrum, the status bar and the
  rest of the sidebars come from the standard desktop layout.

  The skin's job is to name the entrypoint id; the manifest says what that id
  composes. Skins may not import transport, audioManager or `$lib/stores/*`
  (eslint `FORBIDDEN_SKINS_IMPORTS`, the last of those added by MOR-2039);
  `__tests__/architecture-boundaries.test.ts` exercises that rule for this
  path. `SdrVfoScreen.svelte` next door is not mounted — MOR-1065 replaced
  this top slot, and it is kept as the pre-migration prototype reference
  pending MOR-1099.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="sdr-test" />
