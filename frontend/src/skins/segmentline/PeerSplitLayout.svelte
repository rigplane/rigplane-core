<!--
  Peer Split Layout (MOR-2155) — the `peer-split` SkinId's minimal shell.
  Makes the id addressable and loadable only: NOT backed by a layout
  manifest (MOR-2151) and NOT reachable from `resolveSkinId` or the picker
  (MOR-2152). Real composition is MOR-2153 — this mounts the dual-receiver
  wiring (`SemanticRadioSurfaces` with `strips="dual"`) and nothing else,
  same wiring `dual-receiver-cockpit/DualReceiverCockpit.svelte` reuses
  unmodified. Only ONE `SemanticRadioSurfaces` may ever be mounted here: a
  second would be a second TX lease source, and single TX authority is the
  hardest invariant in this tree (see that file's header for the full
  rationale).

  Cannot be mounted standalone: `SemanticRadioSurfaces` calls
  `getAppTxController()`, which throws `Error: App TxController host is not
  provided` unless `provideAppTxControllerHost` ran higher in the tree — that
  is `App.svelte`'s job, not this shell's. Confirmed by mounting this
  component directly with no App-provided context: it throws before adding
  anything to the DOM (empty `innerHTML`, zero children). Do not add a mock
  or stub host here to paper over that — MOR-2153 owns real composition, and
  a real render needs the same harness `DualReceiverCockpit.component.test.ts`
  builds (App's runtime/tx-controller/command mocks), not a shortcut in this
  file.
-->
<script lang="ts">
  // MOR-1257 (N4): the components-v2 theme layer (fonts, base tokens, and
  // the MOR-1232 focus-ring pair --v2-focus-ring-color/--v2-focus-ring) is
  // code-split per skin. A standalone mount that skips this side-effect
  // import never loads the theme layer, and app.css's
  // `outline: var(--v2-focus-ring, var(--focus-ring))` silently falls back
  // to the legacy ring (MOR-1070 evidence run finding N4, same reasoning
  // `DualReceiverCockpit.svelte` documents for its own copy of this import).
  import '../../components-v2/theme/index';
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
</script>

<SemanticRadioSurfaces strips="dual" />
