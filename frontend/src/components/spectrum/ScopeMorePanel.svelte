<!--
  "More" panel for the scope row (MOR-2545 PR1) — an anchored group panel
  that opens under the ⋯ key, over the panorama.

  Positioning (review rounds 3–4): plain `position: fixed` from the ⋯ key's
  rect, recomputed on window `resize` while open and clamped to the viewport
  with an 8 px margin; any scroll while open closes it (one capture-phase
  listener). Round 4, measured in Chromium: inside the container-type surface
  and the LCD column, `fixed` places in viewport coordinates — no top layer.
  MOR-2895 (owner, 2026-09-28): where a layout declares a fixed bottom bar
  (`data-bottom-bar` — the phone's tuning strip), the panel opens DOWN only
  when it fits between the key and the bar's measured top edge; otherwise it
  opens UP above the key, so on the portrait phone it never reaches under the
  strip. A panel that fits neither way (landscape, a tall panel) pins to the
  top margin with its max-height capped at the measured gap, and scrolls
  inside. Layouts with no bottom bar keep the pre-MOR-2895 clamp: always
  below the key, shifted up only far enough to fit (round 3).

  Close semantics, per the ticket: outside click lands on a fixed transparent
  backdrop (z-index 999, the existing popover scheme —
  `ScopeSettingsPopover.svelte`) that swallows it, so a dismiss never retunes
  the panorama underneath; Escape is a WINDOW-level keydown handler that
  exists only while MOUNTED (idempotent — closed means no handler at all, so
  it never steals Esc from the MOR-2514 frequency-digit release); focus moves
  INTO the panel on open and back to `anchor` (the ⋯ key) on close. PR1
  mounts only the RADIO-HELD group.
-->
<script lang="ts">
  import { onMount, type Snippet } from 'svelte';

  interface Props {
    onClose: () => void;
    /** The ⋯ key that opened the panel: position derives from its rect; focus returns to it on close. */
    anchor?: HTMLElement | null;
    /** Radio-held controls group (PR1). */
    radioHeld?: Snippet;
  }
  let { onClose, anchor, radioHeld }: Props = $props();

  let panel: HTMLElement | undefined = $state();

  /** Leftward from the key's right edge, shifted right at the left margin, never past the right one. */
  function place() {
    if (!panel || !anchor) return;
    // The bottom boundary MEASURES the fixed bottom chrome instead of
    // assuming the viewport edge. Any layout bar that owns the screen's
    // bottom declares itself with `data-bottom-bar` (the phone's tuning
    // strip does); the panel must clear the bar's ACTUAL top edge, read
    // live from the element — never a hard-coded height, so the strip
    // growing (76 px since MOR-2874's PTT strip) needs no change here.
    // Layouts without such chrome keep the viewport bottom minus the 8 px
    // margin; the bar gets the same 8 px clearance. jsdom reports 0×0
    // boxes, so degenerate top:0 bars are ignored.
    const barTops = Array.from(document.querySelectorAll<HTMLElement>('[data-bottom-bar]'))
      .map((el) => el.getBoundingClientRect().top)
      .filter((top) => top > 0 && top < window.innerHeight);
    const bottomLimit = barTops.length
      ? Math.min(...barTops) - 8
      : window.innerHeight - 8;
    // MOR-2895 round 3 (review item 2): a panel taller than the gap between
    // the margins (landscape, the phone's 44px rows) must still end above
    // the strip — cap its height at the MEASURED gap before measuring, so
    // the fit arithmetic below works on the rendered box and the panel
    // scrolls inside instead of spilling under the bar. The stylesheet's
    // `calc(100vh - 16px)` stays as the no-bar fallback only.
    if (barTops.length) {
      panel.style.maxHeight = `${Math.max(0, bottomLimit - 8)}px`;
    } else {
      panel.style.maxHeight = '';
    }
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: w, offsetHeight: h } = panel;
    const left = Math.max(8, Math.min(rect.right - w, window.innerWidth - 8 - w));
    // MOR-2895 (owner, 2026-09-28 15:10 EDT): on the portrait phone the ⋯
    // key sits just above the fixed bottom tuning bar, and the old
    // always-down clamp buried the panel's tail (During TX, VBW narrow)
    // under that bar. No room below ⇒ open UP, flush 8 px above the key;
    // when the panel fits neither way it pins to the top margin and the
    // measured max-height above keeps it scrollable short of the bar.
    // Round 3 (review item 3): with no bottom bar (desktop) the
    // pre-MOR-2895 clamp is back — always below the key, shifted up only
    // to fit. The up-flip belongs to the phone's bottom bar.
    let top: number;
    if (!barTops.length) {
      top = Math.max(8, Math.min(rect.bottom, window.innerHeight - 8 - h));
    } else if (rect.bottom <= bottomLimit - h) {
      top = rect.bottom;
    } else if (rect.top - 8 - h >= 8) {
      top = rect.top - 8 - h;
    } else {
      top = 8;
    }
    Object.assign(panel.style, { left: `${left}px`, top: `${top}px` });
  }

  onMount(() => {
    place();
    panel?.focus();
    const onKeydown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeydown);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', onClose, true);
    return () => {
      window.removeEventListener('keydown', onKeydown);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', onClose, true);
      anchor?.focus();
    };
  });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="scope-more-backdrop" data-testid="scope-more-backdrop" role="presentation" onclick={onClose}></div>
<div
  class="scope-more-panel"
  role="dialog"
  aria-label="More scope controls"
  tabindex="-1"
  bind:this={panel}
  data-testid="scope-more-panel"
>
  <div class="scope-more-group" data-testid="scope-more-radio-held">
    {@render radioHeld?.()}
  </div>
</div>

<style>
  .scope-more-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
  }

  .scope-more-panel {
    position: fixed;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 240px;
    max-width: calc(100vw - 16px);
    /* MOR-2895: a panel taller than the viewport (short desktop windows)
       scrolls instead of spilling past either margin. With a declared
       bottom bar place() overwrites this with the measured gap inline. */
    max-height: calc(100vh - 16px);
    overflow-y: auto;
    padding: 8px;
    background: var(--v2-bg-darkest, #0a0a0f);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
    font-size: 12px;
  }

  .scope-more-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
</style>
