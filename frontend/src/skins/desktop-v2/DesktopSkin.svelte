<!--
  Desktop V2 Skin — standard layout with spectrum/waterfall, and the v3
  presentation entrypoint registered under the `desktop-v2` layout manifest
  (MOR-1266, `presentation/layouts/desktop-declarations.ts`).

  It stays a thin delegate to RadioLayout — the same shape SdrTestSkin has, and
  pinned as such by `skins/__tests__/entrypoints.test.ts` — because RadioLayout
  is where the v3 resolution happens: since MOR-1313 it reads THIS entrypoint's
  manifest and suppresses, per declared zone, the legacy twin of every semantic
  surface the manifest mounts (`receiver-deck: [vfo]` → the semantic surfaces
  replace `<VfoHeader>`; `rx-tx: [rxTx]` → the sidebars' `<TxPanel>` does not
  render). The skin's job is to name the entrypoint id; the manifest says what
  that id composes.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="desktop-v2" />
