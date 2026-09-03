<script lang="ts">
  import { onMount } from 'svelte';
  import '../theme/index';
  import { setTheme, getTheme, hasExplicitTheme } from '../theme/theme-switcher';

  if (typeof window !== 'undefined') {
    // Auto-apply warm-dark theme as the amber-lcd skin default, but respect
    // an explicit user override (if they picked another theme, don't stomp it).
    if (hasExplicitTheme()) {
      setTheme(getTheme());
    } else {
      document.documentElement.dataset.theme = 'lcd-warm';
    }
  }

  import { runtime } from '$lib/runtime';
  import { getKeyboardConfig } from '$lib/stores/capabilities.svelte';
  import { applyModeDefault } from '$lib/stores/tuning.svelte';
  import AmberCockpit from '../panels/lcd/AmberCockpit.svelte';
  import AmberScope from '../panels/lcd/AmberScope.svelte';
  import PeerSplitLayout from '../../skins/segmentline/PeerSplitLayout.svelte';
  import LcdContrastControl from '../panels/lcd/LcdContrastControl.svelte';
  import LcdDisplayModeControl from '../panels/lcd/LcdDisplayModeControl.svelte';
  import { getLcdDisplayMode } from '$lib/stores/lcd-display-mode.svelte';
  import '../panels/lcd/lcd-vintage.css';
  import VfoControlPanel from '../panels/lcd/VfoControlPanel.svelte';
  import LeftSidebar from './LeftSidebar.svelte';
  import RightSidebar from './RightSidebar.svelte';
  import KeyboardHandler from './KeyboardHandler.svelte';
  import StatusBar from './StatusBar.svelte';
  import SemanticRadioSurfaces from '../wiring/SemanticRadioSurfaces.svelte';
  import { getKeyboardHandlers } from '$lib/runtime/adapters/panel-adapters';
  import { peerSplitLayout } from '../../presentation/layouts/segmentline-declarations';
  import { getGroup } from '../../presentation/groups/contract';

  // Twin-skin variant selector (#887), widened to three by MOR-2153 PR-1.
  // Default preserves today's behavior. `scope` currently falls through to
  // cockpit until C-PR1 (#895) delivers a dedicated AmberScope component.
  let { variant = 'cockpit' }: { variant?: 'cockpit' | 'scope' | 'peer-split' } = $props();

  // MOR-2253 slice 1: the peer-split glass's canvas comes from the
  // `peer-split-glass` instrument group, resolved through the `peer-split`
  // manifest's zone reference — never a hardcoded group id here (instrument-
  // group ADR §4: "the variant branch resolves the group through the
  // manifest instead of hardcoding an id"). `undefined` when no zone names a
  // group, or the named group is not registered/fixed-native (defensive; no
  // such case exists among registered layouts today).
  const peerSplitGroupId = peerSplitLayout.zones.find((zone) => zone.group !== undefined)?.group;
  const peerSplitGroup = peerSplitGroupId ? getGroup(peerSplitGroupId) : undefined;
  // Gates BOTH the glass mount below and the right-sidebar suppression
  // (MOR-2153 PR-1's former `variant === 'peer-split'` check) on the same
  // value, so a resolution failure (unreachable today — see above) falls
  // back to the cockpit center AND keeps its usual SemanticRadioSurfaces
  // slot, rather than losing the VFO/TX affordance entirely.
  //
  // MOR-2259 carries the group's `minScale` down the same path as its
  // canvas: one resolved object, so the floor cannot be plumbed from a
  // different source than the size it floors.
  let peerSplitStage = $derived(
    variant === 'peer-split' && peerSplitGroup && peerSplitGroup.scaling.mode === 'fixed-native'
      ? { canvas: peerSplitGroup.canvas, minScale: peerSplitGroup.scaling.minScale }
      : undefined,
  );

  let radioState = $derived(runtime.state);
  let keyboardConfig = $derived(getKeyboardConfig());
  // Reactive Display Mode (#838) — the class is applied to .lcd-frame
  // so CSS effects in lcd-vintage.css can layer on top of the base render.
  let displayMode = $derived(getLcdDisplayMode());
  let activeMode = $derived(radioState?.active === 'SUB' ? radioState?.sub?.mode : radioState?.main?.mode);

  const keyboardHandlers = getKeyboardHandlers();

  // MOR-1486 (owner ruling, session 19): an earlier round of this PR
  // removed this $effect on the premise that amber-lcd has "no STEP
  // control anywhere". That premise was false. Neither AmberCockpit nor
  // AmberScope render a visible STEP readout or an AUTO indicator — that
  // part is true, and it's why this skin doesn't get the SpectrumToolbar
  // AUTO toggle (that toggle only exists on skins that render
  // SpectrumToolbar in the first place). But the shared tuning-step store
  // is actively WRITTEN and READ on this skin regardless: the global
  // ArrowUp/Down keyboard binding (`keyboard-map.ts`'s `step-up`/
  // `step-down`, routed here through the `KeyboardHandler` mounted below)
  // calls `adjustTuningStep()`, ArrowLeft/Right tuning
  // (`panel-commands.ts`'s `tune` case, ~line 1389) reads
  // `getTuningStep()` for the increment, and MediaSession volume-key
  // tuning (`lib/media/media-session.ts`) reads it too. Silently freezing
  // mode-follow here — while every other consumption path keeps working
  // — would have changed arrow-key tuning granularity across mode changes
  // on this skin with no explanation, which is worse than the missing
  // on-screen indicator this ticket set out to fix. The owner accepted
  // the indicator gap (tracked, not solved, by this ticket — see the PR
  // body) rather than disabling the behavior that's actually consumed
  // here. Restored.
  $effect(() => {
    if (activeMode) {
      applyModeDefault(activeMode);
    }
  });
</script>

<div class="lcd-layout">
  <StatusBar />
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

  <section class="content-row">
    <div class="content-left">
      <LeftSidebar hideTxPanel />
    </div>

    <main class="content-center">
      <div
        class="lcd-slot"
        data-lcd-variant={variant}
        style:aspect-ratio={peerSplitStage ? `${peerSplitStage.canvas.w} / ${peerSplitStage.canvas.h}` : undefined}
      >
        <div
          class="lcd-frame lcd-mode-{displayMode}"
          data-lcd-variant={variant}
          data-lcd-mode={displayMode}
        >
          {#if variant === 'scope'}
            <AmberScope />
          {:else if peerSplitStage}
            <PeerSplitLayout
              canvasW={peerSplitStage.canvas.w}
              canvasH={peerSplitStage.canvas.h}
              minScale={peerSplitStage.minScale}
            />
          {:else}
            <AmberCockpit />
          {/if}
        </div>
      </div>
      <div class="lcd-control-strip">
        <LcdContrastControl />
        <LcdDisplayModeControl />
      </div>
    </main>

    <!-- MOR-1092: the LCD's VFO facts and TX action are owned by the semantic
         surfaces (MOR-1063/1064). For `cockpit`/`scope`, wired exactly once
         here by SemanticRadioSurfaces — no new TX path. `peer-split`'s glass
         (`PeerSplitLayout.svelte`) mounts its own `SemanticRadioSurfaces
         strips="dual"` instead, so this slot is suppressed whenever that
         glass actually mounts (`peerSplitStage`, MOR-2153 PR-1 / MOR-2253
         slice 1) — each SemanticRadioSurfaces instance takes a
         distinct TX-lease `sourceId` from its own module-level counter
         (`components-v2/wiring/SemanticRadioSurfaces.svelte`), so two
         mounted instances would not crash, just silently duplicate the TX
         affordance. The legacy TX panel is suppressed on BOTH sidebars for
         every variant (a cross-sidebar drag can move it), and
         VfoControlPanel drops the two facts the surface now presents. The
         amber glass (`cockpit`/`scope`) keeps its legacy presentation for
         this slice; MOR-1162 redesigns it. -->
    <div class="content-right">
      {#if !peerSplitStage}
        <div class="semantic-slot">
          <SemanticRadioSurfaces />
        </div>
      {/if}
      <VfoControlPanel hideVfoFacts />
      <RightSidebar hideTxPanel />
    </div>
  </section>


</div>

<!-- Global feedback / power-health / TX indication are hosted by
     AppGlobalHost at the App composition root (MOR-1059). -->

<style>
  .lcd-layout {
    position: relative;
    display: grid;
    grid-template-rows: 28px minmax(0, 1fr);
    height: 100vh;
    height: 100dvh;
    background:
      linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%),
      var(--v2-bg-app, var(--v2-bg-darker));
    gap: 5px;
    padding: 5px;
    box-sizing: border-box;
  }

  .content-row {
    display: grid;
    grid-template-columns: 228px minmax(0, 1fr) 228px;
    grid-template-rows: minmax(0, 1fr);
    gap: 5px;
    min-height: 0;
    overflow: hidden;
  }

  .content-left,
  .content-right {
    min-height: 0;
    max-height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    padding-bottom: 4px;
    border: 1px solid var(--v2-border-panel);
    border-radius: 4px;
    background:
      linear-gradient(180deg, var(--v2-panel-bg-gradient-top) 0%, var(--v2-panel-bg-gradient-bottom) 100%);
    box-shadow: var(--v2-shadow-sm);
    scrollbar-width: none;
    -ms-overflow-style: none;
  }

  .content-left::-webkit-scrollbar,
  .content-right::-webkit-scrollbar {
    display: none;
  }

  .content-right {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .content-center {
    min-height: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 6px;
  }

  /* Control strip beneath the amber LCD surface (issue #861). Houses the
     contrast preset picker and any future non-touch controls that don't
     belong on the amber display itself. */
  .lcd-control-strip {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    flex-wrap: wrap;
    padding: 4px 6px;
    border: 1px solid var(--v2-border-panel);
    border-radius: 4px;
    background:
      linear-gradient(180deg, var(--v2-panel-bg-gradient-top) 0%, var(--v2-panel-bg-gradient-bottom) 100%);
    box-shadow: var(--v2-shadow-sm);
  }

  /* `.content-right` is a definite-height flex column, so the surfaces' own
     `height: 100%` would claim the whole column. Nesting them under an
     auto-height wrapper resolves that percentage to `auto` and leaves the
     soft-button panel and sidebar in place. */
  .semantic-slot {
    flex: 0 0 auto;
    min-height: 0;
  }

  .lcd-slot {
    width: 100%;
    min-height: 0;
    display: flex;
    aspect-ratio: 16 / 7.5;
    max-height: 100%;
  }

  /* `peer-split`'s glass (`PeerSplitLayout.svelte`) is a fixed-native stage
     sized from `peerSplitStage` (script above), not the fluid 16/7.5 the
     amber cockpit/scope variants were tuned for. Matching the slot to the
     glass's own ratio means the frame hits `ScaledStage`'s max-scale-1 clamp
     on both axes at once instead of one axis being starved by a mismatched
     frame shape, which minimises the dead space the fixed-native model
     already produces rather than adding to it.

     MOR-2253 slice 1 F2 (verifier BLOCKED): this used to be a static rule
     here — `.lcd-slot[data-lcd-variant='peer-split'] { aspect-ratio: 1280 /
     540; }` — a SECOND live restatement of the canvas `SEGMENTLINE_GLASS_
     STAGE` already resolves by reference 150-odd lines above, invisible to
     the "declared once" contour scan (neither an import of the stage
     primitive nor a fixed-native sizing literal appears in this file, which
     is exactly what let a plain CSS number slip past it once already).
     Replaced with the `style:aspect-ratio` binding on the element above,
     computed from the same `peerSplitStage` the glass mount itself uses —
     one JS value, not two independent numbers. `undefined` (any variant but
     `peer-split`) makes Svelte's `style:` directive omit the inline
     property entirely, so the base rule below applies unchanged. */

  .lcd-frame {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
    background: var(--v2-bg-card);
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
  }
</style>
