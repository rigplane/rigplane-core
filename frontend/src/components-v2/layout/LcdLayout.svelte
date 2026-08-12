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

  import { getKeyboardConfig } from '$lib/stores/capabilities.svelte';
  import AmberCockpit from '../panels/lcd/AmberCockpit.svelte';
  import AmberScope from '../panels/lcd/AmberScope.svelte';
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

  // Twin-skin variant selector (#887). Default preserves today's behavior.
  // `scope` currently falls through to cockpit until C-PR1 (#895) delivers
  // a dedicated AmberScope component.
  let { variant = 'cockpit' }: { variant?: 'cockpit' | 'scope' } = $props();

  let keyboardConfig = $derived(getKeyboardConfig());
  // Reactive Display Mode (#838) — the class is applied to .lcd-frame
  // so CSS effects in lcd-vintage.css can layer on top of the base render.
  let displayMode = $derived(getLcdDisplayMode());

  const keyboardHandlers = getKeyboardHandlers();

  // MOR-1486: amber-lcd (this skin) has no tuning-STEP control anywhere —
  // neither AmberCockpit nor AmberScope render one — so an operator here
  // has no way to see the shared tuning-step store change, and no way to
  // discover or restore auto-step's state at all. Silently mutating that
  // shared store from mode changes on a skin that cannot show the result
  // is exactly the invisible-state-change dishonesty MOR-1486 was opened
  // to close (see the PR body for the ruling). Building a step affordance
  // into this skin's hardware-mimicking chrome is out of scope here, so
  // the minimal honest fix is: this layout does not drive the tuning-step
  // store's mode-follow behavior at all. Mode-follow still works normally
  // on skins that do have a STEP control (RadioLayout.svelte); amber-lcd
  // just doesn't participate, and the shared step state is left exactly
  // as set elsewhere.
</script>

<div class="lcd-layout">
  <StatusBar />
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

  <section class="content-row">
    <div class="content-left">
      <LeftSidebar hideTxPanel />
    </div>

    <main class="content-center">
      <div class="lcd-slot">
        <div
          class="lcd-frame lcd-mode-{displayMode}"
          data-lcd-variant={variant}
          data-lcd-mode={displayMode}
        >
          {#if variant === 'scope'}
            <AmberScope />
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
         surfaces (MOR-1063/1064), wired exactly once by SemanticRadioSurfaces
         — no new TX path. The legacy TX panel is suppressed on BOTH sidebars
         (a cross-sidebar drag can move it), and VfoControlPanel drops the two
         facts the surface now presents. The amber glass keeps its legacy
         presentation for this slice; MOR-1162 redesigns it. -->
    <div class="content-right">
      <div class="semantic-slot">
        <SemanticRadioSurfaces />
      </div>
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
